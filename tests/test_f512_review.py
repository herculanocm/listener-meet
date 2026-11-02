"""F5.12, a rodada da revisão do diff (pytest-qt, offscreen, com a plataforma falsa e o relógio falso).

- R-01: o apoio informado por outra entrada (bandeja, Sobre, Histórico, Configurações) fecha o cartão à vista sem adiar, e
  nada do que o cartão faria depois (sumir, "Agora não", fechar pela gravação) encurta os 180 dias.
- R-02: qualquer app com o microfone em uso no indicador do sistema segura o convite, também com a F5.7 desligada (ou sem
  avisar para aquele app), e fecha o cartão à vista.
- Decisões: sem resposta e o cartão fechado pela gravação adiam 30 dias; o interruptor desligado fecha o cartão à vista sem
  mexer no estado (CP8); com a revisão de falantes à vista, o convite não aparece.
- O Enter apertado de verdade na caixa real (como o teste da D-076): no Sobre, fecha; no Pix, copia a chave; no PayPal,
  abre o PayPal; em nenhum, informa um apoio que não houve."""
from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from listener import about
from listener.ui import donate
from tests.test_donate import processed
from tests.test_donate_ui import _glue, _shown

pytest.importorskip("pytestqt")


def press_enter_in(qtbot: Any, kind: type, run: Callable[[], Any]) -> tuple[Any, list[str]]:
    """Abre o diálogo de verdade (o `exec` de `run`) e aperta o Enter na caixa real, no widget com o foco, de dentro do laço
    modal dela (como o teste da D-076). Devolve o que `run` devolveu e em que widget o Enter caiu; o diálogo que o Enter não
    fechou fecha 300 ms depois (o teste não trava)."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    hit: list[str] = []

    def press(tries: int = 100) -> None:
        shown = [w for w in QApplication.topLevelWidgets() if isinstance(w, kind) and w.isVisible()]
        if not shown:
            if tries:
                QTimer.singleShot(20, functools.partial(press, tries - 1))     # a caixa ainda não abriu
            return
        dlg: Any = shown[0]
        target = dlg.focusWidget() or dlg
        hit.append(type(target).__name__)
        QTimer.singleShot(300, dlg, dlg.reject)
        qtbot.keyClick(target, Qt.Key.Key_Return)

    QTimer.singleShot(0, press)
    return run(), hit


# ---------------------------------------------------------------- o Enter na caixa real
def test_enter_in_the_about_closes_it_and_never_opens_the_pix(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from listener.ui.about_dialog import AboutDialog, open_about

    support = donate.Support(store)
    opened: list[tuple[Any, ...]] = []
    monkeypatch.setattr(support, "open_pay", lambda *a: opened.append(a))
    result, hit = press_enter_in(qtbot, AboutDialog, functools.partial(open_about, None, store, support=support, via="teste"))
    assert result == QDialog.DialogCode.Rejected and opened == []                 # "Fechar" é o padrão e começa com o foco
    assert hit == ["QPushButton"]


def test_enter_in_the_pix_dialog_copies_the_key_and_never_informs_a_donation(qtbot, store):
    from PySide6.QtGui import QGuiApplication

    from listener.ui.donate_dialogs import PayDialog

    notices: list[str] = []
    support = donate.Support(store, notices.append)
    QGuiApplication.clipboard().setText("")
    informed, _hit = press_enter_in(qtbot, PayDialog, functools.partial(support.open_pay, donate.PIX, None, "teste"))
    assert informed is False and notices == [] and support.next_at() is None
    assert QGuiApplication.clipboard().text() == about.DONATION_EMAIL               # o Enter foi no "Copiar chave"


def test_enter_in_the_paypal_dialog_opens_the_paypal_and_never_informs_a_donation(qtbot, store, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from listener.ui.donate_dialogs import PayDialog

    opened: list[str] = []

    def fake_open(url: Any) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", fake_open)
    notices: list[str] = []
    support = donate.Support(store, notices.append)
    informed, _hit = press_enter_in(qtbot, PayDialog, functools.partial(support.open_pay, donate.PAYPAL, None, "teste"))
    assert informed is False and notices == [] and support.next_at() is None
    assert opened == [about.PAYPAL_URL]                                            # o Enter foi no "Abrir o PayPal"


# ---------------------------------------------------------------- R-01: a doação de outra entrada
@pytest.mark.parametrize("then", ["expired", "later", "recording"])
def test_a_donation_from_another_entry_closes_the_card_and_keeps_its_180_days(qtbot, store, platform, then):
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    card = _shown(qtbot, g)
    g.support.donated("Pix, bandeja")                                              # pela bandeja, com o cartão à vista
    until = g.clock.now + timedelta(days=180)
    assert g.invite.card is None and g.support.next_at() == until and g.host.notices == [donate.THANKS]
    g.clock.advance(minutes=3)
    if then == "recording":
        g.host.busy = True
        g.invite._watch_tick()
    else:
        getattr(card, then).emit()                                                 # o que o cartão faria depois
    assert g.support.next_at() == until                                            # nada encurta os 180 dias


# ---------------------------------------------------------------- R-02: o microfone em uso
def test_any_app_with_the_microphone_holds_the_invite_even_with_the_f57_off(qtbot, store, platform, monkeypatch):
    g = _glue(qtbot, store, platform)                                              # `in_call` falso: a F5.7 não olha
    processed(store, 3)
    platform.mic_user_ids = frozenset({"a1b2c3d4e5f60718"})
    assert g.invite.check() is False and g.invite.blocked() == "microfone em uso (1 app)"
    platform.mic_user_ids = None                                                  # sem indicador nesta plataforma: nada
    card = _shown(qtbot, g)
    assert card.isVisible()
    platform.mic_user_ids = frozenset({"a1b2c3d4e5f60718", "0f1e2d3c4b5a6978"})
    g.invite._watch_tick()                                                        # o microfone entrou em uso à vista
    assert g.invite.card is None and g.support.next_at() == g.clock.now + timedelta(days=30)

    def unreadable() -> frozenset[str]:
        raise OSError("registro")

    monkeypatch.setattr(platform, "mic_users", unreadable)
    assert g.invite.blocked() == "o indicador de microfone não respondeu (OSError)"     # na dúvida, não aparece


# ---------------------------------------------------------------- o interruptor e a revisão
def test_switching_off_closes_the_visible_invite_without_touching_the_state(qtbot, store, platform):
    g = _glue(qtbot, store, platform)
    processed(store, 3)
    _shown(qtbot, g)
    before = g.support.next_at()
    g.invite._watch_tick()
    assert g.invite.card is not None                                              # ligado e parado: fica
    store.set_setting(donate.ENABLED, "False")                                    # o Salvar da página Sobre (CP8)
    g.invite._watch_tick()
    assert g.invite.card is None and g.support.next_at() == before and g.support.shown() == 1


def test_the_speaker_review_on_screen_holds_the_invite(qtbot, store, platform):
    from listener.ui.donate_invite import screen_busy
    from listener.ui.review import ReviewDialog

    g = _glue(qtbot, store, platform)
    mids = processed(store, 3)
    review = ReviewDialog(store, mids[0])
    qtbot.addWidget(review)
    review.show()
    assert g.invite.check() is False and screen_busy() == "a revisão de falantes está aberta"
    review.hide()
    assert screen_busy() == "" and _shown(qtbot, g) is not None
    g.invite.shutdown()
