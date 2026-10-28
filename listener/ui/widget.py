"""Widget flutuante de gravação: sempre no topo, arrastável, fundo translúcido com cantos arredondados; posição
presa a um monitor existente (F1.1 A9). Popup do marcador trazido para a frente mesmo com outro app em foco (A10).
Estilos e tooltips só mudam quando o estado muda (M-12).
F4: selo do ao vivo ("ao vivo", "legenda atrasada", "ao vivo parou") ao lado do estado, sempre que o ao vivo foi lançado;
com `live.in_widget`, uma linha com a última legenda (`Ana — CFO: texto`, cortada com reticências e inteira no tooltip)
cujo clique abre a janela "Ao vivo"; com a linha, o widget ganha a altura dela sem encolher o tamanho salvo.
F5.3 L4 (U-049, U-050): o botão "🎧" abre o painel dos dispositivos (desligado salvando); a linha de alerta tem um botão
genérico (rótulo e ação dados por quem a mostra, A-21) no lugar do "Trocar fonte" fixo: "Usar" no aparelho novo,
"Dispositivos…" na saída sem sinal.
F5.4 (A-11): as cores do estado, da saúde dos canais e do selo do ao vivo são tons dos tokens; o widget segue escuro até o
L2 (U-060), com os tokens do escuro nos dois esquemas (`common.DARK_SURFACE`)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, Qt, Signal, SignalInstance
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..model import AppState, Health
from .common import DARK_SURFACE, HEALTH_TEXT, clamp_to_screens, health_color, state_color
from .meters import LevelMeter
from .theme import Tone

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.widget")
LIVE_MIN_HEIGHT = 118             # F4: 96 do widget mais a linha da legenda
CLICK_SLOP_PX = 6
ALERT_MIN_PX = 170                # F5.3: a linha de alerta à vista abre esse espaço ao lado dos botões (o resto vai na dica)
# selo por estado do ao vivo (`Controller.live_status`): texto e tom; os estados fora daqui escondem o selo
LIVE_BADGES: dict[str, tuple[str, Tone]] = {
    "starting": ("ao vivo", Tone.NEUTRAL), "restarting": ("ao vivo", Tone.NEUTRAL), "live": ("ao vivo", Tone.SUCCESS),
    "degraded": ("legenda atrasada", Tone.WARNING), "skipping": ("legenda atrasada", Tone.WARNING),
    "crashed": ("ao vivo parou", Tone.DANGER),
}


def caption_text(label: str | None, text: str) -> str:
    """"Ana — CFO: texto"; sem rótulo (nomes desligados), só o texto."""
    text = " ".join(str(text or "").split())
    return f"{label}: {text}" if label else text


def _screen_rects() -> list[tuple[int, int, int, int]]:
    out = []
    for s in QGuiApplication.screens():
        g = s.availableGeometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out


class ElidedLabel(QLabel):
    """Uma linha cortada com reticências na largura disponível (não alarga o widget), o texto inteiro no tooltip (a legenda
    do F4 e, F5.3, a linha de alerta com o nome de um aparelho)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_full_text(self, text: str, tip: str = "") -> None:
        self.full_text = text
        self.setToolTip(tip or text)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(20, self.width())))

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()


class CaptionLine(ElidedLabel):
    """A última legenda numa linha, cortada como a `ElidedLabel`; um clique sem arrastar emite `clicked` (o arrasto do
    widget começa fora da linha)."""
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._press: QPoint | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        press, self._press = self._press, None
        if press is not None and (event.position().toPoint() - press).manhattanLength() < CLICK_SLOP_PX:
            self.clicked.emit()
        event.accept()


