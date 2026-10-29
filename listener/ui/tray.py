"""Ícone na bandeja com estado (ocioso, gravando, pausado, salvando) e menu; F4: "Ao vivo…" abre a janela das legendas
durante a gravação (U-021). F5: "Histórico…" abre a busca e as reuniões, e o clique no balão abre a reunião da última
notificação que tinha reunião (Q6).
F5.4 L2 (U-070, A-19): a marca do app (`icons/brand.svg`) com o estado num selo de forma e cor no canto, desenhada em cada
tamanho que o Windows pede (16 a 64 px, legível a 16): gravando, um disco vermelho; pausado, duas barras âmbar; salvando, um
anel azul; ocioso, sem selo. A barra de tarefas segue `SystemUsesLightTheme`, não o tema dos apps: o ícone tem fundo
próprio e cores fixas (a marca em `accent` com as barras em `on_accent`, o selo com um aro claro), as do tema claro, que
contrastam com o aro nos dois temas da barra. O mesmo desenho, sem selo, é o ícone das janelas.
F5.7 (L3, U-094): com uma reunião sem gravação pendente (o aviso à vista ou sumido sem clique), a dica diz qual, o primeiro
item do menu, em negrito e padrão, é "Gravar a reunião do Teams…", logo abaixo vem "Não avisar para o Teams" (com o nome
do que desliga: "Chrome", não "Google Meet no Chrome", R-07) e o clique esquerdo abre o "Antes de gravar" com o app como
fonte (a cola, `ui/call_watch.py`, liga as duas ações)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..model import AppState
from . import icons
from .common import STATE_TONES
from .theme import LIGHT, Tokens, qcolor

if TYPE_CHECKING:
    from .controller import Controller

STATE_TIPS: dict[str, str] = {AppState.IDLE: "listener-meet: pronto", AppState.RECORDING: "listener-meet: gravando",
                              AppState.PAUSED: "listener-meet: pausado", AppState.SAVING: "listener-meet: salvando…"}
TRAY_TOKENS: Tokens = LIGHT                   # A-19: cores fixas, independentes do tema do app e do da barra de tarefas
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64)     # o que o Windows pede na bandeja, na barra e no Alt+Tab, de 100 % a 200 %
BADGE = 0.56                                  # o diâmetro do selo, na fração do lado do ícone (no canto de baixo à direita)
CALL_TIP = "listener-meet: reunião no {label} sem gravação"          # F5.7 (U-094)
CALL_RECORD, CALL_OFF = "Gravar a reunião do {label}…", "Não avisar para o {label}"


def icon_color(state: str) -> str:
    """A cor do selo do estado (o ocioso, sem selo, fica no neutro)."""
    return TRAY_TOKENS.tone(STATE_TONES.get(state, STATE_TONES[AppState.IDLE]))


def _brand(p: QPainter, size: int) -> None:
    """A marca: as barras em `on_accent` por baixo (o fundo próprio, A-19) e o quadrado vazado em `accent` por cima."""
    t = TRAY_TOKENS
    inset = size * 0.2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qcolor(t.on_accent))
    p.drawRect(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    p.drawPixmap(0, 0, icons.pixmap("brand", t.accent, size))


def _badge(p: QPainter, size: int, state: str) -> None:
    """O selo do estado: um aro claro (separa o selo da marca) e, dentro, a forma na cor do estado."""
    t = TRAY_TOKENS
    d = size * BADGE
    box = QRectF(size - d, size - d, d, d)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qcolor(t.on_accent))
    p.drawEllipse(box)
    inner = box.adjusted(d * 0.16, d * 0.16, -d * 0.16, -d * 0.16)
    color = qcolor(icon_color(state))
    if state == AppState.RECORDING:
        p.setBrush(color)
        p.drawEllipse(inner)
    elif state == AppState.PAUSED:
        p.setBrush(color)
        bar = inner.width() * 0.3
        for x in (inner.left() + inner.width() * 0.12, inner.right() - inner.width() * 0.12 - bar):
            p.drawRect(QRectF(x, inner.top() + inner.height() * 0.08, bar, inner.height() * 0.84))
    else:
        width = max(1.5, inner.width() * 0.24)
        p.setPen(QPen(color, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(inner.adjusted(width / 2, width / 2, -width / 2, -width / 2))


def icon_pixmap(state: str, size: int) -> QPixmap:
    """O ícone de um estado em `size` px (DPR 1: o Windows escolhe o tamanho certo para a escala da tela)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _brand(p, size)
    if state in (AppState.RECORDING, AppState.PAUSED, AppState.SAVING):
        _badge(p, size, state)
    p.end()
    return pm


def make_icon(state: str = AppState.IDLE) -> QIcon:
    """O `QIcon` do estado com um pixmap por tamanho (o ícone das janelas é o do ocioso). Sem cache próprio: só muda com o
    estado, a marca vem do cache dos ícones (que o `theme.release` esvazia) e nada fica vivo até a derrubada do PySide (R-06)."""
    result = QIcon()
    for size in ICON_SIZES:
        result.addPixmap(icon_pixmap(state, size))
    return result


