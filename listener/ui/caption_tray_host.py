"""F5.17: o fio da bandeja de legendas com o controlador e o widget de gravação, no padrão do `RecordingDevices`, para o
`ui/controller.py` não crescer. A bandeja nasce filha do widget (some no `deleteLater` dele, sem ciclo pelo C++, R-06),
logo depois de ele aparecer, com a posição, o encosto e o recolhido da última gravação, e lê as legendas que já houver.

- O botão "Legendas" do widget mostra e esconde a bandeja; o "Fechar" dela o deixa no balão riscado (pergunta 6).
- Encostada, acompanha o widget: um filtro de eventos nele (só o `Move`) e o `height_changed` (o alerta que o faz crescer
  para cima), com a posição de uma função pura, na hora. O filtro sai antes de o widget ser apagado (`teardown`): um filtro
  em Python chamado no meio da destruição pode derrubar o processo.
- O controlador repassa a legenda, o estado do ao vivo, o fim da gravação e as bandeiras; a bandeja não conhece o gravador.
- F5.20: o host das sugestões ao vivo (`ui/suggest_host.py`) nasce aqui, porque o cartão mora na bandeja, e recebe pelo mesmo
  fio a legenda, o estado do ao vivo, o fim (o descarte cancela a chamada em voo) e a saída; o controlador só lhe passa o
  atalho, o "Sugerir…" da bandeja do sistema e o fechar das Configurações. Sem a bandeja, o atalho cai em `suggest_outside`:
  com `suggest.enabled` desligada, mudo (Escopo 12)."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

from ..config.defaults import SPECS
from ..log import get_logger
from .caption_geometry import Rect
from .caption_tray import CaptionTray
from .live_text import MarkCaption
from .suggest_host import SuggestHost

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store
    from .widget import RecordingWidget

log = get_logger("ui.captions")
NO_TRAY = "As sugestões só saem durante a gravação, com a legenda ao vivo e a bandeja de legendas."


def suggest_outside(store: Store, notify: Callable[[str], Any], screen: bool = False) -> None:
    """F5.20: o atalho e o "Sugerir…" sem o host (fora da gravação, sem a bandeja ou sem a legenda): o aviso, ou nada com as
    sugestões desligadas nas Configurações (Escopo 12: o atalho fica mudo; R-05 da revisão do L2). F5.21: o atalho da tela
    fica mudo também com `suggest.screen` desligada (R-09 da revisão do L2 da F5.21)."""
    try:
        on = bool(store.get(SPECS["suggest.enabled"])) and (not screen or bool(store.get(SPECS["suggest.screen"])))
    except Exception:                                 # o banco ocupado: o aviso, que não custa nada
        on = True
    if on:
        notify(NO_TRAY)
    else:
        log.info("sugestão pedida com as sugestões desligadas nas Configurações: nada a fazer")


def widget_rect(widget: QWidget) -> Rect:
    """O retângulo do widget de gravação, pela moldura (o encosto da bandeja)."""
    g = widget.frameGeometry()
    return g.x(), g.y(), g.width(), g.height()


class CaptionTrayHost(QObject):
    def __init__(self, store: Store, platform: Platform, widget: RecordingWidget, meeting_id: int, *, on_mark: MarkCaption,
                 on_open_live: Callable[[], Any], on_notice: Callable[[str], Any], marked: set[tuple[Any, ...]],
                 speakers: dict[str, int], live_state: str, suggest: bool = False, elapsed: Callable[[], float] | None = None,
                 on_info: Callable[[str], Any] | None = None, on_budget: Callable[[str], Any] | None = None,
                 recording: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.widget = widget
        self.tray = CaptionTray(store, meeting_id, parent=widget, platform=platform, anchor=functools.partial(widget_rect, widget),
                                on_mark=on_mark, on_notice=on_notice, marked=marked, speakers=speakers)
        self.tray.closed_by_user.connect(self._closed)
        self.tray.open_live_requested.connect(on_open_live)
        widget.captions_clicked.connect(self.toggle)
        widget.height_changed.connect(self.tray.follow)
        widget.installEventFilter(self)
        self.tray.restore_geometry()
        self.tray.set_live_state(live_state)
        self.tray.show()
        self.tray.load_new()
        widget.set_captions_shown(True)
        log.info("bandeja de legendas aberta%s", " (recolhida)" if self.tray.collapsed else "")
        clock = recording.elapsed if recording is not None else elapsed            # F5.21 (A-05): a gravação, com a fonte atual
        self.suggest = SuggestHost(store, platform, widget, self.tray, meeting_id, active=suggest, live_state=live_state, elapsed=clock,
                                   on_notice=on_info or on_notice, on_budget=on_budget, recording=recording,
                                   parent=self) if clock is not None else None

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.widget and event.type() == QEvent.Type.Move:
            self.tray.follow()                        # encostada, acompanha o widget arrastado
        return False

    def toggle(self) -> None:
        """O botão "Legendas": esconde a bandeja à vista, ou a traz de volta com o que chegou enquanto estava fechada."""
        if self.tray.isVisible():
            self.tray.close_by_user()
            return
        self.tray.show_again()
        self.widget.set_captions_shown(True)
        log.info("bandeja de legendas de volta")

    def _closed(self) -> None:
        self.widget.set_captions_shown(False)

    def on_caption(self, msg: object) -> None:
        self.tray.on_caption(msg)
        if self.suggest is not None:
            self.suggest.on_caption(msg)

    def set_live_state(self, state: str) -> None:
        self.tray.set_live_state(state)
        if self.suggest is not None:
            self.suggest.set_live_state(state)

    def set_finished(self, discarded: bool = False) -> None:
        self.tray.set_finished()
        if self.suggest is not None:
            self.suggest.on_stopped(discarded)

    def request_suggestion(self, screen: bool = False) -> None:
        """F5.20: o atalho e o "Sugerir…" da bandeja do sistema; F5.21: `screen`, o "Sugerir com a tela"."""
        if self.suggest is not None:
            self.suggest.request_from_outside(screen=screen)

    def stop_suggestions(self, text: str) -> None:
        """F5.20 (A-13): o resumo parcial bateu no teto partilhado: as sugestões param junto."""
        if self.suggest is not None:
            self.suggest.set_budget_stopped(text)

    def settings_changed(self) -> None:
        """F5.20: o fechar das Configurações (`suggest.enabled` e a chave valem em uso)."""
        if self.suggest is not None:
            self.suggest.reload()

    def refresh_mark(self, key: tuple[Any, ...]) -> None:
        self.tray.refresh_mark(key)

    def teardown(self) -> None:
        """O fim da gravação ou a saída do app, antes de o widget e o banco irem embora: o filtro sai, a posição fica."""
        self.widget.removeEventFilter(self)
        if self.suggest is not None:                  # F5.20: o cartão sai antes da bandeja
            self.suggest.teardown()
            self.suggest = None
        self.tray.save_geometry()
        self.tray.close()
        self.tray.deleteLater()
        self.deleteLater()


__all__ = ["NO_TRAY", "CaptionTrayHost", "suggest_outside", "widget_rect"]