class RecordingWidget(QWidget):
    pause_clicked = Signal()
    mark_clicked = Signal()
    mute_clicked = Signal()
    stop_clicked = Signal()
    cancel_clicked = Signal()
    devices_clicked = Signal()            # F5.3 (U-049): o painel dos dispositivos
    alert_clicked = Signal(str)           # F5.3 (A-21): o botão da linha de alerta, com a ação dela
    settings_requested = Signal()
    live_clicked = Signal()               # F4: clique na linha da legenda abre a janela "Ao vivo"
    moved = Signal(QPoint)

    def __init__(self, size: tuple[int, int] = (320, 96), pos: QPoint | None = None, live_line: bool = False):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("recwidget")
        self.setStyleSheet("""
            QLabel { color: #eee; } QToolButton { color: #eee; background: #3a3f4b; border: none; border-radius: 6px;
            padding: 2px 6px; font-size: 14px; } QToolButton:hover { background: #4a5063; }
            QToolButton:checked { background: #c0392b; }
            QPushButton { color: #fff; background: #c0392b; border-radius: 6px; padding: 2px 8px; }
        """)
        self.live_line = live_line
        width, height = size
        self.resize(width, max(height, LIVE_MIN_HEIGHT) if live_line else height)   # o tamanho salvo nunca encolhe
        self._drag_offset: QPoint | None = None
        self._last_status: tuple[bool, bool, bool] | None = None    # (paused, muted, saving)
        self._last_health: dict[str, str] = {}
        self._last_alert: tuple[str | None, str, str, str] = (None, "", "", "")
        self._alert_action = ""
        self._last_live: tuple[str, str] | None = None
        self.style_changes = 0                                      # instrumentação dos testes (M-12)

        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.status_label = QLabel("gravando")
        self.status_label.setStyleSheet(f"color: {state_color(AppState.RECORDING, DARK_SURFACE)}; font-weight: 600;")
        self.health_l = QLabel("L ●")
        self.health_r = QLabel("R ●")
        self.markers_label = QLabel("")
        self.markers_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self.live_badge = QLabel("")
        self.live_badge.hide()
        self.caption_line = CaptionLine()
        self.caption_line.setStyleSheet("color: #ddd; font-size: 11px;")
        self.caption_line.set_full_text("aguardando a primeira legenda…", "clique para abrir a janela Ao vivo")
        self.caption_line.clicked.connect(self.live_clicked)
        self.caption_line.setVisible(live_line)

        self.meter_l = LevelMeter(height=6)
        self.meter_r = LevelMeter(height=6)

        self.btn_pause = self._btn("⏸", "Pausar / retomar", self.pause_clicked)
        self.btn_mark = self._btn("⚑", "Marcador com nota", self.mark_clicked)
        self.btn_mute = self._btn("🎤", "Mudo global (muta o microfone no sistema)", self.mute_clicked)
        self.btn_mute.setCheckable(True)
        self.btn_devices = self._btn("🎧", "Dispositivos: trocar o microfone e a saída gravada sem parar", self.devices_clicked)
        self.btn_stop = self._btn("■", "Parar e salvar", self.stop_clicked)
        self.btn_cancel = self._btn("✕", "Cancelar e descartar", self.cancel_clicked)

        self.alert_label = ElidedLabel()                  # F5.3: o nome de um aparelho não alarga o widget
        self.alert_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.alert_label.setMinimumWidth(ALERT_MIN_PX)
        self.alert_label.setStyleSheet("color: #ff7675; font-size: 11px;")
        self.alert_btn = QPushButton("")
        self.alert_btn.clicked.connect(self._emit_alert)   # método, não lambda com `self`: o ciclo pelo C++ prendia o widget
        self.alert_label.hide()
        self.alert_btn.hide()

        top = QHBoxLayout()
        top.addWidget(self.time_label)
        top.addWidget(self.status_label)
        top.addWidget(self.live_badge)
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
        for b in self._buttons():
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.alert_label, 10)           # escondida, a folga fica com o espaço; à vista, quase toda com ela
        buttons.addWidget(self.alert_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addLayout(meters)
        lay.addWidget(self.caption_line)
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

    def _emit_alert(self) -> None:
        """O clique no botão da linha de alerta, com a ação dela (A-21). Conectado como método do próprio widget: um lambda
        que captura `self` fica guardado do lado C++ do PySide, num ciclo que o coletor do Python não enxerga, e o widget
        sem dono vivia até a saída, quando a derrubada do Qt o destruía fora de ordem (0xC0000409 na saída)."""
        self.alert_clicked.emit(self._alert_action)

    def _buttons(self) -> tuple[QToolButton, ...]:
        return (self.btn_pause, self.btn_mark, self.btn_mute, self.btn_devices, self.btn_stop, self.btn_cancel)

    def _btn(self, text: str, tip: str, signal: SignalInstance) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.clicked.connect(signal)
        return b

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#555555")))
        p.setBrush(QColor(28, 30, 36, 235))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        p.end()

    # ---------------------------------------------------------------- estado
    def update_state(self, elapsed: float, levels: Mapping[str, float], health: Mapping[str, str], paused: bool, muted: bool,
                     markers: int, saving: bool = False) -> None:
        self.time_label.setText(fmt_mmss(elapsed))
        self.meter_l.set_rms(levels.get("L", 0.0))
        self.meter_r.set_rms(levels.get("R", 0.0))
        status = (paused, muted, saving)
        if status != self._last_status:
            self._last_status = status
            self.style_changes += 1
            text = "salvando…" if saving else ("pausado" if paused else ("gravando (mudo)" if muted else "gravando"))
            state = AppState.SAVING if saving else (AppState.PAUSED if paused else AppState.RECORDING)
            color = state_color(state, DARK_SURFACE)
            self.status_label.setText(text)
            self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.btn_pause.setText("▶" if paused else "⏸")
            self.btn_mute.setChecked(muted)
            self.btn_mute.setText("🔇" if muted else "🎤")
            for b in self._buttons():
                b.setEnabled(not saving)
        for lab, ch in ((self.health_l, "L"), (self.health_r, "R")):
            h = health.get(ch, Health.OFF)
            if self._last_health.get(ch) != h:
                self._last_health[ch] = h
                self.style_changes += 1
                lab.setStyleSheet(f"color: {health_color(h, DARK_SURFACE)}; font-weight: 600;")
                lab.setToolTip(f"canal {ch}: {HEALTH_TEXT.get(h, h)}")
        text = f"⚑ {markers}" if markers else ""
        if self.markers_label.text() != text:
            self.markers_label.setText(text)

    # ---------------------------------------------------------------- ao vivo (F4)
    def set_live_state(self, state: str, tip: str = "") -> None:
        """Selo pelo estado do ao vivo (`LIVE_BADGES`); outro estado esconde. Só mexe no estilo quando muda (M-12)."""
        badge = LIVE_BADGES.get(state)
        key = (state, tip)
        if key == self._last_live:
            return
        self._last_live = key
        if badge is None:
            self.live_badge.hide()
            return
        text, tone = badge
        color = DARK_SURFACE.tone(tone)
        self.style_changes += 1
        self.live_badge.setText(f"● {text}")
        self.live_badge.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        self.live_badge.setToolTip(tip or text)
        self.live_badge.show()

    def set_caption(self, label: str | None, text: str) -> None:
        """A última legenda na linha (só existe com `live.in_widget`)."""
        if not self.live_line:
            return
        line = caption_text(label, text)
        self.caption_line.set_full_text(line, f"{line}\n\n(clique para abrir a janela Ao vivo)")

    def show_alert(self, text: str | None, button: str = "", action: str = "", tip: str = "") -> None:
        """A linha de alerta (só muda quando o alerta muda, M-12); `button` é o rótulo do botão e `action` o que o clique
        emite em `alert_clicked` (F5.3, A-21); sem rótulo, sem botão."""
        key = (text, button, action, tip)
        if key == self._last_alert:
            return
        self._last_alert = key
        self._alert_action = action
        if text:
            self.alert_label.set_full_text(text, tip or text)
            self.alert_label.show()
            self.alert_btn.setText(button)
            self.alert_btn.setToolTip(tip)
            self.alert_btn.setVisible(bool(button))
        else:
            self.alert_label.hide()
            self.alert_btn.hide()

    # ---------------------------------------------------------------- arrastar
    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.pos())

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.settings_requested.emit()


class MarkerPopup(QDialog):
    """Popup junto ao cursor: Enter salva a nota, Esc cancela (U-003). Pede o foco à plataforma ao abrir (A10)."""

    def __init__(self, t_rel: float, platform: Platform | None = None, parent: QWidget | None = None):
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

    @override
    def showEvent(self, event: QShowEvent) -> None:
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
