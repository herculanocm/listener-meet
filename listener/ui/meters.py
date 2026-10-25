"""Medidor de nível (barra fina com gradiente verde, amarelo, vermelho). Não repinta parado; gradiente em cache (M-12)."""
from __future__ import annotations

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from .common import level_fraction


class LevelMeter(QWidget):
    def __init__(self, parent: QWidget | None = None, height: int = 8):
        super().__init__(parent)
        self._fraction = 0.0
        self._peak = 0.0
        self._gradient: QLinearGradient | None = None
        self._gradient_width = -1
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setMinimumWidth(60)

    def set_rms(self, rms_value: float) -> None:
        fraction = level_fraction(rms_value)
        peak = max(fraction, self._peak * 0.92)
        if peak < 0.005:
            peak = 0.0
        if fraction == self._fraction and peak == self._peak:
            return                                         # silêncio ou nível igual: nada a repintar
        self._fraction, self._peak = fraction, peak
        self.update()

    def _gradient_for(self, left: int, right: int) -> QLinearGradient:
        if self._gradient is None or self._gradient_width != right - left:
            g = QLinearGradient(left, 0, right, 0)
            g.setColorAt(0.0, QColor("#2ecc71"))
            g.setColorAt(0.7, QColor("#f1c40f"))
            g.setColorAt(1.0, QColor("#e74c3c"))
            self._gradient, self._gradient_width = g, right - left
        return self._gradient

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(r, 3, 3)
        if self._fraction > 0:
            p.setBrush(self._gradient_for(r.left(), r.right()))
            w = int(r.width() * self._fraction)
            p.drawRoundedRect(r.left(), r.top(), max(2, w), r.height(), 3, 3)
        if self._peak > 0:
            x = r.left() + int(r.width() * self._peak)
            p.setBrush(QColor(255, 255, 255, 180))
            p.drawRect(max(r.left(), x - 1), r.top(), 2, r.height())
        p.end()
