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


def test_request_cancel_racing_with_a_claim_keeps_artifacts(tmp_path):
    """R2-14: o worker pega o job entre a leitura da interface e a escrita; o cancelamento vira pedido, nada é apagado."""
    from listener.store import Store

    ui, worker = Store(tmp_path), Store(tmp_path)
    mid = _meeting(ui)
    j = ui.jobs.enqueue(mid)
    real_tx = ui.core.transaction

    def tx_after_claim():
        got = worker.jobs.claim(pid=7)                                    # o worker chega primeiro
        worker.jobs.put_artifact(j, JobStep.DIARIZE, 0, b"janela 0")
        assert got is not None
        return real_tx()

    ui.core.transaction = tx_after_claim  # type: ignore[method-assign]
    result = ui.jobs.request_cancel(j)
    ui.core.transaction = real_tx  # type: ignore[method-assign]
    row = ui.jobs.get(j)
    assert result == JobStatus.RUNNING and row["status"] == "rodando" and row["cancel_requested"] == 1
    assert ui.jobs.artifact_indexes(j, JobStep.DIARIZE) == {0}
    assert ui.jobs.request_cancel(999) is None
    ui.close()
    worker.close()


def test_concurrent_claims_from_two_stores_give_the_job_to_exactly_one(tmp_path):
    """R2-27: dois workers (threads e conexões próprias) disparam o claim ao mesmo tempo: um leva, o outro recebe None."""
    import threading

    from listener.store import Store

    a, b = Store(tmp_path), Store(tmp_path)
    mid = _meeting(a)
    j = a.jobs.enqueue(mid)
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def claim(name: str, store: Store) -> None:
        barrier.wait(5.0)
        results[name] = store.jobs.claim(pid=1 if name == "a" else 2)
        store.close_thread()

    threads = [threading.Thread(target=claim, args=("a", a)), threading.Thread(target=claim, args=("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10.0)
    got = [r for r in results.values() if r is not None]
    assert len(results) == 2 and len(got) == 1 and got[0]["id"] == j
    assert a.jobs.get(j)["status"] == "rodando" and a.jobs.get(j)["worker_pid"] in (1, 2)
    a.close()
    b.close()


def test_claim_by_queue_class_lets_a_light_worker_run_beside_a_heavy_one(tmp_path):
    """F3 item 2: classes pesada (meeting, reprocess, embed_range) e leve (llm): o `NOT EXISTS ... rodando fresco` vale por
    classe; um job `llm` não espera a diarização; dois leves continuam exclusivos; o pesado nunca pega um `llm`."""
    from listener.model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS

    heavy, light = Store(tmp_path), Store(tmp_path)
    mid = _meeting(heavy)
    j_meeting = heavy.jobs.enqueue(mid)
    j_llm = heavy.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": j_meeting})
    j_llm2 = heavy.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["title"], "parent_job_id": j_meeting})
    got = heavy.jobs.claim(pid=1, now=_at(0), kinds=HEAVY_JOB_KINDS)
    assert got is not None and got["id"] == j_meeting
    assert heavy.jobs.claim(pid=1, now=_at(1), kinds=HEAVY_JOB_KINDS) is None              # pesado ocupado
    assert heavy.jobs.has_pending(kinds=LIGHT_JOB_KINDS) and not heavy.jobs.has_pending(kinds=("reprocess",))
    got_l = light.jobs.claim(pid=2, now=_at(2), kinds=LIGHT_JOB_KINDS)
    assert got_l is not None and got_l["id"] == j_llm and got_l["config_json"] is None       # o leve não espera o pesado
    assert light.jobs.claim(pid=3, now=_at(3), kinds=LIGHT_JOB_KINDS) is None                # dois leves: só um
    assert [j["id"] for j in heavy.jobs.held_fresh(now=_at(4), kinds=LIGHT_JOB_KINDS)] == [j_llm]
    assert [j["id"] for j in heavy.jobs.held_fresh(now=_at(4), kinds=HEAVY_JOB_KINDS)] == [j_meeting]
    assert [j["id"] for j in heavy.jobs.held_fresh(now=_at(4))] == [j_meeting, j_llm]
    assert [j["id"] for j in heavy.jobs.active(kinds=LIGHT_JOB_KINDS)] == [j_llm, j_llm2]
    light.jobs.finish(j_llm, JobStatus.DONE)
    heavy.jobs.finish(j_meeting, JobStatus.DONE)
    assert heavy.jobs.claim(pid=1, now=_at(5), kinds=HEAVY_JOB_KINDS) is None                # o pesado não pega o llm
    got_l2 = light.jobs.claim(pid=2, now=_at(6), kinds=LIGHT_JOB_KINDS)
    assert got_l2 is not None and got_l2["id"] == j_llm2
    assert heavy.jobs.claim(pid=9, now=_at(7)) is None                                       # sem classe: qualquer worker vivo segura
    # tentar de novo com `reset_config` descarta o snapshot (job llm troca para o provedor ativo, F3 item 15)
    light.jobs.finish(j_llm2, JobStatus.ERROR, error="sem rede")
    heavy._x("UPDATE processing_jobs SET config_json = '{\"provider_id\": \"gemini\"}' WHERE id = ?", (j_llm2,))
    assert heavy.jobs.retry(j_llm2) and heavy.jobs.get(j_llm2)["config_json"] is not None
    heavy.jobs.finish(j_llm2, JobStatus.ERROR, error="sem rede")
    assert heavy.jobs.retry(j_llm2, reset_config=True) and heavy.jobs.get(j_llm2)["config_json"] is None
    heavy.close()
    light.close()


def test_current_job_id_ignores_llm_jobs(store):
    """F3A-04: um job `llm` concluído depois do job da reunião não muda o job vigente (clusters e segmentos são do pai)."""
    mid = _meeting(store)
    j_meeting = store.jobs.enqueue(mid)
    assert store.jobs.claim(pid=1, now=_at(0))["id"] == j_meeting
    store.jobs.finish(j_meeting, JobStatus.DONE)
    assert store.segments.current_job_id(mid) == j_meeting
    j_llm = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": j_meeting})
    assert store.segments.current_job_id(mid) == j_meeting                                   # pendente: não conta
    store.jobs.claim(pid=2, now=_at(1), kinds=("llm",))
    store.jobs.finish(j_llm, JobStatus.DONE)
    assert store.segments.current_job_id(mid) == j_meeting                                   # concluído: também não
    only_llm = _meeting(store)
    j = store.jobs.enqueue(only_llm, JobKind.LLM)
    store.jobs.claim(pid=2, now=_at(2), kinds=("llm",))
    store.jobs.finish(j, JobStatus.DONE)
    assert store.segments.current_job_id(only_llm) is None
