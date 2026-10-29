"""F5.6 L1b, revisão do diff: R-01 a R-09 e os desvios 6 e 9, cada um com o teste que falha sem a correção.

- R-01: a falha sobre uma transcrição que o reprocessamento trocou não é mais "falhou" nem pede sem popup; com processamento na
  fila, o "falhou" também pergunta; a dica do "Gerar de novo" respeita o `STALE_TRANSCRIPT`.
- R-02: o job de LLM que termina em erro tira a barra da reunião aberta de "gerando" no polling do controlador.
- R-03: o pedido preso a um processamento conta a ata como desatualizada no popup (a troca de idioma só com o resumo).
- R-04: a frase das vozes com a identidade no núcleo pendente e com o reprocessamento na fila.
- R-05: o resultado do pedido ao parar, e a falha dele (com o rastro no log), vão para o aviso do app.
- R-07: sem falas, nem popup; a barra apagada com o porquê. R-08: a nota da barra quebra a linha, sem alargar a barra.
- R-09: sem chave, o "Resumo parcial" do warm-up apagado. Desvio 6: o Cancelar do popup não grava a escolha das caixas.
  Desvio 9: o resumo "agora" com a ata adiada à espera dos nomes diz que ela sai de novo.
O R-06 (cada evento lido uma vez por recarga) está em `test_llm_request_bar.py`."""
from __future__ import annotations

import json
from typing import Any

import pytest

from listener.llm.fake import FakeProvider
from listener.model import EventType, JobKind, JobStatus
from listener.pipeline import llm_requests, reprocess
from listener.pipeline.llm_request_state import STALE_JOB
from listener.pipeline.llm_requests import Mode, Species, State
from listener.pipeline.llm_steps import STALE_TRANSCRIPT
from listener.pipeline.runner import JobRunner
from listener.ui import llm_ask
from listener.ui.llm_request_bar import RETRY, RETRY_ASK_TIP, RETRY_TIP
from tests.fake_pipeline import FakeDeps
from tests.test_ata_a_pedido import _drain
from tests.test_llm_steps import SCRIPT, _pipeline, _provider_ready, _record, _run_llm
from tests.test_runner import CFG


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


class Chooser:
    def __init__(self, mode: Mode | None) -> None:
        self.mode = mode
        self.seen: list[Any] = []

    def __call__(self, _parent: Any, question: Any) -> Mode | None:
        self.seen.append(question)
        return self.mode

    def bare(self, question: Any) -> Mode | None:
        return self(None, question)


def _processed(platform: Any, store: Any, fake: FakeProvider | None = None) -> int:
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store, fake)                                                            # o núcleo
    return mid


