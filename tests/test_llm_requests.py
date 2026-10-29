"""F5.6 L1a (A-10 a A-17, P-07): os pedidos de ata e resumo. "Agora" e "quando todos tiverem nome", antes e depois do fim do
processamento, inclusive no instante entre o `finish` do runner e o enfileiramento (A-12), consumidos uma vez; o processamento
automático desligado (preso ao próximo); preso a um processamento desfeito, nunca; os seis estados e o desatualizado
calculado; o custo pela duração e pela transcrição, sem montar o contexto da LLM; o pedido com job de LLM ativo entra na fila
(ou fica com ele, se já faz o mesmo); o resumo com a ata em dia não refaz a ata (P-07); as vozes sem nome (A-16)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from listener.llm.base import LlmError
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask
from listener.model import EventType, JobKind, JobStatus, SummaryKind
from listener.pipeline import llm_request_state, llm_requests, llm_steps, reprocess, review, summary_gate
from listener.pipeline.llm_requests import Mode, Species, State
from listener.pipeline.runner import JobRunner
from tests.fake_pipeline import FakeDeps
from tests.test_llm_steps import SCRIPT, _pipeline, _provider_ready, _record, _run_llm
from tests.test_runner import CFG


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _llm(store: Any, mid: int) -> list[dict]:
    return sorted((j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM), key=lambda j: int(j["id"]))


def _tasks(store: Any, job_id: int | None) -> list[str]:
    return json.loads(store.jobs.get(job_id)["params_json"])["tasks"]


def _events(store: Any, mid: int, kind: EventType) -> list[dict]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == kind]


def _ask(store: Any, mid: int, tasks: list[str], mode: Mode = Mode.NOW, after: int | None = None) -> llm_requests.RequestResult:
    return llm_requests.request(store, mid, tasks, mode, "teste", after_job_id=after)


def _heavy(store: Any) -> int:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


# ---------------------------------------------------------------- pedir e consumir (A-11, A-12)
def test_now_and_names_after_the_processing(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    now = _ask(store, mid, ["minutes"])
    assert now.job_id is not None and not now.waiting and _tasks(store, now.job_id) == ["minutes"]
    assert _events(store, mid, EventType.LLM_RELEASED)[-1] == {"parent_job_id": parent, "tasks": ["minutes"], "job_id": now.job_id,
                                                               "reason": "teste", "request_id": now.request_id}
    names = _ask(store, mid, ["executive"], Mode.NAMES)                              # 2 vozes pendentes: espera
    assert names.waiting and names.job_id is None and summary_gate.deferred(store, mid).request_ids == (names.request_id,)
    llm_requests.after_pipeline(store, mid, parent, JobKind.MEETING)               # outro fim do mesmo job: nada (`llm_chained`)
    assert [_tasks(store, j["id"]) for j in _llm(store, mid)] == [["identify", "title", "revise"], ["minutes"]]
    assert len(_events(store, mid, EventType.LLM_DEFERRED)) == 1 and len(_events(store, mid, EventType.LLM_RELEASED)) == 1
    for cid in [c.cluster_id for c in review.pending(review.review_items(store, mid))]:
        review.create_person(store, cid, f"P{cid}")
    assert _ask(store, mid, ["executive"], Mode.NAMES).job_id is not None           # sem voz pendente: "quando" vira "já"


def test_now_and_names_before_the_end_of_the_processing(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    now = _ask(store, mid, ["minutes"], after=job)
    names = _ask(store, mid, ["executive", "pre_summary"], Mode.NAMES)              # sem `after`: o processamento ativo
    assert (now.after_job_id, names.after_job_id) == (job, job) and now.job_id is None and not names.waiting
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.REQUESTED
    assert _heavy(store) == job
    jobs = _llm(store, mid)
    assert [_tasks(store, j["id"]) for j in jobs] == [["identify", "title", "revise"], ["minutes"]]
    deferred = summary_gate.deferred(store, mid)
    assert deferred.request_ids == (names.request_id,) and deferred.tasks == ("executive", "pre_summary")
    released = _events(store, mid, EventType.LLM_RELEASED)
    assert [d["request_id"] for d in released] == [now.request_id] and released[0]["reason"] == "fim do processamento"


def test_a_request_between_the_finish_and_the_enqueue_chains_first_and_is_applied_once(platform, store, monkeypatch):
    """A-12, R-05: o runner marca o job concluído antes de enfileirar a LLM; o pedido nesse meio encadeia antes (o núcleo
    entra na fila na frente da ata) e recolhe o pedido de antes; o enfileiramento do runner, depois, não faz nada de novo."""
    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    asked_before = _ask(store, mid, ["minutes"], after=job)                          # preso: o fim do job o consome
    monkeypatch.setattr(JobRunner, "_enqueue_llm", lambda self: None)              # o fim sem o enfileiramento, ainda
    assert _heavy(store) == job and store.jobs.get(job)["status"] == JobStatus.DONE
    between = _ask(store, mid, ["executive"], after=job)                             # a corrida
    tasks = [_tasks(store, j["id"]) for j in _llm(store, mid)]
    assert tasks == [["identify", "title", "revise"], ["minutes", "executive"]]         # o núcleo na frente (R-05)
    assert between.job_id == _llm(store, mid)[1]["id"] and between.outcome == llm_requests.Outcome.QUEUED
    released = {d["request_id"]: d["job_id"] for d in _events(store, mid, EventType.LLM_RELEASED)}
    assert released == {between.request_id: between.job_id, asked_before.request_id: between.job_id}   # cada pedido uma vez
    llm_requests.after_pipeline(store, mid, job, JobKind.MEETING)                  # o do runner, atrasado: nada de novo
    assert len(_llm(store, mid)) == 2 and len(_events(store, mid, EventType.LLM_RELEASED)) == 2


def test_automatic_processing_off_waits_for_the_next_and_an_undone_one_never_consumes(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    nxt = _ask(store, mid, ["minutes"])                                              # nada na fila: o próximo processamento
    assert nxt.request_id is not None and nxt.job_id is None and nxt.after_job_id is None
    undone = store.jobs.enqueue(mid, JobKind.REPROCESS, {"reason": "language", "language": "en", "only_for_language": True})
    stuck = _ask(store, mid, ["executive"], after=undone)
    store.jobs.request_cancel(undone)                                                # a troca desfeita
    store.jobs.enqueue(mid)
    parent = _heavy(store)
    assert [_tasks(store, j["id"]) for j in _llm(store, mid)] == [["identify", "title", "revise"], ["minutes"]]
    assert {d["request_id"] for d in _events(store, mid, EventType.LLM_RELEASED)} == {nxt.request_id}
    llm_requests.after_pipeline(store, mid, parent, JobKind.MEETING)
    assert stuck.request_id not in {d["request_id"] for d in _events(store, mid, EventType.LLM_RELEASED)}
    assert llm_requests.request_state(store, mid, Species.SUMMARY).state == State.NONE    # preso ao desfeito: não vale


def test_a_request_with_an_active_llm_job_queues_behind_it_or_joins_the_same(platform, store):
    """A-10: antes, "gerar de novo" recusava com job de LLM ativo."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    core = _llm(store, mid)[0]["id"]                                                 # o núcleo pendente
    first = _ask(store, mid, ["minutes"])
    assert first.job_id is not None and first.job_id > core
    again = _ask(store, mid, ["minutes"])                                            # o mesmo: fica com o da fila
    assert again.job_id == first.job_id and len(_llm(store, mid)) == 2
    other = _ask(store, mid, ["executive"])                                          # a ata da fila sai antes (P-07)
    assert other.job_id not in (core, first.job_id) and _tasks(store, other.job_id) == ["executive"]


