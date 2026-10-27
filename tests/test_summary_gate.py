"""U-042 (F5.2 L3): a ata depois da identificação das vozes. Com voz pendente o job `llm` leva só a identidade e a
transcrição revisada; a ata, o executivo, o pré-resumo e o título esperam (`llm_deferred`) e saem uma vez quando a revisão
fica sem voz pendente, no "Concluir" ou em "Gerar ata mesmo assim" (`llm_released`); os nomes que mudam depois acendem
"Atualizar a ata com os nomes"."""
from __future__ import annotations

import json

import pytest

from listener.model import EventType, JobKind, JobStatus, SummaryKind
from listener.pipeline import review, summary_gate
from listener.pipeline.llm_steps import enqueue_after_pipeline
from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

WAITING = ("minutes", "executive", "pre_summary", "title")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _voices(store, mid) -> list[int]:
    return [c.cluster_id for c in review.pending(review.review_items(store, mid))]


def _llm_jobs(store, mid) -> list[dict]:
    return sorted((j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM), key=lambda j: int(j["id"]))


def test_the_minutes_wait_for_the_voices_and_come_out_once(platform, store, caplog):
    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    first = _llm_jobs(store, mid)
    assert len(first) == 1 and json.loads(first[0]["params_json"])["tasks"] == ["identify", "revise"]
    waiting = summary_gate.deferred(store, mid)
    assert waiting is not None and waiting.tasks == WAITING and waiting.pending == 2
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and store.summaries.current(mid, SummaryKind.MINUTES) is None
    assert summary_gate.release(store, mid, "teste") is None                                  # ainda há voz pendente
    with caplog.at_level("INFO", logger="listener.pipeline.summary_gate"):
        voices = _voices(store, mid)
        review.create_person(store, voices[0], "Ana", "CFO")
        review.create_person(store, voices[1], "Bia")
        job = summary_gate.release(store, mid, "revisão sem voz pendente")
    assert job is not None and json.loads(store.jobs.get(job)["params_json"])["tasks"] == list(WAITING)
    assert "ata liberada" in caplog.text and "Ana" not in caplog.text
    assert summary_gate.release(store, mid, "de novo", force=True) is None and summary_gate.deferred(store, mid) is None   # uma vez
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and store.summaries.current(mid, SummaryKind.MINUTES) is not None
    assert not summary_gate.names_changed(store, mid)
    ana = store.people.find_by_name("Ana")["id"]
    review.edit_person(store, ana, mid, "Ana Souza", "CFO")
    assert summary_gate.names_changed(store, mid)                                             # o nome mudou depois da ata


def test_concluding_forces_and_another_parent_or_a_queued_pipeline_holds(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    store.add_event(mid, None, EventType.LLM_DEFERRED, {"parent_job_id": parent + 99, "tasks": ["minutes"], "pending": 1})
    assert summary_gate.deferred(store, mid).tasks == WAITING                                 # o de outro job pai não conta
    heavy = store.jobs.enqueue(mid, JobKind.REPROCESS)
    assert summary_gate.release(store, mid, "concluir", force=True) is None                  # o reprocessamento terá o seu
    store.jobs.request_cancel(heavy)
    job = summary_gate.release(store, mid, "concluir", force=True)
    assert job is not None and summary_gate.deferred(store, mid) is None
    released = [e for e in store.list_events(mid) if e["type"] == EventType.LLM_RELEASED]
    assert released[-1]["data"]["reason"] == "concluir" and released[-1]["data"]["job_id"] == job


def test_without_a_key_or_with_automatic_tasks_off_nothing_waits(platform, store):
    mid = _record(platform, store)
    parent = _pipeline(store, mid)                                                            # sem provedor com chave
    assert _llm_jobs(store, mid) == [] and summary_gate.deferred(store, mid) is None
    _provider_ready(store)
    store.set_setting("llm.auto_tasks", "0")
    assert enqueue_after_pipeline(store, mid, parent, JobKind.MEETING) is None and summary_gate.deferred(store, mid) is None


def test_the_review_releases_the_minutes_when_the_last_voice_is_named(qtbot, platform, store):
    from listener.ui.review import ReviewDialog

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    assert dlg.create_person("Ana", "CFO") and summary_gate.deferred(store, mid) is not None   # falta uma voz
    assert dlg.create_person("Bia") and summary_gate.deferred(store, mid) is None             # a última: a ata sai
    assert launched == [True] and len(_llm_jobs(store, mid)) == 2


def test_the_minutes_tab_offers_generate_now_and_refresh_names(qtbot, platform, store):
    from listener.ui.llm_tabs import LlmTabsGroup

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    group = LlmTabsGroup(store, mid)
    ata = group.text_tabs[str(SummaryKind.MINUTES)]
    qtbot.addWidget(ata)
    group.reload()
    assert ata.btn_extra.text() == "Gerar ata mesmo assim" and "espera a identificação de 2 voz(es)" in ata.notice.text()
    assert not ata.btn_extra.isHidden()
    ata.btn_extra.click()                                                                     # gera já, com as vozes sem nome
    assert summary_gate.deferred(store, mid) is None and len(_llm_jobs(store, mid)) == 2
    _run_llm(store)                                                                           # a identidade e a revisão
    _run_llm(store)                                                                           # a ata liberada
    group.reload()
    assert ata.btn_extra.isHidden()
    review.create_person(store, _voices(store, mid)[0], "Ana", "CFO")                        # o nome chegou depois da ata
    group.reload()
    assert ata.btn_extra.text() == "Atualizar a ata com os nomes" and "Os nomes mudaram" in ata.notice.text()
    ata.btn_extra.click()
    refresh = json.loads(_llm_jobs(store, mid)[-1]["params_json"])["tasks"]
    assert refresh == ["minutes", "executive", "pre_summary"]
