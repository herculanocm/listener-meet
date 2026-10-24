import numpy as np

from listener.capture.audio import encode_flac
from listener.store import Store
from listener.store.playback import decode_range


def test_two_files_and_schema(tmp_path):
    s = Store(tmp_path / "db")
    assert (tmp_path / "db" / "listener.db").exists() and (tmp_path / "db" / "listener-audio.db").exists()
    assert s.get_setting("capture.chunk_seconds") == "60"
    s.set_setting("capture.chunk_seconds", 30)
    assert s.get_int("capture.chunk_seconds") == 30
    assert s.all_settings()["hotkeys.start"] == "ctrl+shift+r"
    s.close()
    s2 = Store(tmp_path / "db")           # reabrir migra sem erro e mantém dados
    assert s2.get_int("capture.chunk_seconds") == 30
    s2.close()


def test_people_presets(tmp_path):
    s = Store(tmp_path)
    assert s.get_me() is None
    me = s.upsert_me("Fulano", "f@x.com", "CTO", "ACME")
    assert s.get_me()["id"] == me and s.get_me()["role"] == "CTO"
    s.upsert_me("Fulano B")
    assert s.get_me()["name"] == "Fulano B" and len([p for p in s._q("SELECT * FROM people")]) == 1
    s.seed_default_presets()
    names = [p["name"] for p in s.list_presets()]
    assert "Teams" in names and "Sistema inteiro" in names
    pid = s.upsert_preset("Meu", "3", "Mic", "app", "zoom.exe", is_in_room=True)
    assert s.get_preset(pid)["is_in_room"] == 1
    s.delete_preset(pid)
    assert s.get_preset(pid) is None
    s.close()


def test_meeting_chunks_markers_events_and_cascade(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting("reunião", "teams", None, "Ana, Bia")
    assert s.get_meeting(mid)["status"] == "gravando"
    tone = (0.2 * np.sin(np.linspace(0, 100, 16000))).astype(np.float32)
    for idx in range(3):
        s.insert_chunk(mid, "L", idx, idx * 1000, 16000, 16000, 0, encode_flac(tone, 16000))
    s.insert_chunk(mid, "R", 0, 0, 16000, 16000, 4000, encode_flac(np.zeros(16000, np.float32), 16000))
    s.add_marker(mid, 1500, "ponto")
    s.add_event(mid, 0, "start", {"source": "system"})
    s.add_event(mid, 2900, "stop", {})
    s.finish_meeting(mid, 3000)
    st = s.chunk_stats(mid)
    assert st["L"]["n"] == 3 and st["L"]["frames"] == 48000 and st["R"]["gaps"] == 4000
    assert s.continuity(mid, "L") == []
    samples, sr = decode_range(s, mid, "L", 500, 2500)
    assert sr == 16000 and len(samples) == 32000
    assert s.list_markers(mid)[0]["note"] == "ponto"
    assert [e["type"] for e in s.list_events(mid)] == ["start", "stop"]
    assert s.list_events(mid)[0]["data"] == {"source": "system"}
    s.update_title(mid, "novo título")
    assert s.get_meeting(mid)["title"] == "novo título"
    assert s._q("SELECT * FROM revisions")[0]["after"] == "novo título"
    s.delete_meeting(mid)
    assert s.get_meeting(mid) is None and s.list_chunks(mid) == [] and s.list_markers(mid) == []
    s.close()


def test_continuity_detects_gap_and_interrupted(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting(None, None, None, None)
    s.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, encode_flac(np.zeros(16000, np.float32), 16000))
    s.insert_chunk(mid, "L", 1, 2500, 16000, 16000, 0, encode_flac(np.zeros(16000, np.float32), 16000))
    issues = s.continuity(mid, "L")
    assert len(issues) == 1 and issues[0]["delta_ms"] == 1500
    assert s.mark_interrupted() == 1 and s.get_meeting(mid)["status"] == "interrompida"
    s.close()
