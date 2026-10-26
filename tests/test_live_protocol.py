"""F4 L1: protocolo do ao vivo (ida em quadros binários, volta em linhas JSON), `spawn_child` com pipes binários e
prioridade, comandos e ambiente do filho, pasta dos modelos (a mesma da F2) e `--prepare` sem rede."""
from __future__ import annotations

import importlib.util
import io
import os
import sys

import numpy as np
import pytest

from listener.live import protocol as pr
from listener.store import Store


def test_audio_and_control_frames_roundtrip():
    pcm = np.array([0, 1, -1, 32767, -32768], "<i2").tobytes()
    hello = pr.Hello(1234.5, names=False, model="base", threads=3)
    data = (pr.encode_control(hello.to_msg()) + pr.encode_audio("L", 16000, 16000, 5, 0, pcm)
            + pr.encode_audio("R", 48000 * 3, 48000, 4800, pr.SILENCED) + pr.encode_audio("R", 10**10, 48000, 96000, pr.GAP)
            + pr.encode_control({"type": pr.STATS, "dropped": 2}))
    r = pr.FrameReader(io.BytesIO(data))
    first = r.read()
    assert isinstance(first, pr.ControlFrame) and first.type == pr.HELLO and first.msg["v"] == pr.PROTOCOL_VERSION
    assert pr.Hello.from_msg(first.msg) == hello
    a = r.read()
    assert a == pr.AudioFrame("L", 16000, 16000, 5, 0, pcm) and not a.silent and a.end == 16005
    b = r.read()
    assert isinstance(b, pr.AudioFrame) and b.silent and b.pcm == b"" and (b.channel, b.n, b.rate) == ("R", 4800, 48000)
    c = r.read()
    assert isinstance(c, pr.AudioFrame) and c.start == 10**10 and c.flags == pr.GAP          # i64: horas de sessão a 48 kHz
    assert r.read() == pr.ControlFrame({"type": "stats", "dropped": 2})
    assert r.read() is None and r.read() is None                                               # EOF limpo na fronteira
    assert pr.HEADER_SIZE == 22 and len(pr.encode_audio("R", 0, 16000, 10**6, pr.SILENCED)) == 22   # silêncio: só o cabeçalho


def test_reader_detects_corruption_and_truncation():
    good = pr.encode_audio("L", 0, 16000, 4, 0, b"\0" * 8)
    control = pr.encode_control({"type": "x"})
    cases = [(good[:10], "cabeçalho truncado"), (good[:-1], "áudio truncado"), (b"XX" + good[2:], "magic"),
             (control[:-2], "controle truncado"), (pr.HEADER.pack(pr.MAGIC, 9, 0, 0, 0, 16000, 0), "tipo de quadro"),
             (pr.HEADER.pack(pr.MAGIC, pr.KIND_AUDIO, 5, 0, 0, 16000, 0), "inválido"),
             (pr.HEADER.pack(pr.MAGIC, pr.KIND_AUDIO, 0, 0, 0, 16000, pr.MAX_AUDIO_SAMPLES + 1), "grande demais"),
             (pr.HEADER.pack(pr.MAGIC, pr.KIND_CONTROL, 0, 0, 0, 0, 3) + b"[1]", "objeto")]
    for raw, message in cases:
        with pytest.raises(pr.ProtocolError, match=message):
            pr.FrameReader(io.BytesIO(raw)).read()
    with pytest.raises(ValueError, match="silencioso"):
        pr.encode_audio("L", 0, 16000, 4, pr.GAP, b"\0" * 8)
    with pytest.raises(ValueError, match="esperava 8 bytes"):
        pr.encode_audio("L", 0, 16000, 4, 0, b"\0" * 6)
    with pytest.raises(pr.ProtocolError, match="esperava hello"):
        pr.Hello.from_msg({"type": "stats"})
    with pytest.raises(pr.ProtocolError, match="hello inválido"):
        pr.Hello.from_msg({"type": "hello", "v": pr.PROTOCOL_VERSION})
    for v in (None, pr.PROTOCOL_VERSION + 1):                                         # app e filho de versões diferentes
        with pytest.raises(pr.ProtocolError, match="reinicie o app"):
            pr.Hello.from_msg({"type": "hello", "v": v, "t0_perf": 2})
    assert pr.Hello.from_msg({"type": "hello", "v": pr.PROTOCOL_VERSION, "t0_perf": 2, "threads": 0}).threads == 1


