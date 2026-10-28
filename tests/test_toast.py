"""F5.3 L3 (U-048): o aviso do app que some sozinho, no lugar do balão (pytest-qt, offscreen). O gerente (até 3 na tela, os
iguais juntos, o prazo, o mouse que segura, o clique com a ação depois do evento), a posição (acima do widget de gravação
quando a pilha o cruzaria, A-14), o balão com 0 s, o texto curto gravando sem a exclusão da captura (A-13), o `notify` e o
notificador de exceções vindos de outra thread (A-10), a trava de reentrada, o limite por minuto e nada saindo na saída, o
erro de job uma vez por (job, finished_at) sem os da abertura (A-11), o polling em 2 s ao lançar, o aviso só da identidade,
o download, a resposta do chat com a janela fora de foco (A-12), o log sem o título, as Configurações e as cores."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
import shiboken6

from listener import log as applog
from listener.model import AppState, JobKind, JobStatus, JobStep
from listener.ui.notices import (CHAT_READY, CRASH_SHORT, HISTORY_READY, IDENTITY_READY, MAX_PER_MINUTE, Notice, NoticeAction,
                                 NoticeKind, Target)

pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento                              # D-050: telas


@pytest.fixture
def controller(qapp: Any, platform: Any, store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from listener.ui import controller as ctl

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)                # nada de PortAudio aqui
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    yield c
    c.emergency_stop()


def _manager(platform: Any, actions: list[Any], recording: bool = False) -> Any:
    from listener.ui.toast import ToastManager

    return ToastManager(platform, on_action=actions.append, recording=lambda: recording, anchor=lambda: None)


def _finished(store: Any, mid: int, kind: str, status: JobStatus, step: JobStep | None = None) -> int:
    job = store.jobs.enqueue(mid, kind)
    store.jobs.claim(pid=1, kinds=(kind,))
    if step is not None:
        store.jobs.set_progress(job, step, 0.3)
    store.jobs.finish(job, status, error="falhou" if status == JobStatus.ERROR else None)
    return job


# ---------------------------------------------------------------- gerente e janela
def test_three_on_screen_without_focus_the_rest_waits_and_equals_merge(qtbot, platform):
    from PySide6.QtCore import Qt

    from listener.ui.toast import ToastManager

    m: ToastManager = _manager(platform, [])
    try:
        notes = [Notice(NoticeKind.INFO, f"aviso {i}") for i in range(5)]
        for n in notes:
            m.show(n, 6)
        assert [t.notice for t in m.toasts] == notes[:3] and [n for n, _count in m.waiting()] == notes[3:]
        assert len(platform.excluded) == 3 and all(t.isVisible() and 5000 < t.remaining_ms() <= 6000 for t in m.toasts)
        first = m.toasts[0]
        flags = first.windowFlags()
        for flag in (Qt.WindowType.Tool, Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                     Qt.WindowType.WindowDoesNotAcceptFocus):
            assert flags & flag == flag                                                    # A-14
        assert first.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) and not first.isActiveWindow()
        assert m.toasts[0].y() > m.toasts[1].y() > m.toasts[2].y()                         # o primeiro embaixo
        m.show(notes[1], 6)
        m.show(notes[4], 6)
        assert m.toasts[1].count == 2 and m.toasts[1].badge.text() == "2x" and m.waiting()[-1] == (notes[4], 2)
        m.show(Notice(NoticeKind.INFO, "aviso 1", action=NoticeAction(Target.QUEUE)), 6)   # outra ação: não é igual
        assert len(m.waiting()) == 3
        first.close_button.click()                                                         # o "✕": sai o de baixo, entra o 1º da fila
        assert [t.notice.text for t in m.toasts] == ["aviso 1", "aviso 2", "aviso 3"] and not first.isVisible()
        qtbot.waitUntil(lambda: not shiboken6.isValid(first), timeout=2000)               # `deleteLater` no giro seguinte
    finally:
        m.shutdown()
    assert m.toasts == [] and m.waiting() == []
    m.show(notes[0], 6)                                                                    # depois da saída, nada
    assert m.toasts == []


def test_a_notice_leaves_on_time_and_the_mouse_holds_it(qtbot, platform):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    m = _manager(platform, [])
    try:
        m.show(Notice(NoticeKind.READY, "pronto"), 1)
        toast = m.toasts[0]
        assert 0 < toast.remaining_ms() <= 1000
        QApplication.sendEvent(toast, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        assert toast.hovered and toast.remaining_ms() == -1                               # o mouse em cima segura
        m.show(Notice(NoticeKind.READY, "pronto"), 1)                                      # um igual chega: conta, sem soltar
        assert toast.count == 2 and toast.remaining_ms() == -1
        qtbot.wait(1200)
        assert m.toasts == [toast] and toast.isVisible()
        QApplication.sendEvent(toast, QEvent(QEvent.Type.Leave))
        assert 0 < toast.remaining_ms() <= 1000
        qtbot.waitUntil(lambda: m.toasts == [], timeout=3000)                              # some no prazo
        qtbot.waitUntil(lambda: not shiboken6.isValid(toast), timeout=2000)               # e a janela sai
    finally:
        m.shutdown()


def test_the_click_closes_and_runs_the_action_after_the_event(qtbot, platform):
    from PySide6.QtCore import Qt

    actions: list[NoticeAction] = []
    m = _manager(platform, actions)
    try:
        m.show(Notice(NoticeKind.ERROR, "Diretoria: erro na LLM", "Erro na LLM", NoticeAction(Target.QUEUE)), 6)
        m.show(Notice(NoticeKind.INFO, "sem ação"), 6)
        with_action, without = m.toasts
        assert with_action.hint.text() == "Clique para abrir a fila" and with_action.hint.isVisibleTo(with_action)
        assert without.hint.isHidden() and with_action.cursor().shape() == Qt.CursorShape.PointingHandCursor
        qtbot.mouseClick(with_action, Qt.MouseButton.LeftButton)
        assert m.toasts == [without] and actions == []                                     # a ação vem depois do evento
        qtbot.waitUntil(lambda: actions == [NoticeAction(Target.QUEUE)], timeout=2000)
        qtbot.mouseClick(without, Qt.MouseButton.LeftButton)                               # sem ação: só fecha
        qtbot.wait(20)
        assert m.toasts == [] and actions == [NoticeAction(Target.QUEUE)]
    finally:
        m.shutdown()


def test_the_notice_height_follows_the_text_at_the_real_width(qtbot):
    """O `adjustSize` de uma janela media a altura pela largura do `sizeHint` do layout, não pelos 360 px fixos: no Windows
    um texto de uma linha ganhava uma segunda, vazia (visto no `win_probe shots` a 150 %), e noutra fonte o texto sairia
    cortado. Para todo tamanho de texto, o texto recebe a altura exata de que precisa na largura de verdade."""
    from listener.ui.toast import TOAST_WIDTH, Toast

    text = "Comitê de investimentos: ata pronta · US$ 0,32; a ata sai depois que as vozes forem identificadas."
    toasts = [Toast(Notice(kind, text[:n]), 6000) for kind in NoticeKind for n in range(4, len(text) + 1)]  # cada quebra
    try:
        for toast in toasts:
            assert (toast.width(), toast.height()) == (TOAST_WIDTH, toast.heightForWidth(TOAST_WIDTH)), toast.notice
        assert toasts[0].height() < toasts[-1].height()
    finally:
        for toast in toasts:
            toast.deleteLater()


def test_the_stack_goes_above_the_recording_widget_when_it_would_cross_it(qtbot, controller):
    from listener.ui.toast import GAP, MARGIN, stack_positions
    from listener.ui.widget import RecordingWidget

    area = (0, 0, 1920, 1040)
    sizes = [(360, 80), (360, 100)]
    corner = [(1920 - MARGIN - 360, 1040 - MARGIN - 80), (1920 - MARGIN - 360, 1040 - MARGIN - 80 - GAP - 100)]
    assert stack_positions(sizes, area) == corner
    assert stack_positions(sizes, area, (0, 0, 320, 96)) == corner                         # longe do widget: no canto
    widget = (1920 - 16 - 320, 1040 - 16 - 96, 320, 96)                                     # o widget no canto (padrão)
    above = stack_positions(sizes, area, widget)
    assert above[0][1] + 80 == widget[1] - GAP and above[1][1] + 100 == above[0][1] - GAP
    assert stack_positions(sizes, area, (1600, 60, 320, 940)) == corner                     # sem espaço acima: fica no canto
    c = controller
    c.widget = RecordingWidget()                                                           # gravando, no canto da tela principal
    c.widget.show()
    c.state = AppState.RECORDING
    try:
        c.notify("gravando")
        toast = c.toasts.toasts[0]
        assert toast.y() + toast.height() <= c.widget.frameGeometry().y()
    finally:
        c.state = AppState.IDLE
        c._teardown_widget()


# ---------------------------------------------------------------- controlador
def test_notify_and_the_crash_notifier_from_another_thread_show_on_the_ui_thread(qtbot, controller, monkeypatch):
    c = controller
    seen: list[tuple[int, Notice]] = []
    real_show = c.toasts.show

    def spy(notice: Notice, seconds: int) -> None:
        seen.append((threading.get_ident(), notice))
        real_show(notice, seconds)

    monkeypatch.setattr(c.toasts, "show", spy)
    worker = threading.Thread(target=c.notify, args=("de outra thread",))
    worker.start()
    worker.join()
    assert seen == []                                                                      # lá só emitiu o sinal
    qtbot.waitUntil(lambda: len(seen) == 1, timeout=3000)
    assert seen[0][0] == threading.get_ident() and seen[0][1].kind == NoticeKind.INFO
    applog.set_crash_notifier(c.notify_crash)                                              # como o app (`ui/app.py`)

    def boom() -> None:                                                                    # o `threading.excepthook` do app
        error = RuntimeError("falhou na thread")
        applog.handle_thread_exception(threading.ExceptHookArgs((RuntimeError, error, None, threading.current_thread())))

    try:
        worker = threading.Thread(target=boom)
        worker.start()
        worker.join()
        qtbot.waitUntil(lambda: len(seen) == 2, timeout=3000)
    finally:
        applog.set_crash_notifier(None)
    crash = seen[1][1]
    assert seen[1][0] == threading.get_ident() and crash.kind == NoticeKind.ERROR and crash.text == "RuntimeError: falhou na thread"
    assert crash.short_text == CRASH_SHORT and c.toasts.toasts[-1].notice == crash


def test_zero_seconds_goes_back_to_the_tray_balloon(qtbot, controller, store):
    c = controller
    mid = store.create_meeting("Diretoria", None, None, None)
    balloons: list[str] = []
    c.tray = SimpleNamespace(showMessage=lambda _title, text: balloons.append(text))
    store.set_setting("ui.toast_seconds", "0")
    c._notify_meeting(mid, "ata pronta")
    assert balloons == ["Diretoria: ata pronta"] and c.toasts.toasts == [] and c._notified_meeting == mid
    store.set_setting("ui.toast_seconds", "6")                                             # vale já (CP8)
    c.notify("de novo na tela")
    assert balloons == ["Diretoria: ata pronta"] and [t.notice.text for t in c.toasts.toasts] == ["de novo na tela"]


def test_recording_without_the_capture_exclusion_shows_only_the_short_text(qtbot, controller, platform, store):
    c = controller
    mid = store.create_meeting("Conversa com a Zuleica", None, None, None)
    platform.capture_exclusion = False
    c.state = AppState.RECORDING
    c._notify_meeting(mid, "ata pronta · US$ 0,32")
    c.notify_crash("ValueError: Zuleica")
    meeting, crash = c.toasts.toasts
    assert meeting.private and meeting.body.text() == "Ata pronta · US$ 0,32" and "Zuleica" not in meeting.accessibleName()
    assert crash.body.text() == CRASH_SHORT
    c.state = AppState.IDLE                                                                # fora da gravação: o texto inteiro
    c._notify_meeting(mid, "pronta para revisão de falantes.")
    assert not c.toasts.toasts[-1].private and c.toasts.toasts[-1].body.text().startswith("Conversa com a Zuleica: ")
    for toast in list(c.toasts.toasts):
        toast.close_button.click()
    platform.capture_exclusion = True
    c.state = AppState.PAUSED
    c._notify_meeting(mid, "processada, sem voz a confirmar.")                            # fora da captura: o texto inteiro
    assert not c.toasts.toasts[-1].private and len(platform.excluded) == 4
    c.state = AppState.IDLE


def test_reentrance_rate_limit_and_quitting_keep_the_notice_in_the_log(qtbot, qapp, platform, store, monkeypatch, caplog):
    from listener.ui.controller import Controller

    c = Controller(qapp, platform, store)
    c.tray = None
    clock = [0.0]
    c._notice_limit.clock = lambda: clock[0]
    real_show = c.toasts.show

    def show_and_reenter(notice: Notice, seconds: int) -> None:
        if notice.text == "primeiro":
            c.notify("de dentro do aviso")                                                 # reentrada: fica no log
        real_show(notice, seconds)

    monkeypatch.setattr(c.toasts, "show", show_and_reenter)
    try:
        with caplog.at_level("INFO", logger="listener"):
            c.notify("primeiro")
            for i in range(MAX_PER_MINUTE):
                c.notify(f"aviso {i}")
            clock[0] = 61.0
            c.notify("um minuto depois")
        texts = [n.text for n in c.notices]
        assert texts == ["primeiro", *(f"aviso {i}" for i in range(MAX_PER_MINUTE - 1)), "um minuto depois"]
        assert "aviso fora da tela (reentrada): de dentro do aviso" in caplog.text
        assert f"aviso fora da tela (limite por minuto): aviso {MAX_PER_MINUTE - 1}" in caplog.text
        assert len(c.toasts.toasts) == 3 and len(c.toasts.toasts) + len(c.toasts.waiting()) == len(c.notices)
    finally:
        c.emergency_stop()
    assert c.toasts.toasts == [] and c.toasts.waiting() == []                             # a saída tira tudo da tela
    c.notify("na saída")                                                                   # banco fechado: nem o lê
    worker = threading.Thread(target=c.notify, args=("na saída, de outra thread",))
    worker.start()
    worker.join()
    qtbot.wait(50)
    assert c.notices[-1].text == "um minuto depois" and c.toasts.toasts == []


def test_a_job_error_warns_once_per_finish_and_not_for_the_old_ones(qtbot, controller, store, monkeypatch):
    c = controller
    opened: list[str] = []
    monkeypatch.setattr(c, "open_queue", lambda: opened.append("fila"))
    old = store.create_meeting("Antiga", None, None, None)
    store.finish_meeting(old, 1000)
    _finished(store, old, JobKind.MEETING, JobStatus.ERROR, JobStep.DIARIZE)
    c.start()                                                                              # semeia: o erro antigo não avisa
    assert not [n for n in c.notices if n.kind == NoticeKind.ERROR]
    mid = store.create_meeting("Planejamento", None, None, None)
    store.finish_meeting(mid, 1000)
    job = _finished(store, mid, JobKind.MEETING, JobStatus.ERROR, JobStep.TRANSCRIBE_R)
    _finished(store, mid, JobKind.INDEX, JobStatus.ERROR)                                  # o índice não avisa
    c._poll_queue()
    c._poll_queue()
    errors = [n for n in c.notices if n.kind == NoticeKind.ERROR]
    first = "Planejamento: erro no processamento (etapa: transcrevendo a reunião); veja o motivo na fila."
    assert [n.text for n in errors] == [first]
    assert errors[0].action == NoticeAction(Target.QUEUE) and errors[0].short_text.startswith("Erro no processamento")
    time.sleep(0.005)                                                                      # outro `finished_at`
    assert store.jobs.retry(job)                                                           # "Tentar de novo" que falha de novo
    store.jobs.claim(pid=2, kinds=(JobKind.MEETING,))
    store.jobs.finish(job, JobStatus.ERROR, error="de novo")
    _finished(store, mid, JobKind.LLM, JobStatus.ERROR, JobStep.MINUTES)
    c._poll_queue()
    texts = [n.text for n in c.notices if n.kind == NoticeKind.ERROR]
    assert len(texts) == 3 and texts.count(first) == 2 and "Planejamento: erro na LLM (etapa: ata); veja o motivo na fila." in texts
    c._on_notice_action(NoticeAction(Target.QUEUE))                                         # o clique abre a fila
    assert opened == ["fila"]


def test_launching_a_worker_with_an_active_job_polls_every_two_seconds(qtbot, controller, store):
    from listener.ui import controller as ctl

    c = controller
    mid = store.create_meeting("Gerar de novo", None, None, None)
    assert c.launch_worker(light=True) is False and c._queue_timer.interval() == ctl.QUEUE_POLL_IDLE_MS   # nada ativo
    store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    c.launch_worker(light=True)
    assert c._queue_timer.interval() == ctl.QUEUE_POLL_ACTIVE_MS


def test_identity_only_warns_that_the_names_are_ready(qtbot, qapp, tmp_path, clock, store, monkeypatch):
    from listener.ui.controller import Controller
    from tests.fake_platform import FakePlatform
    from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

    platform = FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)                                                                  # vozes pendentes: a ata espera
    _run_llm(store)                                                                        # só a identidade e a revisada
    llm = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM and j["status"] == JobStatus.DONE)
    c = Controller(qapp, platform, store)
    c.tray = None
    monkeypatch.setattr(c, "open_review", lambda _mid: None)
    try:
        c._on_job_done(llm)
        notice = c.notices[-1]
        assert notice.text == f"Planejamento 2027: {IDENTITY_READY}" and notice.kind == NoticeKind.READY
        assert notice.action == NoticeAction(Target.MEETING, mid)
    finally:
        c.emergency_stop()


def test_downloads_warn_when_done_or_failed_and_open_the_settings(qtbot, controller, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.live_prepare import ModelPreparer

    c = controller
    prep = ModelPreparer()
    prep.changed.connect(c._on_preparer_changed)
    opened: list[dict[str, Any]] = []
    monkeypatch.setattr(c, "open_settings", lambda **kw: opened.append(kw))
    for state in ({"state": "running", "what": "live", "fraction": 0.5}, {"state": "done", "what": "live"},
                  {"state": "error", "what": "rag", "message": "e5: HTTP 404."},
                  {"state": "error", "what": "live", "message": ctl.PREPARE_INTERRUPTED}):   # a gravação já avisou
        prep.changed.emit(state)
    assert [(n.kind, n.text) for n in c.notices] == [
        (NoticeKind.READY, "Download concluído: os modelos do ao vivo estão prontos."),
        (NoticeKind.ERROR, "O download da busca local falhou: e5: HTTP 404.")]
    assert {n.action for n in c.notices} == {NoticeAction(Target.SETTINGS)}
    c._on_notice_action(NoticeAction(Target.SETTINGS))
    assert opened == [{"focus": True}]


async def _numbered(stream: Any) -> Any:
    i = 0
    async for piece in stream:
        yield i, piece
        i += 1


def test_a_chat_answer_with_the_window_out_of_focus_warns_and_opens_the_chat(qtbot, controller, store, monkeypatch):
    from PySide6.QtCore import Qt

    from listener.llm import fake
    from listener.llm.base import LlmStream
    from tests.test_rag_ui import _indexed

    monkeypatch.setenv("LISTENER_LLM", "fake")
    c = controller
    mid, _other, _who = _indexed(store)
    win = c.open_meeting(mid)
    monkeypatch.setattr(win, "isActiveWindow", lambda: False)                              # o usuário foi para outro app
    win.chat.question.setText("o que ficou decidido?")
    win.chat.ask()
    qtbot.waitUntil(lambda: bool(win.chat.answers), timeout=10000)
    notice = c.notices[-1]
    assert notice.text == f"Orçamento 2027: {CHAT_READY}" and notice.action == NoticeAction(Target.MEETING_CHAT, mid)
    win.show_tab("ata")
    qtbot.mouseClick(c.toasts.toasts[-1], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.tabs.currentWidget() is win.chat, timeout=2000)            # o clique abre a aba Chat
    count = len(c.notices)
    gate = threading.Event()
    real_stream = fake.FakeProvider.stream

    def held_stream(self: fake.FakeProvider, req: Any) -> LlmStream:                         # o 2º pedaço espera o "Parar"
        inner = real_stream(self, req)

        async def chunks() -> Any:
            async for i, piece in _numbered(inner):
                while i and not gate.is_set():
                    await asyncio.sleep(0.01)
                yield piece

        return LlmStream(chunks(), lambda _text: inner.result)

    monkeypatch.setattr(fake.FakeProvider, "stream", held_stream)
    win.chat.question.setText("e os riscos?")
    win.chat.ask()
    qtbot.waitUntil(lambda: bool(win.chat._partial), timeout=10000)                        # o 1º pedaço chegou
    win.chat.stop()                                         # "Parar" (fechar a janela também para): o usuário cancelou
    gate.set()
    qtbot.waitUntil(lambda: win.chat.task is None, timeout=10000)
    assert win.chat.answers[-1].cancelled is False and len(c.notices) == count            # grava a parte, não avisa
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)                               # em foco: não avisa
    answered = len(win.chat.answers)
    win.chat.question.setText("e o prazo?")
    win.chat.ask()
    qtbot.waitUntil(lambda: len(win.chat.answers) == answered + 1, timeout=10000)
    assert len(c.notices) == count
    hist = c.open_history()                                                                # o chat sobre todas as reuniões
    monkeypatch.setattr(hist, "isActiveWindow", lambda: False)
    hist.page.question.setText("o que foi decidido sobre a auditoria?")
    hist.page.ask()
    qtbot.waitUntil(lambda: bool(hist.page.answers), timeout=10000)
    assert c.notices[-1].text == HISTORY_READY and c.notices[-1].action == NoticeAction(Target.HISTORY)


def test_the_log_carries_the_meeting_id_and_never_the_title(qtbot, controller, store, caplog):
    c = controller
    mid = store.create_meeting("Conversa com a Zuleica", None, None, None)
    with caplog.at_level("DEBUG", logger="listener"):
        c._notify_meeting(mid, "ata pronta")
        c._on_job_error({"id": 99, "meeting_id": mid, "kind": "meeting", "step": "diarize", "finished_at": "x"})
        c._chat_answered(mid, True)
        store.set_setting("ui.toast_seconds", "0")
        c._notify_meeting(mid, "pronta para revisão de falantes.")
    assert "Zuleica" not in caplog.text and f"aviso na tela (pronto) sobre a reunião {mid}: ata pronta" in caplog.text
    assert f"aviso na tela (erro) sobre a reunião {mid}: erro no processamento (etapa: separando vozes)" in caplog.text
    assert f"aviso na bandeja (pronto) sobre a reunião {mid}: pronta para revisão de falantes." in caplog.text


# ---------------------------------------------------------------- configurações, cores e plataforma
def test_settings_language_and_notices_group_has_the_toast_seconds(qtbot, store, platform):
    from PySide6.QtWidgets import QGroupBox

    from listener.ui.settings_min import TOAST_SECONDS, SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    group = next(g for g in dlg.findChildren(QGroupBox) if g.title() == "Idioma e avisos")
    edit = dlg._edits[TOAST_SECONDS]
    assert group.isAncestorOf(edit) and edit.text() == "6" and "balão" in edit.toolTip()
    for bad in ("61", "-1", "abc"):
        edit.setText(bad)
        assert (dlg.validation_error() or "").startswith("Aviso some em (s)")
    edit.setText("0")
    assert dlg.validation_error() is None
    dlg._save()
    assert store.get_setting(TOAST_SECONDS) == "0"


def test_notice_colors_are_readable_on_the_dark_background():
    from listener.ui.common import NOTICE_BACKGROUND, NOTICE_COLORS, NOTICE_MUTED, NOTICE_TEXT, contrast

    assert set(NOTICE_COLORS) == {str(k) for k in NoticeKind}
    assert contrast(NOTICE_TEXT, NOTICE_BACKGROUND) >= 4.5 and contrast(NOTICE_MUTED, NOTICE_BACKGROUND) >= 4.5   # texto
    assert all(contrast(color, NOTICE_BACKGROUND) >= 3.0 for color in NOTICE_COLORS.values())                    # a faixa


@pytest.mark.skipif(sys.platform != "win32", reason="SetWindowDisplayAffinity só existe no Windows")
def test_win32_capture_exclusion_refuses_an_invalid_window_without_raising():
    from listener.platform.win32.platform import Win32Platform

    assert Win32Platform().exclude_from_capture(0) is False
