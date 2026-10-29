"""F5.7 (L3, U-094): as respostas do aviso de reunião sem gravação fora do cartão, parte da cola (`ui/call_watch.py`, que
passava de 400 linhas): o "Antes de gravar" com o app como fonte (pelo "Gravar…" do cartão, pela bandeja e pelo atalho), a
chamada que a bandeja e o atalho escolhem e o "Não avisar para o X". O app nunca grava sozinho (D-010, CP6).

- O "Antes de gravar" é do controlador (`open_warmup`, modal). A cola pega o diálogo aberto no giro seguinte do laço e lê o
  `result()` dele quando o `open_warmup` volta: gravando, a borda do `busy` cobre a chamada; aceito sem gravar (o "Gravar"
  que não começou, R-05), a chamada volta a pendente, com o lembrete único; fechado sem gravar (Esc, Cancelar), vale
  "Agora não" até a chamada acabar.
- A chamada da bandeja e do atalho (R-06): a do cartão à vista; sem cartão, o pendente avisado por último.
- "Não avisar para o X" grava o app em `detect.off` (o de reunião inteiro) e o aviso diz o que desligou pelo nome do app
  ("Chrome", nunca "Google Meet no Chrome": desliga o navegador inteiro, R-07)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from ..log import get_logger
from . import call_config as cfg
from .warmup import WarmupDialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..platform.base import WatchedApp
    from ..store import Store
    from .call_prompt import CallPrompt
    from .call_rules import CallRules, Params, Pending

log = get_logger("ui.call_watch")
OFF_NOTICE = "Aviso de reunião desligado para o {label}. Para religar: Configurações, Idioma e avisos."
CLOSED_OFF = "desligado"


class Host(Protocol):
    """O que a cola usa do controlador (os testes passam um falso). `busy`: gravando, pausado, salvando ou saindo."""
    tray: Any
    toasts: Any

    @property
    def busy(self) -> bool: ...
    def open_warmup(self, source_exe: str | None = None) -> None: ...
    def notify(self, message: str) -> None: ...


def open_warmup_dialog() -> WarmupDialog | None:
    """O "Antes de gravar" à vista (aberto pelo aviso ou à mão), ou None."""
    return next((w for w in QApplication.topLevelWidgets() if isinstance(w, WarmupDialog) and w.isVisible()), None)


class CallAnswersMixin:
    """As respostas fora do cartão, no `CallWatch`."""
    host: Host
    store: Store
    rules: CallRules
    params: Params
    watched: tuple[WatchedApp, ...]
    card: CallPrompt | None
    card_exe: str
    _names: dict[str, str]
    _recent: dict[str, float]
    _closed: bool
    _clock: Callable[[], float]
    _warmup: WarmupDialog | None = None

    def _close_card(self, why: str) -> None:
        raise NotImplementedError

    def _refresh(self) -> None:
        raise NotImplementedError

    def _check_busy(self, now: float) -> None:
        raise NotImplementedError

    def reload(self, force: bool = False) -> None:
        raise NotImplementedError

    # ---------------------------------------------------------------- "Gravar…", a bandeja e o atalho
    def open_for(self, exe: str, via: str = "bandeja") -> None:
        """Abre o "Antes de gravar" com o app como fonte (CP6: nunca grava daqui) e, quando ele fecha, responde pela chamada."""
        low = exe.lower()
        if self._closed or self.host.busy or open_warmup_dialog() is not None:
            return
        if self.card_exe == low:
            self._close_card("Gravar")
        self.rules.record(low)
        self._refresh()
        log.info("aviso de reunião (%s): \"Antes de gravar\" pelo %s", low, via)
        self._warmup = None
        QTimer.singleShot(0, self._catch_warmup)                 # já dentro do laço do diálogo modal
        self.host.open_warmup(self._names.get(low, exe))
        if not self._closed:
            self._answered(low, self._accepted())
            self._check_busy(self._clock())
            self._refresh()

    def _catch_warmup(self) -> None:
        self._warmup = open_warmup_dialog()

    def _accepted(self) -> bool:
        """O "Gravar" do "Antes de gravar" (o diálogo segue vivo até o laço voltar: o controlador só pediu o `deleteLater`)."""
        dlg, self._warmup = self._warmup, None
        return dlg is not None and shiboken6.isValid(dlg) and dlg.result() == QDialog.DialogCode.Accepted

    def _answered(self, low: str, accepted: bool) -> None:
        if self.host.busy:
            return                                   # gravando: a borda do `busy` cobre a chamada
        if accepted:
            self.rules.expired(low, self._clock())
            log.info("aviso de reunião (%s): o \"Gravar\" não começou; a chamada volta a pendente", low)
        else:
            self.rules.dismiss(low)
            log.info("aviso de reunião (%s): o \"Antes de gravar\" fechou sem gravar; dispensado até a chamada acabar", low)

    def first_pending(self) -> Pending | None:
        """R-06: a chamada do cartão à vista; sem cartão, o pendente avisado por último."""
        pending = self.rules.pending() if self.params.enabled and not self._closed else ()
        if not pending:
            return None
        return next((p for p in pending if p.exe == self.card_exe),
                    max(pending, key=lambda p: self._recent.get(p.exe, 0.0)))

    def start_from_hotkey(self) -> None:
        """`hotkeys.start`: com uma chamada pendente, o "Antes de gravar" preenchido; sem, o de sempre."""
        first = self.first_pending()
        if first is not None and not self.host.busy:
            self.open_for(first.exe, "atalho")
        else:
            self.host.open_warmup()

    def off_label(self, exe: str) -> str:
        """O nome do que "Não avisar para o X" desliga: o app inteiro ("Chrome", "Teams", "WhatsApp")."""
        return cfg.label_for(self.watched, self._names.get(exe.lower(), exe))

    def silence(self, exe: str) -> None:
        """"Não avisar para o X" (bandeja): o app vai para `detect.off`, a chamada é dispensada e um aviso diz como religar."""
        low = exe.lower()
        try:
            off = cfg.names(self.store.get_setting(cfg.OFF))
            self.store.set_setting(cfg.OFF, cfg.join((*off, *cfg.silence_names(self.watched, self._names.get(low, exe)))))
        except Exception as e:
            log.warning("aviso de reunião: o \"Não avisar\" não foi gravado (%s)", type(e).__name__)
            return
        log.info("aviso de reunião desligado para %s pela bandeja", low)
        if self.card_exe == low:
            self._close_card(CLOSED_OFF)
        self.rules.dismiss(low)
        self.reload()
        self.host.notify(OFF_NOTICE.format(label=self.off_label(exe)))

    def tray_call(self, busy: bool) -> tuple[str, str, str]:
        """(executável, rótulo, nome do que o "Não avisar" desliga) da chamada que a bandeja mostra; vazios sem nenhuma."""
        first = None if busy else self.first_pending()
        return ("", "", "") if first is None else (first.exe, first.label, self.off_label(first.exe))


__all__ = ["OFF_NOTICE", "CallAnswersMixin", "Host", "open_warmup_dialog"]
