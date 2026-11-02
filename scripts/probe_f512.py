"""As telas da F5.12 no `win_probe shots` (não fazem parte do produto): o Sobre com o apoio, no topo e rolado até os créditos;
o "Apoiar o projeto", o "Doar pelo Pix" (também com o aviso da chave copiada) e o "Doar pelo PayPal"; e o convite, com os
números do exemplo da spec (12 reuniões, 9 h, 47 ações), com os do banco da sonda e sem número. A página Sobre das
Configurações sai com as outras páginas (`settings_shots` do `win_probe`) e o link "Sobre o listener-meet" com as
boas-vindas do assistente (`_wizard_shots` do `probe_l4`).

`f512_shots(store, platform, shot, app)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela).
Nada sai da máquina e nada vai à área de transferência do usuário: o "Copiar chave" não é clicado (o aviso é posto à mão), o
navegador não abre e o banco é o temporário da sonda."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _discard(w: Any) -> None:
    """R-06: a janela da foto é apagada, não só fechada."""
    w.close()
    w.deleteLater()


def _about_shots(store: Any, support: Any, shot: Callable[..., None], app: Any) -> None:
    from PySide6.QtWidgets import QScrollArea

    from listener.ui.about_dialog import AboutDialog

    dlg = AboutDialog(store.base_dir, store.default_log_path(), support=support)
    shot(dlg, "sobre")
    scroll = dlg.findChild(QScrollArea)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        shot(dlg, "sobre-creditos")
    _discard(dlg)


def _support_shots(support: Any, shot: Callable[..., None]) -> None:
    """U-123 a U-125: o diálogo pequeno e os dois meios de pagamento."""
    from listener.ui.donate import PAYPAL, PIX
    from listener.ui.donate_dialogs import TEXTS, PayDialog, SupportDialog
    from listener.ui.theme import Tone

    choice = SupportDialog()
    shot(choice, "apoiar-o-projeto")
    _discard(choice)
    pix = PayDialog(PIX, support)
    shot(pix, "doar-pelo-pix")
    pix._say(TEXTS[PIX]["copied"], Tone.SUCCESS)          # o aviso depois do "Copiar chave", sem tocar na área de transferência
    shot(pix, "doar-pelo-pix-copiada")
    _discard(pix)
    paypal = PayDialog(PAYPAL, support)
    shot(paypal, "doar-pelo-paypal")
    _discard(paypal)


def _invite_shots(store: Any, shot: Callable[..., None]) -> None:
    """U-126: o cartão com o exemplo da spec, com os números do banco da sonda e sem número (a reserva)."""
    from listener.ui.donate import Stats, invite_texts, meeting_stats
    from listener.ui.donate_card import InviteCard

    for name, stats in (("convite", Stats(12, 9 * 3_600_000, 47)), ("convite-banco-da-sonda", meeting_stats(store)),
                        ("convite-sem-numeros", None)):
        card = InviteCard(*invite_texts(stats))
        shot(card, name)
        _discard(card)


def f512_shots(store: Any, platform: Any, shot: Callable[..., None], app: Any) -> None:
    """Todas as telas da F5.12, na ordem: o Sobre, o apoio e o convite."""
    from listener.ui.donate import Support

    support = Support(store, platform=platform)
    _about_shots(store, support, shot, app)
    _support_shots(support, shot)
    _invite_shots(store, shot)
    support.deleteLater()


__all__ = ["f512_shots"]
