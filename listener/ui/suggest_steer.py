"""F5.20 (U-155, U-156, U-157), saído do `ui/suggest_host.py` no L2 da F5.21 (o host passava de 400 linhas com a tela), sem
mudar nada: avaliar e direcionar. "Boa", "Ruim" com o motivo e "Usei" gravam na hora e refazem o "ajustada por"; os chips e o
texto livre montam o "Como sugerir agora"; "Objetivo…" grava o objetivo da reunião; o fato citado rola o chat até a legenda.
Os popups do texto livre e do objetivo ficam fora da captura (`SuggestTextPopup`); a bandeja apagada no meio do `exec()` não
derruba nada (R-10 da revisão do L2 da F5.20)."""
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from ..log import get_logger
from .suggest_card import CHIPS
from .suggest_popup import SuggestTextPopup

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store
    from .caption_tray import CaptionTray
    from .suggest_card import SuggestCard

log = get_logger("ui.suggest")

NUDGE_ASK = ("Como sugerir", "como a IA deve sugerir (Enter salva, Esc cancela)")       # o título e a dica dos popups
GOAL_ASK = ("Objetivo", "meu objetivo nesta reunião (Enter salva, Esc cancela)")


class SteerMixin:
    """Avaliar e direcionar, do `SuggestHost`."""
    if TYPE_CHECKING:
        store: Store
        platform: Platform
        tray: CaptionTray
        card: SuggestCard
        meeting_id: int
        closed: bool
        chips: list[str]
        free: str

        @property
        def nudge(self) -> str: ...

    def rate(self, option_id: int, rating: object, reason: object) -> None:
        try:
            self.store.suggestions.set_rating(option_id, rating if isinstance(rating, int) else None,
                                              reason if isinstance(reason, str) else None)
        except Exception as e:
            log.warning("sugestão: opção %s não avaliada: %s", option_id, e)
            return
        verdict = "boa" if rating == 1 else "ruim" if rating == 0 else "desfeita"
        log.info("sugestão: opção %s avaliada (%s%s)", option_id, verdict, f", {reason}" if reason else "")
        self.card.set_adjusted(self.adjusted())

    def set_used(self, option_id: int, used: bool) -> None:
        try:
            self.store.suggestions.set_used(option_id, used)
        except Exception as e:
            log.warning("sugestão: opção %s não marcada: %s", option_id, e)
            return
        log.info("sugestão: opção %s %s", option_id, "usada" if used else "não usada")
        self.card.set_adjusted(self.adjusted())

    def adjusted(self) -> str:
        """O "ajustada por" do cartão: as avaliações desta gravação, os motivos do "Ruim" contados e as boas e usadas."""
        try:
            rows = self.store.suggestions.rated_for_meeting(self.meeting_id, 200)
        except Exception:
            return ""
        reasons: dict[str, int] = {}
        good = used = bad = 0
        for r in rows:
            if r.get("rating") == 0:
                bad += 1
                if r.get("reason"):
                    reasons[str(r["reason"])] = reasons.get(str(r["reason"]), 0) + 1
            good += r.get("rating") == 1
            used += bool(r.get("used"))
        parts = []
        if bad:
            parts.append("Ruim: " + (", ".join(f"{k} ({n})" for k, n in reasons.items()) or str(bad)))
        if good or used:
            parts.append(", ".join(x for x in (f"Boa: {good}" if good else "", f"Usei: {used}" if used else "") if x))
        return "; ".join(parts)

    def toggle_chip(self, text: str, checked: bool) -> None:
        chosen = set(self.chips) | {text} if checked else set(self.chips) - {text}
        self.chips = [c for c in CHIPS if c in chosen]
        self.card.set_nudge(self.nudge)

    def clear_nudge(self) -> None:
        self.chips, self.free = [], ""
        self.card.set_chips(())
        self.card.set_nudge("")

    def edit_nudge(self) -> None:
        text = self._ask(NUDGE_ASK[0], self.free, NUDGE_ASK[1])
        if text is not None and not self.closed:
            self.free = text
            self.card.set_nudge(self.nudge)

    def edit_goal(self) -> None:
        meeting = self.store.get_meeting(self.meeting_id) or {}
        text = self._ask(GOAL_ASK[0], str(meeting.get("goal") or ""), GOAL_ASK[1])
        if text is not None and not self.closed and self.store.meetings.set_goal(self.meeting_id, text or None):
            what = "trocado" if text else "apagado"           # o log nunca leva o objetivo (D-053; `tests/log_privacy.py`)
            log.info("reunião %s: objetivo %s", self.meeting_id, what)

    def _ask(self, title: str, text: str, placeholder: str) -> str | None:
        popup = SuggestTextPopup(title, text, placeholder, self.platform, self.tray)
        try:
            return popup.value() if popup.exec() == SuggestTextPopup.DialogCode.Accepted else None
        finally:
            with suppress(RuntimeError):          # R-10 da revisão do L2: a bandeja (a mãe) saiu no meio do `exec()`
                popup.deleteLater()

    def show_fact(self, caption_id: int) -> None:
        if self.tray.collapsed:
            self.tray.restore()
        self.tray.chat.scroll_to_caption(caption_id)



__all__ = ["GOAL_ASK", "NUDGE_ASK", "SteerMixin"]
