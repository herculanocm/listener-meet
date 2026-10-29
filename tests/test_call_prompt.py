"""F5.7 (L3, U-094): o cartão do aviso de reunião sem gravação, o "Antes de gravar" pelo aviso, o controlador (`busy`,
`open_warmup` com a fonte e o atalho), a bandeja com uma chamada pendente e as regras do código (pytest-qt, offscreen, com a
plataforma falsa). O cartão: as flags sem foco, o tamanho, os textos, o mouse que segura os 20 s, sumir, o ✕ e o clique no
corpo, o canto da tela e o alerta do leitor de tela. O "Antes de gravar": a linha do app, a faixa, o foco no Gravar, a saída
da captura sempre e o Esc que dispensa. Por AST: nenhum módulo `call_*` chama `start_recording` (D-010, CP6) e nenhum log
dos módulos novos leva título, caminho ou nome de aparelho (D-053)."""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest

from listener.model import AppState
from listener.ui.call_rules import State
from tests.fake_calls import act, teams

pytest.importorskip("pytestqt")

ROOT = Path(__file__).resolve().parent.parent
CALL_MODULES = ("call_answers.py", "call_config.py", "call_prompt.py", "call_rules.py", "call_sensor.py",   # a lista fechada
                "call_watch.py")


def _card(qtbot: Any, **kw: Any) -> Any:
    from listener.ui.call_prompt import CallPrompt

    card = CallPrompt("Teams", kw.pop("seconds", 8), **kw)
    qtbot.addWidget(card)
    return card


