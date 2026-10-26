"""F5 L2: sincronia e diff dos trechos (`rag.index.sync_text`), vetores pelo fake (`fill_vectors`: lotes, normalização,
assinatura, `mu` refeito ao dobrar, `llm_calls` por lote, trecho mudado no meio), o job `index` sem embedder e sem rede, a
retentativa do `embed`, a varredura, o encadeamento pelo pesado e pelo `llm` e a espera do `on_edited` no app."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest

from listener.llm.base import AuthError, LlmError, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.registry import RetryingProvider
from listener.llm.types import Embeddings, EmbedTask, LlmTask
from listener.model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, ChunkKind, JobStatus, SummaryKind
from listener.pipeline.index_steps import IndexJobRunner
from listener.rag import index, vectors
from listener.store import Store
from listener.store.segments import SegmentInsert
from listener.store.summaries import SummaryInsert

KEY = "AIzaSy-chave-de-teste-ABCDEFGH12345678"
MODEL = "gemini/gemini-embedding-001@768"
LINES = [(0, 30, "ana", "Bom dia, vamos falar do orçamento de 2027."), (30, 70, "bia", "O fluxo de caixa do fundo está apertado."),
         (70, 100, "ana", "Precisamos aprovar a auditoria até sexta."), (100, 140, "bia", "Concordo, e o contrato com o cedente vence."),
         (140, 200, "ana", "Então fechamos a proposta na próxima semana."), (200, 260, "bia", "Vou mandar a planilha revisada amanhã.")]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _people(store: Store) -> dict[str, int]:
    return {"ana": store.people.create_person("Ana", role="CFO"), "bia": store.people.create_person("Bia")}


def _pipeline_job(store: Store, mid: int) -> int:
    job = store.jobs.enqueue(mid)
    store.jobs.claim(pid=1, kinds=HEAVY_JOB_KINDS)
    store.jobs.finish(job, JobStatus.DONE)
    return job


def _meeting(store: Store, who: dict[str, int], title: str = "Orçamento 2027", shift_s: float = 0.0) -> tuple[int, int]:
    """Reunião com pipeline concluído e as falas de LINES (sem áudio): (reunião, job)."""
    mid = store.create_meeting(title, None, None, None)
    store.finish_meeting(mid, 300_000)
    job = _pipeline_job(store, mid)
    _segments(store, mid, job, who, shift_s)
    return mid, job


def _segments(store: Store, mid: int, job: int, who: dict[str, int], shift_s: float = 0.0) -> None:
    store.segments.insert_segments([SegmentInsert(mid, job, "R", int((t0 + shift_s) * 1000), int((t1 + shift_s) * 1000), text, "pyannote",
                                                  "final", person_id=who[p]) for t0, t1, p, text in LINES])


def _ready(store: Store) -> None:
    store.providers.update_provider("gemini", api_key=KEY)


def _vector_ids(store: Store) -> dict[int, int]:
    return {int(r[0]): int(r[1]) for r in store._q("SELECT chunk_id, id FROM rag_vectors")}


def test_sync_text_diff_keeps_vectors_moves_metadata_and_replaces_what_changed(store):
    who = _people(store)
    mid, job = _meeting(store, who)
    _ready(store)
    first = index.sync_text(store, mid)
    assert (first.total, first.added, first.kept, first.removed) == (5, 5, 0, 0)
    chunks = store.rag.list_chunks(mid)
    assert [c["kind"] for c in chunks] == ["transcricao"] * 4 + ["reuniao"]
    assert chunks[0]["text"] == "Ana — CFO: Bom dia, vamos falar do orçamento de 2027.\nBia: O fluxo de caixa do fundo está apertado."
    assert chunks[0]["speakers"] == "Ana — CFO, Bia" and json.loads(chunks[0]["person_ids"]) == sorted(who.values())
    assert chunks[0]["job_id"] == job and chunks[0]["title"] == "Orçamento 2027"
    assert "Participantes: Ana — CFO, Bia" in chunks[-1]["text"] and "Duração: 5 min" in chunks[-1]["text"]
    with store.connection() as c:
        hits = [r[0] for r in c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ? ORDER BY rank", ('"auditoria"',))]
    assert hits == [chunks[1]["id"]]
    filled = index.fill_vectors(store, mid, provider=FakeProvider())
    assert (filled.wanted, filled.written, filled.missing, filled.note) == (5, 5, 0, "")
    before = _vector_ids(store)
    assert index.sync_text(store, mid) == index.SyncResult(5, 5, 0, 0, 0)                       # nada mudou: nada escrito
    # reprocessar: o mesmo texto 1 s depois, segmentos novos, job novo; os vetores ficam
    job2 = _pipeline_job(store, mid)
    _segments(store, mid, job2, who, shift_s=1.0)
    store.segments.swap_job(mid, job2)
    moved = index.sync_text(store, mid)
    assert (moved.kept, moved.added, moved.removed, moved.moved) == (5, 0, 0, 5)
    after = store.rag.list_chunks(mid)
    assert _vector_ids(store) == before and after[0]["t_start_ms"] == 1000 and after[0]["job_id"] == job2
    assert after[0]["seg_first"] != chunks[0]["seg_first"] and after[-1]["job_id"] == job2
    # a transcrição revisada muda um trecho: só ele sai (com o vetor, pela cascata) e entra de novo
    seg = store.segments.list_segments(mid, job2)[2]
    store.segments.set_text_llm(mid, job2, [(int(seg["id"]), "Precisamos aprovar a auditoria externa até sexta.")])
    changed = index.sync_text(store, mid)
    assert (changed.kept, changed.added, changed.removed) == (4, 1, 1)
    assert len(_vector_ids(store)) == 4 and store.rag.count_missing(mid, MODEL) == 1
    # ocorrência repetida: dois itens iguais da ata são dois trechos, mantidos na volta
    store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar.\n- Aprovar.\n", "gemini", "m",
                                         "minutes/1.0", job2, None, "pt-BR"))
    twice = index.sync_text(store, mid)
    assert twice.added == 2 and [c["kind"] for c in store.rag.list_chunks(mid)].count(ChunkKind.DECISION) == 2
    assert index.sync_text(store, mid).added == 0
    assert index.sync_text(store, 9999) == index.SyncResult()                                    # reunião que não existe


class ScaledFake(FakeProvider):
    """Vetores fora da norma (como a OpenAI) e um gancho no meio do pedido número `at` (o de outro processo, o do usuário)."""

    def __init__(self, *a: Any, during: Any = None, at: int = 1, **kw: Any):
        super().__init__(*a, **kw)
        self.during = during
        self.at = at

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        emb = await super().embed(texts, model=model, dim=dim, task=task)
        if self.during is not None and len(self.embed_calls) >= self.at:
            self.during, hook = None, self.during
            hook()
        return Embeddings(emb.model, emb.dim, emb.vectors * 3.0, emb.usage)


def test_fill_vectors_batches_normalizes_signs_refreshes_mu_and_records_calls(store):
    who = _people(store)
    mid, job = _meeting(store, who)
    _ready(store)
    store.set_setting("llm.max_parallel", 2)
    index.sync_text(store, mid)
    fake = ScaledFake(delay_s=0.01)
    progress: list[tuple[int, int]] = []
    filled = index.fill_vectors(store, mid, provider=fake, batch_size=2, on_progress=lambda d, t: progress.append((d, t)))
    assert (filled.written, filled.missing, filled.batches, filled.calls) == (5, 0, 3, 3)
    assert sorted(progress) == [(2, 5), (4, 5), (5, 5)] and 1 <= fake.peak_parallel <= 2         # gravados de pedidos
    assert [c[0] for c in fake.embed_calls] == [2, 2, 1]
    assert {c[1:] for c in fake.embed_calls} == {("gemini-embedding-001", 768, "document")}
    ids, mat = store.rag.vectors_by_model(MODEL)
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0)                                          # normaliza sempre
    model = store.rag.get_model(MODEL)
    assert model["mu_n"] == 4 and model["dim"] == 768                                            # refeito em 2 e 4; 5 não dobra
    _vids, chunk_ids, sigs = store.rag.signatures(MODEL)
    assert np.array_equal(sigs, vectors.signatures(mat, model["mu"])) and len(chunk_ids) == 5     # todas no `mu` vigente
    assert store.rag.vector_version(MODEL) == (5, int(ids.max()), 4)
    calls = [c for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "rag_embed"]
    assert len(calls) == 3 and {c["job_id"] for c in calls} == {job} and all(c["status"] == "ok" for c in calls)
    assert sorted(c["strategy"] for c in calls) == ["batch:1", "batch:2", "batch:2"] and all(c["cost_usd"] > 0 for c in calls)
    assert filled.cost_usd == pytest.approx(sum(c["cost_usd"] for c in calls))
    assert index.fill_vectors(store, mid, provider=fake).wanted == 0                               # nada falta: nenhuma chamada


def test_vector_is_dropped_when_the_chunk_changes_during_the_request(store):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    victim = store.rag.list_chunks(mid)[0]["id"]
    fake = ScaledFake(during=lambda: store._x("UPDATE rag_chunks SET text_hash = 'outro' WHERE id = ?", (victim,)))
    filled = index.fill_vectors(store, mid, provider=fake)
    assert (filled.written, filled.missing, filled.reason) == (4, 1, "o trecho mudou durante o pedido")
    assert victim not in _vector_ids(store)


def _index_job(store: Store, mid: int, **kw: Any) -> tuple[JobStatus, dict[str, Any]]:
    job_id = index.enqueue_index(store, mid, "sweep")
    job = store.jobs.claim(pid=3, kinds=LIGHT_JOB_KINDS)
    assert job is not None and job["id"] == job_id
    status = IndexJobRunner(store, job, heartbeat_thread=False, **kw).run()
    return status, json.loads(store.jobs.get(job_id)["timings_json"] or "{}")


def test_index_job_without_embedder_or_network_concludes_with_the_fts_and_a_note(store):
    who = _people(store)
    mid, _job = _meeting(store, who)
    status, timings = _index_job(store, mid)
    assert status == JobStatus.DONE and store.rag.count_chunks(mid) == 5                         # o FTS foi gravado
    assert timings["index"]["note"] == ("5 trechos sem vetor: sem chave do provedor gemini para embeddings (Configurações, "
                                        "Chaves de acesso)")
    assert timings["index"]["chunks"] == 5 and "index_text" in timings and store.llm_calls.list_for_meeting(mid) == []
    store.providers.update_provider("anthropic", api_key=KEY)
    store.providers.set_active("anthropic", embed=True)
    _status, timings = _index_job(store, mid)
    assert timings["index"]["note"].startswith("5 trechos sem vetor: a Anthropic não oferece embeddings")
    store.providers.set_active("gemini", embed=True)
    _ready(store)
    fake = FakeProvider()
    fake.fail_next(TransientError("rede: sem conexão"), LlmTask.RAG_EMBED)
    status, timings = _index_job(store, mid, provider=fake)
    assert status == JobStatus.DONE and timings["index"]["note"] == "5 trechos sem vetor: sem rede (sem conexão)"
    errors = [c for c in store.llm_calls.list_for_meeting(mid) if c["status"] == "erro"]
    assert len(errors) == 1 and errors[0]["task"] == "rag_embed" and errors[0]["error"] == "rede: sem conexão"
    assert not store.jobs.has_pending(kinds=("index",))                                           # sem laço de retentativa
    status, timings = _index_job(store, mid, provider=FakeProvider())
    assert timings["index"]["vectors"] == 5 and timings["index"]["note"] == "" and timings["index"]["model"] == MODEL
    events = [e["type"] for e in store.list_events(mid)]
    assert "job_start" not in events and "job_step" not in events                                # o índice não enche a história


def test_retrying_provider_embed_retries_transient_errors_and_times_out(monkeypatch):
    from listener.llm import registry

    async def no_sleep(_s: float) -> None:
        return None

    fake = FakeProvider()
    fake.fail_next(TransientError("rede: caiu"), LlmTask.RAG_EMBED)
    fake.fail_next(TransientError("rede: caiu de novo"), LlmTask.RAG_EMBED)
    rp = RetryingProvider(fake, sleep=no_sleep)
    emb = run(rp.embed(["orçamento"], model="m", dim=8, task=EmbedTask.DOCUMENT))
    assert emb.vectors.shape == (1, 8) and rp.retries == 2
    fake.fail_next(AuthError("chave inválida"), LlmTask.RAG_EMBED)
    with pytest.raises(AuthError):
        run(rp.embed(["x"], model="m", dim=8, task=EmbedTask.DOCUMENT))
    assert rp.retries == 2                                                                          # permanente: sem nova tentativa
    monkeypatch.setattr(registry, "EMBED_TIMEOUT_S", 0.01)
    slow = RetryingProvider(FakeProvider(delay_s=0.2), sleep=no_sleep)
    with pytest.raises(LlmError, match="tempo esgotado"):
        run(slow.embed(["x"], model="m", dim=8, task=EmbedTask.DOCUMENT))
    assert slow.retries == 2
    bow = run(FakeProvider().embed(["Orçamento de 2027", "orcamento 2027", "futebol no domingo"], model="m", dim=256,
                                   task=EmbedTask.QUERY)).vectors
    assert bow[0] @ bow[1] > 0.6 and abs(float(bow[0] @ bow[2])) < 0.3                            # palavras em comum ficam perto


def test_sweep_enqueues_processed_meetings_without_chunks_and_missing_vectors_only_when_ready(store):
    who = _people(store)
    a, _ = _meeting(store, who, "sem trechos")
    b, _ = _meeting(store, who, "completa")
    c, _ = _meeting(store, who, "sem vetores")
    d = store.create_meeting("só gravada", None, None, None)
    store.finish_meeting(d, 1000)
    for mid in (b, c):
        index.sync_text(store, mid)
    _ready(store)
    index.fill_vectors(store, b, provider=FakeProvider())
    store.providers.update_provider("gemini", api_key="")
    assert index.sweep(store) == 1
    pending = store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))
    assert [(j["meeting_id"], json.loads(j["params_json"])["reason"]) for j in pending] == [(a, "sweep")]
    _ready(store)
    assert index.sweep(store) == 1 and {j["meeting_id"] for j in store.jobs.list_jobs(kinds=("index",))} == {a, c}
    assert index.sweep(store) == 0                                                                # um pendente por reunião
    silent = store.create_meeting("sem fala", None, None, None)
    store.finish_meeting(silent, 1000)
    _pipeline_job(store, silent)
    index.sync_text(store, silent)
    assert [ch["kind"] for ch in store.rag.list_chunks(silent)] == ["reuniao"]                    # a reunião sem fala tem a marca
    index.fill_vectors(store, silent, provider=FakeProvider())
    assert index.sweep(store) == 0                                                                # e não volta a cada abertura


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def test_heavy_and_llm_jobs_chain_the_index(platform, store):
    """F5A-03: o pesado grava trechos e FTS na hora e enfileira o `index`; o `llm` enfileira de novo quando alguma tarefa
    (salvo `test`) deu certo, inclusive com falha parcial; o job só de teste não."""
    from listener.pipeline import llm_steps
    from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    kinds = [c["kind"] for c in store.rag.list_chunks(mid)]
    assert kinds.count("transcricao") >= 1 and kinds[-1] == "reuniao"                              # antes da ata
    pending = store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))
    assert [json.loads(j["params_json"])["reason"] for j in pending] == ["pipeline"]
    store.jobs.request_cancel(int(pending[0]["id"]))                                              # tira o pendente do caminho
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE
    pending = store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))
    assert [json.loads(j["params_json"])["reason"] for j in pending] == ["llm"]
    store.jobs.request_cancel(int(pending[0]["id"]))
    llm_steps.enqueue_llm_job(store, mid, ["title", "minutes"])
    failing = FakeProvider()
    failing.fail_next(LlmError("recusado", permanent=True), LlmTask.MINUTES)
    _runner, status, _fake = _run_llm(store, failing)
    assert status == JobStatus.ERROR and store.jobs.has_pending(kinds=("index",))                 # o título deu certo
    store.jobs.request_cancel(int(store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))[0]["id"]))
    llm_steps.enqueue_test_job(store, mid)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and not store.jobs.has_pending(kinds=("index",))


def test_index_job_end_to_end_in_the_light_worker_with_the_fake_llm(tmp_path, store, monkeypatch):
    from listener.pipeline import worker

    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    job_id = index.enqueue_index(store, mid, "edit")
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(job_id)
    info = json.loads(row["timings_json"])["index"]
    assert row["status"] == JobStatus.DONE and row["progress"] == 1.0 and info["vectors"] == 5 and info["calls"] == 1
    assert store.rag.coverage(MODEL) == {"chunks": 5, "meetings": 1, "vectors": 5, "missing": 0}


def _launch_spy(c: Any, monkeypatch: Any) -> list[bool]:
    launched: list[bool] = []

    def launch(light: bool = False) -> bool:
        launched.append(light)
        return True

    monkeypatch.setattr(c, "launch_worker", launch)
    return launched


def _not_before(store: Store, job_id: int) -> datetime | None:
    raw = json.loads(store.jobs.get(job_id)["params_json"]).get("not_before")
    return datetime.fromisoformat(raw) if raw else None


def test_on_edited_queues_a_durable_delayed_index_and_conclude_or_close_expedite(qapp, platform, store, monkeypatch):
    """R5-07 e R5-12: a edição põe o `index` na fila na hora, com "não antes de" agora + 30 s renovado a cada edição (o
    job espera, não um timer do app: sobrevive ao app fechar); o leve sobe já; "Concluir" e fechar a revisão tiram a espera.
    O relógio do claim é injetado: nada dorme de verdade."""
    from listener.ui.controller import Controller

    who = _people(store)
    mid, _job = _meeting(store, who)
    c = Controller(qapp, platform, store)
    c.tray = None
    launched = _launch_spy(c, monkeypatch)
    try:
        t0 = datetime.now().astimezone()
        c.on_edited(mid, "edit")
        (job,) = [int(j["id"]) for j in store.jobs.list_jobs(kinds=("index",))]
        first = _not_before(store, job)
        assert first is not None and timedelta(seconds=29) <= first - t0 <= timedelta(seconds=31) and launched == [True]
        assert store.jobs.claim(pid=1, kinds=LIGHT_JOB_KINDS, now=(first - timedelta(seconds=1)).isoformat()) is None
        c.on_edited(mid, "edit")                                                                     # renova, mesmo job
        assert [int(j["id"]) for j in store.jobs.list_jobs(kinds=("index",))] == [job]
        assert (_not_before(store, job) or first) >= first
        c._review_closed(mid, 0)                                                                     # fechar: sem espera
        assert _not_before(store, job) is None and json.loads(store.jobs.get(job)["params_json"])["reason"] == "edit"
        assert store.jobs.claim(pid=1, kinds=LIGHT_JOB_KINDS)["id"] == job
        store.jobs.finish(job, JobStatus.DONE)
        c.on_edited(mid, "review", now=True)                                                         # "Concluir": na hora
        (done, new) = [int(j["id"]) for j in store.jobs.list_jobs(kinds=("index",))][::-1]
        assert done == job and _not_before(store, new) is None
        c._review_closed(mid, 0)                                                                     # nada esperando: não mexe
        assert len(store.jobs.list_jobs(kinds=("index",))) == 2
    finally:
        c.emergency_stop()


def test_app_launches_the_light_worker_only_when_the_delayed_index_is_due(qapp, platform, store):
    """R5-07: só esperar a edição assentar não lança um worker (seriam vários esperando, um a cada minuto de edição); o
    polling da fila lança quando o `index` vence, e "Concluir" (sem espera) lança na hora."""
    from listener.platform.common.process import worker_command
    from listener.ui.controller import Controller

    who = _people(store)
    mid, _job = _meeting(store, who)
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        c.on_edited(mid, "edit")
        assert platform.spawned == [] and store.jobs.delayed(LIGHT_JOB_KINDS) == 1
        c._poll_queue()
        assert platform.spawned == []                                                                # ainda na espera
        c.on_edited(mid, "review", now=True)
        assert [argv for argv, _env in platform.spawned] == [worker_command(light=True)]
    finally:
        c.emergency_stop()


def test_summary_dialog_reports_a_new_title(qtbot, store):
    """R5-07: o título salvo em "Gravação salva" também reindexa (o pesado pode já ter indexado com o antigo)."""
    from listener.ui.summary import SummaryDialog

    mid = store.create_meeting("Antigo", None, None, None)
    store.finish_meeting(mid, 1000)
    calls: list[tuple[Any, ...]] = []
    dlg = SummaryDialog(store, mid, {"duration_s": 1.0}, on_edited=lambda *a, **k: calls.append(a))
    qtbot.addWidget(dlg)
    dlg.accept()
    assert calls == []                                                                               # sem mudança: nada
    dlg = SummaryDialog(store, mid, {"duration_s": 1.0}, on_edited=lambda *a, **k: calls.append(a))
    qtbot.addWidget(dlg)
    dlg.title_edit.setText("Orçamento 2027")
    dlg.accept()
    assert calls == [(mid, "edit")] and store.get_meeting(mid)["title"] == "Orçamento 2027"


def test_review_dialog_and_llm_tabs_report_edits(qtbot, store):
    from listener.model import ActionStatus
    from listener.store.actions import ActionInsert
    from listener.ui.review import ReviewDialog

    who = _people(store)
    mid, job = _meeting(store, who)
    minutes = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar.\n", "gemini", "m",
                                                   "minutes/1.0", job, None, "pt-BR"))
    store.summaries.insert(SummaryInsert(mid, SummaryKind.PRE_SUMMARY, "{}", "Pré.", "gemini", "m", "pre_summary/1.0", job, None, "pt-BR"))
    (action,) = store.actions.insert_many([ActionInsert(mid, minutes, "Enviar", "Ana", None, None, None, None, job)])
    calls: list[tuple[Any, ...]] = []
    dlg = ReviewDialog(store, mid, on_edited=lambda *a, **k: calls.append((*a, k.get("now", False))))
    qtbot.addWidget(dlg)
    dlg.title_tab.title.setText("Orçamento final")
    dlg.title_tab.save()
    dlg.title_tab.save()                                                                           # sem mudança: não chama
    dlg.text_tabs["minutes"].editor.setPlainText("## Decisões\n\n- Aprovar o orçamento.\n")
    dlg.text_tabs["minutes"].save()
    dlg.text_tabs["pre_summary"].editor.setPlainText("Outro pré-resumo.")
    dlg.text_tabs["pre_summary"].save()                                                            # o pré-resumo não entra na busca
    dlg.actions_tab._after_write(action, store.actions.set_status(action, ActionStatus.DONE))
    assert calls == [(mid, "edit", False)] * 3
    dlg.finish()
    assert calls[-1] == (mid, "review", True)


def test_controller_start_sweeps_in_a_task_and_launches_the_light_worker(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui.controller import Controller

    who = _people(store)
    mid, _job = _meeting(store, who)
    c = Controller(qapp, platform, store)
    c.tray = None
    launched = _launch_spy(c, monkeypatch)
    try:
        c.start()
        qtbot.waitUntil(lambda: True in launched, timeout=5000)
        assert [j["meeting_id"] for j in store.jobs.list_jobs(kinds=("index",))] == [mid]
    finally:
        c.emergency_stop()


def test_pause_mid_vectors_lets_batches_in_flight_finish_and_waits_outside_the_loop(store):
    """R5-04: dentro do laço só `check` (sem bloquear); a pausa para de despachar lotes, os do ar terminam, a espera roda fora
    do laço asyncio e a rodada recomeça com o que falta: nenhum trecho é pedido duas vezes."""
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    state = {"checks": 0, "paused": False, "waits": 0}

    def check() -> None:
        state["checks"] += 1
        if state["checks"] == 2:                                                 # o usuário pausa com o 1º lote no ar
            state["paused"] = True

    def wait() -> None:
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()                                           # fora do laço: pode bloquear à vontade
        state["waits"] += 1
        state["paused"] = False

    fake = FakeProvider(delay_s=0.01)
    filled = index.fill_vectors(store, mid, provider=fake, batch_size=1, check=check, paused=lambda: bool(state["paused"]), wait=wait)
    assert (filled.written, filled.missing, filled.pauses, state["waits"]) == (5, 0, 1, 0 + 1)
    assert sum(n for n, *_rest in fake.embed_calls) == 5                         # cada trecho pedido uma vez


def test_index_job_waits_for_the_manual_pause_between_batches(store, monkeypatch):
    """R5-04 no runner: "Pausar fila" no meio dos vetores; o lote no ar termina, o job fica `pausado` e volta sozinho."""
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    store.set_setting("llm.max_parallel", 1)
    monkeypatch.setattr(index, "EMBED_BATCH", 1)
    statuses: list[str] = []

    def resume(_s: float) -> None:
        statuses.append(str(store.jobs.get(job_id)["status"]))
        store.set_setting("pipeline.paused_manual", False)

    fake = ScaledFake(during=lambda: store.set_setting("pipeline.paused_manual", True))
    job_id = index.enqueue_index(store, mid, "edit")
    job = store.jobs.claim(pid=3, kinds=LIGHT_JOB_KINDS)
    status = IndexJobRunner(store, job, provider=fake, heartbeat_thread=False, sleep=resume, wait_poll_s=0.01).run()
    info = json.loads(store.jobs.get(job_id)["timings_json"])["index"]
    assert status == JobStatus.DONE and statuses == ["pausado"] and info["vectors"] == 5 and len(fake.embed_calls) == 5


def test_each_batch_is_signed_with_the_mu_read_in_its_own_write_transaction(store):
    """R5-06: o `mu` muda por outro processo no meio do pedido (aqui, no 4º lote: o 3º deixou um `mu` lido para trás);
    o lote seguinte é assinado com o `mu` lido na transação que o grava, e todas as assinaturas batem com o `mu` final."""
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    store.set_setting("llm.max_parallel", 1)
    index.sync_text(store, mid)
    frozen = np.full(768, 0.01, np.float32)

    def other_process() -> None:
        ids, mat = store.rag.vectors_by_model(MODEL)
        sigs = vectors.signatures(mat, frozen)
        with store.transaction():
            store.rag.set_signatures([(int(v), sigs[i].tobytes()) for i, v in enumerate(ids)])
            store.rag.set_model(MODEL, 768, frozen, 5000)                                         # congelado lá

    index.fill_vectors(store, mid, provider=ScaledFake(during=other_process, at=4), batch_size=1)
    _ids, mat = store.rag.vectors_by_model(MODEL)
    _vids, _chunks, sigs = store.rag.signatures(MODEL)
    model = store.rag.get_model(MODEL)
    assert model["mu_n"] == 5000 and np.array_equal(sigs, vectors.signatures(mat, model["mu"]))


def test_sweep_completes_only_a_confirmed_model_and_reindex_all_confirms_it(store):
    """R5-09: trocar o embedder ativo não reindexa tudo na próxima abertura; o "reindexar agora" (`reindex_all`) confirma o
    modelo novo e põe tudo na fila com `force`; depois disso a varredura completa o que faltar dele."""
    who = _people(store)
    a, _ = _meeting(store, who, "Primeira")
    b, _ = _meeting(store, who, "Segunda")
    _ready(store)
    for mid in (a, b):
        index.sync_text(store, mid)
        index.fill_vectors(store, mid, provider=FakeProvider())
    store.providers.update_provider("openai", api_key=KEY)
    store.providers.set_active("openai", embed=True)
    status = index.embedder_status(store)
    key = status.model_key
    assert tuple(status) == (True, "", "openai/text-embedding-3-small@768", "openai") and key is not None
    assert store.rag.model_confirmed(MODEL) and not store.rag.model_confirmed(key)
    assert index.sweep(store) == 0                                                                  # sem confirmação: nada
    assert index.reindex_all(store) == 2
    pending = store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))
    assert {json.dumps(json.loads(j["params_json"]), sort_keys=True) for j in pending} == {'{"force": true, "reason": "reindex"}'}
    assert store.rag.model_confirmed(key) and store.rag.get_model(key)["backfill"] == 1
    for j in pending:
        store.jobs.request_cancel(int(j["id"]))
    assert index.sweep(store) == 2                                                                  # confirmado: completa


def test_fill_vectors_builds_the_provider_by_its_id_and_records_it(store, monkeypatch):
    """R5-10: o provedor nasce pelo id da linha (`EmbedderStatus.provider_id`), não pelo tipo."""
    from listener.llm import registry

    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    asked: list[Any] = []
    real = registry.build_provider

    def spy(store_: Store, provider_id: str | None = None, **kw: Any) -> Any:
        asked.append(provider_id)
        return real(store_, provider_id, **kw)

    monkeypatch.setenv("LISTENER_LLM", "fake")
    monkeypatch.setattr(registry, "build_provider", spy)
    filled = index.fill_vectors(store, mid)
    assert asked == ["gemini"] and filled.written == 5
    assert {c["provider"] for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "rag_embed"} == {"gemini"}


def test_sweep_retries_failed_index_and_stops_when_asked(store):
    """R5-07: a reunião cujo último `index` terminou em erro volta na varredura (uma vez por abertura); R5-02: a saída do
    app interrompe a varredura entre as consultas e as reuniões."""
    who = _people(store)
    a, _ = _meeting(store, who, "Indexada")
    index.sync_text(store, a)
    failed = index.enqueue_index(store, a, "edit")
    store.jobs.claim(pid=1, kinds=LIGHT_JOB_KINDS)
    store.jobs.finish(failed, JobStatus.ERROR, error="queda no sync_text")
    b, _ = _meeting(store, who, "Nova")
    assert index.sweep(store, should_stop=lambda: True) == 0 and not store.jobs.has_pending(kinds=("index",))
    calls = {"n": 0}

    def stop_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] > 3                                                                      # as 2 consultas e 1 reunião
    assert index.sweep(store, should_stop=stop_after_first) == 1
    assert index.sweep(store) == 1
    assert {j["meeting_id"] for j in store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))} == {a, b}


def test_the_first_model_stays_confirmed_after_switching_away_and_back(store):
    """R5-09: o primeiro modelo do índice (Q1) fica confirmado na varredura; trocar para outro e voltar não o desconfirma."""
    who = _people(store)
    a, _ = _meeting(store, who, "Primeira")
    _ready(store)
    index.sync_text(store, a)
    assert index.sweep(store) == 1 and store.rag.get_model(MODEL)["backfill"] == 1
    index.fill_vectors(store, a, provider=FakeProvider())
    store.providers.update_provider("openai", api_key=KEY)
    store.providers.set_active("openai", embed=True)
    index.fill_vectors(store, a, provider=FakeProvider())                                            # um vetor do outro modelo
    store.providers.set_active("gemini", embed=True)
    assert store.rag.model_confirmed(MODEL)


def test_sweep_puts_meetings_without_chunks_in_the_fts_even_without_automatic_vectors(store):
    """R6-09: com os vetores automáticos desligados, a varredura ainda põe na fila as reuniões processadas sem trecho (as
    anteriores à F5 entram na busca por palavra); só a falta de vetor depende dos vetores automáticos."""
    who = _people(store)
    old, _ = _meeting(store, who, "Anterior à F5")
    vectorless, _ = _meeting(store, who, "Sem vetor")
    index.sync_text(store, vectorless)
    _ready(store)
    store.set_setting("rag.auto_index", False)
    assert index.sweep(store) == 1
    assert [j["meeting_id"] for j in store.jobs.list_jobs(kinds=("index",))] == [old]
    store.set_setting("rag.auto_index", True)
    assert index.sweep(store) == 1 and {j["meeting_id"] for j in store.jobs.list_jobs(kinds=("index",))} == {old, vectorless}
