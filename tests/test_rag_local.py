"""F5.1 L6, o embedder local no índice e na busca, com a sessão e o tokenizer falsos e a pasta de modelos no tmp (A-13):
estado e chave `local/multilingual-e5-small@384` (A-02), lotes de 8 um por vez (A-14) e custo 0 (A-04), estimativa sem custo
com 0,19 s por trecho até medir o próprio modelo (A-05), vetores adiados com a lease e completados pela varredura sem modelo
confirmado (A-06, A-07), também quando a gravação para antes de o job concluir (R10-05), a falha passageira do local refeita
pela varredura (R10-04), o local nunca completa as reuniões antigas sem o "Reindexar agora" (Q3), o vetor da consulta sem
custo e o worker leve com 2 threads e as versões no log (A-03). As telas estão em `tests/test_rag_local_ui.py`."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from listener.llm import local_embed
from listener.llm.types import EmbedTask
from listener.model import JobStatus
from listener.pipeline.index_steps import IndexJobRunner
from listener.rag import index, search
from listener.store import Store
from tests.fake_local import KEY, Factories, Local, ready_folder, use_local
from tests.fake_local import long_meeting as _long_meeting


@pytest.fixture(autouse=True)
def extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """O CI instala só o `[dev]`: o extra `local` conta como instalado (as fábricas da sessão são falsas)."""
    monkeypatch.setattr(local_embed, "extra_installed", lambda: True)
    monkeypatch.delenv("LISTENER_LLM", raising=False)


@pytest.fixture
def local(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Local]:
    search.clear_caches()
    yield use_local(store, tmp_path, monkeypatch)
    search.clear_caches()


def _index_job(store: Store, mid: int) -> tuple[JobStatus, dict[str, Any]]:
    job_id = index.enqueue_index(store, mid, "edit")
    job = store.jobs.claim(pid=3, kinds=("index",))
    assert job is not None and job["id"] == job_id
    status = IndexJobRunner(store, job, heartbeat_thread=False).run()
    return status, json.loads(store.jobs.get(job_id)["timings_json"] or "{}")["index"]


def test_embedder_status_key_and_reasons_of_the_local(store, tmp_path, monkeypatch):
    from listener.ui.history import friendly_reason

    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    st = index.embedder_status(store)
    assert (st.ready, st.reason, st.model_key, st.provider_id) == (False, local_embed.NOT_DOWNLOADED, KEY, "local")
    ready_folder(models)
    assert index.embedder_status(store) == index.EmbedderStatus(True, "", KEY, "local")
    monkeypatch.setattr(local_embed, "extra_installed", lambda: False)
    assert index.embedder_status(store).reason == local_embed.NO_EXTRA
    assert index.model_key("local", "multilingual-e5-small") == KEY and index.model_key("gemini", "g") == "gemini/g@768"
    assert index.is_local(KEY) and not index.is_local("gemini/g@768") and not index.is_local(None)
    assert friendly_reason(local_embed.NOT_DOWNLOADED) == "a busca local ainda não foi baixada: prepare em Configurações (Busca)"
    assert "extra local" in friendly_reason(local_embed.NO_EXTRA)
    assert friendly_reason(local_embed.LOAD_FAILED) == "a busca local não abriu o modelo: prepare de novo em Configurações (Busca)"
    run_failed = friendly_reason(local_embed.RUN_FAILED)                       # R10-07: falha de cálculo não pede preparo
    assert run_failed.startswith("a busca local falhou nesta busca") and "prepare" not in run_failed


def test_fill_vectors_with_the_local_goes_in_small_batches_one_at_a_time_and_free(store, local):
    mid = _long_meeting(store, 40)
    synced = index.sync_text(store, mid)
    assert synced.total == 41 and local_embed.BATCH == 8                       # A-14: o lote de 32 passou de 500 MB no leve
    filled = index.fill_vectors(store, mid)                                     # o provedor sai do registro: o local da pasta
    assert (filled.model_key, filled.written, filled.missing, filled.cost_usd, filled.note) == (KEY, 41, 0, 0.0, "")
    calls = [c for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "rag_embed"]
    assert [c["strategy"] for c in calls] == ["batch:8"] * 5 + ["batch:1"] and {c["cost_usd"] for c in calls} == {0.0}
    assert {(c["provider"], c["model"]) for c in calls} == {("local", "multilingual-e5-small")} and calls[0]["input_tokens"] > 0
    assert local.f.session is not None and len(local.f.session.feeds) == 6 and local.f.sessions[0][1] == 1   # uma sessão, 1 thread
    assert local.f.tokenizer is not None and local.f.tokenizer.batches[0][0].startswith("passage: Comitê do fundo")
    assert [tuple(r) for r in store._q("SELECT DISTINCT model, dim FROM rag_vectors")] == [(KEY, 384)]
    assert store.rag.get_model(KEY) is not None and store.rag.get_model(KEY)["mu"].shape == (384,)


def test_reindex_estimate_for_the_local_is_free_with_seconds_per_chunk(store, local):
    from listener.llm.types import LlmTask
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert

    mid = _long_meeting(store, 40)
    index.sync_text(store, mid)
    est = index.reindex_estimate(store)
    assert (est.model_key, est.ready, est.chunks, est.meetings, est.cost_usd, est.batches) == (KEY, True, 41, 1, 0.0, 6)
    assert est.seconds == pytest.approx(41 * 0.19) and est.local and not est.measured
    text = est.text()
    assert text == "41 trechos sem vetor em 1 reunião · sem custo · ≈ 8 s nesta máquina (pela estimativa de 0,19 s por trecho)"
    for latency, n in ((6400, 32), (1800, 9), (900, 0)):                       # medido no próprio modelo: 8.200 ms em 41 trechos
        store.llm_calls.insert(LlmCallInsert(mid, None, str(LlmTask.RAG_EMBED), "local", "multilingual-e5-small", None, None, 1, 0, 0, 0,
                                             0.0, latency, LlmCallStatus.OK, None, f"batch:{n}"), store_payloads=False)
    store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.RAG_EMBED), "gemini", "gemini-embedding-001", None, None, 1, 0, 0, 0,
                                         0.0, 99_000, LlmCallStatus.OK, None, "batch:1"), store_payloads=False)   # outro modelo: fora
    measured = index.reindex_estimate(store)
    assert measured.measured and measured.seconds == pytest.approx(41 * 8.2 / 41) and "estimativa" not in measured.text()
    assert index._duration(30) == "30 s" and index._duration(150) == "2 min"


def test_index_job_with_the_local_defers_vectors_while_recording_and_the_sweep_completes(store, local):
    from datetime import datetime, timedelta

    mid = _long_meeting(store, 4)
    until = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="milliseconds")
    store.set_setting("pipeline.paused_until", until)                         # a lease da gravação (pause_on_record ou ao vivo)
    status, info = _index_job(store, mid)
    assert status == JobStatus.DONE and store.rag.count_chunks(mid) == 5 and info["vectors"] == 0     # trechos e FTS na hora
    assert info["deferred"] is True and info["note"] == "vetores adiados: gravando (5 trechos)" and local.f.sessions == []
    assert store.jobs.deferred_index_meetings() == [mid] and not index.model_confirmed(store, KEY)
    assert index.sweep(store) == 1                                            # sem modelo confirmado: a reunião adiada volta
    store.set_setting("pipeline.paused_until", "")
    job = store.jobs.claim(pid=4, kinds=("index",))
    assert job is not None and IndexJobRunner(store, job, heartbeat_thread=False).run() == JobStatus.DONE
    info = json.loads(store.jobs.get(int(job["id"]))["timings_json"])["index"]
    assert (info["vectors"], info["deferred"], info["note"]) == (5, False, "") and store.jobs.deferred_index_meetings() == []
    # um pago com a lease não adia (não disputa a CPU)
    from listener.llm.fake import FakeProvider

    other = _long_meeting(store, 2, "Outra")
    index.sync_text(store, other)
    store.set_setting("pipeline.paused_until", until)
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-ABCDEFGH12345678")
    store.providers.set_role("embed", "gemini")
    paid = index.fill_vectors(store, other, provider=FakeProvider(), defer=lambda: True)
    assert paid.written == 3 and not paid.deferred


def test_the_local_stops_between_batches_when_the_recording_starts(store, local):
    mid = _long_meeting(store, 40)
    index.sync_text(store, mid)
    lease = {"on": False}
    filled = index.fill_vectors(store, mid, defer=lambda: lease["on"], on_progress=lambda _done, _total: lease.update(on=True))
    assert (filled.written, filled.missing, filled.deferred) == (8, 33, True) and filled.reason == index.DEFERRED
    assert filled.note == "vetores adiados: gravando (33 trechos)"


def test_the_sweep_completes_old_meetings_with_the_local_only_after_reindex_now(store, local):
    old = _long_meeting(store, 3, "Antiga")
    index.sync_text(store, old)
    assert store.rag.count_vectors() == 0 and store.rag.model_confirmed(KEY)    # seria o primeiro modelo do índice...
    assert index.sweep(store) == 0 and not index.model_confirmed(store, KEY)     # ...mas o local espera o "Reindexar agora" (Q3)
    assert index.reindex_all(store) == 1 and index.model_confirmed(store, KEY)
    job = store.jobs.claim(pid=5, kinds=("index",))
    assert job is not None and IndexJobRunner(store, job, heartbeat_thread=False).run() == JobStatus.DONE
    newer = _long_meeting(store, 2, "Nova")
    index.sync_text(store, newer)
    assert index.sweep(store) == 1                                              # confirmado: a varredura completa as outras


def test_query_vector_of_the_local_is_free_logged_and_the_search_is_hybrid(store, local, caplog):
    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid)
    with caplog.at_level(logging.INFO, logger="listener.rag.search"):
        qv = search.embed_query(store, "orçamento 2")
    assert qv.vector is not None and qv.vector.shape == (384,) and qv.model_key == KEY and qv.cost_usd == 0.0 and qv.reason == ""
    assert "vetor da consulta (local/multilingual-e5-small@384) em" in caplog.text
    (call,) = [dict(r) for r in store._q("SELECT provider, model, cost_usd, strategy FROM llm_calls WHERE strategy = 'query'")]
    assert call == {"provider": "local", "model": "multilingual-e5-small", "cost_usd": 0.0, "strategy": "query"}
    assert local.f.tokenizer is not None and local.f.tokenizer.batches[-1] == ["query: orçamento 2"]
    result = search.search(store, "orçamento", query_vector=qv)
    assert result.hybrid and result.hits


def test_a_local_that_does_not_load_leaves_a_note_without_network_jargon(store, tmp_path, monkeypatch):
    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    ready_folder(models)
    broken = Factories(fail_session=OSError("[WinError 126] o módulo especificado não foi encontrado"))
    monkeypatch.setattr(local_embed, "_SESSION", broken.local_session())
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    mid = _long_meeting(store, 4)
    status, info = _index_job(store, mid)
    assert status == JobStatus.DONE and info["note"] == f"5 trechos sem vetor: {local_embed.LOAD_FAILED}" and "rede" not in info["note"]
    errors = [c for c in store.llm_calls.list_for_meeting(mid) if c["status"] == "erro"]
    assert len(errors) == 1 and errors[0]["error"] == local_embed.LOAD_FAILED
    assert info["retry"] is True and store.jobs.retry_index_meetings() == [mid]                       # R10-04: a varredura refaz


def test_a_passing_local_failure_on_a_new_meeting_is_retried_once_per_sweep(store, local):
    """R10-04: a memória falta num lote de uma reunião nova; o job conclui com a nota e a marca `retry`, e a varredura (sem
    modelo confirmado: a Q3 é das antigas) a põe na fila uma vez; refeita com sucesso, sai da lista."""
    folder = local.folder
    local_embed.shared().embed(folder, ["aquece"], EmbedTask.QUERY)
    assert local.f.session is not None
    real_run, calls = local.f.session.run, {"n": 0}

    def flaky(names: Any, feed: dict[str, Any]) -> list[Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise MemoryError("bad allocation")
        return real_run(names, feed)

    local.f.session.run = flaky                                                 # type: ignore[method-assign]
    mid = _long_meeting(store, 4)
    status, info = _index_job(store, mid)
    assert status == JobStatus.DONE and info["note"] == f"5 trechos sem vetor: {local_embed.RUN_FAILED}" and info["retry"] is True
    assert not index.model_confirmed(store, KEY) and store.jobs.retry_index_meetings() == [mid]
    assert index.sweep(store) == 1 and index.sweep(store) == 0                  # uma por varredura: já está na fila
    job = store.jobs.claim(pid=7, kinds=("index",))
    assert job is not None and IndexJobRunner(store, job, heartbeat_thread=False).run() == JobStatus.DONE
    again = json.loads(store.jobs.get(int(job["id"]))["timings_json"])["index"]
    assert (again["vectors"], again["retry"]) == (5, False) and store.jobs.retry_index_meetings() == [] and index.sweep(store) == 0


def test_the_index_requeues_itself_when_the_recording_stops_before_it_concludes(store, local):
    """R10-05: o worker lê a lease de pé e adia; antes de ele gravar `concluído`, o app limpa a lease e roda a varredura (o
    job ainda `rodando` não está entre os adiados: 0). Gravado o `concluído`, o job relê a lease e se põe de volta na fila."""
    from datetime import datetime, timedelta

    mid = _long_meeting(store, 4)
    store.set_setting("pipeline.paused_until", (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="milliseconds"))
    job_id = index.enqueue_index(store, mid, "edit")
    job = store.jobs.claim(pid=8, kinds=("index",))
    assert job is not None and job["id"] == job_id
    runner = IndexJobRunner(store, job, heartbeat_thread=False)
    original, swept = runner._recording, []

    def the_user_stops_right_after_the_read() -> bool:
        runner._recording = original                                            # type: ignore[method-assign]  # só a 1ª leitura
        active = original()
        store.set_setting("pipeline.paused_until", "")                          # `_on_stopped`: a lease sai...
        swept.append(index.sweep(store))                                        # ...e a varredura roda com o job `rodando`
        return active

    runner._recording = the_user_stops_right_after_the_read                    # type: ignore[method-assign]
    assert runner.run() == JobStatus.DONE and swept == [0]
    assert json.loads(store.jobs.get(job_id)["timings_json"])["index"]["deferred"] is True
    pending = store.jobs.active(kinds=("index",))
    assert [(int(j["meeting_id"]), json.loads(j["params_json"])["reason"]) for j in pending] == [(mid, "sweep")]
    assert index.sweep(store) == 0                                              # a varredura seguinte não duplica


def test_the_light_worker_uses_two_threads_and_logs_the_versions(store, local, tmp_path):
    from listener import log as applog
    from listener.pipeline import worker

    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = _long_meeting(store, 3)
    job_id = index.enqueue_index(store, mid, "edit")
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(job_id)
    assert row["status"] == JobStatus.DONE and json.loads(row["timings_json"])["index"]["vectors"] == 4
    assert local.f.sessions and {threads for _path, threads in local.f.sessions} == {worker.LOCAL_EMBED_THREADS}
    applog.flush()
    text = (tmp_path / "logs" / "listener-llm.log").read_text(encoding="utf-8")
    for dist in ("onnxruntime", "tokenizers"):
        try:
            assert f"{dist} {metadata.version(dist)}" in text
        except metadata.PackageNotFoundError:                                  # o CI instala só o [dev]
            continue
