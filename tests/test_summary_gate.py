"""U-042 (F5.2 L3) e F5.6 L1a (A-02, A-03, A-09, A-12, P-01): a ata depois da identificação das vozes, só a pedido. Sem
pedido nada espera nem sai; o pedido "quando todos tiverem nome" com voz pendente vira o adiamento `llm_deferred` pedido e
sai uma vez quando a revisão fica sem voz pendente ou no "Concluir" (`llm_released`); o adiamento automático da F5.2, sem a
marca, nunca dispara; "Gerar ata mesmo assim" é um pedido "agora"; "Atualizar a ata com os nomes" refaz só o que já existe;
o pedido não é cortado pelo alerta de custo."""
from __future__ import annotations

import json

import pytest

from listener.llm.fake import FakeProvider
from listener.llm.fake_scripts import DEFAULTS
from listener.llm.types import LlmTask
from listener.model import EventType, JobKind, JobStatus, MeetingStatus, SummaryKind
from listener.pipeline import llm_requests, review, summary_gate
from listener.pipeline.llm_steps import enqueue_after_pipeline
from tests.test_llm_steps import _ask, _pipeline, _provider_ready, _record, _run_llm

WAITING = ("minutes",)


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _voices(store, mid) -> list[int]:
    return [c.cluster_id for c in review.pending(review.review_items(store, mid))]


def _llm_jobs(store, mid) -> list[dict]:
    return sorted((j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM), key=lambda j: int(j["id"]))


def _names(store, mid, tasks=WAITING) -> llm_requests.RequestResult:
    return llm_requests.request(store, mid, list(tasks), llm_requests.Mode.NAMES, "teste")


def test_the_minutes_wait_for_the_voices_and_come_out_once(platform, store, caplog):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    first = _llm_jobs(store, mid)
    assert len(first) == 1 and json.loads(first[0]["params_json"])["tasks"] == ["identify", "title", "revise"]
    assert summary_gate.deferred(store, mid) is None                                          # F5.6: sem pedido, nada espera
    asked = _names(store, mid)
    waiting = summary_gate.deferred(store, mid)
    assert asked.waiting and asked.job_id is None and waiting is not None and waiting.tasks == WAITING and waiting.pending == 2
    assert waiting.request_ids == (asked.request_id,)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and store.summaries.current(mid, SummaryKind.MINUTES) is None
    assert llm_requests.release(store, mid, "teste") is None                                  # ainda há voz pendente
    with caplog.at_level("INFO", logger="listener.pipeline"):
        voices = _voices(store, mid)
        review.create_person(store, voices[0], "Ana", "CFO")
        review.create_person(store, voices[1], "Bia")
        job = llm_requests.release(store, mid, "revisão sem voz pendente")
    assert job is not None
    assert json.loads(store.jobs.get(job)["params_json"]) == {"tasks": list(WAITING), "parent_job_id": parent, "auto": False,
                                                              "mode": "names"}
    assert "liberado" in caplog.text and "Ana" not in caplog.text and "Bia" not in caplog.text
    assert llm_requests.release(store, mid, "de novo", force=True) is None and summary_gate.deferred(store, mid) is None   # uma vez
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and store.summaries.current(mid, SummaryKind.MINUTES) is not None
    assert not summary_gate.names_changed(store, mid)
    ana = store.people.find_by_name("Ana")["id"]
    review.edit_person(store, ana, mid, "Ana Souza", "CFO")
    assert summary_gate.names_changed(store, mid)                                             # o nome mudou depois da ata
    assert _llm_jobs(store, mid)[-1]["id"] == job                                             # nomes mudados: nada sai sozinho


def test_an_old_automatic_deferral_without_the_request_mark_never_fires(platform, store):
    """A-03: o banco do dono tem adiamentos automáticos da F5.2 (sem `requested`): nenhum disparo, nem no "Concluir"."""
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    store.add_event(mid, None, EventType.LLM_DEFERRED, {"parent_job_id": parent, "tasks": ["minutes", "executive"], "pending": 2})
    assert summary_gate.deferred(store, mid) is None
    for voice in _voices(store, mid):
        review.create_person(store, voice, f"Pessoa {voice}")
    assert llm_requests.release(store, mid, "revisão sem voz pendente") is None
    assert llm_requests.release(store, mid, "concluir", force=True) is None
    assert len(_llm_jobs(store, mid)) == 1                                                    # só o núcleo