def test_reader_reassembles_frames_split_across_pipe_reads():
    """O pipe entrega aos pedaços: um leitor que devolve 3 bytes por vez reconstrói os quadros inteiros."""
    class Trickle(io.RawIOBase):
        def __init__(self, data: bytes):
            self.buf = io.BytesIO(data)

        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> bytes:
            return self.buf.read(min(3, n if n >= 0 else 3))

    pcm = np.arange(-50, 50, dtype="<i2").tobytes()
    data = pr.encode_audio("R", 7, 16000, 100, 0, pcm) + pr.encode_control({"type": "stats", "x": "ção"})
    r = pr.FrameReader(Trickle(data))  # type: ignore[arg-type]
    a = r.read()
    assert isinstance(a, pr.AudioFrame) and a.pcm == pcm and a.start == 7
    assert r.read() == pr.ControlFrame({"type": "stats", "x": "ção"}) and r.read() is None


def test_return_lines_roundtrip():
    msg = {"type": pr.CAPTION, "text": "olá\nmundo — ação", "latency_ms": 5400, "label": None}
    line = pr.encode_line(msg)
    assert line.endswith(b"\n") and line.count(b"\n") == 1 and pr.decode_line(line) == msg
    for bad in (b"[1]\n", b'{"x": 1}\n', b"lixo\n", b'{"type": 3}\n'):
        with pytest.raises(ValueError):
            pr.decode_line(bad)
    with pytest.raises(ValueError):
        pr.encode_line({"type": pr.CPU, "live_pct": float("nan")})


def test_live_command_env_and_models_dir(tmp_path, monkeypatch):
    from listener.live import client
    from listener.platform.common import process

    assert process.live_command(tmp_path, 7) == [sys.executable, "-m", "listener", "--live", "--db", str(tmp_path), "--meeting", "7"]
    assert process.live_prepare_command() == [sys.executable, "-m", "listener", "--live", "--prepare"]
    assert process.live_prepare_command(tmp_path)[-3:] == ["--prepare", "--db", str(tmp_path)]
    env = process.live_env(2, {"PATH": "/x"})
    assert env["OMP_NUM_THREADS"] == env["MKL_NUM_THREADS"] == "2" and env["PATH"] == "/x" and env["PYTHONUTF8"] == "1"
    from listener.pipeline import models
    from listener.pipeline.types import JobConfig

    try:
        assert client.live_models_dir("") == models.models_dir(JobConfig())                       # a mesma pasta da F2
        assert models.whisper_dir(JobConfig(models_dir=str(tmp_path), whisper_model="small")) == client.whisper_model_dir(tmp_path, "small")
    finally:
        sys.modules.pop("listener.pipeline.models", None)                                          # o app não carrega models.py
    assert client.live_models_dir(str(tmp_path)) == tmp_path
    monkeypatch.delenv("LISTENER_LIVE", raising=False)
    assert not client.models_ready(tmp_path, "small")
    (tmp_path / "faster-whisper-small").mkdir()
    (tmp_path / "faster-whisper-small" / "model.bin").write_bytes(b"x")
    assert not client.models_ready(tmp_path, "small")                                              # falta o base da degradação
    (tmp_path / "faster-whisper-base").mkdir()
    (tmp_path / "faster-whisper-base" / "model.bin").write_bytes(b"x")
    assert client.models_ready(tmp_path, "small") and client.models_ready(tmp_path, "base")
    assert client.required_models("small") == ("small", "base") and client.required_models("base") == ("base",)
    monkeypatch.setenv("LISTENER_LIVE", "fake")
    assert client.models_ready(tmp_path / "nada", "small")                                          # fake: sempre pronto


@pytest.mark.lento                                           # D-050: subprocesso
def test_spawn_child_has_binary_pipes_and_lower_priority():
    """F4: stdin e stdout binários (nada de tradução de fim de linha), stderr descartado; no posix, nice +10 pelo pai."""
    from listener.platform.common import process

    code = ("import os, sys; d = sys.stdin.buffer.read(); sys.stderr.write('ruído' * 100); "
            "sys.stdout.buffer.write(d[::-1] + str(os.nice(0) if hasattr(os, 'nice') else -99).encode())")
    p = process.spawn_child([sys.executable, "-c", code])
    out, err = p.communicate(b"a\r\nb\x00\xff", timeout=30)
    assert p.returncode == 0 and err is None and out[:6] == b"\xff\x00b\n\ra"
    if sys.platform != "win32":
        assert int(out[6:]) == min(process.NICE_MAX, os.nice(0) + process.NICE_STEP)
        assert process.lower_priority_of(2**22 + 12345) is False                                # pid inexistente: não levanta
        assert process.disable_own_power_throttling() is False and process.sync_stdout_handle() is None


