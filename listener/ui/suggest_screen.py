"""F5.21: a tela no host das sugestões (`ScreenFlow`), fora do `ui/suggest_host.py` para ele seguir abaixo de 400 linhas
(A-27).

- O estado da chave (`reload`): à vista com `suggest.screen`; apagada fora do Windows (a plataforma sem monitores, A-41) ou
  com o modelo rápido sem imagem (pela tabela, A-12), com o motivo na dica. Lido no começo e no fechar das Configurações, não
  a cada legenda.
- A chave nasce desligada em cada gravação (A-31); a escolha rápida da seta (o automático, o monitor em uso ou um monitor) fica
  na memória desta gravação (A-25), e a das Configurações vale de novo na seguinte.
- A confirmação da primeira vez com o provedor (U-168, A-25, A-31): um pedido pendente por vez, o novo no lugar do anterior;
  o cartão recolhido abre para ela; "Mandar" grava o provedor com a data em `suggest.screen_consent` e pede com a tela (o
  print sai naquele momento); "Agora não" desliga a chave e pede sem ela. Um pedido que começa sem ela e o Parar a tiram da
  vista (R-05 da revisão do L2).
- O print (`capturer`): uma função para a tarefa, com o que se lê agora na thread da interface (a fonte atual da gravação,
  A-05, com o `fallback_exe`; a escolha; a lista dos apps; guardar a cópia); o alvo, o print e o preparo rodam na tarefa
  (A-02, D-103).
- A tela enviada (`sent`): a miniatura no chat (sem a imagem se a bandeja não saiu da captura, pergunta 15), a linha no
  cartão (com a nota do alvo: o monitor fixo que sumiu, a reunião coberta) e a imagem na memória para a janela maior (as 10
  últimas), decodificada e escalada numa `Task` no clique (R-07).
O log leva o estado, as dimensões e a fonte; nunca a imagem nem o título."""
from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRect
from PySide6.QtGui import QImage

from ..config.defaults import SPECS
from ..llm.jobconfig import image_capable
from ..llm.suggest_ctx import ScreenSent, ScreenShot
from ..log import get_logger
from ..model import ScreenStatus, SuggestMode
from .caption_chat import AltProofMenu
from .screen_grab import AUTO, COVERED_NOTE, CURSOR, MONITOR, capture
from .screen_view import ScreenView, fitted
from .settings_search import PROVIDER_NAMES
from .tasks import Task, run_task
from .widget_parts import shortcut_text

if TYPE_CHECKING:
    from .suggest_host import SuggestHost

log = get_logger("ui.suggest")

ONLY_WINDOWS = "A tela nas sugestões, por enquanto, só no Windows."
NO_IMAGE_MODEL = "O modelo rápido não recebe imagem: troque-o nas Configurações (LLM)."
EMPTY_NOTE = "a tela veio preta (conteúdo protegido?): só as legendas"
FAILED_NOTE = "a tela não foi capturada: só as legendas"
KEEP_IMAGES = 10


def screen_items(monitors: Any, current: str) -> list[tuple[str, str]]:
    """As escolhas da tela (o menu da seta e a página Ao vivo): o automático, o monitor em uso e cada monitor ligado; o fixo
    que sumiu fica na lista, marcado (A-30: ele cai no automático, com a nota no cartão)."""
    items = [("Automático: a janela da reunião", AUTO), ("O monitor em uso", CURSOR)]
    items += [(m.label, f"{MONITOR}{m.key}") for m in monitors]
    if current.startswith(MONITOR) and all(value != current for _t, value in items):
        items.append(("Monitor desligado (vale o automático)", current))
    return items