def test_concluding_forces_and_another_parent_or_a_queued_pipeline_holds(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    asked = _names(store, mid)
    store.add_event(mid, None, EventType.LLM_DEFERRED, {"parent_job_id": parent + 99, "tasks": ["executive"], "pending": 1,
                                                        "requested": True, "request_id": asked.request_id + 99})
    assert summary_gate.deferred(store, mid).tasks == WAITING                                 # o de outro job pai não conta
    heavy = store.jobs.enqueue(mid, JobKind.REPROCESS)
    store.jobs.enqueue(mid, JobKind.EMBED_RANGE, {"t0_ms": 0, "t1_ms": 1000})              # R-09: o perfil de trecho não segura
    assert llm_requests.release(store, mid, "concluir", force=True) is None                 # o reprocessamento o refaz
    store.jobs.request_cancel(heavy)
    job = llm_requests.release(store, mid, "concluir", force=True)
    assert job is not None and summary_gate.deferred(store, mid) is None
    released = [e for e in store.list_events(mid) if e["type"] == EventType.LLM_RELEASED]
    assert released[-1]["data"]["reason"] == "concluir" and released[-1]["data"]["job_id"] == job
    assert released[-1]["data"]["request_id"] == asked.request_id


def test_concluding_the_review_fires_the_names_request_once(qtbot, platform, store):
    """P-01: o "Concluir" com voz sem nome dispara o pedido "quando todos tiverem nome", uma vez."""
    from listener.ui.review import ReviewDialog

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _names(store, mid, ["minutes", "executive"])
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    dlg.finish()
    jobs = _llm_jobs(store, mid)
    assert launched == [True] and len(jobs) == 2 and summary_gate.deferred(store, mid) is None
    assert json.loads(jobs[-1]["params_json"])["tasks"] == ["minutes", "executive"]
    assert llm_requests.release(store, mid, "concluir", force=True) is None and len(_llm_jobs(store, mid)) == 2


def test_a_review_concluded_during_the_processing_does_not_hold_the_minutes(platform, store, caplog):
    """R-01: o "Concluir" durante o Whisper deixa a reunião `revisada` com voz sem nome por decisão do usuário (R2-11); o fim
    do processamento não adia o pedido feito antes dele (nada mais o liberaria). O feito depois espera (R-03)."""
    mid = _record(platform, store)
    _provider_ready(store)
    asked = _names(store, mid)                                                                # antes de processar: o próximo
    assert asked.request_id is not None and asked.job_id is None and asked.after_job_id is None and not asked.waiting
    review.finish(store, mid)                                                                 # o "Concluir" durante o processamento
    with caplog.at_level("INFO", logger="listener.pipeline"):
        _pipeline(store, mid)
    assert str(store.get_meeting(mid)["status"]) == MeetingStatus.REVIEWED and summary_gate.pending_voices(store, mid) == 2
    assert summary_gate.deferred(store, mid) is None
    assert [json.loads(j["params_json"])["tasks"] for j in _llm_jobs(store, mid)] == [["identify", "title", "revise"], ["minutes"]]
    assert 'anterior ao "Concluir"' in caplog.text
    after = _names(store, mid, ["executive"])                                                 # pedido depois do "Concluir"
    assert after.waiting and summary_gate.deferred(store, mid).request_ids == (after.request_id,)


def test_a_name_given_while_the_minutes_are_written_lights_the_refresh(platform, store):
    """R-02: a assinatura de quem fala é a do contexto que a ata leu, não a do fim da geração: o nome dado na revisão
    enquanto a ata era escrita acende "Atualizar a ata com os nomes"."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)                                                                           # o núcleo
    _names(store, mid)
    first, second = _voices(store, mid)
    review.create_person(store, first, "Ana", "CFO")
    assert llm_requests.release(store, mid, "teste", force=True) is not None
    named: list[int] = []

    def minutes(req):
        if not named:                                                                         # a revisão anda durante a chamada
            named.append(review.create_person(store, second, "Bia"))
        return DEFAULTS[LlmTask.MINUTES](req)

    _runner, status, _fake = _run_llm(store, FakeProvider(scripts={LlmTask.MINUTES: minutes}))
    assert status == JobStatus.DONE and named and store.summaries.current(mid, SummaryKind.MINUTES) is not None
    assert summary_gate.names_changed(store, mid)


def test_the_tray_promises_the_wait_only_with_a_request_that_waits(qtbot, qapp, platform, store, monkeypatch, caplog):
    """A-06: o aviso "a ata sai depois" só com um pedido "quando todos tiverem nome" que vai esperar: adiado, ou ainda por
    consumir no instante entre o fim do job e o enfileiramento (A-12), salvo o feito antes do "Concluir" (R-01); o feito
    depois espera (R-03). R-03 da F5.3: o log do balão leva o id e o texto, nunca o título, nem o pedaço depois de ": "."""
    from listener.pipeline.runner import JobRunner
    from listener.ui.controller import Controller

    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    store._x("UPDATE meetings SET title = ? WHERE id = ?", ("Conversa: Zuleica e Bia", mid))
    c = Controller(qapp, platform, store)
    c.tray = None
    opened: list[int] = []
    monkeypatch.setattr(c, "open_review", opened.append)
    try:
        caplog.clear()
        with caplog.at_level("INFO", logger="listener.ui"):
            c._on_job_done(store.jobs.get(parent))
        assert opened == [mid] and "Zuleica" not in caplog.text
        assert f"aviso na tela (pronto) sobre a reunião {mid}: pronta para revisão de falantes." in caplog.text   # sem pedido
        rep = store.jobs.enqueue(mid, JobKind.REPROCESS)
        _names(store, mid)                                                                    # preso ao reprocessamento
        with monkeypatch.context() as m:
            m.setattr(JobRunner, "_enqueue_llm", lambda self: None)                          # o aviso antes do enfileiramento
            assert _pipeline_claimed(store) == rep
        caplog.clear()
        with caplog.at_level("INFO", logger="listener.ui"):
            c._on_job_done(store.jobs.get(rep))
        assert "pronta para revisão de falantes; a ata sai depois que as vozes forem identificadas." in caplog.text
        with monkeypatch.context() as m:                                       # o "Concluir" e o pedido no mesmo milissegundo:
            m.setattr("listener.store.meetings._now", lambda: "2099-01-01T00:00:00.000-03:00")  # a ordem é a dos eventos
            review.finish(store, mid)
            assert not c._summary_waits(mid)                                                  # R-01: o de antes do "Concluir"
            _names(store, mid, ["executive"])                                                 # depois: espera (R-03)
        assert c._summary_waits(mid)
    finally:
        c.emergency_stop()


def _pipeline_claimed(store) -> int:
    from listener.pipeline.runner import JobRunner
    from tests.fake_pipeline import FakeDeps
    from tests.test_llm_steps import SCRIPT
    from tests.test_runner import CFG

    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


def test_without_a_key_nothing_and_with_automatic_tasks_off_the_request_still_works(platform, store):
    mid = _record(platform, store)
    parent = _pipeline(store, mid)                                                            # sem provedor com chave
    assert _llm_jobs(store, mid) == [] and summary_gate.deferred(store, mid) is None
    assert _names(store, mid).refused == llm_requests.NO_KEY                                  # comportamento 1: nada registrado
    _provider_ready(store)
    store.set_setting("llm.auto_tasks", "0")
    assert enqueue_after_pipeline(store, mid, parent, JobKind.MEETING) is None and summary_gate.deferred(store, mid) is None
    job = _ask(store, mid, ["minutes"])                                                       # A-20: desligada, o pedido vale
    assert job is not None and [j["id"] for j in _llm_jobs(store, mid)] == [job]


def test_the_review_releases_the_minutes_when_the_last_voice_is_named(qtbot, platform, store):
    from listener.ui.review import ReviewDialog

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _names(store, mid)
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    assert dlg.create_person("Ana", "CFO") and summary_gate.deferred(store, mid) is not None   # falta uma voz
    assert dlg.create_person("Bia") and summary_gate.deferred(store, mid) is None             # a última: a ata sai
    assert launched == [True] and len(_llm_jobs(store, mid)) == 2


def test_the_minutes_tab_offers_generate_now_and_refresh_only_what_exists(qtbot, platform, store):
    """A-02: "Gerar ata mesmo assim" é um pedido "agora" (o adiamento sai com ele) e "Atualizar a ata com os nomes" refaz só
    as espécies que já têm versão atual (antes refazia executivo e pré-resumo junto)."""
    from listener.ui.llm_tabs import LlmTabsGroup

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _names(store, mid)
    group = LlmTabsGroup(store, mid)
    ata = group.text_tabs[str(SummaryKind.MINUTES)]
    qtbot.addWidget(ata)
    group.reload()
    assert ata.btn_extra.text() == "Gerar ata mesmo assim" and "espera a identificação de 2 voz(es)" in ata.notice.text()
    assert not ata.btn_extra.isHidden()
    ata.btn_extra.click()                                                                     # gera já, com as vozes sem nome
    assert summary_gate.deferred(store, mid) is None and len(_llm_jobs(store, mid)) == 2
    assert json.loads(_llm_jobs(store, mid)[-1]["params_json"])["tasks"] == ["minutes"]
    _run_llm(store)                                                                           # o núcleo
    _run_llm(store)                                                                           # a ata pedida
    group.reload()
    assert ata.btn_extra.isHidden()
    review.create_person(store, _voices(store, mid)[0], "Ana", "CFO")                        # o nome chegou depois da ata
    group.reload()
    assert ata.btn_extra.text() == "Atualizar a ata com os nomes" and "Os nomes mudaram" in ata.notice.text()
    ata.btn_extra.click()
    refresh = json.loads(_llm_jobs(store, mid)[-1]["params_json"])["tasks"]
    assert refresh == ["minutes"]                                                             # sem executivo nem pré-resumo


def test_a_request_is_never_cut_by_the_cost_alert(platform, store):
    """A-09: o job do pedido entra com `auto=False`; acima do alerta, o executivo pedido sai mesmo assim."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    store.set_setting("llm.cost_alert_usd", 0.0000001)
    _run_llm(store)                                                                           # o núcleo (a revisada é cortada)
    job = _ask(store, mid, ["executive", "pre_summary"])
    assert json.loads(store.jobs.get(job)["params_json"])["auto"] is False
    runner, status, _fake = _run_llm(store)
    timings = json.loads(store.jobs.get(runner.job_id)["timings_json"])["llm"]
    assert status == JobStatus.DONE and timings["skipped"] == {}
    assert store.summaries.current(mid, SummaryKind.EXECUTIVE) is not None


# ---------------------------------------------------------------- revisão do diff do L1a (R-03, desvio 4)
def test_names_on_a_reviewed_meeting_waits_like_any_other(platform, store):
    """R-03: com a revisão já concluída e voz sem nome, "quando todos tiverem nome" espera como os outros (o estado diz
    "pedido") e sai no "Concluir" de novo; outro sai ao resolver a última voz."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    review.finish(store, mid)
    first = _names(store, mid)
    assert first.waiting and first.job_id is None and first.outcome == llm_requests.Outcome.WAITING
    assert llm_requests.request_state(store, mid, llm_requests.Species.MINUTES).state == llm_requests.State.REQUESTED
    assert llm_requests.release(store, mid, "concluir", force=True) is not None          # o "Concluir" de novo
    second = _names(store, mid, ["executive"])
    assert second.waiting
    for voice in _voices(store, mid):
        review.create_person(store, voice, f"Pessoa {voice}")
    job = llm_requests.release(store, mid, "revisão sem voz pendente")
    assert job is not None and "executive" in json.loads(store.jobs.get(job)["params_json"])["tasks"]



def test_a_now_request_takes_its_tasks_out_of_the_pending_deferrals(platform, store):
    """Desvio 4: o pedido "agora" tira as tarefas dele dos adiamentos pendentes: o adiamento que só tinha a ata sai com ele;
    o de ata, executivo e pré-resumo fica com executivo e pré-resumo, e na liberação a ata volta (P-07: os nomes mudaram)."""
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    _names(store, mid)
    now = llm_requests.request(store, mid, ["minutes"], llm_requests.Mode.NOW, "teste")
    assert now.job_id is not None and summary_gate.deferred(store, mid) is None             # subconjunto: consumido
    _run_llm(store)
    big = _names(store, mid, ["minutes", "executive", "pre_summary"])
    llm_requests.request(store, mid, ["minutes"], llm_requests.Mode.NOW, "teste")
    deferred = summary_gate.deferred(store, mid)
    assert deferred.request_ids == (big.request_id,) and deferred.tasks == ("executive", "pre_summary")   # sobreposição
    _run_llm(store)
    for voice in _voices(store, mid):
        review.create_person(store, voice, f"Pessoa {voice}")
    job = llm_requests.release(store, mid, "revisão sem voz pendente")
    assert json.loads(store.jobs.get(job)["params_json"])["tasks"] == ["minutes", "executive", "pre_summary"]
