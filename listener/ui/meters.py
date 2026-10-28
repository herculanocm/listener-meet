"""Medidor de nível (barra fina com gradiente verde, amarelo, vermelho). Não repinta parado; gradiente em cache (M-12).
F5.4 (A-11): as cores são os tokens da janela (`success`, `warning` e `danger`; o trilho em `stroke` e o pico no texto), lidos
na pintura: o widget de gravação, escuro até o L2, usa os do escuro; o cache leva os tokens na chave (a troca de tema refaz).
As zonas por dBFS e o trilho novo são do L2 (U-063)."""
from __future__ import annotations

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from .common import level_fraction
from .theme import Tokens, qcolor, surface_tokens

PEAK_ALPHA = 0.7                  # o traço do pico, na cor do texto


class LevelMeter(QWidget):
    def __init__(self, parent: QWidget | None = None, height: int = 8):
        super().__init__(parent)
        self._fraction = 0.0
        self._peak = 0.0
        self._gradient: QLinearGradient | None = None
        self._gradient_key: tuple[int, Tokens] | None = None
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

    def _gradient_for(self, left: int, right: int, t: Tokens) -> QLinearGradient:
        key = (right - left, t)
        if self._gradient is None or self._gradient_key != key:
            g = QLinearGradient(left, 0, right, 0)
            g.setColorAt(0.0, qcolor(t.success))
            g.setColorAt(0.7, qcolor(t.warning))
            g.setColorAt(1.0, qcolor(t.danger))
            self._gradient, self._gradient_key = g, key
        return self._gradient

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(t.stroke))
        p.drawRoundedRect(r, 3, 3)
        if self._fraction > 0:
            p.setBrush(self._gradient_for(r.left(), r.right(), t))
            w = int(r.width() * self._fraction)
            p.drawRoundedRect(r.left(), r.top(), max(2, w), r.height(), 3, 3)
        if self._peak > 0:
            x = r.left() + int(r.width() * self._peak)
            p.setBrush(qcolor(t.text, PEAK_ALPHA))
            p.drawRect(max(r.left(), x - 1), r.top(), 2, r.height())
        p.end()
