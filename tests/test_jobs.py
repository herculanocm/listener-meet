"""Fila de processamento (F2 item 2) e artefatos (item 10) sem relógio real: `now` é injetado como texto ISO."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from listener.model import JobKind, JobStatus, JobStep
from listener.store import Store

T0 = "2026-10-24T10:00:00.000-03:00"


def _at(seconds: float) -> str:
    return (datetime.fromisoformat(T0) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _meeting(store: Store) -> int:
    mid = store.create_meeting("r", None, None, None)
    store.finish_meeting(mid, 5000)
    return mid


def test_claim_is_atomic_between_two_stores_and_keeps_the_first_config(tmp_path):
    a, b = Store(tmp_path), Store(tmp_path)
    mid = _meeting(a)
    j1 = a.jobs.enqueue(mid)
    j2 = a.jobs.enqueue(mid, JobKind.REPROCESS, {"why": "teste"})
    assert a.jobs.has_pending() and [j["id"] for j in a.jobs.active()] == [j1, j2]
    got = a.jobs.claim(pid=111, config_json='{"pipeline.whisper_model": "small"}', now=_at(0))
    assert got is not None and got["id"] == j1 and got["status"] == "rodando" and got["worker_pid"] == 111
    assert got["config_json"] == '{"pipeline.whisper_model": "small"}' and got["started_at"] == _at(0)
    assert b.jobs.claim(pid=222, now=_at(1)) is None                       # há um worker vivo: ninguém pega o j2
    assert [j["id"] for j in b.jobs.held_fresh(now=_at(30))] == [j1]
    a.jobs.heartbeat(j1, now=_at(50))
    assert b.jobs.claim(pid=222, now=_at(100)) is None                     # heartbeat de 50 s atrás ainda vale
    # worker morreu: 61 s sem heartbeat -> j1 volta a pendente com attempts + 1 e o outro worker o pega (não o j2)
    got2 = b.jobs.claim(pid=222, config_json='{"pipeline.whisper_model": "medium"}', now=_at(111))
    assert got2 is not None and got2["id"] == j1 and got2["attempts"] == 1 and got2["worker_pid"] == 222
    assert got2["config_json"] == '{"pipeline.whisper_model": "small"}'    # o snapshot do primeiro claim fica
    assert got2["error"] == "worker parou sem concluir"
    b.jobs.finish(j1, JobStatus.DONE, timings={"diarize": {"wall_s": 1.0}})
    done = a.jobs.get(j1)
    assert done["status"] == "concluído" and done["progress"] == 1.0 and done["worker_pid"] is None and done["finished_at"]
    assert '"diarize"' in done["timings_json"]
    got3 = a.jobs.claim(pid=111, now=_at(120))
    assert got3 is not None and got3["id"] == j2 and got3["kind"] == "reprocess" and '"why"' in got3["params_json"]
    assert a.jobs.summary()["active"] == 1 and a.jobs.summary()["running"]["id"] == j2
    a.close()
    b.close()


def test_attempts_exhaust_into_error_and_retry_resets(store):
    mid = _meeting(store)
    j = store.jobs.enqueue(mid)
    assert store.jobs.claim(pid=1, now=_at(0))["attempts"] == 0
    store.jobs.finish(j, JobStatus.ERROR, error="ValueError: x", count_attempt=True)      # exceção conta tentativa
    assert store.jobs.get(j)["attempts"] == 1 and store.jobs.get(j)["status"] == "erro"
    assert store.jobs.claim(pid=1, now=_at(1)) is None                                   # erro não é pego sozinho
    assert store.jobs.retry(j) and store.jobs.get(j)["attempts"] == 0 and store.jobs.get(j)["status"] == "pendente"
    assert not store.jobs.retry(j)                                                       # só erro ou cancelado voltam
    # três quedas seguidas (claim sem heartbeat): cada claim seguinte devolve a pendente com attempts + 1; a terceira esgota
    t = 10.0
    for expected in (0, 1, 2):
        got = store.jobs.claim(pid=1, now=_at(t))
        assert got is not None and got["id"] == j and got["attempts"] == expected
        t += 61
    assert store.jobs.claim(pid=1, now=_at(t)) is None
    row = store.jobs.get(j)
    assert row["attempts"] == 3 and row["status"] == "erro" and "esgotadas" in row["error"]
    store.jobs.finish(j, JobStatus.ERROR, error="permanente: token ausente", count_attempt=False)
    assert store.jobs.get(j)["attempts"] == 3                                            # erro permanente não conta


def test_cancel_pending_now_and_running_cooperatively(store):
    mid = _meeting(store)
    j_pending, j_running = store.jobs.enqueue(mid), store.jobs.enqueue(mid)
    assert store.jobs.claim(pid=1, now=_at(0))["id"] == j_pending
    store.jobs.put_artifact(j_running, JobStep.DIARIZE, 0, b"x")
    assert store.jobs.request_cancel(j_running) == JobStatus.CANCELLED           # pendente: na hora, artefatos fora
    assert store.jobs.get(j_running)["status"] == "cancelado" and store.jobs.artifacts(j_running, JobStep.DIARIZE) == []
    assert store.jobs.request_cancel(j_pending) == JobStatus.RUNNING              # rodando: só pede
    assert store.jobs.cancel_requested(j_pending) and store.jobs.get(j_pending)["status"] == "rodando"
    store.jobs.finish(j_pending, JobStatus.CANCELLED)
    assert store.jobs.request_cancel(999) is None
    store.jobs.set_status(j_pending, JobStatus.PAUSED, JobStep.WAITING)
    assert store.jobs.get(j_pending)["step"] == "waiting"


def test_progress_and_artifacts_roundtrip(store):
    mid = _meeting(store)
    j = store.jobs.enqueue(mid)
    store.jobs.set_progress(j, JobStep.TRANSCRIBE_R, 0.25, {"transcribe_r": {"audio_s": 10, "speech_s": 4, "wall_s": 3}})
    row = store.jobs.get(j)
    assert row["step"] == "transcribe_r" and row["progress"] == 0.25 and '"speech_s": 4' in row["timings_json"]
    store.jobs.set_progress(j, None, 0.5)
    assert store.jobs.get(j)["step"] is None and '"speech_s": 4' in store.jobs.get(j)["timings_json"]   # timings ficam
    for idx in (2, 0, 1):
        store.jobs.put_artifact(j, JobStep.TRANSCRIBE_R, idx, bytes([idx]))
    store.jobs.put_artifact(j, JobStep.TRANSCRIBE_R, 1, b"novo")                  # REPLACE
    assert store.jobs.artifact_indexes(j, JobStep.TRANSCRIBE_R) == {0, 1, 2}
    assert store.jobs.artifacts(j, JobStep.TRANSCRIBE_R) == [(0, b"\x00"), (1, b"novo"), (2, b"\x02")]
    assert store.jobs.get_artifact(j, JobStep.TRANSCRIBE_R, 1) == b"novo" and store.jobs.get_artifact(j, JobStep.DIARIZE, 0) is None
    store.jobs.put_artifact(j, JobStep.DIARIZE, 0, b"d")
    assert store.jobs.delete_artifacts(j, JobStep.TRANSCRIBE_R) == 3 and store.jobs.artifact_indexes(j, JobStep.DIARIZE) == {0}
    assert store.jobs.delete_artifacts(j) == 1
    assert [x["id"] for x in store.jobs.for_meeting(mid)] == [j]
    store.delete_meeting(mid)                                                      # cascata: job e artefatos somem
    assert store.jobs.get(j) is None


def test_list_jobs_orders_active_first(store):
    mid = _meeting(store)
    done, pend = store.jobs.enqueue(mid), store.jobs.enqueue(mid)
    store.jobs.claim(pid=1, now=_at(0))
    store.jobs.finish(done, JobStatus.DONE)
    assert [j["id"] for j in store.jobs.list_jobs()] == [pend, done]
    with pytest.raises(ValueError):
        store.segments.update_cluster(1, nome="x")
