"""Widget flutuante de gravação: sempre no topo, arrastável, fundo translúcido com cantos arredondados; posição
presa a um monitor existente (F1.1 A9). Popup do marcador trazido para a frente mesmo com outro app em foco (A10)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget

from ..capture.clock import fmt_mmss
from ..log import get_logger
from .common import HEALTH_COLORS, HEALTH_TEXT, clamp_to_screens
from .meters import LevelMeter

log = get_logger("ui.widget")


def _screen_rects() -> list[tuple[int, int, int, int]]:
    return [tuple(s.availableGeometry().getRect()) for s in QGuiApplication.screens()]


class RecordingWidget(QWidget):
    pause_clicked = Signal()
    mark_clicked = Signal()
    mute_clicked = Signal()
    stop_clicked = Signal()
    cancel_clicked = Signal()
    switch_source_clicked = Signal()
    settings_requested = Signal()
    moved = Signal(QPoint)

    def __init__(self, size=(320, 96), pos: QPoint | None = None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("recwidget")
        self.setStyleSheet("""
            QLabel { color: #eee; } QToolButton { color: #eee; background: #3a3f4b; border: none; border-radius: 6px;
            padding: 2px 6px; font-size: 14px; } QToolButton:hover { background: #4a5063; }
            QToolButton:checked { background: #c0392b; }
            QPushButton { color: #fff; background: #c0392b; border-radius: 6px; padding: 2px 8px; }
        """)
        self.resize(*size)
        self._drag_offset: QPoint | None = None

        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.status_label = QLabel("gravando")
        self.status_label.setStyleSheet("color: #e74c3c; font-weight: 600;")
        self.health_l = QLabel("L ●")
        self.health_r = QLabel("R ●")
        self.markers_label = QLabel("")
        self.markers_label.setStyleSheet("color: #aaa; font-size: 11px;")

        self.meter_l = LevelMeter(height=6)
        self.meter_r = LevelMeter(height=6)

        self.btn_pause = self._btn("⏸", "Pausar / retomar", self.pause_clicked)
        self.btn_mark = self._btn("⚑", "Marcador com nota", self.mark_clicked)
        self.btn_mute = self._btn("🎤", "Mudo global (muta o microfone no sistema)", self.mute_clicked)
        self.btn_mute.setCheckable(True)
        self.btn_stop = self._btn("■", "Parar e salvar", self.stop_clicked)
        self.btn_cancel = self._btn("✕", "Cancelar e descartar", self.cancel_clicked)

        self.alert_label = QLabel("")
        self.alert_label.setStyleSheet("color: #ff7675; font-size: 11px;")
        self.alert_btn = QPushButton("Trocar fonte")
        self.alert_btn.clicked.connect(self.switch_source_clicked)
        self.alert_label.hide()
        self.alert_btn.hide()

        top = QHBoxLayout()
        top.addWidget(self.time_label)
        top.addWidget(self.status_label)
        top.addStretch(1)
        top.addWidget(self.markers_label)
        top.addWidget(self.health_l)
        top.addWidget(self.health_r)
        meters = QHBoxLayout()
        meters.addWidget(QLabel("L"))
        meters.addWidget(self.meter_l, 1)
        meters.addWidget(QLabel("R"))
        meters.addWidget(self.meter_r, 1)
        buttons = QHBoxLayout()
        for b in (self.btn_pause, self.btn_mark, self.btn_mute, self.btn_stop, self.btn_cancel):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.alert_label)
        buttons.addWidget(self.alert_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addLayout(meters)
        lay.addLayout(buttons)
        self.move(self._initial_pos(pos))

    def _initial_pos(self, pos: QPoint | None) -> QPoint:
        screens = _screen_rects()
        if pos is not None:
            clamped = clamp_to_screens(pos.x(), pos.y(), self.width(), self.height(), screens)
            if clamped is not None:
                return QPoint(*clamped)
            log.info("posição salva do widget fora dos monitores atuais; voltando ao canto padrão")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def _btn(self, text: str, tip: str, signal) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.clicked.connect(signal)
        return b

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#555555")))
        p.setBrush(QColor(28, 30, 36, 235))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        p.end()

    # ---------------------------------------------------------------- estado
    def update_state(self, elapsed: float, levels: dict, health: dict, paused: bool, muted: bool, markers: int) -> None:
        self.time_label.setText(fmt_mmss(elapsed))
        self.status_label.setText("pausado" if paused else ("gravando (mudo)" if muted else "gravando"))
        self.status_label.setStyleSheet("color: %s; font-weight: 600;" % ("#f1c40f" if paused else "#e74c3c"))
        self.meter_l.set_rms(levels.get("L", 0.0))
        self.meter_r.set_rms(levels.get("R", 0.0))
        for lab, ch in ((self.health_l, "L"), (self.health_r, "R")):
            h = health.get(ch, "off")
            lab.setStyleSheet(f"color: {HEALTH_COLORS.get(h, '#888')}; font-weight: 600;")
            lab.setToolTip(f"canal {ch}: {HEALTH_TEXT.get(h, h)}")
        self.btn_pause.setText("▶" if paused else "⏸")
        self.btn_mute.setChecked(muted)
        self.btn_mute.setText("🔇" if muted else "🎤")
        self.markers_label.setText(f"⚑ {markers}" if markers else "")

    def show_alert(self, text: str | None, can_switch: bool = False) -> None:
        if text:
            self.alert_label.setText(text)
            self.alert_label.show()
            self.alert_btn.setVisible(can_switch)
        else:
            self.alert_label.hide()
            self.alert_btn.hide()

    # ---------------------------------------------------------------- arrastar
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.pos())

    def mouseDoubleClickEvent(self, event) -> None:
        self.settings_requested.emit()


class MarkerPopup(QDialog):
    """Popup junto ao cursor: Enter salva a nota, Esc cancela (U-003). Pede o foco à plataforma ao abrir (A10)."""

    def __init__(self, t_rel: float, platform=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.platform = platform
        self.setStyleSheet("QDialog { background: #1c1e24; border: 1px solid #666; border-radius: 8px; } QLabel { color: #eee; } "
                           "QLineEdit { background: #2b2f3a; color: #fff; border: 1px solid #555; border-radius: 4px; padding: 4px; }")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("nota do marcador (Enter salva, Esc cancela)")
        self.edit.setMinimumWidth(320)
        self.edit.returnPressed.connect(self.accept)
        lay = QHBoxLayout(self)
        lay.addWidget(QLabel(f"⚑ [{fmt_mmss(t_rel)}]"))
        lay.addWidget(self.edit)
        pos = QCursor.pos()
        self.move(pos.x() - 20, pos.y() - 20)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.platform is not None:
            try:
                ok = self.platform.bring_to_front(int(self.winId()))
                log.debug("popup do marcador para a frente: %s", ok)
            except Exception as e:
                log.debug("bring_to_front: %s", e)
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def note(self) -> str:
        return self.edit.text().strip()