@pytest.mark.lento                                           # D-050: subprocesso
def test_win32_spawn_child_is_below_normal_without_window():
    """Só no Windows (o CI tem um): prioridade abaixo do normal pelas flags de criação, pipes binários; o EcoQoS que o filho
    desliga em si mesmo."""
    if sys.platform != "win32":
        pytest.skip("Win32Platform.spawn_child só existe no Windows")
    import ctypes

    from listener.platform.win32.platform import (BELOW_NORMAL_PRIORITY_CLASS, POWER_THROTTLING_EXECUTION_SPEED,
                                                  PROCESS_POWER_THROTTLING, ProcessPowerThrottlingState, Win32Platform)

    plat = Win32Platform()
    p = plat.spawn_child([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"])
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, p.pid)                                                # PROCESS_QUERY_LIMITED_INFORMATION
        assert k32.GetPriorityClass(ctypes.c_void_p(h)) == BELOW_NORMAL_PRIORITY_CLASS
        k32.CloseHandle(ctypes.c_void_p(h))
    finally:
        out, _ = p.communicate(b"\r\n\x00", timeout=30)                                          # type: ignore[attr-defined]
    assert out == b"\r\n\x00"
    # o EcoQoS o filho desliga em si mesmo (num venv o pid do Popen é o do lançador): conferido aqui no próprio processo
    if plat.disable_own_power_throttling():
        state = ProcessPowerThrottlingState(1, 0, 0)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        if k32.GetProcessInformation(ctypes.c_void_p(k32.GetCurrentProcess()), PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                     ctypes.sizeof(state)):                                      # Windows 11 e Server 2022+
            assert state.ControlMask & POWER_THROTTLING_EXECUTION_SPEED and not state.StateMask & POWER_THROTTLING_EXECUTION_SPEED


def _lines(out: io.BytesIO) -> list[dict]:
    return [pr.decode_line(line) for line in out.getvalue().splitlines()]


def test_prepare_reports_progress_without_network(tmp_path, monkeypatch):
    from listener.live import proc

    monkeypatch.setenv("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")                     # restaurada ao fim (o prepare a define)
    monkeypatch.setenv("LISTENER_LIVE", "fake")
    out = io.BytesIO()
    assert proc.prepare(None, out) == 0
    lines = _lines(out)
    assert [(m["model"], m["status"]) for m in lines if m["type"] == "progress"] == [("small", "pronto"), ("base", "pronto")]
    assert lines[-1]["type"] == "done"
    monkeypatch.delenv("LISTENER_LIVE")
    db, models_dir = tmp_path / "db", tmp_path / "modelos"
    with Store(db) as s:
        s.set_setting("pipeline.models_dir", str(models_dir))
    (models_dir / "faster-whisper-small").mkdir(parents=True)
    (models_dir / "faster-whisper-small" / "model.bin").write_bytes(b"x")
    try:
        out = io.BytesIO()
        if importlib.util.find_spec("faster_whisper") is None:                         # sem o extra: erro claro, sem rede
            assert proc.prepare(db, out) == 1
            lines = _lines(out)
            assert lines[0] == {"type": "progress", "model": "small", "status": "pronto", "fraction": 0.5}
            assert lines[1]["status"] == "baixando" and lines[-1]["type"] == "error" and "pip install" in lines[-1]["message"]
        (models_dir / "faster-whisper-base").mkdir()
        (models_dir / "faster-whisper-base" / "model.bin").write_bytes(b"x")
        out = io.BytesIO()
        assert proc.prepare(db, out) == 0                                             # tudo presente: nada a baixar
        lines = _lines(out)
        assert [m["status"] for m in lines if m["type"] == "progress"] == ["pronto", "pronto"]
        assert lines[-1] == {"type": "done", "models_dir": str(models_dir)}
    finally:
        sys.modules.pop("listener.pipeline.models", None)
    from listener import log as applog

    applog.flush()
    text = (db / "logs" / "listener-live.log").read_text(encoding="utf-8")                  # log próprio, como o do worker
    assert importlib.util.find_spec("faster_whisper") is not None or "download do modelo base falhou" in text

