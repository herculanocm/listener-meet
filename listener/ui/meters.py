"""Medidor de nível (barra fina com gradiente verde, amarelo, vermelho)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from .common import level_fraction


class LevelMeter(QWidget):
    def __init__(self, parent=None, height: int = 8):
        super().__init__(parent)
        self._fraction = 0.0
        self._peak = 0.0
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setMinimumWidth(60)

    def set_rms(self, rms_value: float) -> None:
        self._fraction = level_fraction(rms_value)
        self._peak = max(self._fraction, self._peak * 0.92)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(r, 3, 3)
        if self._fraction > 0:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0.0, QColor("#2ecc71"))
            g.setColorAt(0.7, QColor("#f1c40f"))
            g.setColorAt(1.0, QColor("#e74c3c"))
            p.setBrush(g)
            w = int(r.width() * self._fraction)
            p.drawRoundedRect(r.left(), r.top(), max(2, w), r.height(), 3, 3)
        if self._peak > 0:
            x = r.left() + int(r.width() * self._peak)
            p.setBrush(QColor(255, 255, 255, 180))
            p.drawRect(max(r.left(), x - 1), r.top(), 2, r.height())
        p.end()