def _heavy(store: Any, script: dict = SCRIPT) -> int:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None
    assert JobRunner(store, FakeDeps(script), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


def _fail(store: Any, error: str) -> int:
    job = store.jobs.claim(pid=2, kinds=("llm",))
    assert job is not None
    store.jobs.finish(int(job["id"]), JobStatus.ERROR, error=error)
    return int(job["id"])


def _state(store: Any, mid: int) -> Any:
    return llm_requests.request_state(store, mid, Species.MINUTES)


# ---------------------------------------------------------------- R-01
def test_a_failure_on_a_replaced_transcript_is_not_failed_and_the_retry_asks(platform, store):
    fake = FakeProvider()
    mid = _processed(platform, store, fake)
    llm_ask.submit(store, mid, ["minutes"], Mode.NOW, "teste")
    _drain(store, fake)                                                              # uma ata existe
    popup = Chooser(Mode.NOW)
    assert llm_ask.ask(store, mid, ["minutes"], "teste", popup.bare) is not None and len(popup.seen) == 1
    _fail(store, "Ata falhou: tempo esgotado")
    assert _state(store, mid).state == State.FAILED
    assert llm_ask.ask(store, mid, ["minutes"], "teste", popup.bare) is not None and len(popup.seen) == 1   # sem popup
    _fail(store, "Ata falhou: tempo esgotado")
    job = reprocess.request_reprocess(store, mid, "en").job_id                       # a troca de idioma, sem caixa
    cancel = Chooser(None)
    assert llm_ask.ask(store, mid, ["minutes"], "teste", cancel.bare) is None and len(cancel.seen) == 1   # na fila: pergunta
    assert _heavy(store) == job
    after = _state(store, mid)
    assert after.state == State.STALE and STALE_JOB in after.reasons                 # não é mais "falhou"
    assert llm_ask.ask(store, mid, ["minutes"], "teste", cancel.bare) is None and len(cancel.seen) == 2


def test_the_retry_tip_says_it_asks_when_the_transcript_changed(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow

    mid = _processed(platform, store)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    win.llm.choose = Chooser(Mode.NOW)
    button = win.llm.bar.buttons[Species.MINUTES]
    for error, tip in (("Ata falhou: tempo esgotado", RETRY_TIP), (f"Ata falhou: {STALE_TRANSCRIPT}", RETRY_ASK_TIP)):
        button.click()
        _fail(store, error)
        win.reload()
        assert (button.text(), button.toolTip()) == (RETRY, tip), error


# ---------------------------------------------------------------- R-02
def test_a_failed_or_cancelled_llm_job_leaves_generating_on_the_next_poll(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui.controller import Controller

    mid = _processed(platform, store)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(c, "launch_worker", lambda light=False: False)
    monkeypatch.setattr(c, "notify", lambda *_a, **_k: None)
    try:
        c._poll_queue()
        win = c.open_meeting(mid)
        assert win is not None and win.llm.bar is not None
        win.llm.choose = Chooser(Mode.NOW)
        bar = win.llm.bar
        bar.buttons[Species.MINUTES].click()
        assert bar.badges[Species.MINUTES].text() == "gerando" and not bar.buttons[Species.MINUTES].isEnabled()
        _fail(store, "Ata falhou: tempo esgotado")
        c._poll_queue()                                                              # o polling, não uma recarga à mão
        assert (bar.badges[Species.MINUTES].text(), bar.buttons[Species.MINUTES].text()) == ("falhou", RETRY)
        assert bar.buttons[Species.MINUTES].isEnabled()
        bar.buttons[Species.MINUTES].click()                                         # de novo; e cancelado na fila
        pending = next(int(j["id"]) for j in store.jobs.for_meeting(mid) if j["status"] == JobStatus.PENDING)
        assert store.jobs.request_cancel(pending) == JobStatus.CANCELLED
        c._poll_queue()
        assert bar.badges[Species.MINUTES].text() == "não gerado" and bar.buttons[Species.MINUTES].isEnabled()
    finally:
        c.emergency_stop()


# ---------------------------------------------------------------- R-03
def test_a_request_bound_to_a_processing_counts_the_minutes_as_outdated(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow

    fake = FakeProvider()
    mid = _processed(platform, store, fake)
    llm_ask.submit(store, mid, ["minutes", "executive", "pre_summary"], Mode.NOW, "teste")
    _drain(store, fake)
    assert llm_ask.WITH_MINUTES not in llm_ask.question(store, mid, ["executive"]).lines     # a ata em dia (P-07)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    lang = win.language_bar
    lang.confirm = lambda *_a: frozenset({Species.SUMMARY})                          # só "Gerar resumo"
    popup = lang.choose = Chooser(Mode.NOW)
    lang.button.click()
    shown = popup.seen[0]
    assert llm_ask.WITH_MINUTES in shown.lines                                       # o popup conta a ata que vai junto
    _heavy(store)
    pending = [json.loads(j["params_json"])["tasks"] for j in store.jobs.for_meeting(mid)
               if j["kind"] == JobKind.LLM and j["status"] == JobStatus.PENDING]
    assert ["minutes", "executive", "pre_summary"] in pending                       # e é o que sai
    assert reprocess.request_reprocess(store, mid).job_id is not None                # o clique na barra durante um reprocessamento
    during = llm_ask.facts(store, mid, ["executive"])
    assert "minutes" in during.cost.tasks and during.cost.core == ()


# ---------------------------------------------------------------- R-04
def test_the_voices_line_with_the_core_pending_and_with_a_reprocessing_queued(platform, store):
    mid = _record(platform, store)
    _pipeline(store, mid)                                                            # sem chave: o núcleo pendente
    _provider_ready(store)
    pending = llm_ask.question(store, mid, ["minutes"])
    assert pending.lines[0] == llm_ask.NOT_RUN and not pending.named                 # não "2 vozes ainda sem nome"
    llm_ask.submit(store, mid, ["minutes"], Mode.NOW, "teste")
    _drain(store, FakeProvider())
    assert llm_ask.question(store, mid, ["minutes"]).lines[0] == "2 vozes ainda sem nome."
    assert reprocess.request_reprocess(store, mid).job_id is not None
    assert llm_ask.question(store, mid, ["minutes"]).lines[0] == llm_ask.RERUN


# ---------------------------------------------------------------- R-05
def test_the_stop_request_result_and_its_failure_reach_the_notice(qtbot, platform, store, monkeypatch):
    from listener.ui import summary as summary_module
    from listener.ui.summary import SummaryDialog

    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    notices: list[str] = []
    ok = SummaryDialog(store, mid, {"duration_s": 3.0}, after_job_id=job, on_message=notices.append)
    qtbot.addWidget(ok)
    ok.choose = Chooser(Mode.NOW)
    ok.boxes.boxes[Species.MINUTES].setChecked(True)
    ok.accept()
    assert notices == ["Ata: sai quando o processamento da reunião terminar."]
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(summary_module.log, "warning", lambda *_a, **k: logged.append(k))

    def broken(_parent: Any, _question: Any) -> Mode | None:
        raise RuntimeError("database is locked")

    bad = SummaryDialog(store, mid, {"duration_s": 3.0}, after_job_id=job, on_message=notices.append)
    qtbot.addWidget(bad)
    bad.choose = broken
    bad.boxes.boxes[Species.MINUTES].setChecked(True)
    bad.accept()
    assert notices[-1] == llm_ask.ASK_FAILED and logged == [{"exc_info": True}]


# ---------------------------------------------------------------- R-07 e R-08
def test_a_meeting_without_speech_refuses_before_the_popup(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow

    mid = _record(platform, store)
    _provider_ready(store)
    store.jobs.enqueue(mid)
    _heavy(store, {"R": [], "L": []})
    popup = Chooser(Mode.NOW)
    sent = llm_ask.ask(store, mid, ["minutes"], "teste", popup.bare)
    assert sent is not None and sent.message == llm_ask.NO_SPEECH_TIP and popup.seen == []
    assert not [e for e in store.list_events(mid) if e["type"] == EventType.LLM_REQUESTED]
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    bar = win.llm.bar
    assert bar is not None and not any(b.isEnabled() for b in bar.buttons.values())
    assert bar.buttons[Species.MINUTES].toolTip() == llm_ask.NO_SPEECH_TIP and bar.note.text() == llm_ask.NO_SPEECH_TIP


def test_the_bar_note_wraps_below_without_widening_the_bar(qtbot):
    from listener.pipeline.llm_requests import RequestState
    from listener.ui.llm_request_bar import RequestBar

    states = {sp: RequestState(State.NONE) for sp in Species}
    on, off = RequestBar(lambda _s: None), RequestBar(lambda _s: None)
    for bar, why in ((on, ""), (off, llm_ask.NO_KEY_TIP)):
        qtbot.addWidget(bar)
        bar.show_states(states, why)
    assert off.note.wordWrap() and off.note.text() == llm_ask.NO_KEY_TIP and not off.note.isHidden()
    assert off.minimumSizeHint().width() == on.minimumSizeHint().width()             # a nota não alarga a reunião (640 px)


# ---------------------------------------------------------------- R-09
def test_without_a_key_the_partial_box_is_off_until_the_key_arrives(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    store.set_setting("live.enabled", True)
    store.set_setting("live.partial", True)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    box = dlg.partial_check
    assert not box.isEnabled() and box.toolTip() == llm_ask.NO_KEY_TIP and dlg.result_config()[1]["live_partial"] is False
    _provider_ready(store)
    dlg._update_keys_note()                                                          # a volta das Configurações
    assert box.isEnabled() and dlg.result_config()[1]["live_partial"] is True
    dlg.reject()


# ---------------------------------------------------------------- desvios 6 e 9
def test_cancel_in_the_popup_keeps_the_last_choice_at_stop_and_on_language_change(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow
    from listener.ui.summary import SummaryDialog

    mid = _processed(platform, store)
    llm_ask.save_choice(store, {Species.SUMMARY})
    stop = SummaryDialog(store, mid, {"duration_s": 3.0})
    qtbot.addWidget(stop)
    stop.choose = Chooser(None)
    stop.boxes.boxes[Species.MINUTES].setChecked(True)
    stop.accept()                                                                    # Cancelar no popup: a escolha fica
    assert llm_ask.last_choice(store) == {Species.SUMMARY}
    empty = SummaryDialog(store, mid, {"duration_s": 3.0})
    qtbot.addWidget(empty)
    empty.boxes.boxes[Species.SUMMARY].setChecked(False)
    empty.accept()                                                                   # OK sem caixa: grava "nenhuma"
    assert llm_ask.last_choice(store) == frozenset()
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    lang = win.language_bar
    lang.confirm = lambda *_a: frozenset({Species.MINUTES})
    lang.choose = Chooser(None)
    lang.button.click()
    assert llm_ask.last_choice(store) == frozenset()                                 # cancelado no popup
    lang.button.click()                                                              # desfaz (sem caixas)
    lang.choose = Chooser(Mode.NAMES)
    lang.button.click()
    assert llm_ask.last_choice(store) == {Species.MINUTES}                           # pedido feito: grava


def test_a_summary_now_says_the_deferred_minutes_comes_again(platform, store):
    mid = _processed(platform, store)
    assert llm_ask.AGAIN_WITH_NAMES not in llm_ask.question(store, mid, ["executive", "pre_summary"]).lines
    assert llm_ask.submit(store, mid, ["minutes"], Mode.NAMES, "teste").outcome == llm_requests.Outcome.WAITING
    shown = llm_ask.question(store, mid, ["executive", "pre_summary"]).lines
    assert llm_ask.WITH_MINUTES in shown and llm_ask.AGAIN_WITH_NAMES in shown
