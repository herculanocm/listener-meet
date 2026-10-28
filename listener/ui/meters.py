"""Medidor de nível (barra fina). Não repinta parado; o pincel das zonas em cache (M-12).
F5.4 (A-11): as cores são os tokens da janela, lidos na pintura; o cache leva os tokens na chave (a troca de tema refaz).
F5.4 L2 (U-063): o trilho visível nos dois temas (o traço do controle com 40 % do traço forte: no claro, `stroke` puro some
sobre o cartão) e as zonas por dBFS, em faixas de cor sem degradê: verde até -18 dBFS, âmbar até -6 e vermelho acima, na
escala de `common.level_fraction` (piso em -60 dBFS). O pico fica num traço na cor do texto."""
from __future__ import annotations

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from .common import level_fraction
from .theme import Tokens, mix, qcolor, surface_tokens

PEAK_ALPHA = 0.7                  # o traço do pico, na cor do texto
TRACK_MIX = 0.4                   # o trilho: `stroke` com 40 % de `stroke_strong` (visível no claro e no escuro)
FLOOR_DB = -60.0                  # o piso da escala (o de `level_fraction`)
AMBER_DB, RED_DB = -18.0, -6.0    # U-063: verde até -18 dBFS, âmbar até -6, vermelho acima
EDGE = 1e-4                       # a passagem de uma zona para a outra, sem degradê


def db_fraction(db: float) -> float:
    """A posição de um nível em dBFS na barra (0 no piso, 1 em 0 dBFS)."""
    return max(0.0, min(1.0, (db - FLOOR_DB) / -FLOOR_DB))


def zone_stops(t: Tokens) -> tuple[tuple[float, str], ...]:
    """As paradas do pincel das zonas (posição na barra, cor): faixas chapadas, trocando de cor em -18 e -6 dBFS."""
    amber, red = db_fraction(AMBER_DB), db_fraction(RED_DB)
    return ((0.0, t.success), (amber, t.success), (amber + EDGE, t.warning), (red, t.warning), (red + EDGE, t.danger),
            (1.0, t.danger))


def track_color(t: Tokens) -> str:
    """O trilho nos tokens `t` (o medidor guarda o pincel dele enquanto os tokens forem os mesmos, `_track_and_peak`)."""
    return mix(t.stroke, t.stroke_strong, TRACK_MIX)


class LevelMeter(QWidget):
    def __init__(self, parent: QWidget | None = None, height: int = 8):
        super().__init__(parent)
        self._fraction = 0.0
        self._peak = 0.0
        self._gradient: QLinearGradient | None = None
        self._gradient_key: tuple[int, int, Tokens] | None = None
        self._brushes: tuple[Tokens, QBrush, QBrush] | None = None     # os tokens, o trilho e o pico
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
        key = (left, right, t)
        if self._gradient is None or self._gradient_key != key:
            g = QLinearGradient(left, 0, right, 0)
            for at, color in zone_stops(t):
                g.setColorAt(at, qcolor(color))
            self._gradient, self._gradient_key = g, key
        return self._gradient

    def _track_and_peak(self, t: Tokens) -> tuple[QBrush, QBrush]:
        """O pincel do trilho e o do pico nos tokens `t`, guardados enquanto os tokens forem o mesmo objeto (a troca de tema
        traz outro): sem o hash dos tokens, que o `lru_cache` refazia, nem dois `QColor` a cada pintura (quatro por tique
        do widget)."""
        if self._brushes is None or self._brushes[0] is not t:
            self._brushes = (t, QBrush(qcolor(track_color(t))), QBrush(qcolor(t.text, PEAK_ALPHA)))
        return self._brushes[1], self._brushes[2]

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        radius = min(3.0, r.height() / 2)
        p.setPen(Qt.PenStyle.NoPen)
        track, peak = self._track_and_peak(t)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)
        if self._fraction > 0:
            p.setBrush(self._gradient_for(r.left(), r.right(), t))
            w = int(r.width() * self._fraction)
            p.drawRoundedRect(r.left(), r.top(), max(2, w), r.height(), radius, radius)
        if self._peak > 0:
            x = r.left() + int(r.width() * self._peak)
            p.setBrush(peak)
            p.drawRect(max(r.left(), x - 1), r.top(), 2, r.height())
        p.end()