class TrayIcon(QSystemTrayIcon):
    def __init__(self, controller: Controller, parent: QObject | None = None):
        super().__init__(parent)
        self.controller = controller
        self.menu = QMenu()
        self.act_record = QAction("Gravar…", self.menu)
        self.act_pause = QAction("Pausar / retomar", self.menu)
        self.act_mark = QAction("Marcador", self.menu)
        self.act_mute = QAction("Mudo", self.menu)
        self.act_stop = QAction("Parar", self.menu)
        self.act_live = QAction("Ao vivo…", self.menu)
        self.act_history = QAction("Histórico…", self.menu)
        self.act_queue = QAction("Fila de processamento…", self.menu)
        self.act_settings = QAction("Configurações…", self.menu)
        self.act_quit = QAction("Sair", self.menu)
        self._call_actions()
        for a in (self.act_record, self.act_pause, self.act_mark, self.act_mute, self.act_stop, self.act_live):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.act_history)
        self.menu.addAction(self.act_queue)
        self.menu.addAction(self.act_settings)
        self.menu.addAction(self.act_quit)
        self.queue_tip = ""
        self._state = AppState.IDLE
        self.setContextMenu(self.menu)
        self.act_record.triggered.connect(self._open_warmup)        # método: o `triggered(bool)` não vira a fonte
        self.act_pause.triggered.connect(controller.toggle_pause)
        self.act_mark.triggered.connect(controller.mark)
        self.act_mute.triggered.connect(controller.toggle_mute)
        self.act_stop.triggered.connect(controller.stop_recording)
        self.act_live.triggered.connect(controller.open_live_window)
        self.act_history.triggered.connect(self._open_history)
        self.act_queue.triggered.connect(controller.open_queue)
        self.act_settings.triggered.connect(controller.open_settings)
        self.act_quit.triggered.connect(controller.quit)
        self.activated.connect(self._activated)
        self.messageClicked.connect(controller.open_last_notified)
        self.set_state(AppState.IDLE)

    def _open_history(self) -> None:
        self.controller.open_history()

    def _open_warmup(self) -> None:
        self.controller.open_warmup()

    # ---------------------------------------------------------------- F5.7: a reunião sem gravação
    def _call_actions(self) -> None:
        """Os dois itens da reunião pendente, no topo do menu e escondidos sem ela; o primeiro em negrito."""
        self.act_call = QAction("", self.menu)
        font = self.act_call.font()
        font.setBold(True)
        self.act_call.setFont(font)
        self.act_call_off = QAction("", self.menu)
        self.menu.addAction(self.act_call)
        self.menu.addAction(self.act_call_off)
        self.call_separator = self.menu.addSeparator()
        self.menu.setDefaultAction(self.act_call)                  # o padrão sai em negrito no menu do Windows
        for a in (self.act_call, self.act_call_off, self.call_separator):
            a.setVisible(False)
        self.act_call.triggered.connect(self._call_record)
        self.act_call_off.triggered.connect(self._call_off)
        self.call_exe = self.call_label = self.call_off_label = ""
        self._on_call_record: Callable[[str], Any] | None = None
        self._on_call_off: Callable[[str], Any] | None = None

    def set_call_handlers(self, on_record: Callable[[str], Any], on_off: Callable[[str], Any]) -> None:
        """O "Gravar a reunião do X…" (e o clique esquerdo) e o "Não avisar para o X", com o executável."""
        self._on_call_record, self._on_call_off = on_record, on_off

    def set_call(self, exe: str, label: str, off_label: str = "") -> None:
        """A reunião sem gravação pendente (vazio: nenhuma): os itens, o padrão do menu e a dica; `off_label` é o nome do que
        "Não avisar" desliga (o app inteiro; vazio = o `label`)."""
        if (exe, label, off_label) == (self.call_exe, self.call_label, self.call_off_label):
            return
        self.call_exe, self.call_label, self.call_off_label = exe, label, off_label
        on = bool(exe)
        self.act_call.setText(CALL_RECORD.format(label=label) if on else "")
        self.act_call_off.setText(CALL_OFF.format(label=off_label or label) if on else "")
        for a in (self.act_call, self.act_call_off, self.call_separator):
            a.setVisible(on)
        self._apply_tip()

    def _call_record(self) -> None:
        if self.call_exe and self._on_call_record is not None:
            self._on_call_record(self.call_exe)

    def _call_off(self) -> None:
        if self.call_exe and self._on_call_off is not None:
            self._on_call_off(self.call_exe)

    def set_state(self, state: AppState) -> None:
        self._state = state
        self.setIcon(make_icon(state))
        self._apply_tip()
        recording = state in (AppState.RECORDING, AppState.PAUSED)
        self.act_record.setEnabled(state == AppState.IDLE)
        for a in (self.act_pause, self.act_mark, self.act_mute, self.act_stop, self.act_live):
            a.setEnabled(recording)

    def set_queue_tip(self, text: str) -> None:
        """"processando 2/6 · 43 %" ao lado do estado (F2 item 15); vazio esconde."""
        if text != self.queue_tip:
            self.queue_tip = text
            self._apply_tip()

    def _apply_tip(self) -> None:
        base = CALL_TIP.format(label=self.call_label) if self.call_exe else STATE_TIPS.get(self._state, "listener-meet")
        self.setToolTip(f"{base} · {self.queue_tip}" if self.queue_tip else base)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            match self.controller.state:
                case AppState.IDLE if self.call_exe and self._on_call_record is not None:
                    self._on_call_record(self.call_exe)          # F5.7: o "Antes de gravar" com o app pendente
                case AppState.IDLE:
                    self.controller.open_warmup()
                case _:
                    self.controller.show_widget()
