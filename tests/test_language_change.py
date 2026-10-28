"""F5.3 L1, trocar o idioma de uma reunião gravada (U-046, A-05, A-06, perguntas 3 e 9): o helper único de reprocessar
(gravando recusa; pendente só troca o idioma do job e solta as janelas já transcritas; rodando cancela e reenfileira; o
"Reprocessar" da fila não duplica um ativo); o runner grava `meetings.language` com `revisions` só ao concluir (cancelado
ou com erro, fica o antigo); depois da troca, todas as tarefas da LLM entram como pedido manual, sem a espera da U-042 e sem
o corte do alerta de custo, mesmo com as automáticas desligadas; a reunião aberta mostra o idioma, pergunta com tempo e
custo e mostra "pt-BR → en (reprocessando)". R-08: voltar ao idioma que a reunião já tem desfaz a troca ainda não aplicada
sem custo a mais (o job criado só para ela sai, na fila ou rodando; um processamento que já existia volta a ser o que era),
e o botão volta a oferecer o outro idioma."""
from __future__ import annotations

import json

import pytest

from listener.model import EventType, JobKind, JobStatus, MeetingStatus, SummaryKind
from listener.pipeline import reprocess, summary_gate
from listener.pipeline.llm_steps import TASKS_AFTER_MEETING
from listener.pipeline.runner import JobRunner
from listener.store import Store
from tests.fake_pipeline import FakeDeps
from tests.test_language import SCRIPT_EN, _record, _run_heavy
from tests.test_llm_steps import SCRIPT, _provider_ready, _run_llm
from tests.test_runner import CFG


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _processed(platform, store: Store, lang: str = "pt") -> int:
    store.upsert_me("Eu")
    mid = _record(platform, store, lang)
    store.jobs.enqueue(mid)
    _job, status = _run_heavy(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE
    return mid


def _language_revisions(store: Store, mid: int) -> list[tuple[str, str, str]]:
    rows = store._q("SELECT before, after, author FROM revisions WHERE entity = 'meeting' AND entity_id = ? AND field = 'language'", (mid,))
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------- o helper único
def test_recording_refuses_and_the_plain_reprocess_never_duplicates_an_active_one(store):
    mid = store.create_meeting("t", None, None, None, language="pt")                 # gravando
    res = reprocess.request_reprocess(store, mid, "en")
    assert res.job_id is None and "sendo gravada" in res.message and store.jobs.for_meeting(mid) == []
    store.finish_meeting(mid, 60_000)
    first = reprocess.request_reprocess(store, mid)
    row = store.jobs.get(first.job_id)
    assert row["kind"] == JobKind.REPROCESS and row["params_json"] is None and f"job {first.job_id}" in first.message
    again = reprocess.request_reprocess(store, mid)
    assert again.job_id is None and "na fila" in again.message and len(store.jobs.for_meeting(mid)) == 1
    store.jobs.claim(pid=1, kinds=(JobKind.REPROCESS,))
    assert "em andamento" in reprocess.request_reprocess(store, mid).message
    assert reprocess.request_reprocess(store, 9999).job_id is None


def test_a_pending_processing_only_changes_its_language_and_drops_the_old_transcription(store):
    mid = store.create_meeting("t", None, None, None, language="pt")
    store.finish_meeting(mid, 60_000)
    job = store.jobs.enqueue(mid)                                                    # o processamento ainda na fila
    for step in ("diarize", "transcribe_r", "transcribe_l"):                         # uma tentativa anterior já andou (em pt)
        store.jobs.put_artifact(job, step, 0, b"x")
    same = reprocess.request_reprocess(store, mid, "pt-BR")                          # o idioma que ela já tem: nada muda (R-08)
    assert same.job_id is None and "nada a trocar" in same.message and store.jobs.get(job)["params_json"] is None
    assert store.jobs.artifact_indexes(job, "transcribe_r") == {0}
    res = reprocess.request_reprocess(store, mid, "en-US")
    assert res.job_id == job and len(store.jobs.for_meeting(mid)) == 1 and "vai transcrever em en" in res.message
    assert json.loads(store.jobs.get(job)["params_json"]) == {"reason": "language", "language": "en"}
    assert store.jobs.artifact_indexes(job, "diarize") == {0}                         # as vozes ficam (o pyannote não tem idioma)
    assert not store.jobs.artifact_indexes(job, "transcribe_r") and not store.jobs.artifact_indexes(job, "transcribe_l")
    assert reprocess.pending_language(store, mid) == (job, "en") and store.get_meeting(mid)["language"] == "pt"   # só no fim
    store.jobs.put_artifact(job, "transcribe_r", 0, b"en")                            # já em inglês: pedir inglês de novo não apaga
    assert reprocess.request_reprocess(store, mid, "en").job_id == job and store.jobs.artifact_indexes(job, "transcribe_r") == {0}


def test_running_is_cancelled_and_requeued_and_the_language_lands_only_when_done(platform, store, caplog):
    mid = _processed(platform, store, "pt")
    first = reprocess.request_reprocess(store, mid, "en")
    running = store.jobs.claim(pid=1, kinds=(JobKind.REPROCESS,))
    assert running is not None and int(running["id"]) == first.job_id
    second = reprocess.request_reprocess(store, mid, "en")                           # rodando: cancela e reenfileira
    assert second.cancelled == first.job_id and second.job_id not in (None, first.job_id) and "foi cancelado" in second.message
    status = JobRunner(store, FakeDeps(SCRIPT_EN), running, config=CFG, heartbeat_thread=False).run()
    assert status == JobStatus.CANCELLED and store.get_meeting(mid)["language"] == "pt" and _language_revisions(store, mid) == []
    failing = FakeDeps(SCRIPT_EN, fail_once_at={("L", 0)})
    job, status = _run_heavy(store, failing)
    assert job == second.job_id and status == JobStatus.ERROR and store.get_meeting(mid)["language"] == "pt"   # com erro, fica
    assert _language_revisions(store, mid) == [] and reprocess.pending_language(store, mid) is None
    assert store.jobs.retry(job)
    deps = FakeDeps(SCRIPT_EN)
    with caplog.at_level("INFO", logger="listener.pipeline.runner"):
        _job, status = _run_heavy(store, deps)
    assert status == JobStatus.DONE and {c[2] for c in deps.scripted.calls} == {"en"}
    assert store.get_meeting(mid)["language"] == "en" and _language_revisions(store, mid) == [("pt", "en", "usuario")]
    assert f"job {job}: transcrição em en (troca pedida na reunião; grava ao concluir)" in caplog.text
    assert f"job {job}: idioma da reunião {mid}: pt -> en" in caplog.text
    assert store.segments.current_job_id(mid) == job


def test_the_queue_reprocess_goes_through_the_helper(qtbot, store):
    from listener.ui.queue import QueueDialog

    mid = store.create_meeting("t", None, None, None)                                # gravando: o helper recusa
    store.jobs.enqueue(mid)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg.table.selectRow(0)
    assert dlg.reprocess_selected() is None and "sendo gravada" in dlg.message.text() and len(store.jobs.for_meeting(mid)) == 1


# ---------------------------------------------------------------- as tarefas da LLM depois da troca (A-06)
def test_after_a_language_change_every_llm_task_goes_as_a_manual_request(qtbot, qapp, platform, store):
    from listener.ui.controller import Controller

    mid = _processed(platform, store, "pt")                                          # sem chave: nenhum job llm ainda
    _provider_ready(store)
    store.set_setting("llm.auto_tasks", False)
    store.set_setting("llm.cost_alert_usd", 0.0000001)                               # o corte das opcionais não vale aqui
    res = reprocess.request_reprocess(store, mid, "en")
    job, status = _run_heavy(store, FakeDeps(SCRIPT_EN))
    assert job == res.job_id and status == JobStatus.DONE and summary_gate.pending_voices(store, mid) == 2   # voz pendente...
    llm_jobs = [j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM]
    params = json.loads(llm_jobs[0]["params_json"])
    assert len(llm_jobs) == 1 and params["tasks"] == [str(t) for t in TASKS_AFTER_MEETING] and params["auto"] is False
    assert summary_gate.deferred(store, mid) is None                                 # ...e a ata não espera (U-042)
    assert not [e for e in store.list_events(mid) if e["type"] == EventType.LLM_DEFERRED]
    c = Controller(qapp, platform, store)
    c.tray = None
    store.set_setting("llm.auto_tasks", True)
    try:
        assert c._summary_waits(mid) and not c._summary_waits(mid, store.jobs.get(job))   # o mesmo critério do worker
    finally:
        c.emergency_stop()


def test_the_manual_tasks_after_the_change_skip_nothing_and_come_out_in_english(platform, store):
    mid = _processed(platform, store, "pt")
    _provider_ready(store)
    store.set_setting("llm.auto_tasks", False)
    store.set_setting("llm.cost_alert_usd", 0.0000001)
    reprocess.request_reprocess(store, mid, "en")
    _run_heavy(store, FakeDeps(SCRIPT_EN))
    runner, status, fake = _run_llm(store)
    timings = json.loads(store.jobs.get(runner.job_id)["timings_json"])["llm"]
    assert status == JobStatus.DONE and timings["skipped"] == {}                     # executivo e revisada saem (pedido manual)
    assert {r.task for r in fake.requests} >= {"minutes", "executive", "revise"}
    assert store.summaries.current(mid, SummaryKind.MINUTES)["language"] == "en"


# ---------------------------------------------------------------- a reunião aberta (U-046)
def test_the_open_meeting_shows_the_language_and_asks_before_reprocessing(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow
    from listener.ui.meeting_language import RECORDING_TIP, change_question

    mid = _processed(platform, store, "pt")
    assert "sem chave de LLM" in change_question(store, mid, "en")
    _provider_ready(store)
    launched: list[bool] = []
    win = MeetingWindow(store, mid, on_launch=launched.append)
    qtbot.addWidget(win)
    bar = win.language_bar
    assert bar.label.text() == "Idioma: pt-BR" and bar.button.text() == "Trocar para Inglês (en)…" and bar.button.isEnabled()
    asked: list[tuple[str, str]] = []

    def no(_parent, text, yes):
        asked.append((text, yes))
        return False

    bar.confirm = no
    bar.button.click()
    text, yes = asked[0]
    assert yes == "Reprocessar em Inglês (en)" and not reprocess.active_jobs(store, mid) and launched == []
    assert "Tempo estimado: " in text and "refaz a separação de vozes" in text and "custo estimado: US$" in text
    assert "versão anterior" in text
    bar.confirm = lambda *_a: True
    bar.button.click()
    job = reprocess.pending_language(store, mid)
    assert job is not None and job[1] == "en" and launched == [False] and f"job {job[0]}" in win.message.text()
    assert bar.label.text() == "Idioma: pt-BR → en (reprocessando)" and bar.button.text() == "Voltar para Português (pt-BR)…"
    _done, status = _run_heavy(store, FakeDeps(SCRIPT_EN))
    win.reload()
    assert status == JobStatus.DONE and bar.label.text() == "Idioma: en" and bar.button.text() == "Trocar para Português (pt-BR)…"
    store.meetings.set_status(mid, MeetingStatus.RECORDING)                          # gravando: desabilitado
    win.reload_header()
    assert not bar.button.isEnabled() and bar.button.toolTip() == RECORDING_TIP


# ---------------------------------------------------------------- desfazer a troca ainda não aplicada (R-08)
def _llm_jobs(store: Store, mid: int) -> list[int]:
    return [int(j["id"]) for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM]


def test_undoing_a_change_still_in_the_queue_costs_nothing(platform, store):
    mid = _processed(platform, store, "pt")
    _provider_ready(store)
    store.set_setting("llm.auto_tasks", False)
    asked = reprocess.request_reprocess(store, mid, "en")                            # a troca na fila, num job só dela
    assert json.loads(store.jobs.get(asked.job_id)["params_json"]) == {"reason": "language", "language": "en", "only_for_language": True}
    undo = reprocess.request_reprocess(store, mid, "pt-BR")                          # desistiu antes de rodar
    assert undo.job_id is None and "desfeita" in undo.message and store.jobs.get(asked.job_id)["status"] == JobStatus.CANCELLED
    assert reprocess.active_jobs(store, mid) == [] and reprocess.pending_language(store, mid) is None
    assert store.get_meeting(mid)["language"] == "pt" and _llm_jobs(store, mid) == []
    # um processamento que já estava na fila só perde o idioma pedido e roda como era: nada da LLM paga da troca (A-06)
    plain = reprocess.request_reprocess(store, mid).job_id
    assert plain is not None and reprocess.request_reprocess(store, mid, "en").job_id == plain
    store.jobs.put_artifact(plain, "transcribe_r", 0, b"en")                          # uma tentativa em inglês já andou
    back = reprocess.request_reprocess(store, mid, "pt")
    assert back.job_id == plain and "segue como era" in back.message and reprocess.target_language(store.jobs.get(plain)) is None
    assert json.loads(store.jobs.get(plain)["params_json"]) == {} and not store.jobs.artifact_indexes(plain, "transcribe_r")
    job, status = _run_heavy(store, FakeDeps(SCRIPT))
    assert job == plain and status == JobStatus.DONE and _llm_jobs(store, mid) == [] and store.get_meeting(mid)["language"] == "pt"
    assert _language_revisions(store, mid) == []


def test_undoing_a_change_already_running_stops_it_and_keeps_what_existed(platform, store):
    mid = _processed(platform, store, "pt")
    first = reprocess.request_reprocess(store, mid, "en")
    running = store.jobs.claim(pid=1, kinds=(JobKind.REPROCESS,))
    undo = reprocess.request_reprocess(store, mid, "pt")                             # rodando, só dela: para e nada entra
    assert running is not None and undo.job_id is None and store.jobs.cancel_requested(first.job_id)
    assert reprocess.active_jobs(store, mid) == [] and reprocess.pending_language(store, mid) is None
    status = JobRunner(store, FakeDeps(SCRIPT_EN), running, config=CFG, heartbeat_thread=False).run()
    assert status == JobStatus.CANCELLED and store.get_meeting(mid)["language"] == "pt" and _language_revisions(store, mid) == []
    # a troca que tomou o lugar de um processamento em curso: desfeita já rodando, ele volta à fila como era
    plain = reprocess.request_reprocess(store, mid).job_id
    claimed = store.jobs.claim(pid=1, kinds=(JobKind.REPROCESS,))
    swap = reprocess.request_reprocess(store, mid, "en")
    assert claimed is not None and int(claimed["id"]) == plain and swap.cancelled == plain and swap.job_id is not None
    assert "only_for_language" not in json.loads(store.jobs.get(swap.job_id)["params_json"])
    JobRunner(store, FakeDeps(SCRIPT), claimed, config=CFG, heartbeat_thread=False).run()   # o comum atende o cancelamento
    assert store.jobs.claim(pid=1, kinds=(JobKind.REPROCESS,)) is not None              # a troca começa a rodar
    back = reprocess.request_reprocess(store, mid, "pt")
    assert store.jobs.cancel_requested(swap.job_id) and back.job_id not in (None, swap.job_id)
    redo = store.jobs.get(back.job_id)
    assert redo["kind"] == JobKind.REPROCESS and redo["status"] == JobStatus.PENDING and reprocess.target_language(redo) is None


def test_the_open_meeting_offers_to_undo_and_then_the_other_language_again(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow

    mid = _processed(platform, store, "pt")
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    bar = win.language_bar
    asked: list[tuple[str, str]] = []

    def yes(_parent, text, button):
        asked.append((text, button))
        return True

    bar.confirm = yes
    bar.button.click()                                                                # pt-BR -> en, na fila
    assert bar.label.text() == "Idioma: pt-BR → en (reprocessando)" and bar.button.text() == "Voltar para Português (pt-BR)…"
    bar.button.click()                                                                # desistiu antes de rodar
    text, button = asked[-1]
    assert button == "Desfazer a troca" and text.startswith("Desfazer a troca para Inglês (en)?") and "Tempo estimado" not in text
    assert "a LLM não refaz nada" in text and "desfeita" in win.message.text()
    assert bar.label.text() == "Idioma: pt-BR" and bar.button.text() == "Trocar para Inglês (en)…" and not reprocess.active_jobs(store, mid)


def test_a_cancelled_or_failed_language_change_leaves_the_reprocessing_header(qtbot, qapp, platform, store, monkeypatch):
    """R-07: a troca de idioma cancelada na fila ou que termina com erro não deixa a reunião aberta em "(reprocessando)" com
    o botão oferecendo o idioma errado: o polling da fila relê o cabeçalho dela quando um job da reunião acaba assim."""
    from listener.ui.controller import Controller

    mid = _processed(platform, store, "pt")
    c = Controller(qapp, platform, store)
    c.tray = None
    monkeypatch.setattr(c, "notify", lambda _text: None)
    try:
        bar = c.open_meeting(mid).language_bar
        for end in ("cancelado", "erro"):
            job = reprocess.request_reprocess(store, mid, "en").job_id
            assert job is not None
            bar.reload()
            assert bar.label.text() == "Idioma: pt-BR → en (reprocessando)"
            if end == "cancelado":
                assert store.jobs.request_cancel(job) == JobStatus.CANCELLED
            else:
                store.jobs.finish(job, JobStatus.ERROR, "falhou")
            c._poll_queue()
            assert (bar.label.text(), bar.button.text()) == ("Idioma: pt-BR", "Trocar para Inglês (en)…"), end
    finally:
        c.emergency_stop()
