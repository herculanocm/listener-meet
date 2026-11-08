"""F5.20 (C12, U-156, U-157, A-05, A-28): o popup do texto livre do cartão de sugestão ("Como sugerir agora" digitado e
"Objetivo…"). A bandeja de legendas não aceita teclado (não ativa, para o foco ficar no app de reunião), então o texto vai
num popup pequeno, no molde do popup do marcador (`ui/widget_parts.py`): junto ao cursor, por cima, pede a frente à
plataforma e o foco no campo; Enter salva, Esc cancela; até 300 caracteres. Fora da captura de tela, como a bandeja: o que
se digita não aparece no Teams compartilhando a tela (a falha só no DEBUG do log)."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QCursor, QPainter, QPaintEvent, QPen, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QWidget

from ..log import get_logger
from .theme import Tone, note, qcolor, surface_tokens
from .widget_parts import icon_label

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.suggest")
MAX_CHARS = 300


class SuggestTextPopup(QDialog):
    def __init__(self, title: str, text: str, placeholder: str, platform: Platform | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.platform = platform
        self.edit = QLineEdit(text)
        self.edit.setMaxLength(MAX_CHARS)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setAccessibleName(title)
        self.edit.setMinimumWidth(320)
        self.edit.returnPressed.connect(self.accept)
        self.title = QLabel(title)
        note(self.title)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)
        lay.addWidget(icon_label("edit", Tone.ACCENT))
        lay.addWidget(self.title)
        lay.addWidget(self.edit)
        pos = QCursor.pos()
        self.move(pos.x() - 20, pos.y() - 20)

    def value(self) -> str:
        return self.edit.text().strip()

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
                excluded = self.platform.exclude_from_capture(int(self.winId()))
                front = self.platform.bring_to_front(int(self.winId()))
                log.debug("popup da sugestão: fora da captura %s, à frente %s", excluded, front)
            except Exception as e:
                log.debug("popup da sugestão: %s", e)
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()


__all__ = ["MAX_CHARS", "SuggestTextPopup"]
