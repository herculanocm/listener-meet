import json

from listener.__main__ import main
from listener.capture.recorder import Recorder
from listener.store import Store
from tests.conftest import make_config
from tests.fake_platform import FakePlatform


def _reports(text: str) -> list[dict]:
    dec, pos, out = json.JSONDecoder(), 0, []
    while pos < len(text):
        obj, end = dec.raw_decode(text, pos)
        out.append(obj)
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def test_check_reads_a_real_recording_readonly(tmp_path, capsys):
    platform = FakePlatform(tmp_path)
    db = tmp_path / "db"
    with Store(db) as store:
        mid = store.create_meeting("t", "meeting", None, None)
        rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
        rec.start()
        platform.pump(1.0)
        rec.stop()
    before = {p.name: p.stat().st_size for p in db.iterdir() if p.suffix == ".db"}
    assert main(["--check", str(db)]) == 0
    reports = _reports(capsys.readouterr().out)
    assert reports[0]["meeting"] == mid and reports[0]["status"] == "gravada" and reports[0]["audio_file"]
    assert reports[0]["channels"]["L"]["continuity_issues"] == [] and reports[0]["channels"]["R"]["chunks"] == 2
    assert reports[0]["drift"]["L"]["fills"] == 0 and reports[0]["orphans"] == {} and reports[0]["missing_audio_files"] == []
    assert "error" in reports[0]["alignment"]                     # 1 s de áudio: janela de 10 s não cabe
    after = {p.name: p.stat().st_size for p in db.iterdir() if p.suffix == ".db"}
    assert before == after and all(p.stat().st_size == 0 for p in db.glob("*.db-wal"))   # --check não escreve nada (mode=ro)
