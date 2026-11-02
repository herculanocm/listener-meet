"""F5.12 L2 (U-123 a U-128): o apoio na tela (pytest-qt, offscreen, com a plataforma falsa e o relógio falso). Os diálogos
(o "Apoiar o projeto", o Pix e o PayPal), o cartão do convite (sem foco, os textos e as respostas), a cola com a cadência
inteira do Aceite 2 (nunca gravando, com chamada ou no assistente; o primeiro depois da 3ª; 30, 7 e 180 dias; nunca
desligado), nenhum socket aberto pelo convite, o log sem o e-mail e as entradas (bandeja, Sobre, página Sobre, rodapé do
Histórico e o aviso do controlador)."""
from __future__ import annotations

import json
import logging
import socket
import sys
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from listener import about
from listener.ui import donate
from tests.fake_watch import FakeHost
from tests.test_donate import Clock, processed

pytest.importorskip("pytestqt")


def _glue(qtbot: Any, store: Any, platform: Any, **kw: Any) -> SimpleNamespace:
    """A cola com o relógio falso, o controlador falso (`busy`, avisos) e a F5.7 falsa (`in_call`)."""
    from listener.ui.donate_invite import DonateInvite

    clock, host, call = Clock(), FakeHost(), {"on": False}
    support = donate.Support(store, host.notify, clock=clock)
    invite = DonateInvite(host, platform, store, support, in_call=lambda: call["on"], **kw)
    invite.start()
    return SimpleNamespace(clock=clock, host=host, call=call, support=support, invite=invite)


def _shown(qtbot: Any, g: SimpleNamespace) -> Any:
    assert g.invite.check() is True
    card = g.invite.card
    qtbot.addWidget(card)
    return card


# ---------------------------------------------------------------- os diálogos (U-123 a U-125)
def test_the_support_dialog_has_the_heart_the_text_and_the_two_paths(qtbot):
    from listener.ui.donate_dialogs import PAYPAL_RESULT, PIX_RESULT, SupportDialog
    from listener.ui.icons import ICON_PROP
    from listener.ui.theme import KIND_PROP, PRIMARY

    dlg = SupportDialog()
    qtbot.addWidget(dlg)
    section = dlg.section
    assert dlg.windowTitle() == "Apoiar o projeto" and section.title() == "Apoie o projeto"
    assert section.text.text() == donate.SUPPORT_TEXT and str(section.heart.property(ICON_PROP)).startswith("heart:")
    assert (section.pix.text(), section.paypal.text()) == ("Doar pelo Pix…", "Doar pelo PayPal…")
    assert section.pix.property(KIND_PROP) == PRIMARY                    # o Pix é o principal (pergunta 6)
    section.pix.click()
    assert dlg.result() == PIX_RESULT
    section.paypal.click()
    assert dlg.result() == PAYPAL_RESULT


