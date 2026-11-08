"""F5.20 (C10): o fio das sugestões ao vivo com a gravação, no padrão do `CaptionTrayHost`, que o cria (o cartão mora na
bandeja de legendas) e lhe repassa a legenda, o estado do ao vivo, o fim e a saída: o controlador não cresce.

- O botão "Sugerir" (widget e bandeja) e o atalho: escondidos sem a caixa "Sugestões" desta gravação ou com
  `suggest.enabled` desligada (vale em uso: `reload` no fechar das Configurações); apagados, com o motivo na dica, sem a
  legenda ao vivo, sem a chave, com o teto de custo ou com um pedido em voo; a dica leva o atalho e o custo estimado.
- O pedido: uma `StreamTask` por vez (`run_suggest`), registrada em `live_threads` (a saída do app a cancela e espera, A-02);
  cada opção chega ao cartão pelo `progress`, na thread da interface; o fim grava e liga as avaliações.
- "Boa", "Ruim" com o motivo e "Usei" gravam na hora; "Como sugerir agora" (os chips e o texto) vale até ser limpa; o
  "Objetivo…" grava em `meetings.goal`; o texto livre abre o popup fora da captura (`ui/suggest_popup.py`).
- Parar: o cartão fica para as avaliações, sem pedir de novo; Descartar: a chamada em voo é cancelada.
Sinais ligados a métodos (R-06). O log leva ids, contagens e estados; nunca o texto da sugestão, da linha nem do objetivo."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from ..config.defaults import SPECS
from ..llm import suggest_task
from ..llm.jobconfig import LlmJobConfig
from ..llm.suggest_task import Outcome, SuggestOption, SuggestResult, run_suggest
from ..log import get_logger
from ..model import SuggestMode
from .llm_ask import NO_KEY_TIP, has_key
from .suggest_card import CHIPS, SuggestCard
from .suggest_popup import SuggestTextPopup
from .tasks import StreamTask, run_stream
from .widget_parts import shortcut_text

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store
    from .caption_tray import CaptionTray
    from .widget import RecordingWidget

log = get_logger("ui.suggest")
LIVE_OK = frozenset({"starting", "restarting", "live", "degraded", "skipping"})   # a legenda ao vivo de pé (a que a sugestão lê)
NO_LIVE = "A sugestão lê a legenda ao vivo: ela não está de pé nesta gravação."
IN_FLIGHT = "a sugestão anterior ainda está chegando"
ENDED = "Gravação encerrada: as sugestões param aqui."
TIP = "Sugerir o que falar agora"
EST_IN, EST_OUT = 11_000, 600                     # os caracteres de um pedido típico, para a dica do custo


class SuggestHost(QObject):
    def __init__(self, store: Store, platform: Platform, widget: RecordingWidget, tray: CaptionTray, meeting_id: int, *, active: bool,
                 live_state: str, elapsed: Callable[[], float], on_notice: Callable[[str], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store, self.platform, self.widget, self.tray, self.meeting_id = store, platform, widget, tray, meeting_id
        self.active = active                      # U-163: a caixa "Sugestões" desta gravação
        self.live_state = live_state
        self.elapsed = elapsed                    # o tempo da gravação em segundos (o relógio do gravador)
        self.on_notice = on_notice
        self.task: StreamTask | None = None
        self.finished = self.closed = False
        self.mode = SuggestMode.SPEAK
        self.chips: list[str] = []                # "Como sugerir agora", na ordem dos chips
        self.free = ""                            # e o texto livre do popup
        self.last_end_ms: int | None = None       # o fim da legenda mais nova que chegou (o "até [mm:ss]" do cartão)
        self.newest_caption = 0                   # o id da legenda mais nova que chegou
        self.answered_until = 0                   # a última legenda lida pela sugestão anterior
        self.enabled = self.keyed = False
        self.cost_tip = ""
        self.card = SuggestCard()
        for signal, slot in ((self.card.request_clicked, self.request), (self.card.cancel_clicked, self.cancel),
                             (self.card.rated, self.rate), (self.card.used_toggled, self.set_used),
                             (self.card.nudge_chip_toggled, self.toggle_chip), (self.card.nudge_text_requested, self.edit_nudge),
                             (self.card.nudge_cleared, self.clear_nudge), (self.card.goal_requested, self.edit_goal),
                             (self.card.fact_clicked, self.show_fact)):
            signal.connect(slot)
        tray.set_card(self.card)
        tray.suggest_requested.connect(self.request_from_outside)
        widget.suggest_clicked.connect(self.request_from_outside)
        self.reload()

    # ---------------------------------------------------------------- estado do botão
    def reload(self) -> None:
        """As configurações e a chave relidas (o começo da gravação e o fechar das Configurações: vale em uso, CP8)."""
        try:
            self.enabled = bool(self.store.get(SPECS["suggest.enabled"]))
            self.keyed = has_key(self.store)
            self.cost_tip = self._cost_tip()
        except Exception as e:                    # o banco ocupado: o estado de antes fica
            log.debug("sugestões: configurações não relidas: %s", e)
        self.refresh()

    def _cost_tip(self) -> str:
        cost = suggest_task.estimate_usd(self.store, LlmJobConfig.from_store(self.store), EST_IN, EST_OUT) if self.keyed else None
        shortcut = shortcut_text(self.store.get_setting("hotkeys.suggest") or "")
        parts = [TIP] + ([f"({shortcut})"] if shortcut else []) + ([f"· ~{_usd(cost)} por pedido"] if cost is not None else [])
        return " ".join(parts)

    def available(self) -> tuple[bool, bool, str]:
        """(à vista, pode pedir, a dica)."""
        if not self.active or not self.enabled:
            return False, False, ""
        if self.finished:
            return True, False, ENDED
        if self.live_state not in LIVE_OK:
            return True, False, NO_LIVE
        if not self.keyed:
            return True, False, NO_KEY_TIP
        if self.task is not None:
            return True, False, f"{IN_FLIGHT[:1].upper()}{IN_FLIGHT[1:]}."
        return True, True, self.cost_tip

    def refresh(self) -> None:
        if self.closed:
            return
        visible, can, tip = self.available()
        for view in (self.widget, self.tray):
            view.set_suggest_state(visible, can, tip, False)
        self.card.set_can_request(can)

    # ---------------------------------------------------------------- pedir
    @property
    def nudge(self) -> str:
        return "; ".join([*self.chips, *([self.free] if self.free else [])])

    def request_from_outside(self, *_args: object) -> None:
        """O botão "Sugerir" (widget e bandeja), o atalho e o menu da bandeja do sistema: a bandeja fechada volta e a
        recolhida restaura antes do pedido; indisponível, o motivo num aviso."""
        visible, can, tip = self.available()
        if not visible or not can:
            self.on_notice(tip or "As sugestões estão desligadas nesta gravação.")
            return
        if self.tray.isHidden():
            self.tray.show_again()
            self.widget.set_captions_shown(True)
        if self.tray.collapsed:
            self.tray.restore()
        self.request(self.card.mode)

    def request(self, mode: object = None) -> str:
        """Um pedido (o cartão: "Nova sugestão" e "Tentar de novo"); recusado com outro em voo ou indisponível."""
        _visible, can, tip = self.available()
        if self.task is not None:
            return IN_FLIGHT
        if not can:
            return tip
        self.mode = mode if isinstance(mode, SuggestMode) else self.mode
        store, mid, mode_now, nudge = self.store, self.meeting_id, self.mode, self.nudge
        t_ms = round(self.elapsed() * 1000)
        options_n = int(store.get(SPECS["suggest.options"]))
        no_news = self.last_end_ms if self.answered_until and self.newest_caption <= self.answered_until else None
        self.tray.show_card()                     # antes do `begin`: a bandeja baixa cresce do lugar certo
        self.card.begin(mode_now, datetime.now().astimezone().strftime("%H:%M"), self.last_end_ms, no_news)

        def job(_emit: Callable[[str], None], cancelled: Callable[[], bool], report: Callable[[Any], None]) -> SuggestResult:
            return run_suggest(store, mid, mode=mode_now, t_ms=t_ms, nudge=nudge, options_n=options_n, dossier=None, on_option=report,
                               cancelled=cancelled)

        self.task = run_stream(job, store, self._chunk, self._done, on_progress=self._option)
        self.refresh()
        log.info("reunião %s: sugestão pedida (%s)", mid, mode_now)
        return "pedindo…"

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()

    def _chunk(self, _text: str) -> None:
        """O texto cru não vai à tela: cada opção chega inteira pelo `progress`."""

    def _option(self, option: object) -> None:
        if not self.closed and isinstance(option, SuggestOption):
            self.card.add_option(option)

    def _done(self, result: object) -> None:
        self.task = None
        if self.closed:
            return
        if isinstance(result, BaseException):
            log.warning("reunião %s: sugestão falhou: %s", self.meeting_id, type(result).__name__)
            self.card.fail(f"{type(result).__name__}: {result}", retry=True)
        elif isinstance(result, SuggestResult):
            self._outcome(result)
        self.refresh()

    def _outcome(self, r: SuggestResult) -> None:
        match r.outcome:
            case Outcome.DONE:
                self.answered_until = max(self.answered_until, r.until_caption_id)
                self.card.set_title(datetime.now().astimezone().strftime("%H:%M"), r.t_until_ms or self.last_end_ms)
                self.card.finish(r, r.option_ids, self.adjusted())
            case Outcome.CANCELLED:
                self.card.cancelled()
            case Outcome.NO_PROVIDER:
                self.keyed = False
                self.card.fail(r.error or NO_KEY_TIP, retry=False)
            case Outcome.GONE:
                self.card.fail("a reunião não existe mais", retry=False)
            case _:
                self.card.fail(r.error or "falha", retry=True)

    # ---------------------------------------------------------------- avaliar e direcionar
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
        text = self._ask("Como sugerir", self.free, "como a IA deve sugerir (Enter salva, Esc cancela)")
        if text is not None:
            self.free = text
            self.card.set_nudge(self.nudge)

    def edit_goal(self) -> None:
        meeting = self.store.get_meeting(self.meeting_id) or {}
        text = self._ask("Objetivo", str(meeting.get("goal") or ""), "meu objetivo nesta reunião (Enter salva, Esc cancela)")
        if text is not None and self.store.meetings.set_goal(self.meeting_id, text or None):
            log.info("reunião %s: objetivo %s", self.meeting_id, "trocado" if text else "apagado")

    def _ask(self, title: str, text: str, placeholder: str) -> str | None:
        popup = SuggestTextPopup(title, text, placeholder, self.platform, self.tray)
        try:
            return popup.value() if popup.exec() == SuggestTextPopup.DialogCode.Accepted else None
        finally:
            popup.deleteLater()

    def show_fact(self, caption_id: int) -> None:
        if self.tray.collapsed:
            self.tray.restore()
        self.tray.chat.scroll_to_caption(caption_id)

    # ---------------------------------------------------------------- a gravação
    def on_caption(self, msg: object) -> None:
        if not isinstance(msg, dict):
            return
        self.newest_caption = max(self.newest_caption, int(msg.get("id") or 0))
        end = msg.get("t_end_ms")
        if isinstance(end, int):
            self.last_end_ms = max(self.last_end_ms or 0, end)

    def set_live_state(self, state: str) -> None:
        if state != self.live_state:
            self.live_state = state
            self.refresh()

    def on_stopped(self, discarded: bool = False) -> None:
        """Parar ou Descartar: sem pedir de novo; no descarte, a chamada em voo é cancelada (a reunião vai ser apagada)."""
        if discarded:
            self.cancel()
        self.finished = True
        self.card.set_finished()
        self.refresh()

    def teardown(self) -> None:
        """O fim da gravação ou a saída do app, antes de o widget e a bandeja irem embora: o cartão sai da bandeja. A chamada em
        voo termina sozinha e grava (Parar) ou a saída a cancela e espera (`_stop_windows`)."""
        self.closed = True
        self.tray.set_card(None)
        self.card.deleteLater()
        self.deleteLater()


def _usd(value: float) -> str:
    return f"US$ {value:.3f}".replace(".", ",") if value < 0.01 else f"US$ {value:.2f}".replace(".", ",")


__all__ = ["ENDED", "IN_FLIGHT", "LIVE_OK", "NO_LIVE", "SuggestHost"]
