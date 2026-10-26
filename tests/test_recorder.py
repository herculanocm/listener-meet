"""Gravador com relógio manual e streams dirigidas (F1.2 M-27): nada de `sleep`, nada de thread, salvo o teste da thread."""
import tracemalloc

import numpy as np
import pytest

from listener.capture.audio import rms
from listener.capture.recorder import Recorder, SourceSpec
from listener.capture.sinks import MemorySink
from listener.model import EventType, Health, MeetingStatus
from listener.store import Store
from listener.store.playback import decode_range
from tests.conftest import make_config
from tests.fake_platform import FakePlatform


def _events(store, mid):
    return [e["type"] for e in store.list_events(mid)]


def test_records_two_channels_continuous(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.pump(1.3)
    rec.mark("ponto")
    stats = rec.stop()
    mid = rec.meeting_id
    assert stats["chunks"]["L"] == 3 and stats["chunks"]["R"] == 3        # 1,3 s em pedaços de 0,5 s
    st = store.chunk_stats(mid)
    dur = store.get_meeting(mid)["duration_ms"]
    assert dur == 1300
    for ch in ("L", "R"):
        assert st[ch]["frames"] * 1000 / st[ch]["sr"] == pytest.approx(dur, abs=1)   # cobre a duração inteira, exato
        assert st[ch]["sr"] == 16000                                               # R foi reamostrado de 48 kHz
        assert store.continuity(mid, ch) == []
    assert store.get_meeting(mid)["status"] == MeetingStatus.RECORDED == "gravada"
    assert _events(store, mid)[0] == "start" and "marker" in _events(store, mid) and _events(store, mid)[-1] == "stop"
    l, _ = decode_range(store, mid, "L", 200, 700)
    r, _ = decode_range(store, mid, "R", 200, 700)
    assert rms(l) > 0.1 and rms(r) > 0.1
    health = rec.health()
    assert set(health) == {"L", "R", "db"} and set(health.values()) <= {"ok", "warn", "alert", "off"}
    assert stats["drift"]["L"]["fills"] == 0 and stats["drift"]["R"]["fills"] == 0
    assert stats["unsaved"] == [] and stats["pending"] == 0
    assert store.get_meeting(mid)["audio_file"] in store.audio_files()


def test_resampled_channel_is_continuous_across_chunks(platform, store, recorder_factory):
    """A emenda entre pedaços do R (48 kHz -> 16 kHz) não tem tique: o tom de 220 Hz sai limpo (M-15)."""
    rec = recorder_factory()
    rec.start()
    platform.pump(1.5)
    rec.stop()
    r, sr = decode_range(store, rec.meeting_id, "R", 0, 1500)
    # derivada do sinal: um tique na fronteira de 0,5 s e 1,0 s apareceria como salto; o seno de 220 Hz tem salto máximo conhecido
    d = np.abs(np.diff(r))
    expected = 0.3 * 2 * np.pi * 220 / sr                     # |dx/dt| máximo do seno por amostra
    assert d[100:-100].max() < expected * 1.1


def test_pause_and_mute_write_silence_without_breaking_timeline(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.pump(0.4)
    rec.pause()
    platform.pump(0.5)
    rec.resume()
    platform.pump(0.3)
    rec.set_muted(True)
    platform.pump(0.4)
    rec.set_muted(False)
    platform.pump(0.2)
    assert rec.health()["L"] in ("ok", "warn")
    rec.stop()
    mid = rec.meeting_id
    types = _events(store, mid)
    assert [t for t in types if t in ("pause", "resume", "mute", "unmute")] == ["pause", "resume", "mute", "unmute"]
    ev = {e["type"]: e["t_ms"] for e in store.list_events(mid) if e["type"] in ("pause", "resume", "mute", "unmute")}
    assert ev == {"pause": 400, "resume": 900, "mute": 1200, "unmute": 1600}
    paused_l, _ = decode_range(store, mid, "L", ev["pause"] + 100, ev["resume"] - 100)
    paused_r, _ = decode_range(store, mid, "R", ev["pause"] + 100, ev["resume"] - 100)
    muted_l, _ = decode_range(store, mid, "L", ev["mute"] + 100, ev["unmute"] - 100)
    muted_r, _ = decode_range(store, mid, "R", ev["mute"] + 100, ev["unmute"] - 100)
    assert rms(paused_l) < 1e-3 and rms(paused_r) < 1e-3       # pausa: silêncio nos dois canais
    assert rms(muted_l) < 1e-3 and rms(muted_r) > 0.1          # mudo: só o L fica em silêncio
    assert store.continuity(mid, "L") == [] and store.continuity(mid, "R") == []


def test_loopback_silence_is_filled_and_health_alerts(tmp_path, store):
    platform = FakePlatform(tmp_path, r_silent_windows=[(0.3, 1.2)])   # driver não entrega nada entre 0,3 e 1,2 s
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(r_alert_seconds=0.5), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(1.0)
    assert rec.health()["R"] in (Health.WARN, Health.ALERT)
    platform.pump(0.6)
    assert rec.health()["R"] == Health.OK
    rec.stop()
    assert store.chunk_stats(mid)["R"]["gaps"] == 16000 * 0.9
    assert store.continuity(mid, "R") == []
    assert rec.stats()["drift"]["R"]["fills"] == 1


def test_thirty_minute_gap_stays_under_memory_budget(tmp_path, store):
    """Buraco de 30 min no R: fatias de zeros e FLAC de silêncio em cache; pico abaixo de 10 MB (M-03)."""
    platform = FakePlatform(tmp_path, mic_fail=True, r_silent_windows=[(0.5, 1800.5)])
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(chunk_seconds=60.0), clock=platform.clock, inline_worker=True)
    tracemalloc.start()
    try:
        rec.start()
        platform.pump(1801.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    stats = rec.stop()
    assert peak < 10_000_000, f"pico de {peak / 1e6:.1f} MB"
    assert stats["chunks"]["R"] == 31 and stats["drift"]["R"]["fills"] == 1
    assert store.continuity(mid, "R") == []
    sizes = [c["bytes"] for c in store.list_chunks(mid, "R")]
    assert max(sizes[1:30]) < 20_000                                   # pedaços 100 % silêncio saem do cache


def test_source_log_text_never_carries_the_window_title():
    """D-053: o log e os eventos levam o programa e o pid da fonte; o título da janela traz o nome de quem está do outro lado
    (dado pessoal de terceiro, LGPD) e fica só na tela."""
    app = SourceSpec("app", pid=36964, exe="ms-teams.exe", label="ms-teams — Chat | Fulano de Tal | Microsoft Teams")
    assert app.log_text() == "ms-teams.exe (pid 36964)" and "Fulano" not in app.log_text()
    assert SourceSpec("system", label="Sistema inteiro").log_text() == "sistema inteiro"
    assert SourceSpec("none").log_text() == "sem saída"


def test_switch_source_with_format_change_keeps_timeline(tmp_path, store):
    platform = FakePlatform(tmp_path, r_format=(48000, 2), system_format=(44100, 1))
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(0.3)
    assert rec.R.detail == "meeting.exe (pid 4242)"                   # o log e os eventos: sem o título da janela (D-053)
    old = platform.live_streams()
    rec.switch_source(SourceSpec("system", label="Sistema"))
    assert all(s.stopped for s in old if s.channels == 2)
    # callback tardio da stream antiga depois da troca: ignorado pelo id da fonte
    old_r = next(s for s in old if s.channels == 2)
    old_r.on_audio(np.ones((4800, 2), np.float32), platform.clock.perf)
    platform.pump(0.7)
    assert "source_change" in _events(store, mid)
    assert rec.source.kind == "system" and rec.R.detail == "sistema inteiro"
    rec.stop()
    assert store.continuity(mid, "R") == []
    assert store.chunk_stats(mid)["R"]["frames"] == 16000            # 1,0 s contínuo, sem o bloco tardio
    rec2 = Recorder(platform, store, store.create_meeting(None, None, None, None), make_config(), clock=platform.clock,
                    inline_worker=True)
    rec2.start()
    platform.pump(0.3)
    rec2.cancel()
    assert store.get_meeting(rec2.meeting_id) is None and store.list_chunks(rec2.meeting_id) == []


def test_app_failure_falls_back_to_system(tmp_path, store):
    platform = FakePlatform(tmp_path, app_fail=True)
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(0.3)
    stats = rec.stop()
    assert stats["source"] == "system" and any("não iniciou" in w for w in stats["warnings"])
    assert store.chunk_stats(mid)["R"]["n"] >= 1


def test_insert_failing_n_times_loses_nothing(platform, store, recorder_factory):
    """Banco preso 3 vezes seguidas: retentativa com backoff, ordem preservada, nada descartado (M-04)."""
    sink = MemorySink(fail_times=3)
    rec = recorder_factory(sink=sink)
    rec.start()
    platform.pump(1.6)
    assert rec.health()["db"] in (Health.WARN, Health.ALERT) or rec.snapshot.pending == 0
    stats = rec.stop()
    assert stats["unsaved"] == [] and sink.attempts >= 6 + 3
    assert [c.key for c in sink.chunks if c.channel == "L"] == ["L0", "L1", "L2", "L3"]
    assert sink.finished[-1][2] == "gravada"


def test_insert_failing_forever_marks_interrupted_and_lists_idx(platform, store, recorder_factory):
    sink = MemorySink(fail_times=10**9)
    rec = recorder_factory(make_config(stop_timeout_s=0.05), sink=sink)         # o prazo é cumprido até o fim: curto no teste
    rec.start()
    platform.pump(1.1)
    assert rec.health()["db"] == Health.WARN
    stats = rec.stop()
    assert set(stats["unsaved"]) == {"L0", "L1", "L2", "R0", "R1", "R2"}
    assert sink.finished[-1][2] == MeetingStatus.INTERRUPTED
    assert any(t == "error" and e["unsaved"] for _, _, t, e in sink.events)


def test_device_lost_and_back(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.pump(0.5)
    mic = next(s for s in platform.live_streams() if s.channels == 1)
    mic.fail("headset removido")
    platform.pump(0.3)
    assert rec.health()["L"] == Health.LOST
    rec.check_devices()                                        # registra device_lost e tenta reabrir na hora
    platform.pump(0.5)
    assert rec.health()["L"] == Health.OK
    rec.stop()
    mid = rec.meeting_id
    types = _events(store, mid)
    assert types.index("device_lost") < types.index("device_back")
    assert store.continuity(mid, "L") == []
    assert store.chunk_stats(mid)["L"]["gaps"] > 0            # o buraco virou silêncio


def test_extra_sinks_receive_every_chunk(platform, store, recorder_factory):
    mem = MemorySink()
    rec = recorder_factory(extra_sinks=[mem])
    rec.start()
    platform.pump(1.0)
    rec.stop()
    assert sorted(c.key for c in mem.chunks) == sorted(c["channel"] + str(c["idx"]) for c in store.list_chunks(rec.meeting_id))


def test_invalid_config_is_refused(platform, store):
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(target_rate=0), clock=platform.clock, inline_worker=True)
    with pytest.raises(ValueError, match="taxa gravada"):
        rec.start()


def test_worker_thread_persists_and_releases_connection(tmp_path):
    """O mesmo fluxo com a thread de trabalho real: a fila entrega em ordem e `stop()` espera esvaziar."""
    platform = FakePlatform(tmp_path)
    with Store(tmp_path / "db") as store:
        mid = store.create_meeting(None, None, None, None)
        rec = Recorder(platform, store, mid, make_config(), clock=platform.clock)
        rec.start()
        platform.pump(1.0)
        stats = rec.stop()
        assert stats["chunks"] == {"L": 2, "R": 2} and store.continuity(mid, "L") == []
        assert rec.health()["db"] == Health.OK
        assert _events(store, mid)[-1] == EventType.STOP
