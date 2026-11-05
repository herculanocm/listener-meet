"""F5.16: sem o token do Hugging Face, a reunião sai transcrita mesmo assim. O L é o usuário; o R fica sem voz nem pessoa
("Desconhecido"); nada de diarização, casamento, vetor automático nem download do pyannote; o job e o `job_done` dizem o
motivo; a reunião aberta e a fila mostram como resolver (U-139). Com o token, tudo como antes (os testes da F2 seguem)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from listener.config.defaults import HF_PROVIDER_ID
from listener.model import JobKind, JobStatus, MeetingStatus
from listener.pipeline import no_diarization
from tests.fake_pipeline import FakeDeps
from tests.test_runner import SCRIPT, _record, _run


@pytest.fixture
def platform(tmp_path, clock):
    """Tons a 16 kHz mono nos dois canais, como em `tests/test_runner.py`."""
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _no_token_deps() -> FakeDeps:
    """O pipeline falso sem o token: o diarizador e o embedder não podem ser chamados."""
    deps = FakeDeps(SCRIPT, diarization=False)

    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("sem o token, nada de pyannote")

    deps.tones.diarize = forbidden                            # type: ignore[method-assign]
    deps.tone_embedder.embed = forbidden                      # type: ignore[method-assign]
    return deps


def _processed_without_token(platform: Any, store: Any) -> tuple[int, int]:
    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    _runner, status = _run(store, _no_token_deps())
    assert status == JobStatus.DONE
    return mid, job_id


def test_without_the_token_the_meeting_is_transcribed_with_r_unknown(platform, store, caplog):
    from listener.pipeline import review

    caplog.set_level("INFO", logger="listener")
    mid, job_id = _processed_without_token(platform, store)
    assert store.get_meeting(mid)["status"] == MeetingStatus.PROCESSED
    assert [(c["channel"], c["cluster"]) for c in store.segments.list_clusters(mid)] == [("L", "L")]   # só o do usuário
    segments = store.segments.list_segments(mid)
    me = store.get_me()
    r = [s for s in segments if s["channel"] == "R"]
    l = [s for s in segments if s["channel"] == "L"]
    assert r and l
    assert all(s["cluster_id"] is None and s["cluster"] is None and s["person_id"] is None and s["attribution"] == "canal" for s in r)
    assert all(s["person_id"] == me["id"] and s["attribution"] == "canal" for s in l)        # o L segue o usuário
    rows = review.transcript_rows(store, mid)
    assert {row.name for row in rows if row.channel == "R"} == {"Desconhecido"}
    assert store._q("SELECT COUNT(*) FROM voice_embeddings")[0][0] == 0                       # nem o vetor automático do L
    timings = json.loads(store.jobs.get(job_id)["timings_json"])
    assert timings[no_diarization.KEY] == {"motivo": "token"} and "diarize" not in timings and "transcribe_r" in timings
    done = [e for e in store.list_events(mid) if e["type"] == "job_done"][-1]
    assert done["data"]["timings"][no_diarization.KEY] == {"motivo": "token"}
    assert no_diarization.meeting_reason(store, mid) == "token"
    assert store.jobs.get(job_id)["progress"] == 1.0
    assert "sem diarização" in caplog.text


def test_in_room_without_the_token_leaves_both_channels_without_a_person(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    store._x("UPDATE meetings SET is_in_room = 1 WHERE id = ?", (mid,))
    store.jobs.enqueue(mid)
    _runner, status = _run(store, _no_token_deps())
    assert status == JobStatus.DONE and store.segments.list_clusters(mid) == []
    segments = store.segments.list_segments(mid)
    assert {s["channel"] for s in segments} == {"R", "L"} and all(s["person_id"] is None for s in segments)


def test_reprocess_without_the_token_stays_without_diarization(platform, store):
    mid, _job = _processed_without_token(platform, store)
    rep = store.jobs.enqueue(mid, JobKind.REPROCESS)
    _runner, status = _run(store, _no_token_deps())
    assert status == JobStatus.DONE and store.segments.current_job_id(mid) == rep
    assert no_diarization.meeting_reason(store, mid) == "token"
    assert [c["channel"] for c in store.segments.list_clusters(mid, rep)] == ["L"]                    # nenhuma voz no R


def test_reprocess_without_the_token_never_takes_voices_away(platform, store):
    """Desvio da spec (D-093): a reunião que já tem vozes não reprocessa sem o token (também a troca de idioma, que
    reprocessa): o job recusa como erro permanente, e os segmentos e as vozes de antes ficam (CP3)."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    first = store.jobs.enqueue(mid)
    _runner, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE
    voices = [c["id"] for c in store.segments.list_clusters(mid, first) if c["channel"] == "R"]
    before = [(s["id"], s["cluster_id"]) for s in store.segments.list_segments(mid)]
    rep = store.jobs.enqueue(mid, JobKind.REPROCESS)
    _runner, status = _run(store, _no_token_deps())
    row = store.jobs.get(rep)
    assert status == JobStatus.ERROR and row["attempts"] == 0 and "token" in row["error"]
    assert voices and store.segments.current_job_id(mid) == first
    assert [(s["id"], s["cluster_id"]) for s in store.segments.list_segments(mid)] == before


def test_with_diarization_the_job_has_no_mark(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    _runner, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and store.segments.list_clusters(mid)
    assert no_diarization.KEY not in json.loads(store.jobs.get(job_id)["timings_json"])
    assert no_diarization.meeting_reason(store, mid) is None


def test_the_marks_are_read_from_the_timings():
    """O `prepare_models` sem o token e o `real_deps` estão em `tests/test_worker.py`, que já importa `pipeline/models.py`
    (aqui ele quebraria a regra de `test_app_modules_do_not_import_models`, que roda depois na mesma sessão)."""
    assert no_diarization.reason_in(json.dumps({no_diarization.KEY: {"motivo": "token"}})) == "token"
    assert no_diarization.reason_in(json.dumps({"diarize": {"audio_s": 1.0}})) is None
    assert no_diarization.reason_in("{texto inválido") is None and no_diarization.reason_in(None) is None
    assert no_diarization.notice(False) == no_diarization.NOTICE_MISSING and no_diarization.notice(True) == no_diarization.NOTICE_PRESENT


def test_the_queue_row_says_who_spoke_is_missing(platform, store):
    from listener.ui.queue_rows import job_row_texts

    mid, job_id = _processed_without_token(platform, store)
    texts = job_row_texts(store.jobs.get(job_id), store.get_meeting(mid), "")
    assert texts[-1] == no_diarization.QUEUE_NOTE


def test_the_open_meeting_shows_how_to_solve_it(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow

    mid, _job = _processed_without_token(platform, store)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    assert win.message.text() == no_diarization.NOTICE_MISSING and not win.message_bar.isHidden()
    store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key="hf_teste")
    win.reload_header()
    assert win.message.text() == no_diarization.NOTICE_PRESENT
    win.show_message("Ata copiada (texto e formatação).")
    win.reload_header()
    assert win.message.text() == "Ata copiada (texto e formatação)."                     # o resultado de outra ação fica