class ScreenFlow(QObject):
    def __init__(self, host: SuggestHost) -> None:
        super().__init__(host)
        self.host = host
        self.on = False
        self.pick: str | None = None
        self.pending: SuggestMode | None = None
        self.key: int | None = None                 # o tempo do pedido da última tela (a linha do cartão rola até ela)
        self.images: dict[int, bytes] = {}
        self.view: ScreenView | None = None
        self.view_task: Task | None = None
        self.view_near: QRect | None = None
        self.visible = self.enabled = False
        self.tip = ""
        row = host.card.screen_row
        row.toggled.connect(self.set_on)
        row.pick_requested.connect(self.menu)
        row.consent_answered.connect(self.answer)
        row.line_clicked.connect(self.show_in_chat)
        host.card.screen_clicked.connect(self.show_in_chat)
        host.tray.chat.screen_clicked.connect(self.open_view)

    # ---------------------------------------------------------------- a chave
    def reload(self) -> None:
        store = self.host.store
        self.visible = bool(store.get(SPECS["suggest.screen"]))
        self.tip = ("" if not self.visible else ONLY_WINDOWS if not self.host.platform.monitors()
                    else NO_IMAGE_MODEL if not image_capable(store) else "")
        self.enabled = self.visible and not self.tip
        if not self.enabled:
            self.on = False
        shortcut = shortcut_text(store.get_setting("hotkeys.suggest_screen") or "")
        self.host.card.screen_row.set_state(self.visible and self.host.enabled and self.host.active,
                                            self.enabled and not self.host.finished, self.tip, shortcut)   # R-05, R-09
        self.host.card.screen_row.set_on(self.on)

    def set_on(self, on: bool) -> None:
        self.on = bool(on) and self.enabled
        self.host.card.screen_row.set_on(self.on)
        log.info("tela nas sugestões %s nesta gravação", "ligada" if self.on else "desligada")
        with suppress(Exception):
            self.host.cost_tip = self.host._cost_tip()
        self.host.refresh()

    def source(self) -> str:
        return self.pick if self.pick is not None else str(self.host.store.get(SPECS["suggest.screen_source"]))

    def menu(self) -> None:
        """A-28: o menu numa janela que não ativa pede a frente à plataforma, e o Alt injetado não o fecha."""
        menu = AltProofMenu(self.host.tray)
        current = self.source()
        for text, value in screen_items(self.host.platform.monitors(), current):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(functools.partial(self._picked, value))
        menu.aboutToHide.connect(menu.deleteLater)
        pick = self.host.card.screen_row.pick
        menu.popup(pick.mapToGlobal(pick.rect().bottomLeft()))
        with suppress(Exception):
            self.host.platform.bring_to_front(int(menu.winId()))

    def _picked(self, value: str, *_args: object) -> None:
        self.pick = value
        log.info("tela desta gravação trocada (%s)", value.split(":", 1)[0])

    # ---------------------------------------------------------------- a confirmação da primeira vez
    def needs_consent(self, mode: SuggestMode) -> bool:
        """True quando o provedor ativo ainda não recebeu a tela: o cartão pergunta, e o pedido espera (um por vez)."""
        row = self.host.store.providers.active_with_key("llm")
        provider = str(row["id"]) if row is not None else ""
        given = {p.split(":", 1)[0] for p in str(self.host.store.get(SPECS["suggest.screen_consent"])).split(";") if p}
        if not provider or provider in given:
            return False
        self.pending = mode
        self.host.tray.show_card()
        self.host.card.show()
        self.host.card.set_collapsed(False)                 # R-05: recolhido, a pergunta ficava no corpo escondido
        self.host.card.screen_row.ask(PROVIDER_NAMES.get(provider, provider))
        return True

    def drop_pending(self) -> None:
        """Um pedido que começa (sem a tela, ou já confirmado) e o Parar: a pergunta pendente sai da vista (R-05)."""
        self.pending = None
        self.host.card.screen_row.ask(None)

    def answer(self, ok: bool) -> None:
        mode, self.pending = self.pending, None
        if mode is None:
            return
        if ok:
            row = self.host.store.providers.active_with_key("llm")
            provider = str(row["id"]) if row is not None else ""
            given = [p for p in str(self.host.store.get(SPECS["suggest.screen_consent"])).split(";") if p]
            stamp_day = datetime.now().astimezone().date().isoformat()
            self.host.store.set_setting("suggest.screen_consent", ";".join([*given, f"{provider}:{stamp_day}"]))
            log.info("tela confirmada para o provedor %s", provider)
        else:
            self.set_on(False)
        self.host.request(mode, screen=ok)

    # ---------------------------------------------------------------- o print e a tela enviada
    def capturer(self) -> Callable[[], ScreenShot]:
        rec = self.host.recording
        src = getattr(rec, "source", None)
        pid = getattr(src, "pid", None)
        exe, fallback = str(getattr(src, "exe", "") or ""), str(getattr(src, "fallback_exe", "") or "")
        platform, store = self.host.platform, self.host.store
        return functools.partial(capture, platform, self.source(), pid, exe, platform.watched_apps(),
                                 bool(store.get(SPECS["suggest.screen_keep"])), fallback)

    def sent(self, s: ScreenSent) -> None:
        shot = s.shot
        self.key = s.t_ms
        ok = shot.status == ScreenStatus.SENT
        image = QImage.fromData(shot.thumb) if shot.thumb and self.host.tray.excluded is not False else None
        covered = shot.note == COVERED_NOTE
        text = (f"Tela enviada · {shot.app}" if ok else ("Tela coberta" if covered else "Tela preta") + " · não enviada"
                if shot.status == ScreenStatus.EMPTY else "Tela não capturada")
        self.host.tray.chat.add_screen(s.t_ms, image, text, ok)
        line = (f"com a tela · {shot.app}" if ok else shot.note if covered else EMPTY_NOTE if shot.status == ScreenStatus.EMPTY
                else FAILED_NOTE)
        self.host.card.screen_row.show_line(f"{line} ({shot.note})" if shot.note and not covered else line)   # R-08: A-30
        if ok and shot.image is not None:
            self.images[s.t_ms] = shot.copy or shot.image.data
            for old in sorted(self.images)[:-KEEP_IMAGES]:
                del self.images[old]

    def show_in_chat(self, *_args: object) -> None:
        if self.key is None:
            return
        if self.host.tray.collapsed:
            self.host.tray.restore()
        self.host.tray.chat.scroll_to_screen(self.key)

    def open_view(self, key: int) -> None:
        """R-07: a imagem decodificada e escalada numa `Task`; na volta, só o pixmap nasce na thread da interface."""
        data = self.images.get(key)
        if data is None or self.view_task is not None:
            return
        self.view_near = self.host.tray.frameGeometry()
        size = ScreenView.fit_size(self.view_near)
        self.view_task = run_task(functools.partial(fitted, data, size.width(), size.height()), None, self._view_ready)

    def _view_ready(self, image: object) -> None:
        self.view_task = None
        if self.host.closed or not isinstance(image, QImage) or image.isNull():
            return
        if self.view is not None:
            with suppress(RuntimeError):
                self.view.close()
        self.view = ScreenView(image, "Tela enviada", self.host.platform, self.view_near)
        self.view.show()

    def teardown(self) -> None:
        if self.view is not None:
            with suppress(RuntimeError):
                self.view.close()
        self.view = None
        self.images.clear()


__all__ = ["EMPTY_NOTE", "FAILED_NOTE", "NO_IMAGE_MODEL", "ONLY_WINDOWS", "ScreenFlow", "screen_items"]
