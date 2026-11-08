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
- O dossiê (U-161) numa `Task` ao começar, com `suggest.dossier`; o teto de custo é o do resumo parcial, partilhado (A-13):
  quem bater nele para os dois (`on_budget` e `set_budget_stopped`); "falaram com você" (U-160) destaca o botão por 20 s
  quando o primeiro nome de quem pede aparece numa legenda do R, sem banco e sem LLM; ao parar, com `suggest.learn` e 10
  avaliações novas, o memorando numa `Task` (A-19; o descarte e a saída do app não o pedem).
Sinais ligados a métodos (R-06). O log leva ids, contagens e estados; nunca o texto da sugestão, da linha nem do objetivo."""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer

from ..config.defaults import SPECS
from ..llm import suggest_task
from ..llm.costs import fmt_usd
from ..llm.jobconfig import LlmJobConfig
from ..llm.suggest_compact import first_name, mentions
from ..llm.suggest_task import (MemoResult, Outcome, ScreenSent, SuggestOption, SuggestResult, dossier_text, memo_due, run_memo,
                                run_suggest)
from ..log import get_logger
from ..model import SuggestMode
from .llm_ask import NO_KEY_TIP, has_key
from .suggest_card import SuggestCard
from .suggest_screen import ScreenFlow
from .suggest_steer import GOAL_ASK, NUDGE_ASK, SteerMixin
from .tasks import StreamTask, Task, run_stream, run_task
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
HINT_MS = 20_000                                  # U-160: o destaque do "falaram com você"


class SuggestHost(SteerMixin, QObject):
    def __init__(self, store: Store, platform: Platform, widget: RecordingWidget, tray: CaptionTray, meeting_id: int, *, active: bool,
                 live_state: str, elapsed: Callable[[], float], on_notice: Callable[[str], Any],
                 on_budget: Callable[[str], Any] | None = None, recording: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store, self.platform, self.widget, self.tray, self.meeting_id = store, platform, widget, tray, meeting_id
        self.active = active                      # U-163: a caixa "Sugestões" desta gravação
        self.live_state = live_state
        self.elapsed = elapsed                    # o tempo da gravação em segundos (o relógio do gravador)
        self.recording = recording                # F5.21 (A-05): a fonte atual da gravação, lida a cada pedido com a tela
        self.on_notice = on_notice
        self.on_budget = on_budget                # A-13: o teto partilhado; o controlador para o resumo parcial junto
        self.budget_stopped: str | None = None
        self.dossier: str | None = None
        self.dossier_task: Task | None = None
        self.hint = self.name_hint = False
        me = store.get_me()
        self.first = first_name(str(me["name"])) if me else ""
        self.hint_timer = QTimer(self)
        self.hint_timer.setSingleShot(True)
        self.hint_timer.setInterval(HINT_MS)
        self.hint_timer.timeout.connect(self._hint_off)
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
                             (self.card.fact_clicked, self.show_fact), (self.card.plain_requested, self.request_plain)):
            signal.connect(slot)
        self.screen = ScreenFlow(self)            # F5.21: a tela (a chave, a escolha, a confirmação, a miniatura)
        tray.set_card(self.card)
        tray.suggest_requested.connect(self.request_from_outside)
        widget.suggest_clicked.connect(self.request_from_outside)
        self.reload()
        if self.active and self.enabled and bool(store.get(SPECS["suggest.dossier"])):         # U-161: uma leitura, sem LLM
            self.dossier_task = run_task(functools.partial(dossier_text, store, meeting_id), store, self._dossier_done)

    # ---------------------------------------------------------------- estado do botão
    def reload(self) -> None:
        """As configurações e a chave relidas (o começo da gravação e o fechar das Configurações: vale em uso, CP8)."""
        try:
            self.enabled = bool(self.store.get(SPECS["suggest.enabled"]))
            self.name_hint = bool(self.store.get(SPECS["suggest.name_hint"]))
            self.keyed = has_key(self.store)
            self.screen.reload()
            self.cost_tip = self._cost_tip()
        except Exception as e:                    # o banco ocupado: o estado de antes fica
            log.debug("sugestões: configurações não relidas: %s", e)
        self.refresh()

    def _cost_tip(self) -> str:
        """F5.21: com a chave "Com a tela" ligada, a imagem entra na estimativa (A-13) e a dica diz "com a tela"."""
        images = int(self.screen.on)
        cfg = LlmJobConfig.from_store(self.store)
        cost = suggest_task.estimate_usd(self.store, cfg, EST_IN, EST_OUT, images) if self.keyed else None
        shortcut = shortcut_text(self.store.get_setting("hotkeys.suggest") or "")
        each = f"· ~{fmt_usd(cost)} por pedido{' com a tela' if images else ''}"
        return " ".join([TIP] + ([f"({shortcut})"] if shortcut else []) + ([each] if cost is not None else []))

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
        if self.budget_stopped:
            return True, False, f"{self.budget_stopped[:1].upper()}{self.budget_stopped[1:]}."
        if self.task is not None:
            return True, False, f"{IN_FLIGHT[:1].upper()}{IN_FLIGHT[1:]}."
        return True, True, self.cost_tip

    def refresh(self) -> None:
        if self.closed:
            return
        visible, can, tip = self.available()
        for view in (self.widget, self.tray):
            view.set_suggest_state(visible, can, tip, self.hint and visible)
        self.card.set_can_request(can)

    # ---------------------------------------------------------------- pedir
    @property
    def nudge(self) -> str:
        return "; ".join([*self.chips, *([self.free] if self.free else [])])

    def request_from_outside(self, *_args: object, screen: bool = False) -> None:
        """O botão "Sugerir" (widget e bandeja), o atalho e o menu da bandeja do sistema: a bandeja fechada volta e a
        recolhida restaura antes do pedido; indisponível, o motivo num aviso. Com `suggest.enabled` desligada, o atalho fica
        mudo (Escopo 12; R-05 da revisão do L2): o botão e o item do menu já somem, e só o log fica. F5.21: `screen` é o
        atalho "Sugerir com a tela" (um pedido); sem o recurso da tela, ele fica mudo também (A-29)."""
        if not self.enabled or (screen and not self.screen.enabled):
            log.info("sugestão pedida com as sugestões ou a tela desligadas: nada a fazer")
            return
        visible, can, tip = self.available()
        if not visible or not can:
            self.on_notice(tip or "As sugestões estão desligadas nesta gravação.")
            return
        if self.tray.isHidden():
            self.tray.show_again()
            self.widget.set_captions_shown(True)
        if self.tray.collapsed:
            self.tray.restore()
        self.request(self.card.mode, screen=True if screen else None)

    def request_plain(self, mode: object = None) -> str:
        """F5.21 (A-18): "Tentar sem a tela", depois de o pedido com a tela falhar."""
        return self.request(mode, screen=False)

    def request(self, mode: object = None, screen: bool | None = None) -> str:
        """Um pedido (o cartão: "Nova sugestão" e "Tentar de novo"); recusado com outro em voo ou indisponível. F5.21: `screen`
        nulo segue a chave "Com a tela"; com a tela e o provedor ainda sem a confirmação, o cartão pergunta e o pedido espera."""
        _visible, can, tip = self.available()
        if self.task is not None:
            return IN_FLIGHT
        if not can:
            return tip
        self.mode = mode if isinstance(mode, SuggestMode) else self.mode
        with_screen = self.screen.enabled and (self.screen.on if screen is None else screen)
        if with_screen and self.screen.needs_consent(self.mode):
            return "confirme a tela"
        self.screen.drop_pending()                # R-05: o pedido que começa tira a pergunta da vista
        capture = self.screen.capturer() if with_screen else None
        clicked = time.monotonic()                # A-19: o tempo conta do clique, com o print e o preparo
        store, mid, mode_now, nudge, dossier = self.store, self.meeting_id, self.mode, self.nudge, self.dossier
        t_ms = round(self.elapsed() * 1000)
        options_n = int(store.get(SPECS["suggest.options"]))
        no_news = self.last_end_ms if self.answered_until and self.newest_caption <= self.answered_until else None
        self.tray.show_card()                     # antes do `begin`: a bandeja baixa cresce do lugar certo
        self.card.begin(mode_now, datetime.now().astimezone().strftime("%H:%M"), self.last_end_ms, no_news)

        def job(_emit: Callable[[str], None], cancelled: Callable[[], bool], report: Callable[[Any], None]) -> SuggestResult:
            shot = capture() if capture is not None else None                  # o alvo, o print e o preparo, aqui na tarefa
            return run_suggest(store, mid, mode=mode_now, t_ms=t_ms, nudge=nudge, options_n=options_n, dossier=dossier, on_option=report,
                               cancelled=cancelled, screen=shot, on_screen=report, clicked_at=clicked)

        self.task = run_stream(job, store, self._chunk, self._done, on_progress=self._option)
        self.refresh()
        log.info("reunião %s: sugestão pedida (%s%s)", mid, mode_now, ", com a tela" if capture is not None else "")
        return "pedindo…"

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()

    def _chunk(self, _text: str) -> None:
        """O texto cru não vai à tela: cada opção chega inteira pelo `progress`."""

    def _option(self, option: object) -> None:
        if self.closed:
            return
        if isinstance(option, SuggestOption):
            self.card.add_option(option)
        elif isinstance(option, ScreenSent):                # F5.21 (A-33): a miniatura no chat, antes das opções
            self.screen.sent(option)

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
                self.card.keep(r, r.option_ids)
                self.card.cancelled()
            case Outcome.BUDGET:
                text = r.error or "sugestões paradas nesta reunião: o custo da IA chegou à metade do alerta"
                self.set_budget_stopped(text)
                if self.on_budget is not None:
                    self.on_budget(text)
                self.on_notice(f"{text[:1].upper()}{text[1:]}.")
                self.card.stopped(text)
            case Outcome.NO_PROVIDER:
                self.keyed = False
                self.card.fail(r.error or NO_KEY_TIP, retry=False)
            case Outcome.GONE:
                self.card.fail("a reunião não existe mais", retry=False)
            case _:
                self.card.keep(r, r.option_ids)
                self.card.fail(r.error or "falha", retry=True, plain_retry=r.with_screen)

    # ---------------------------------------------------------------- a gravação
    def on_caption(self, msg: object) -> None:
        if not isinstance(msg, dict):
            return
        self.newest_caption = max(self.newest_caption, int(msg.get("id") or 0))
        end = msg.get("t_end_ms")
        if isinstance(end, int):
            self.last_end_ms = max(self.last_end_ms or 0, end)
        if self.name_hint and msg.get("channel") == "R" and mentions(str(msg.get("text") or ""), self.first):   # U-160
            self.hint = True
            self.hint_timer.start()
            self.refresh()

    def _hint_off(self) -> None:
        self.hint = False
        self.refresh()

    def _dossier_done(self, result: object) -> None:
        self.dossier_task = None
        if isinstance(result, str):
            self.dossier = result or None
            log.info("reunião %s: dossiê das sugestões com %d caracteres", self.meeting_id, len(result))
        else:
            log.warning("reunião %s: dossiê das sugestões não montado: %s", self.meeting_id, type(result).__name__)

    def set_budget_stopped(self, text: str) -> None:
        """A-13: o teto partilhado com o resumo parcial (a sugestão ou o parcial bateu nele): as sugestões param nesta reunião."""
        self.budget_stopped = text
        self.refresh()

    def set_live_state(self, state: str) -> None:
        if state != self.live_state:
            self.live_state = state
            self.refresh()

    def on_stopped(self, discarded: bool = False) -> None:
        """Parar ou Descartar: sem pedir de novo; no descarte, a chamada em voo é cancelada (a reunião vai ser apagada). O
        memorando é decidido com o banco protegido: o Parar roda isto antes de parar o gravador, e um banco ocupado não pode
        travá-lo (R-02 da revisão do L3); sem a leitura, o memorando fica para o próximo Parar."""
        if discarded:
            self.cancel()
        self.finished = True
        self.card.set_finished()
        self.screen.drop_pending()
        self.refresh()
        if discarded or not self.enabled:
            return
        try:
            due = bool(self.store.get(SPECS["suggest.learn"])) and memo_due(self.store)
        except Exception as e:
            log.warning("memorando das sugestões não decidido: %s", type(e).__name__)
            return
        if due:
            cancel = threading.Event()               # A-19: a saída do app o cancela e espera (`_stop_windows`)
            run_task(functools.partial(run_memo, self.store, cancelled=cancel.is_set), self.store, _memo_done, cancel=cancel)
            log.info("memorando das sugestões pedido")

    def teardown(self) -> None:
        """O fim da gravação ou a saída do app, antes de o widget e a bandeja irem embora: o cartão sai da bandeja. A chamada em
        voo termina sozinha e grava (Parar) ou a saída a cancela e espera (`_stop_windows`)."""
        self.closed = True
        self.hint_timer.stop()
        self.screen.teardown()
        self.tray.set_card(None)
        self.card.deleteLater()
        self.deleteLater()


def _memo_done(result: object) -> None:
    """Fora do host: o memorando termina depois de o host ir embora (a gravação parou). Só o log, sem o texto."""
    if isinstance(result, MemoResult):
        log.info("memorando das sugestões: %s (%d avaliações)", result.outcome, result.ratings_used)
    else:
        log.warning("memorando das sugestões falhou: %s", type(result).__name__)


__all__ = ["ENDED", "GOAL_ASK", "IN_FLIGHT", "LIVE_OK", "NO_LIVE", "NUDGE_ASK", "SuggestHost"]