def test_the_summary_with_up_to_date_minutes_does_not_redo_them(platform, store):
    """P-07: o resumo leva a ata só quando ela falta ou está desatualizada."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    assert llm_requests.resolve(store, mid, ["executive", "pre_summary"]) == ["minutes", "executive", "pre_summary"]   # falta
    _ask(store, mid, ["minutes"])
    _run_llm(store)
    asked = _ask(store, mid, ["executive", "pre_summary"])
    assert _tasks(store, asked.job_id) == ["executive", "pre_summary"]               # em dia: só o resumo
    _run_llm(store)
    review.create_person(store, review.pending(review.review_items(store, mid))[0].cluster_id, "Ana")
    assert llm_requests.resolve(store, mid, ["executive"]) == ["minutes", "executive"]   # os nomes mudaram: a ata vai junto


def test_reprocess_cancels_stale_pending_llm_jobs_and_keeps_what_never_ran(platform, store):
    """R3-02: o reprocessamento cancela os jobs `llm` pendentes feitos para o pai antigo (o de teste fica) e o job novo leva,
    além de identidade e revisão (P7), o título que a reunião ainda não tem; F5.6 (A-05): nunca a ata que falta."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    stale = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    regen = llm_steps.enqueue_llm_job(store, mid, ["title"])
    test_job = llm_steps.enqueue_test_job(store, mid)
    rep = _pipeline(store, mid, JobKind.REPROCESS)
    assert store.jobs.get(stale["id"])["status"] == "cancelado" and store.jobs.get(regen)["status"] == "cancelado"
    assert store.jobs.get(test_job)["status"] == "pendente"
    new = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente" and j["id"] != test_job)
    params = json.loads(new["params_json"])
    assert params["parent_job_id"] == rep and params["tasks"] == ["identify", "title", "revise"]
    for _ in range(2):
        _, status, _fake = _run_llm(store)
        assert status == JobStatus.DONE
    assert store.summaries.current(mid, SummaryKind.MINUTES) is None and store.get_meeting(mid)["title_llm"]
    assert llm_steps.auto_tasks_for(store, mid, JobKind.REPROCESS) == ["identify", "revise"]          # o título já existe (P7)
    # um pedido pendente durante outro reprocessamento passa para o job novo, mesmo sem tarefas automáticas (pelo pedido)
    store.set_setting("llm.auto_tasks", False)
    manual = _ask(store, mid, ["executive"]).job_id
    rep2 = _pipeline(store, mid, JobKind.REPROCESS)
    assert store.jobs.get(manual)["status"] == "cancelado"
    moved = [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente" and j["id"] != test_job]
    assert [json.loads(j["params_json"]) for j in moved] == [{"tasks": ["minutes", "executive"], "parent_job_id": rep2, "auto": False,
                                                             "mode": "now"}]


# ---------------------------------------------------------------- estados (A-13, A-14)
def test_the_six_states_and_the_computed_stale(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    assert {sp: st.state for sp, st in llm_requests.request_states(store, mid).items()} == {Species.MINUTES: State.NONE,
                                                                                           Species.SUMMARY: State.NONE}
    _ask(store, mid, ["minutes"], Mode.NAMES)
    assert llm_requests.request_state(store, mid, "minutes").state == State.REQUESTED    # adiado
    assert llm_requests.request_state(store, mid, "summary").state == State.NONE
    llm_requests.release(store, mid, "concluir", force=True)
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.RUNNING
    failing = FakeProvider()
    failing.fail_next(LlmError("recusado", permanent=True), LlmTask.MINUTES)
    _run_llm(store, failing)
    failed = llm_requests.request_state(store, mid, Species.MINUTES)
    assert failed.state == State.FAILED and "recusado" in failed.error
    _ask(store, mid, ["minutes", "executive", "pre_summary"])
    _run_llm(store)
    done = llm_requests.request_states(store, mid)
    assert done[Species.MINUTES].state == done[Species.SUMMARY].state == State.DONE and done[Species.MINUTES].when
    _ask(store, mid, ["minutes"])                                                    # a ata nova: o resumo fica mais antigo
    _run_llm(store)
    summary = llm_requests.request_state(store, mid, Species.SUMMARY)
    assert summary.state == State.STALE and summary.reasons == ("ata nova",)
    store._x("UPDATE summaries SET language = 'en' WHERE meeting_id = ? AND kind = 'minutes' AND status = 'atual'", (mid,))
    assert llm_requests.request_state(store, mid, Species.MINUTES).reasons == ("idioma",)
    review.create_person(store, review.pending(review.review_items(store, mid))[0].cluster_id, "Ana")
    assert "nomes" in llm_requests.request_state(store, mid, Species.MINUTES).reasons    # F5.5 v2
    _pipeline(store, mid, JobKind.REPROCESS)
    assert "transcrição" in llm_requests.request_state(store, mid, Species.MINUTES).reasons


def test_an_older_failure_gives_way_to_a_newer_request(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    _ask(store, mid, ["minutes"])
    failing = FakeProvider()
    failing.fail_next(LlmError("recusado", permanent=True), LlmTask.MINUTES)
    _run_llm(store, failing)
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.FAILED
    _ask(store, mid, ["minutes"], Mode.NAMES)                                        # pedido de novo, esperando os nomes
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.REQUESTED


# ---------------------------------------------------------------- custo (A-17) e vozes (A-16)
def test_the_cost_by_duration_then_by_the_transcript_without_the_llm_context(platform, store, monkeypatch):
    def no_context(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("o custo do pedido não monta o contexto da LLM (A-17)")

    monkeypatch.setattr(llm_steps, "build_context", no_context)
    mid = _record(platform, store)
    _provider_ready(store)
    store._x("UPDATE meetings SET duration_ms = ? WHERE id = ?", (60 * 60_000, mid))
    before = llm_requests.estimate(store, mid, ["minutes"])
    assert before.by_duration and before.tokens == 60 * llm_request_state.TOKENS_PER_MIN and before.usd and before.usd > 0
    _pipeline(store, mid)
    after = llm_requests.estimate(store, mid, ["executive"])
    job = store.segments.current_job_id(mid)
    chars, lines = store.segments.text_size(mid, job)
    assert lines == store.segments.count_segments(mid, job, with_text=True) and chars == sum(
        len(s["text"]) for s in store.segments.list_segments(mid, job) if s["text"].strip())
    assert not after.by_duration and after.tasks == ("minutes", "executive")      # P-07: a ata junto
    assert after.tokens == round((chars + llm_request_state.LINE_CHARS * lines) / 3.5) and after.usd and after.usd > 0
    store.providers.update_provider("gemini", strong_model="modelo-sem-preco")
    assert llm_requests.estimate(store, mid, ["minutes"]).usd is None                   # sem preço cadastrado: sem conta


def test_the_unnamed_voices_in_the_three_cases(platform, store):
    """A-16: sem transcrição ("a identificação ainda não rodou"), a identidade na fila e as vozes contadas."""
    mid = _record(platform, store)
    _provider_ready(store)
    assert llm_requests.voices(store, mid) == llm_requests.Voices(0, False, False)
    _pipeline(store, mid)
    assert llm_requests.voices(store, mid) == llm_requests.Voices(2, True, True)       # o núcleo pendente
    _run_llm(store)
    for cid in [c.cluster_id for c in review.pending(review.review_items(store, mid))]:
        review.create_person(store, cid, f"P{cid}")
    assert llm_requests.voices(store, mid) == llm_requests.Voices(0, True, False)


def test_a_deferred_request_follows_the_reprocessing_and_a_request_without_speech_is_refused(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    asked = _ask(store, mid, ["minutes"], Mode.NAMES)
    rep = _pipeline(store, mid, JobKind.REPROCESS)                                    # o adiamento vai para a transcrição nova
    deferred = summary_gate.deferred(store, mid)
    assert deferred is not None and deferred.parent_job_id == rep and deferred.request_ids == (asked.request_id,)
    assert llm_requests.request(store, mid, ["title"], Mode.NOW, "teste").refused == llm_requests.NOTHING   # o núcleo não se pede
    with pytest.raises(ValueError):
        llm_requests.request(store, mid, ["minutes"], "talvez", "teste")


# ---------------------------------------------------------------- revisão do diff do L1a (R-01, R-02, R-04, R-08, desvio 3)
def _claim(store: Any) -> int:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None
    return int(job["id"])


def test_a_request_follows_the_processing_that_replaces_its_own(platform, store):
    """R-01: a troca de idioma cancela o processamento que roda e põe outro no lugar (`replaces`): o pedido feito ao parar,
    preso ao primeiro, segue "pedido" e sai no fim do substituto."""
    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    assert _claim(store) == job
    asked = _ask(store, mid, ["minutes"], after=job)
    res = reprocess.request_reprocess(store, mid, "en")
    assert res.cancelled == job and json.loads(store.jobs.get(res.job_id)["params_json"])["replaces"] == job
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.REQUESTED
    store.jobs.finish(job, JobStatus.CANCELLED)                                       # o worker atende o cancelamento
    assert _heavy(store) == res.job_id
    released = {d["request_id"]: d["job_id"] for d in _events(store, mid, EventType.LLM_RELEASED)}
    assert _tasks(store, released[asked.request_id]) == ["minutes"]


def test_a_request_follows_the_job_restored_by_the_undo(platform, store):
    """R-01: desfazer a troca com o processamento de antes rodando no idioma pedido: ele para e outro refaz o de antes
    (`replaces`); o pedido preso ao primeiro sai no fim do segundo."""
    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    assert reprocess.request_reprocess(store, mid, "en").job_id == job               # pendente: só troca o idioma
    asked = _ask(store, mid, ["minutes"], after=job)
    assert _claim(store) == job
    undo = reprocess.request_reprocess(store, mid, "pt-BR")
    assert undo.job_id not in (None, job) and json.loads(store.jobs.get(undo.job_id)["params_json"])["replaces"] == job
    store.jobs.finish(job, JobStatus.CANCELLED)
    assert _heavy(store) == undo.job_id
    assert asked.request_id in {d["request_id"] for d in _events(store, mid, EventType.LLM_RELEASED)}
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.RUNNING


def test_bind_never_holds_a_request_to_a_dead_processing(platform, store):
    """R-01: preso a um processamento cancelado ou com erro e sem substituto, o pedido vai para o próximo (a tela não promete
    "sai quando o processamento terminar"); com substituto, para ele."""
    from listener.ui.llm_tabs import ask_now

    mid = _record(platform, store)
    _provider_ready(store)
    dead = store.jobs.enqueue(mid)
    store.jobs.request_cancel(dead)
    first = _ask(store, mid, ["minutes"], after=dead)
    assert first.outcome == llm_requests.Outcome.NEXT and first.after_job_id is None
    err = store.jobs.enqueue(mid)
    assert _claim(store) == err
    store.jobs.finish(err, JobStatus.ERROR, error="falhou")
    assert _ask(store, mid, ["minutes"], after=err).outcome == llm_requests.Outcome.NEXT
    assert ask_now(store, mid, ["minutes"], "teste")[1] == "Ata: sai quando a reunião for processada."
    old = store.jobs.enqueue(mid)
    assert _claim(store) == old
    new = reprocess.request_reprocess(store, mid, "en").job_id                       # o que roda é cancelado e substituído
    res = _ask(store, mid, ["executive"], after=old)
    assert res.outcome == llm_requests.Outcome.BOUND and res.after_job_id == new
    assert ask_now(store, mid, ["executive"], "teste")[1] == "Resumo executivo: sai quando o processamento da reunião terminar."


def test_a_failed_chain_keeps_the_request_and_the_next_request_chains_first(platform, store, monkeypatch):
    """R-02: o encadeamento que falha (o runner engole o erro) deixa o pedido vivo e sem o marcador; o próximo pedido
    encadeia (o núcleo na frente) e leva os dois num job só; com a ata pronta, o estado sai de "pedido"."""
    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    asked = _ask(store, mid, ["minutes"], after=job)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(llm_requests, "enqueue_after_pipeline", boom)
        assert _heavy(store) == job
    assert _llm(store, mid) == [] and llm_requests.request_state(store, mid, Species.MINUTES).state == State.REQUESTED
    other = _ask(store, mid, ["minutes"])                                            # o usuário clica de novo
    assert [_tasks(store, j["id"]) for j in _llm(store, mid)] == [["identify", "title", "revise"], ["minutes"]]
    released = {d["request_id"]: d["job_id"] for d in _events(store, mid, EventType.LLM_RELEASED)}
    assert released == {asked.request_id: other.job_id, other.request_id: other.job_id}
    _run_llm(store)
    _run_llm(store)
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.DONE


def test_without_a_key_at_the_end_the_request_stays_and_comes_out_with_the_key(platform, store):
    """R-04: a chave sai entre o pedido e o fim do processamento: nada é gasto nem marcado, o pedido segue "pedido"; o "agora"
    sem chave tem a mensagem dele; com a chave de volta, o próximo pedido encadeia e leva o de antes."""
    from listener.ui.llm_tabs import ask_now

    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    asked = _ask(store, mid, ["minutes"], after=job)
    store.providers.update_provider("gemini", api_key="")
    assert _heavy(store) == job and _llm(store, mid) == [] and not _events(store, mid, EventType.LLM_RELEASED)
    assert llm_requests.request_state(store, mid, Species.MINUTES).state == State.REQUESTED
    assert ask_now(store, mid, ["minutes"], "teste") == (None, llm_requests.NO_KEY)
    _provider_ready(store)
    other = _ask(store, mid, ["executive"])
    assert [_tasks(store, j["id"]) for j in _llm(store, mid)] == [["identify", "title", "revise"], ["minutes", "executive"]]
    assert {d["request_id"] for d in _events(store, mid, EventType.LLM_RELEASED)} == {asked.request_id, other.request_id}


def test_a_request_joins_only_a_job_that_still_has_the_step_to_do(platform, store):
    """R-08: com o job da ata rodando e a ata já gravada (no executivo), "Atualizar a ata com os nomes" entra na fila atrás
    dele, não fica com ele."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    first = _ask(store, mid, ["minutes", "executive"]).job_id
    running = store.jobs.claim(pid=2, kinds=("llm",))
    assert int(running["id"]) == first
    assert _ask(store, mid, ["minutes"]).job_id == first                             # rodando, ainda antes da ata: fica com ele
    store.jobs.put_artifact(first, llm_steps.JobStep.MINUTES, 0, b"ok")                # a ata saiu; roda o executivo
    again = _ask(store, mid, ["minutes"])
    assert again.job_id not in (None, first) and _tasks(store, again.job_id) == ["minutes"]


def test_a_names_request_carried_by_the_reprocessing_waits_again(platform, store, monkeypatch):
    """Desvio 3: o job de um pedido "quando todos tiverem nome" guarda o modo; cancelado pelo reprocessamento, volta como
    "names" e, com voz pendente na transcrição nova, espera de novo (o "agora" vira job, no teste do reprocessamento)."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    with monkeypatch.context() as m:
        m.setattr(summary_gate, "pending_voices", lambda *_a, **_k: 0)
        job = _ask(store, mid, ["minutes"], Mode.NAMES).job_id                         # sem voz pendente agora: vira job
    assert json.loads(store.jobs.get(job)["params_json"])["mode"] == "names"
    rep = _pipeline(store, mid, JobKind.REPROCESS)
    assert store.jobs.get(job)["status"] == JobStatus.CANCELLED and summary_gate.pending_voices(store, mid) == 2
    deferred = summary_gate.deferred(store, mid)
    assert deferred is not None and deferred.parent_job_id == rep and deferred.tasks == ("minutes",)
    assert [_tasks(store, j["id"]) for j in _llm(store, mid) if j["status"] == JobStatus.PENDING] == [["identify", "revise"]]
