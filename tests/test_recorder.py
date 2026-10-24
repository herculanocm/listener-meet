import time


from listener.capture.audio import rms
from listener.capture.recorder import RecordConfig, Recorder, SourceSpec
from listener.store import Store
from listener.store.playback import decode_range
from tests.fake_platform import FakePlatform


def cfg(**kw):
    base = dict(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5,
                silence_rms=0.01, l_alert_seconds=1.0, r_alert_seconds=1.0)
    base.update(kw)
    return RecordConfig(**base)


def test_records_two_channels_continuous(tmp_path):
    plat = FakePlatform(tmp_path)
    store = Store(tmp_path / "db")
    mid = store.create_meeting("t", "meeting", None, None)
    rec = Recorder(plat, store, mid, cfg())
    rec.start()
    time.sleep(1.3)
    rec.mark("ponto")
    stats = rec.stop()
    assert stats["chunks"]["L"] >= 2 and stats["chunks"]["R"] >= 2
    st = store.chunk_stats(mid)
    dur = store.get_meeting(mid)["duration_ms"]
    for ch in ("L", "R"):
        covered = st[ch]["frames"] * 1000 / st[ch]["sr"]
        assert abs(covered - dur) < 300, (ch, covered, dur)      # cobre a duração inteira (tolerância folgada: relógio real)
        assert st[ch]["sr"] == 16000                              # R foi reamostrado de 48 kHz
        assert store.continuity(mid, ch) == []
    assert store.get_meeting(mid)["status"] == "gravada"
    assert [e["type"] for e in store.list_events(mid)][:1] == ["start"]
    assert any(e["type"] == "marker" for e in store.list_events(mid))
    l, _ = decode_range(store, mid, "L", 200, 700)
    r, _ = decode_range(store, mid, "R", 200, 700)
    assert rms(l) > 0.1 and rms(r) > 0.1
    health = rec.health()
    assert set(health) == {"L", "R"} and set(health.values()) <= {"ok", "warn", "alert", "off"}
    assert stats["drift"]["L"]["fills"] == 0 and stats["drift"]["R"]["fills"] == 0
    store.close()


def test_pause_and_mute_write_silence_without_breaking_timeline(tmp_path):
    plat = FakePlatform(tmp_path)
    store = Store(tmp_path / "db")
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(plat, store, mid, cfg())
    rec.start()
    time.sleep(0.4)
    rec.pause()
    time.sleep(0.5)
    rec.resume()
    time.sleep(0.3)
    rec.set_muted(True)
    time.sleep(0.4)
    rec.set_muted(False)
    time.sleep(0.2)
    rec.stop()
    types = [e["type"] for e in store.list_events(mid)]
    assert ["pause", "resume", "mute", "unmute"] == [t for t in types if t in ("pause", "resume", "mute", "unmute")]
    ev = {e["type"]: e["t_ms"] for e in store.list_events(mid) if e["type"] in ("pause", "resume", "mute", "unmute")}
    paused_l, _ = decode_range(store, mid, "L", ev["pause"] + 100, ev["resume"] - 100)
    paused_r, _ = decode_range(store, mid, "R", ev["pause"] + 100, ev["resume"] - 100)
    muted_l, _ = decode_range(store, mid, "L", ev["mute"] + 100, ev["unmute"] - 100)
    muted_r, _ = decode_range(store, mid, "R", ev["mute"] + 100, ev["unmute"] - 100)
    assert rms(paused_l) < 1e-3 and rms(paused_r) < 1e-3       # pausa: silêncio nos dois canais
    assert rms(muted_l) < 1e-3 and rms(muted_r) > 0.1          # mudo: só o L fica em silêncio
    assert store.continuity(mid, "L") == [] and store.continuity(mid, "R") == []
    store.close()


def test_loopback_silence_is_filled_and_health_alerts(tmp_path):
    plat = FakePlatform(tmp_path, r_silent_windows=[(0.3, 1.2)])   # driver não entrega nada entre 0,3 e 1,2 s
    store = Store(tmp_path / "db")
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(plat, store, mid, cfg(r_alert_seconds=0.5))
    rec.start()
    time.sleep(1.0)
    assert rec.health()["R"] in ("warn", "alert")
    time.sleep(0.6)
    rec.stop()
    assert store.chunk_stats(mid)["R"]["gaps"] > 0
    assert store.continuity(mid, "R") == []
    store.close()


def test_switch_source_and_cancel(tmp_path):
    plat = FakePlatform(tmp_path)
    store = Store(tmp_path / "db")
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(plat, store, mid, cfg())
    rec.start()
    time.sleep(0.3)
    rec.switch_source(SourceSpec("system", label="Sistema"))
    time.sleep(0.4)
    assert any(e["type"] == "source_change" for e in store.list_events(mid))
    rec.cancel()
    assert store.get_meeting(mid) is None and store.list_chunks(mid) == []
    store.close()


def test_app_failure_falls_back_to_system(tmp_path):
    plat = FakePlatform(tmp_path, app_fail=True)
    store = Store(tmp_path / "db")
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(plat, store, mid, cfg())
    rec.start()
    time.sleep(0.3)
    stats = rec.stop()
    assert stats["source"] == "system" and any("não iniciou" in w for w in stats["warnings"])
    assert store.chunk_stats(mid)["R"]["n"] >= 1
    store.close()
