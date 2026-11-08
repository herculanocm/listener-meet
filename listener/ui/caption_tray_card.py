"""F5.20 (C13, A-17): o lugar do cartão da sugestão na bandeja de legendas, fora do `ui/caption_tray.py` para ele ficar abaixo
de 400 linhas. O cartão entra entre o chat e a linha da recolhida; a altura dele vai de 120 px a 45 % da bandeja
(`SuggestCard.preferred_height`), refeita quando a bandeja muda de tamanho ou o cartão muda de conteúdo. A bandeja mais baixa
que o mínimo dela mais o do cartão cresce para cima ao mostrar o cartão (`card_extra`, somado à altura da aberta), sem gravar
essa altura como a da aberta (`full_height`); o cartão escondido ou a bandeja recolhida devolvem a altura. O botão "Sugerir"
do cabeçalho segue o host (`apply_suggest_state`, o mesmo do widget)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .caption_geometry import MIN_H, Rect
from .suggest_card import CARD_MIN_H
from .widget_parts import apply_suggest_state

if TYPE_CHECKING:
    from PySide6.QtWidgets import QVBoxLayout

    from .components import IconButton
    from .suggest_card import SuggestCard

CARD_INDEX = 2                    # o cabeçalho, o chat, o cartão e a linha da recolhida


class TrayCardMixin:
    """O cartão da sugestão e o botão "Sugerir" da `CaptionTray`."""
    if TYPE_CHECKING:
        lay: QVBoxLayout
        btn_suggest: IconButton
        collapsed: bool
        full_height: int

        def height(self) -> int: ...
        def isHidden(self) -> bool: ...
        def _rect(self) -> Rect: ...
        def _apply_height(self, before: Rect) -> None: ...

    card: SuggestCard | None = None
    card_extra = 0                # a altura somada à da aberta enquanto o cartão está à vista numa bandeja baixa
    card_wanted = False           # o cartão foi mostrado (a bandeja recolhida e restaurada o traz de volta)

    def set_suggest_state(self, visible: bool, enabled: bool, tip: str, hint: bool) -> None:
        apply_suggest_state(self.btn_suggest, visible, enabled, tip, hint)

    def set_card(self, card: SuggestCard | None) -> None:
        """O cartão do host (None tira o de agora, que o host apaga)."""
        if self.card is not None:
            self.card.content_changed.disconnect(self.fit_card)
            self.lay.removeWidget(self.card)
            self.card.hide()
        self.card, self.card_wanted = card, False
        if card is not None:
            self.lay.insertWidget(CARD_INDEX, card)
            card.hide()
            card.content_changed.connect(self.fit_card)
        self._card_height(0)

    def show_card(self) -> None:
        """O cartão à vista (um pedido começou); a bandeja baixa cresce para caber o chat e o cartão."""
        if self.card is None:
            return
        self.card_wanted = True
        if not self.collapsed:
            before = self._rect()                         # antes do `show`: o layout já empurraria a bandeja para baixo
            self.card.show()
            self.fit_card()
            self._card_height(max(0, MIN_H + CARD_MIN_H - self.full_height), before)
        self.fit_card()

    def hide_card(self) -> None:
        if self.card is not None:
            self.card_wanted = False
            self.card.hide()
            self._card_height(0)

    def card_collapsed(self, collapsed: bool) -> None:
        """A bandeja recolheu ou restaurou: o cartão some com o chat e volta com ele, se estava à vista."""
        if self.card is None or not self.card_wanted:
            return
        self.card.setVisible(not collapsed)
        if not collapsed:
            self.card_extra = max(0, MIN_H + CARD_MIN_H - self.full_height)
        else:
            self.card_extra = 0
        self.fit_card()

    def fit_card(self) -> None:
        if self.card is not None and not self.card.isHidden():
            self.card.setFixedHeight(self.card.preferred_height(self.height()))

    def _card_height(self, extra: int, before: Rect | None = None) -> None:
        if extra == self.card_extra:
            return
        start = before if before is not None else self._rect()
        self.card_extra = extra
        if not self.collapsed and not self.isHidden():
            self.lay.activate()                           # o mínimo da bandeja já sem (ou com) o cartão, antes da geometria
            self._apply_height(start)


__all__ = ["CARD_INDEX", "TrayCardMixin"]
