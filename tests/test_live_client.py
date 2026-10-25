"""F4 L2, lado do app: o desvio no gravador (quadros contínuos, pausa e mudo marcados, buraco como GAP, exceção contida)
e o `LiveClient` (deque limitado que descarta o mais antigo, ordem no pipe, filho de verdade numa thread, queda, cancelamento
e CPU)."""
from __future__ import annotations

import io
import itertools
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from listener.capture.chunker import ChannelChunker, TapFlag
from listener.live import protocol as pr
from listener.live.client import EXIT, CpuMeter, LiveClient
from listener.store import Store
from tests.fake_live import FakeEngines, ThreadChild, serve_target, tone
from tests.fake_platform import FakeChild


class Tap:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int, int, int, float]] = []

    def __call__(self, channel: str, start: int, rate: int, block: np.ndarray, flags: int) -> None:
        self.calls.append((channel, start, rate, len(block), flags, float(np.abs(block).max()) if len(block) else 0.0))

    def of(self, channel: str) -> list[tuple[str, int, int, int, int, float]]:
        return [c for c in self.calls if c[0] == channel]


def test_chunker_tap_gets_contiguous_frames_and_gaps_without_memory():
    tap = Tap()
    c = ChannelChunker("L", 100, 1, lambda _chunk: None, chunk_seconds=1.0, tap=tap)
    c.write(np.ones(50, np.float32), t_rel=0.0)
    c.write(np.ones(30, np.float32), t_rel=2.0, silenced=True)          # 1,5 s de buraco e um bloco em pausa
    c.close(end_rel=4.0)
    assert [(s, n, f) for _, s, _, n, f, _ in tap.calls] == [(0, 50, 0), (50, 150, TapFlag.GAP), (200, 30, TapFlag.SILENCED),
                                                             (230, 170, TapFlag.GAP)]
    strides = Tap()
    gap = ChannelChunker("R", 16000, 1, lambda _chunk: None,
                         tap=lambda ch, s, r, b, f: strides.calls.append((ch, s, r, len(b), f, float(b.strides[0]))))
    gap.write(np.ones(10, np.float32), t_rel=0.0)
    gap.write(np.ones(10, np.float32), t_rel=3600.0)                    # 1 h de buraco: vista de zeros sem memória
    assert strides.calls[-2][3] == 3600 * 16000 - 10 and strides.calls[-2][5] == 0
    assert int(TapFlag.GAP) == pr.GAP and int(TapFlag.SILENCED) == pr.SILENCED


def test_recorder_tap_marks_pause_mute_and_gaps_and_survives_a_failing_tap(platform, store, recorder_factory):
    platform.r_silent_windows = [(0.8, 1.6)]                               # o loopback não entrega nada de 0,8 a 1,6 s
    rec = recorder_factory()
    tap = Tap()
    rec.start()
    platform.pump(0.4)
    assert tap.calls == []
    rec.set_live_tap(tap)
    platform.pump(0.4)                                                     # 0,4..0,8 s
    rec.pause()
    platform.pump(0.4)                                                     # 0,8..1,2 s em pausa
    rec.resume()
    rec.set_muted(True)
    platform.pump(0.4)                                                     # 1,2..1,6 s: L mudo, R sem pacotes
    rec.set_muted(False)
    platform.pump(0.4)                                                     # 1,6..2,0 s: R volta e o buraco é preenchido
    rec.set_live_tap(None)
    platform.pump(0.2)
    n_calls = len(tap.calls)
    rec.stop()
    assert len(tap.calls) == n_calls                                       # desligado: nada mais
    for ch in ("L", "R"):
        frames = tap.of(ch)
        assert all(r == 16000 for _, _, r, _, _, _ in frames)
        assert all(a[1] + a[3] == b[1] for a, b in itertools.pairwise(frames))   # contíguos na linha do tempo gravada
    l_flags = {round(s / 16000, 1): f for _, s, _, _, f, _ in tap.of("L")}
    assert l_flags[0.4] == 0 and l_flags[0.8] == TapFlag.SILENCED and l_flags[1.2] == TapFlag.SILENCED and l_flags[1.6] == 0
    assert all(peak == 0 for _, _, _, _, f, peak in tap.of("L") if f)     # pausa e mudo: zeros gravados, mas marcados
    gaps = [(s, n) for _, s, _, n, f, peak in tap.of("R") if f == TapFlag.GAP and peak == 0]
    assert len(gaps) == 1 and 0.8 * 16000 <= gaps[0][0] <= 0.95 * 16000 and gaps[0][1] >= 0.6 * 16000   # o buraco do R chega como GAP
    rec2 = recorder_factory()

    def boom(*_a: object) -> None:
        raise RuntimeError("cliente quebrado")

    rec2.set_live_tap(boom)
    rec2.start()
    platform.pump(0.6)
    stats = rec2.stop()
    assert rec2.tap_errors > 2 and stats["chunks"]["L"] >= 1 and stats["unsaved"] == []       # a gravação não sente


