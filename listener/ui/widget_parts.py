"""Peças do widget de gravação (F5.4 L2), separadas de `ui/widget.py` para os dois ficarem abaixo de 400 linhas: a hora
(mm:ss e, passando de 1 h, h:mm:ss; U-062), o atalho escrito como o Windows o escreve (U-061), a linha cortada com o texto
inteiro na dica (a legenda do F4 e o alerta da F5.3), o rótulo de ícone e o popup do marcador (U-068: no tema, com o ícone
do marcador e a hora em nota, sobre o cartão com borda e cantos de 8 px). `ui/widget.py` as reexporta.
F5.7 (L3): as preferências do widget no banco (posição, tamanho e os atalhos das dicas), que saíram do `ui/controller.py`
para ele não crescer."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget

from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..model import Health
from .icons import set_icon
from .theme import Tone, note, qcolor, surface_tokens

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.widget")
DEFAULT_SIZE = (320, 96)
CLICK_SLOP_PX = 6
ICON_PX = 16
CHANNEL_NAMES = {"L": "microfone", "R": "saída"}
CHANNEL_ICONS = {"L": "mic", "R": "speaker"}
# U-062: o ícone do canal muda de forma com o estado (o silêncio curto, WARN, só muda a cor: o medidor vazio já diz)
HEALTH_ICONS: dict[str, dict[str, str]] = {
    "L": {Health.ALERT: "mic-off", Health.MUTED: "mic-off", Health.PAUSED: "pause", Health.LOST: "error"},
    "R": {Health.ALERT: "warning", Health.PAUSED: "pause", Health.LOST: "error"},
}


def health_icon(channel: str, health: str) -> str:
    """O ícone do canal no estado dele (U-062)."""
    return HEALTH_ICONS[channel].get(health, CHANNEL_ICONS[channel])


def caption_text(label: str | None, text: str) -> str:
    """"Ana — CFO: texto"; sem rótulo (nomes desligados), só o texto."""
    text = " ".join(str(text or "").split())
    return f"{label}: {text}" if label else text


def clock_text(seconds: float) -> str:
    """U-062: mm:ss na primeira hora e h:mm:ss depois ("1:02:03")."""
    total = max(0, int(seconds))
    return fmt_mmss(total) if total < 3600 else f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}"


def shortcut_text(combo: str) -> str:
    """"ctrl+alt+shift+p" como o Windows escreve: "Ctrl+Alt+Shift+P"."""
    return "+".join(part.strip().capitalize() for part in combo.split("+") if part.strip())


class ElidedLabel(QLabel):
    """Uma linha cortada com reticências na largura disponível (não alarga o widget), o texto inteiro no tooltip (a legenda
    do F4 e, F5.3, a linha de alerta com o nome de um aparelho). Em texto puro (revisão da F5.4): um nome de aparelho ou uma
    legenda com "<b>" aparece como foi escrito, sem virar texto formatado."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
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


def icon_label(name: str, value: Tone) -> QLabel:
    label = QLabel()
    label.setFixedSize(ICON_PX, ICON_PX)
    set_icon(label, name, value, ICON_PX)
    return label


def hbox(first: QWidget | None, spacing: int) -> QHBoxLayout:
    """Uma linha sem margens, com o primeiro widget já posto."""
    line = QHBoxLayout()
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(spacing)
    if first is not None:
        line.addWidget(first)
    return line


def channel_letter(text: str, height: int) -> QLabel:
    """A letra do canal (L ou R) ao lado do ícone dele, em nota."""
    label = QLabel(text)
    note(label)
    label.setFixedHeight(height)
    return label


class MarkerPopup(QDialog):
    """Popup junto ao cursor: Enter salva a nota, Esc cancela (U-003). Pede o foco à plataforma ao abrir (A10). F5.4 L2
    (U-068): no tema, com o ícone do marcador e a hora em nota, sobre o cartão com borda e cantos de 8 px."""

    def __init__(self, t_rel: float, platform: Platform | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.platform = platform
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("nota do marcador (Enter salva, Esc cancela)")
        self.edit.setMinimumWidth(320)
        self.edit.returnPressed.connect(self.accept)
        self.time = QLabel(f"[{clock_text(t_rel)}]")
        note(self.time)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)
        lay.addWidget(icon_label("flag", Tone.ACCENT))
        lay.addWidget(self.time)
        lay.addWidget(self.edit)
        pos = QCursor.pos()
        self.move(pos.x() - 20, pos.y() - 20)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcolor(t.stroke_strong), 1))
        p.setBrush(qcolor(t.card))
        radius = t.metrics.radius_card
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.end()

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


# ---------------------------------------------------------------- preferências no banco (F5.7: saíram do controlador)
def save_widget_pos(store: Store, pos: QPoint) -> None:
    store.set_setting("ui.widget_pos", f"{pos.x()},{pos.y()}")


def saved_widget_pos(store: Store) -> QPoint | None:
    try:
        x, y = (int(v) for v in store.get_setting("ui.widget_pos").split(","))
    except ValueError:
        return None
    return QPoint(x, y)


def saved_widget_size(store: Store) -> tuple[int, int]:
    raw = store.get_setting("ui.widget_size") or "320x96"
    try:
        w, h = (int(v) for v in raw.lower().split("x"))
    except ValueError:
        return DEFAULT_SIZE
    return (w, h)


def widget_prefs(store: Store) -> dict[str, Any]:
    """O tamanho, a posição e (F5.4 L2, U-061) os atalhos globais das ações do widget, para a dica dos botões."""
    shortcuts = {name: store.get_setting(f"hotkeys.{name}") or "" for name in ("pause", "mark", "mute", "stop")}
    return {"size": saved_widget_size(store), "pos": saved_widget_pos(store), "shortcuts": shortcuts}


__all__ = ["CHANNEL_ICONS", "CHANNEL_NAMES", "CLICK_SLOP_PX", "HEALTH_ICONS", "ICON_PX", "CaptionLine", "ElidedLabel", "MarkerPopup",
           "caption_text", "channel_letter", "clock_text", "hbox", "health_icon", "icon_label", "save_widget_pos",
           "saved_widget_pos", "saved_widget_size", "shortcut_text", "widget_prefs"]
