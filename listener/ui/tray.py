"""Ícone na bandeja com estado (ocioso, gravando, pausado) e menu."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

STATE_COLORS = {"idle": "#7f8c8d", "recording": "#e74c3c", "paused": "#f1c40f"}


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
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.menu = QMenu()
        self.act_record = QAction("Gravar…", self.menu)
        self.act_pause = QAction("Pausar / retomar", self.menu)
        self.act_mark = QAction("Marcador", self.menu)
        self.act_mute = QAction("Mudo", self.menu)
        self.act_stop = QAction("Parar", self.menu)
        self.act_settings = QAction("Configurações…", self.menu)
        self.act_quit = QAction("Sair", self.menu)
        for a in (self.act_record, self.act_pause, self.act_mark, self.act_mute, self.act_stop):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.act_settings)
        self.menu.addAction(self.act_quit)
        self.setContextMenu(self.menu)
        self.act_record.triggered.connect(controller.open_warmup)
        self.act_pause.triggered.connect(controller.toggle_pause)
        self.act_mark.triggered.connect(controller.mark)
        self.act_mute.triggered.connect(controller.toggle_mute)
        self.act_stop.triggered.connect(controller.stop_recording)
        self.act_settings.triggered.connect(controller.open_settings)
        self.act_quit.triggered.connect(controller.quit)
        self.activated.connect(self._activated)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        self.setIcon(make_icon(STATE_COLORS.get(state, "#7f8c8d")))
        self.setToolTip({"idle": "listener-meet: pronto", "recording": "listener-meet: gravando",
                         "paused": "listener-meet: pausado"}.get(state, "listener-meet"))
        recording = state in ("recording", "paused")
        self.act_record.setEnabled(not recording)
        for a in (self.act_pause, self.act_mark, self.act_mute, self.act_stop):
            a.setEnabled(recording)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            if self.controller.state == "idle":
                self.controller.open_warmup()
            else:
                self.controller.show_widget()