def test_client_queue_drops_the_oldest_and_writes_frames_in_time_order(platform):
    msgs: list[dict] = []
    client = LiveClient(platform.spawn_child, ["listener", "--live"], None, pr.Hello(5.0, model="base"), msgs.append, max_blocks=3)
    for i in range(5):                                                     # antes do start: o deque enche e descarta
        client.tap("L", i * 1600, 16000, np.full(1600, 0.5, np.float32), 0)
    client.tap("R", 2400, 16000, np.broadcast_to(np.float32(0), (8000,)), pr.GAP)
    client.tap("X", 0, 16000, np.zeros(1, np.float32), 0)                  # canal desconhecido: ignorado
    assert client.dropped == 2 and [s for s, *_ in client._queues["L"]] == [3200, 4800, 6400]
    client.start()
    child = platform.children[0]
    client.close()
    client.join(3.0)
    frames = []
    reader = pr.FrameReader(io.BytesIO(bytes(child.received)))
    while (f := reader.read()) is not None:
        frames.append(f)
    assert isinstance(frames[0], pr.ControlFrame) and pr.Hello.from_msg(frames[0].msg).model == "base"
    audio = [f for f in frames if isinstance(f, pr.AudioFrame)]
    assert [(f.channel, f.start) for f in audio] == [("R", 2400), ("L", 3200), ("L", 4800), ("L", 6400)]   # ordem de tempo
    assert audio[0].silent and audio[0].n == 8000 and audio[0].pcm == b""
    assert np.frombuffer(audio[1].pcm, "<i2")[0] == round(0.5 * 32767)
    assert frames[-1] == pr.ControlFrame({"type": "stats", "dropped": 2})
    assert msgs[-1] == {"type": EXIT, "code": 0, "expected": True, "bye": False}
    client.tap("L", 99, 16000, np.zeros(10, np.float32), 0)                # depois de fechar: no-op
    assert len(client._queues["L"]) == 0


def test_client_with_the_real_child_in_a_thread_delivers_captions_and_cpu(tmp_path):
    db = tmp_path / "db"
    with Store(db) as s:
        mid = s.create_meeting("cliente", None, None, None)
    msgs: list[dict] = []
    got_exit = threading.Event()

    def on_message(msg: dict) -> None:
        msgs.append(msg)
        if msg["type"] == EXIT:
            got_exit.set()

    child_ticks = itertools.count(0.0, 10.0)
    child_cpu = CpuMeter(times=lambda: SimpleNamespace(user=next(child_ticks) * 0.1, system=0.0), clock=itertools.count(0.0, 10.0).__next__,
                         cpus=1)

    def spawn(argv, env=None):
        return ThreadChild(serve_target(db, mid, FakeEngines(), clock=lambda: 1003.0, cpu=child_cpu, cpu_interval_s=0.0), argv=argv)

    app_cpu = CpuMeter(times=lambda: SimpleNamespace(user=0.5, system=0.0), clock=itertools.count(0.0, 10.0).__next__, cpus=4)
    client = LiveClient(spawn, ["listener", "--live"], None, pr.Hello(1000.0), on_message, meeting_id=mid, cpu=app_cpu)
    client.start()
    sig = np.concatenate([tone(1.3), np.zeros(16000 * 2, np.float32)])
    for i in range(0, len(sig), 1600):
        client.tap("R", i, 16000, sig[i:i + 1600], 0)
        client.tap("L", i, 16000, sig[i:i + 1600] * 0, pr.SILENCED)
    client.close()
    assert got_exit.wait(10.0)
    client.join(3.0)
    types = [m["type"] for m in msgs if m["type"] != "cpu"]                # a CPU sai a qualquer hora (aqui, a cada quadro)
    assert types == ["ready", "caption", "bye", EXIT]
    caption = next(m for m in msgs if m["type"] == "caption")
    assert caption["channel"] == "R" and caption["text"] == "fala ao vivo 1"
    assert abs(caption["latency_ms"] - (3000 - caption["t_end_ms"])) <= 1
    cpu = [m for m in msgs if m["type"] == "cpu"]
    assert cpu and cpu[0]["live_pct"] == pytest.approx(10.0) and cpu[0]["app_pct"] == 0.0           # % da máquina, dos dois lados
    assert msgs[-1]["expected"] is True and msgs[-1]["code"] == 0 and msgs[-1]["bye"] is True
    with Store.open_readonly(db) as s:
        assert [c["text"] for c in s.captions.list_for_meeting(mid)] == ["fala ao vivo 1"]


def test_client_reports_a_crash_and_terminate_kills_at_once(platform):
    msgs: list[dict] = []
    client = LiveClient(platform.spawn_child, ["listener", "--live"], None, pr.Hello(1.0), msgs.append)
    client.start()
    child = platform.children[-1]
    child.kill()                                                           # o filho morre sozinho
    client.join(3.0)
    assert msgs == [{"type": EXIT, "code": 0, "expected": False, "bye": False}] and client.dead
    client.tap("L", 0, 16000, np.zeros(10, np.float32), 0)                 # morto: o desvio vira no-op
    assert len(client._queues["L"]) == 0
    msgs.clear()
    client2 = LiveClient(platform.spawn_child, ["listener", "--live"], None, pr.Hello(1.0), msgs.append)
    client2.start()
    t0 = time.monotonic()
    client2.terminate()
    assert platform.children[-1].killed and time.monotonic() - t0 < 1.0 and not client2.alive
    client2.join(3.0)
    assert msgs[-1]["expected"] is True


def test_client_close_kills_a_child_that_does_not_leave_in_time():
    child = FakeChild(["x"], None, 1, ignore_eof=True)                     # travado: o EOF não o encerra
    client = LiveClient(lambda argv, env: child, ["x"], None, pr.Hello(1.0), lambda m: None)
    client.start()
    client.close(grace_s=0.05)
    client.join(3.0)
    assert child.killed


def test_cpu_meter_is_machine_wide():
    t = {"cpu": 0.0, "now": 0.0}
    m = CpuMeter(times=lambda: SimpleNamespace(user=t["cpu"] * 0.75, system=t["cpu"] * 0.25), clock=lambda: t["now"], cpus=8)
    assert m.sample() is None
    t.update(cpu=4.0, now=10.0)
    assert m.sample() == pytest.approx(5.0)                                # 4 s de CPU em 10 s de 8 núcleos
    assert m.sample() is None                                              # sem tempo de parede: sem número