def test_the_pix_dialog_copies_the_key_and_says_the_three_steps(qtbot, store, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from listener.ui.donate_dialogs import DONATED_RESULT, PIX_STEPS, PayDialog
    from listener.ui.theme import KIND_PROP, PRIMARY

    dlg = PayDialog(donate.PIX, donate.Support(store))
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Doar pelo Pix" and dlg.key.text() == about.DONATION_EMAIL and dlg.key.isReadOnly()
    assert dlg.amount.text() == "Valor sugerido: R$ 0,99" and dlg.open_button is None
    assert dlg.steps.text().splitlines() == list(PIX_STEPS) and len(PIX_STEPS) == 3                 # os três passos (U-124)
    assert PIX_STEPS[0].endswith("Pix \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Pagar com chave.")
    assert PIX_STEPS[1] == "2. Cole a chave." and PIX_STEPS[2] == "3. Pague R$ 0,99, ou o que quiser, e confirme."
    assert dlg.copy_button.text() == "Copiar chave" and dlg.copy_button.property(KIND_PROP) == PRIMARY
    assert dlg.done_button.property(KIND_PROP) != PRIMARY                # o Enter nunca informa um apoio que não houve
    QGuiApplication.clipboard().setText("")
    assert dlg.status.isHidden()
    dlg.copy_button.click()
    assert QGuiApplication.clipboard().text() == about.DONATION_EMAIL and not dlg.status.isHidden()
    dlg.close_button.click()
    assert dlg.result() == dlg.DialogCode.Rejected
    dlg.done_button.click()
    assert dlg.result() == DONATED_RESULT


def test_the_paypal_dialog_opens_the_browser_and_copies_the_email(qtbot, store, monkeypatch):
    from PySide6.QtGui import QDesktopServices, QGuiApplication

    from listener.ui.donate_dialogs import PAYPAL_OPENED, PayDialog
    from listener.ui.theme import KIND_PROP, PRIMARY

    opened: list[str] = []

    def fake_open(url: Any) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", fake_open)
    dlg = PayDialog(donate.PAYPAL, donate.Support(store))
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Doar pelo PayPal" and dlg.copy_button.text() == "Copiar e-mail"
    assert dlg.amount.text() == "Valor sugerido: US$ 0,20 (ou R$ 0,99)"
    assert dlg.open_button is not None and dlg.open_button.text() == "Abrir o PayPal" and dlg.open_button.property(KIND_PROP) == PRIMARY
    QGuiApplication.clipboard().setText("")
    dlg.open_button.click()
    assert opened == [about.PAYPAL_URL] and QGuiApplication.clipboard().text() == about.DONATION_EMAIL
    assert dlg.status.text() == PAYPAL_OPENED


def test_done_in_a_payment_dialog_thanks_and_defers_180_days(qtbot, store, monkeypatch):
    from listener.ui.donate_dialogs import DONATED_RESULT, PIX_RESULT

    clock, notices, ran = Clock(), list[str](), list[str]()
    support = donate.Support(store, notices.append, clock=clock)
    results = iter([PIX_RESULT, DONATED_RESULT])

    def fake_run(dlg: Any) -> int:
        ran.append(type(dlg).__name__)
        dlg.deleteLater()
        return next(results)

    monkeypatch.setattr(support, "run", fake_run)
    support.open_choice(None, "bandeja")                                  # "Apoiar o projeto…", Pix e "Pronto, doei"
    assert ran == ["SupportDialog", "PayDialog"] and notices == [donate.THANKS]
    assert support.next_at() == clock.now + timedelta(days=180)


# ---------------------------------------------------------------- o cartão (U-126)
def test_the_card_never_takes_focus_and_has_the_texts_and_buttons_of_u126(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from listener.ui.donate_card import HEADER, InviteCard
    from listener.ui.theme import KIND_PROP, PRIMARY

    title, message = donate.invite_texts(donate.Stats(12, 9 * 3_600_000, 47))
    card = InviteCard(title, message)
    qtbot.addWidget(card)
    for flag in (Qt.WindowType.Tool, Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                 Qt.WindowType.WindowDoesNotAcceptFocus):
        assert card.windowFlags() & flag == flag
    assert card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert [w for w in card.findChildren(QWidget) if w.focusPolicy() != Qt.FocusPolicy.NoFocus] == []
    card.show()
    qtbot.waitExposed(card)
    assert not card.isActiveWindow() and card.width() == 360
    assert (card.header.text(), card.title.text(), card.message.text()) == (HEADER, title, message)
    assert title == "O listener-meet já guardou 12 reuniões suas"
    assert (card.pix_button.text(), card.paypal_button.text(), card.later_button.text()) == \
        ("Doar R$ 0,99 pelo Pix", "Doar US$ 0,20 pelo PayPal", "Agora não")
    assert card.pix_button.property(KIND_PROP) == PRIMARY and card.donated_link.accessibleName() == "Já doei"
    assert card.paypal_button.y() == card.later_button.y() > card.pix_button.y() and card.pix_button.height() == 32
    assert card.pix_button.width() > card.paypal_button.width()            # o Pix na linha inteira (o principal)


def test_the_card_answers_and_expires_after_its_time_held_by_the_mouse(qtbot):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    from listener.ui.donate_card import InviteCard

    card = InviteCard("t", "m", duration_ms=150)
    qtbot.addWidget(card)
    got: list[str] = []
    for name in ("pix_clicked", "paypal_clicked", "later", "donated", "expired"):
        getattr(card, name).connect(lambda n=name: got.append(n))
    card.close_button.click()
    card.later_button.click()
    card.pix_button.click()
    card.paypal_button.click()
    card.donated_link.linkActivated.emit("link")
    assert got == ["later", "later", "pix_clicked", "paypal_clicked", "donated"]
    card.show()
    card.start()
    QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert card.hovered and card.remaining_ms() == -1                    # o mouse em cima segura
    qtbot.wait(400)                                                      # mais que o prazo: não some
    assert "expired" not in got and card.isVisible()
    QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
    assert 0 < card.remaining_ms() <= 150
    qtbot.waitUntil(lambda: "expired" in got, timeout=2000)


# ---------------------------------------------------------------- a cola: a cadência do Aceite 2
def test_the_first_invite_only_after_the_3rd_processed_meeting_8_s_after_the_notice(qtbot, store, platform):
    g = _glue(qtbot, store, platform)
    processed(store, 2)
    g.support.job_noticed({"kind": "meeting"})
    assert g.invite.pending() and g.invite._timer.interval() == 8000 and g.invite._timer.isSingleShot()
    assert g.invite.check() is False and g.invite.card is None
    processed(store, 1)
    card = _shown(qtbot, g)
    assert card.isVisible() and platform.excluded[-1] == int(card.winId())            # fora da captura (A-13)
    assert card.title.text() == "O listener-meet já guardou 3 reuniões suas"
    assert g.support.shown() == 1 and g.support.next_at() == g.clock.now + timedelta(days=30)
    assert g.host.toasts.avoid is not None and g.host.toasts.avoid() == g.invite.card_rect() and g.host.toasts.relayouts == 1
    g.invite.shutdown()
    assert g.invite.card is None and not g.invite.pending()


@pytest.mark.parametrize(("answer", "days"), [("later", 30), ("close", 30), ("expired", 7), ("donated", 180)])
def test_each_answer_defers_the_next_invite(qtbot, store, platform, answer, days):
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    card = _shown(qtbot, g)
    {"later": card.later_button.click, "close": card.close_button.click, "expired": card.expired.emit,
     "donated": card.donated.emit}[answer]()
    assert g.invite.card is None and g.support.next_at() == g.clock.now + timedelta(days=days)
    assert g.host.notices == ([donate.THANKS] if answer == "donated" else [])
    g.clock.advance(days=days - 1)
    assert g.invite.check() is False                                      # antes da data, não
    g.clock.advance(days=1)
    assert _shown(qtbot, g) is not None and g.support.shown() == 2         # na data, sim
    g.invite.shutdown()


def test_never_while_recording_with_a_call_in_the_wizard_or_switched_off(qtbot, store, platform, tmp_path, monkeypatch):
    from listener.ui import donate_invite
    from listener.ui.wizard import FirstRunWizard

    g = _glue(qtbot, store, platform)
    processed(store, 5)
    g.host.busy = True
    assert g.invite.check() is False and g.invite.blocked() == "gravando, salvando ou saindo"
    g.host.busy, g.call["on"] = False, True
    assert g.invite.check() is False and g.invite.blocked() == "chamada detectada"
    g.call["on"] = False
    monkeypatch.setattr(donate_invite, "open_warmup_dialog", object)            # um "Antes de gravar" à vista
    assert g.invite.check() is False and "Antes de gravar" in g.invite.blocked()
    monkeypatch.setattr(donate_invite, "open_warmup_dialog", lambda: None)
    wiz = FirstRunWizard(platform, tmp_path / "db")
    qtbot.addWidget(wiz)
    wiz.show()
    assert g.invite.check() is False and g.invite.blocked() == "o assistente está aberto"
    wiz.hide()
    store.set_setting(donate.ENABLED, "False")                            # nunca desligado (CP8)
    assert g.invite.check() is False and g.support.shown() == 0
    store.set_setting(donate.ENABLED, "True")
    assert _shown(qtbot, g) is not None
    g.invite.shutdown()


def test_a_visible_invite_closes_when_recording_starts_or_a_call_shows_up(qtbot, store, platform):
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    _shown(qtbot, g)
    g.invite._watch_tick()
    assert g.invite.card is not None                                      # parado: fica
    g.call["on"] = True
    g.invite._watch_tick()
    assert g.invite.card is None and g.support.next_at() == g.clock.now + timedelta(days=7)    # vale sem resposta
    g.call["on"] = False
    g.clock.advance(days=7)
    _shown(qtbot, g)
    g.host.busy = True
    g.invite._watch_tick()
    assert g.invite.card is None


def test_without_the_capture_exclusion_the_invite_waits_and_nothing_changes(qtbot, store, platform, caplog):
    platform.capture_exclusion = False
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    with caplog.at_level(logging.WARNING, logger="listener.ui.donate"):
        assert g.invite.check() is False and g.invite.check() is False
    assert g.invite.card is None and g.support.shown() == 0 and g.support.next_at() is None
    assert caplog.text.count("não saiu da captura de tela") == 1             # uma vez na sessão


def test_a_busy_database_or_an_open_dialog_leaves_it_for_the_next_notice(qtbot, store, platform, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from listener.ui import donate_invite

    g = _glue(qtbot, store, platform)
    processed(store, 3)

    def busy(_store: Any) -> Any:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(donate_invite, "meeting_stats", busy)
    assert g.invite.check() is False and g.support.shown() == 0               # sem os números, não confere a 3ª: espera
    monkeypatch.undo()
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(object))   # um diálogo modal à vista
    assert g.invite.check() is False                                          # o cartão não receberia o clique
    monkeypatch.undo()
    assert _shown(qtbot, g) is not None
    g.invite.shutdown()


def test_the_pix_and_paypal_buttons_close_the_card_and_open_the_payment_dialog(qtbot, store, platform, monkeypatch):
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    opened: list[tuple[Any, ...]] = []
    monkeypatch.setattr(g.support, "open_pay", lambda *a: opened.append(a))
    card = _shown(qtbot, g)
    card.pix_button.click()
    assert g.invite.card is None
    qtbot.waitUntil(lambda: opened == [(donate.PIX, None, "convite")], timeout=1000)       # no giro seguinte (é modal)
    assert g.support.next_at() == g.clock.now + timedelta(days=30)        # a data da aparição fica
    g.clock.advance(days=30)
    _shown(qtbot, g).paypal_button.click()
    qtbot.waitUntil(lambda: opened[-1] == (donate.PAYPAL, None, "convite"), timeout=1000)


# ---------------------------------------------------------------- rede e log (Aceite 3, Comportamento 5)
def test_the_invite_opens_no_socket_and_never_logs_the_email(qtbot, store, platform, monkeypatch, caplog):
    from PySide6.QtGui import QDesktopServices

    from listener.ui import donate_card, donate_invite  # noqa: F401 (importados antes da foto dos módulos)
    from listener.ui.donate_dialogs import DONATED_RESULT, PayDialog

    sockets: list[Any] = []
    real_init = socket.socket.__init__

    def spy(self: Any, *a: Any, **k: Any) -> None:
        sockets.append(a)
        real_init(self, *a, **k)

    monkeypatch.setattr(socket.socket, "__init__", spy)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: True)
    before = set(sys.modules)
    g = _glue(qtbot, store, platform)

    def click_through(dlg: Any) -> int:
        if isinstance(dlg, PayDialog):
            dlg.copy_button.click()
            if dlg.open_button is not None:
                dlg.open_button.click()
        dlg.deleteLater()
        return DONATED_RESULT

    monkeypatch.setattr(g.support, "run", click_through)
    processed(store, 3)
    with caplog.at_level(logging.DEBUG, logger="listener"):
        g.support.job_noticed({"kind": "llm", "timings_json": json.dumps({"llm": {"done": ["minutes"]}})})
        _shown(qtbot, g).later_button.click()
        g.clock.advance(days=30)
        _shown(qtbot, g).paypal_button.click()
        qtbot.waitUntil(lambda: g.host.notices == [donate.THANKS], timeout=1000)
    assert sockets == []
    assert not {"PySide6.QtNetwork", "http.client", "urllib.request", "ssl"} & (set(sys.modules) - before)
    assert about.DONATION_EMAIL not in caplog.text and "herculanocm" not in caplog.text
    for line in ("convite de apoio mostrado (vez 1", "convite de apoio adiado por 30 dias (Agora não", "apoio informado (PayPal, convite"):
        assert line in caplog.text, line


# ---------------------------------------------------------------- as entradas (U-123, U-127, U-128)
def test_the_tray_has_the_support_item_with_the_heart_after_the_about(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.tray import ABOUT_ITEM, SUPPORT_ITEM, TrayIcon

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    chosen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(c.support, "open_choice", lambda *a: chosen.append(a))
    tray = TrayIcon(c)
    names = [a.text() for a in tray.menu.actions()]
    assert SUPPORT_ITEM == "Apoiar o projeto…" and names.index(SUPPORT_ITEM) == names.index(ABOUT_ITEM) + 1
    assert not tray.act_support.icon().isNull()
    tray.act_support.trigger()
    assert chosen == [(None, "bandeja")]
    tray.hide()
    tray.deleteLater()


def test_the_about_with_support_opens_each_payment_dialog_over_itself(qtbot, store, monkeypatch):
    from listener.ui.about_dialog import AboutDialog

    support = donate.Support(store)
    opened: list[tuple[Any, ...]] = []
    monkeypatch.setattr(support, "open_pay", lambda *a: opened.append(a))
    dlg = AboutDialog(store.base_dir, None, support=support, via="bandeja")
    qtbot.addWidget(dlg)
    section = dlg.view.support_section
    assert section is not None and section.text.text() == donate.SUPPORT_TEXT
    section.pix.click()
    section.paypal.click()
    assert opened == [(donate.PIX, dlg, "bandeja"), (donate.PAYPAL, dlg, "bandeja")]


def test_the_settings_about_page_has_the_support_and_the_switch_that_turns_it_off(qtbot, store, platform):
    from PySide6.QtWidgets import QCheckBox

    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {}, support=donate.Support(store))
    qtbot.addWidget(dlg)
    assert donate.ENABLED not in dlg._editors                              # nasce com a página (Aceite 4)
    dlg.nav.setCurrentRow(dlg.nav.count() - 1)
    view = dlg.about_view
    assert view is not None and view.support_section is not None
    box = dlg.editor(donate.ENABLED).widget
    assert isinstance(box, QCheckBox) and box.isChecked() and view.support_section.isAncestorOf(box)
    assert box.text() == "Mostrar o convite para apoiar de vez em quando"
    box.setChecked(False)
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted and store.get_setting(donate.ENABLED) == "False"
    assert donate.Support(store).enabled() is False


def test_the_history_footer_has_the_discreet_support_link(qtbot, store):
    from listener.ui.components import LinkLabel
    from listener.ui.history import HistoryWindow

    clicked: list[bool] = []
    win = HistoryWindow(store, on_support=lambda: clicked.append(True))
    qtbot.addWidget(win)
    links = [w for w in win.findChildren(LinkLabel) if w.accessibleName() == "Apoiar o listener-meet"]
    assert len(links) == 1 and not win.page.isAncestorOf(links[0])         # no rodapé da janela, fora da página
    links[0].linkActivated.emit("link")
    assert clicked == [True]
    win.page.shutdown()
    bare = HistoryWindow(store)                                            # sem quem abra o apoio (a sonda): nada quebra
    qtbot.addWidget(bare)
    bare.findChildren(LinkLabel)[-1].linkActivated.emit("link")
    bare.page.shutdown()


def test_the_history_footer_of_the_app_opens_the_support_from_the_history(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.components import LinkLabel

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    chosen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(c.support, "open_choice", lambda *a: chosen.append(a))
    win = c.open_history()
    try:
        link = next(w for w in win.findChildren(LinkLabel) if w.accessibleName() == "Apoiar o listener-meet")
        link.linkActivated.emit("link")
        assert chosen == [(None, "histórico")]
    finally:
        win.page.shutdown()
        win.hide()


def test_the_controller_schedules_the_check_on_the_processed_and_minutes_notices(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    seen: list[bool] = []
    c.support.meeting_notified.connect(lambda: seen.append(True))
    mid = processed(store, 1)[0]
    c._on_job_done({"id": 1, "meeting_id": mid, "kind": "meeting"})
    c._on_job_done({"id": 2, "meeting_id": mid, "kind": "index"})
    c._on_job_done({"id": 3, "meeting_id": mid, "kind": "llm", "timings_json": json.dumps({"llm": {"done": ["minutes"]}})})
    assert seen == [True, True]


def test_the_app_wires_the_invite_after_the_call_watch_and_closes_it_on_quit(qtbot, qapp, platform, store, monkeypatch):
    from PySide6.QtCore import QObject, Signal

    from listener.ui import controller as ctl
    from listener.ui.app import attach_calls, attach_invite
    from listener.ui.call_sensor import CallSensor

    class App(QObject):
        aboutToQuit = Signal()                                                           # noqa: N815 (o nome do Qt)
        commitDataRequest = Signal(object)                                               # noqa: N815

        def quit(self) -> None:
            pass

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    monkeypatch.setattr(CallSensor, "start", lambda _self: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    app = App()
    calls = attach_calls(app, c, platform, store)
    calls.start()
    invite = attach_invite(app, c, platform, store, calls)
    assert invite.parent() is c and invite.in_call == calls.in_call and calls.in_call() is False
    assert c.toasts._avoid_also == invite.avoid_rect                       # a pilha evita os dois cartões
    c.support.meeting_notified.emit()
    assert invite.pending()
    app.aboutToQuit.emit()
    assert not invite.pending() and invite._closed
