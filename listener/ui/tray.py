"""Ícone na bandeja com estado (ocioso, gravando, pausado, salvando) e menu; F4: "Ao vivo…" abre a janela das legendas
durante a gravação (U-021). F5: "Histórico…" abre a busca e as reuniões, e o clique no balão abre a reunião da última
notificação que tinha reunião (Q6). F5.4 (A-11, A-19): a cor do estado é um tom dos tokens do escuro, sobre o fundo próprio
do ícone (a barra de tarefas segue o tema dela, não o do app)."""
from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..model import AppState
from .common import DARK_SURFACE, state_color

if TYPE_CHECKING:
    from .controller import Controller

STATE_TIPS: dict[str, str] = {AppState.IDLE: "listener-meet: pronto", AppState.RECORDING: "listener-meet: gravando",
                              AppState.PAUSED: "listener-meet: pausado", AppState.SAVING: "listener-meet: salvando…"}


def icon_color(state: str) -> str:
    """A cor do estado no ícone (o fundo do ícone é escuro: os tokens do escuro)."""
    return state_color(state, DARK_SURFACE)


@cache
def make_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2c3e50"))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor(color))
    p.drawEllipse(18, 18, 28, 28)
    p.end()
    return QIcon(pm)


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
        self.act_record.triggered.connect(controller.open_warmup)
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

    def set_state(self, state: AppState) -> None:
        self._state = state
        self.setIcon(make_icon(icon_color(state)))
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
        base = STATE_TIPS.get(self._state, "listener-meet")
        self.setToolTip(f"{base} · {self.queue_tip}" if self.queue_tip else base)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            match self.controller.state:
                case AppState.IDLE:
                    self.controller.open_warmup()
                case _:
                    self.controller.show_widget()