# ---------------------------------------------------------------- o cartão
@pytest.mark.lento
def test_the_card_never_takes_focus_and_has_the_size_and_texts_of_u094(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from listener.ui.call_prompt import HEADER

    card = _card(qtbot, record_tip="dica")
    for flag in (Qt.WindowType.Tool, Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                 Qt.WindowType.WindowDoesNotAcceptFocus):
        assert card.windowFlags() & flag == flag                                           # A-14
    assert card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    focusable = [w for w in card.findChildren(QWidget) if w.focusPolicy() != Qt.FocusPolicy.NoFocus]
    assert focusable == [] and card.focusPolicy() == Qt.FocusPolicy.NoFocus             # o teclado nunca chega aqui
    card.show()
    qtbot.waitExposed(card)
    assert not card.isActiveWindow()
    assert card.width() == 360 and 110 <= card.height() <= 160                           # ~136 px lógicos
    assert card.title.text() == HEADER == "Reunião detectada · listener-meet"
    assert card.title.font().weight() == card.title.font().Weight.DemiBold
    assert (card.question.text(), card.detail.text()) == ("Gravar a reunião do Teams?",
                                                          "Áudio de chamada há 8 s. Nada está sendo gravado.")
    record, later = card.record_button, card.later_button
    assert (record.text(), later.text(), record.toolTip()) == ("Gravar…", "Agora não", "dica")
    assert record.width() == later.width() and record.height() == later.height() == 32
    assert card.close_button.width() == card.close_button.height() == 32 and card.close_button.accessibleName()
    assert "Gravar a reunião do Teams?" in card.accessibleName()


@pytest.mark.lento
def test_the_reminder_texts_and_the_time_in_minutes(qtbot):
    from listener.ui.call_prompt import prompt_texts, since_text

    assert [since_text(s) for s in (8, 45, 119, 120, 185)] == ["8 s", "45 s", "119 s", "2 min", "3 min"]
    card = _card(qtbot, seconds=133, reminder=True)
    assert card.question.text() == "Ainda sem gravar: a reunião do Teams segue com áudio."
    assert card.detail.text() == "Começou há 2 min."
    assert prompt_texts("Google Meet no Chrome", 9, False)[0] == "Gravar a reunião do Google Meet no Chrome?"


@pytest.mark.lento
def test_the_mouse_holds_the_card_and_leaving_it_counts_again(qtbot):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    card = _card(qtbot, duration_ms=200)
    vanished: list[int] = []
    card.expired.connect(lambda: vanished.append(1))
    card.show()
    card.start()
    QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert card.hovered and card.remaining_ms() == -1
    qtbot.wait(500)                                                                        # mais que o prazo: não some
    assert vanished == [] and card.isVisible()
    QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
    assert 0 < card.remaining_ms() <= 200
    qtbot.waitUntil(lambda: vanished == [1], timeout=2000)                                # sumir sem clique


@pytest.mark.lento
def test_a_closed_card_with_the_mouse_on_it_never_counts_again(qtbot):
    """O `hide` do `finish` manda o Leave; o `leaveEvent` não religa o prazo de um cartão fechado (nem de um pausado, R-08)."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    for close in ("finish", "pause"):
        card = _card(qtbot)
        card.show()
        card.start()
        QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        getattr(card, close)()
        QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
        assert card.remaining_ms() == -1 and not card.isVisible(), close


@pytest.mark.lento
def test_the_x_and_now_not_dismiss_and_the_body_click_does_nothing(qtbot):
    from PySide6.QtCore import QPoint, Qt

    from listener.ui.call_prompt import PROMPT_MS

    card = _card(qtbot)
    got: list[str] = []
    card.dismissed.connect(lambda: got.append("dispensou"))
    card.record_clicked.connect(lambda: got.append("gravar"))
    card.expired.connect(lambda: got.append("sumiu"))
    card.show()
    card.start()
    assert PROMPT_MS == 20_000 and 19_000 < card.remaining_ms() <= 20_000                # 20 s fixos
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(60, card.question.y() + 4))
    qtbot.mouseClick(card.close_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(card.later_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(card.record_button, Qt.MouseButton.LeftButton)
    assert got == ["dispensou", "dispensou", "gravar"]


@pytest.mark.lento
def test_the_card_sits_in_the_bottom_right_corner_and_alerts_the_screen_reader(qtbot, monkeypatch):
    from PySide6.QtGui import QAccessible, QGuiApplication

    card = _card(qtbot)
    card.place()
    g = QGuiApplication.primaryScreen().availableGeometry()
    assert (card.x() + card.width(), card.y() + card.height()) == (g.right() + 1 - 16, g.bottom() + 1 - 16)
    events: list[Any] = []
    monkeypatch.setattr(QAccessible, "updateAccessibility", staticmethod(lambda e: events.append((e.type(), e.object()))))
    assert card.announce() in (True, False)
    assert events == [(QAccessible.Event.Alert, card)]


# ---------------------------------------------------------------- o "Antes de gravar" pelo aviso
@pytest.mark.lento
def test_the_warmup_from_the_card_picks_the_app_shows_the_bar_and_focuses_record(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    store.set_setting("warmup.last_source_kind", "system")
    dlg = WarmupDialog(platform, store, source_hint="meeting.exe")
    qtbot.addWidget(dlg)
    assert dlg.hint_bar is not None and dlg.layout().itemAt(0).widget() is dlg.hint_bar             # no topo
    assert dlg.hint_bar.label.text() == "Fonte: meeting, pelo aviso de reunião. Confira os medidores e clique em Gravar."
    dlg.show()
    qtbot.waitUntil(lambda: dlg.sources.selected_spec().exe == "meeting.exe", timeout=3000)   # a linha do app
    assert dlg.focusWidget() is dlg.record_btn and dlg.record_btn.isDefault()
    assert int(dlg.winId()) in platform.excluded and dlg.hint_bar.kind == "info"
    dlg.reject()


@pytest.mark.lento
def test_the_warmup_always_leaves_the_capture_and_an_app_not_playing_turns_the_bar_into_a_warning(qtbot, store, platform, caplog):
    from listener.ui.warmup import WarmupDialog

    plain = WarmupDialog(platform, store)
    qtbot.addWidget(plain)
    plain.show()
    assert int(plain.winId()) in platform.excluded and plain.hint_bar is None             # D-065, pergunta 6: sempre
    plain.reject()
    platform.capture_exclusion = False
    with caplog.at_level(logging.INFO, logger="listener.ui.warmup"):
        dlg = WarmupDialog(platform, store, source_hint="ms-teams.exe")
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitUntil(lambda: dlg.hint_bar.kind == "warning", timeout=3000)
    assert dlg.hint_bar.label.text() == "O Teams não está tocando agora: escolha a fonte na lista e clique em Gravar."
    assert dlg.sources.selected_spec().kind == "system" and dlg.isVisible()                # segue normal
    assert "segue na captura de tela (o sistema recusou)" in caplog.text
    dlg.reject()


@pytest.mark.lento
def test_esc_in_the_warmup_from_the_card_dismisses_the_call(qtbot, qapp, store, platform, monkeypatch):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest

    from listener.ui import controller as ctl
    from listener.ui import tasks
    from listener.ui.call_answers import open_warmup_dialog
    from listener.ui.call_sensor import CallSensor
    from listener.ui.call_watch import CallWatch
    from listener.ui.warmup import WarmupDialog

    class EscWarmup(WarmupDialog):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            seen.append((k.get("source_hint"), self))
            QTimer.singleShot(50, self, self._esc)
            QTimer.singleShot(3000, self, self.reject)                                  # R-11: um Esc quebrado falha, não trava

        def _esc(self) -> None:
            busy_inside.append((c.busy, open_warmup_dialog() is self))                   # sem `assert` aqui: o Esc sempre sai
            QTest.keyClick(self, Qt.Key.Key_Escape)

    seen: list[Any] = []
    busy_inside: list[tuple[bool, bool]] = []
    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    monkeypatch.setattr(ctl, "WarmupDialog", EscWarmup)
    monkeypatch.setattr(CallSensor, "start", lambda _self: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    calls = CallWatch(c, platform, store)
    calls.start()
    try:
        calls._on_reading(act(teams()))
        calls.rules.step(act(teams()), calls._clock() + 9, False, False, calls.params)       # o aviso das regras
        assert calls.rules.state("ms-teams.exe") == State.PROMPTED
        calls.open_for("ms-teams.exe", "aviso")
        assert seen and seen[0][0] == "ms-teams.exe" and not c.busy and c.state == AppState.IDLE
        assert busy_inside == [(False, True)]                     # R-02: aberto, não é `busy`; a cola o vê como "Antes de gravar"
        assert calls.rules.state("ms-teams.exe") == State.DISMISSED                          # o Esc vale "Agora não"
    finally:
        tasks.live_threads.discard(calls.sensor)
        calls.shutdown()
        c.emergency_stop()


# ---------------------------------------------------------------- o controlador
@pytest.mark.lento
def test_the_controller_is_busy_while_recording_saving_with_the_warmup_open_or_quitting(qtbot, qapp, store, platform, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QDialog

    from listener.ui import controller as ctl

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    try:
        assert not c.busy
        for state in (AppState.RECORDING, AppState.PAUSED, AppState.SAVING):
            c.state = state
            assert c.busy, state
        c.state = AppState.IDLE
        hints: list[Any] = []

        def warmup(*_a: Any, **k: Any) -> Any:
            hints.append((k.get("source_hint"), c.busy))
            return SimpleNamespace(exec=lambda: QDialog.DialogCode.Rejected, deleteLater=lambda: None)

        monkeypatch.setattr(ctl, "WarmupDialog", warmup)
        c.open_warmup("ms-teams.exe")
        c.open_warmup()
        assert hints == [("ms-teams.exe", False), (None, False)]                        # R-02: o "Antes de gravar" não é `busy`
        opened: list[str] = []
        c._on_hotkey("start")
        c.on_start = lambda: opened.append("pendente")                                     # o atalho com a cola do aviso
        c._on_hotkey("start")
        assert len(hints) == 3 and opened == ["pendente"]
        c._quitting = True
        assert c.busy
    finally:
        c._quitting = False
        c.emergency_stop()


# ---------------------------------------------------------------- a bandeja
@pytest.mark.lento
def test_the_tray_shows_the_pending_call_and_its_two_items(qtbot, qapp, store, platform, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    from listener.ui import controller as ctl
    from listener.ui.tray import TrayIcon

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    opened: list[Any] = []

    def open_warmup(source_exe: str | None = None) -> None:                             # a assinatura do controlador
        opened.append(source_exe)

    monkeypatch.setattr(c, "open_warmup", open_warmup)
    tray = TrayIcon(c)
    try:
        record: list[str] = []
        off: list[str] = []
        tray.set_call_handlers(record.append, off.append)
        actions = tray.menu.actions()
        assert not tray.act_call.isVisible() and tray.toolTip() == "listener-meet: pronto"
        tray.act_record.trigger()
        assert opened == [None]                                                            # o `checked` não vira a fonte
        tray.set_call("chrome.exe", "Google Meet no Chrome", "Chrome")
        assert tray.act_call.text() == "Gravar a reunião do Google Meet no Chrome…"
        assert tray.act_call_off.text() == "Não avisar para o Chrome"                       # R-07: o que ele desliga de fato
        tray.set_call("ms-teams.exe", "Teams", "Teams")
        assert actions[:3] == [tray.act_call, tray.act_call_off, tray.call_separator]      # no topo do menu
        assert tray.act_call.isVisible() and tray.act_call.text() == "Gravar a reunião do Teams…"
        assert tray.act_call.font().bold() and tray.menu.defaultAction() is tray.act_call  # em negrito e padrão
        assert tray.act_call_off.text() == "Não avisar para o Teams"
        assert tray.toolTip() == "listener-meet: reunião no Teams sem gravação"
        tray.act_call.trigger()
        tray.act_call_off.trigger()
        tray._activated(QSystemTrayIcon.ActivationReason.Trigger)                          # o clique esquerdo
        assert record == ["ms-teams.exe", "ms-teams.exe"] and off == ["ms-teams.exe"] and opened == [None]
        tray.set_call("", "")
        tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
        assert opened == [None, None] and not tray.act_call_off.isVisible() and tray.toolTip() == "listener-meet: pronto"
    finally:
        tray.deleteLater()
        c.emergency_stop()


@pytest.mark.lento
def test_toasts_go_above_the_card(qtbot, platform):
    from listener.ui.notices import Notice, NoticeKind
    from listener.ui.toast import ToastManager

    card: list[tuple[int, int, int, int] | None] = [None]
    m = ToastManager(platform, on_action=lambda _a: None, recording=lambda: False, anchor=lambda: None)
    try:
        m.show(Notice(NoticeKind.INFO, "aviso"), 6)
        corner = m.toasts[0].geometry().bottom()
        toast = m.toasts[0]
        rect = (toast.x(), toast.y() - 20, toast.width(), 136)                            # o cartão no mesmo canto
        card[0] = rect
        m.avoid_also(lambda: card[0])
        m.relayout()
        assert toast.geometry().bottom() < rect[1] < corner                                # a pilha sobe acima dele
    finally:
        m.shutdown()


# ---------------------------------------------------------------- regras do código
def _calls_to(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.lineno for n in ast.walk(tree)
            if (isinstance(n, ast.Attribute) and n.attr == name) or (isinstance(n, ast.Name) and n.id == name)
            or (isinstance(n, ast.Constant) and n.value == name)]


def test_no_call_module_starts_a_recording():
    """Aceite 2 (D-010, CP6): o aviso nunca grava sozinho; a lista dos módulos `call_*` é fechada (um novo entra de propósito)."""
    found = sorted(p.name for p in (ROOT / "listener" / "ui").glob("call_*.py"))
    assert found == sorted(CALL_MODULES)
    for name in (*CALL_MODULES, "settings_detect.py", "warmup_source.py"):
        assert _calls_to(ROOT / "listener" / "ui" / name, "start_recording") == [], name
    assert _calls_to(ROOT / "listener" / "ui" / "controller.py", "start_recording")         # o detector acha quando existe


def test_no_log_of_the_new_modules_gets_a_title_a_path_or_a_device_name():
    """Aceite 4 (D-053): nenhum `log.*` nem `print` dos módulos novos recebe título, caminho ou nome de aparelho."""
    from tests.log_privacy import L3_MODULES, leaks

    assert {str(m): leaks(m) for m in L3_MODULES} == {str(m): [] for m in L3_MODULES}
