"""A barra de progresso da fila (F5.3 item 8, U-047, A-08): um `QStyledItemDelegate` que pinta a coluna "%" pelo dado do
item (a fração em `PROGRESS_ROLE` e o estado em `STATUS_ROLE`), nunca por widget de célula: o `refresh` da fila refaz os
itens a cada 2 s e reordena as linhas, e um widget de célula ficaria no job errado. O trilho vem da palette() da tabela, o
preenchido tem a cor do estado (`common.JOB_STATUS_TONES`, nos tokens do tema; F5.4), o cancelado é hachurado e o número
fica preto ou branco pela luminância do que está embaixo dele (sobre o preenchido e sobre o trilho, cada lado com a sua
cor). No "rodando" uma faixa anda pela barra; quem a move é o timer da fila (`phase`), ligado só com alguma linha rodando e
a fila visível."""
from __future__ import annotations

from collections.abc import Sequence
from typing import override

from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..model import JobStatus
from .common import BAND_OPACITY, bar_colors

PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 1      # fração de 0 a 1; None = sem barra (a linha agregada do índice com erro)
STATUS_ROLE = Qt.ItemDataRole.UserRole + 2        # o estado do job, que dá a cor
MARGIN_X, MARGIN_Y = 4, 4                          # a barra dentro da célula: a seleção da linha aparece em volta dela
BAND_SHARE = 0.35                                  # largura da faixa animada em relação à barra
BAND_MIN_PX = 24.0
BAND_STEP = 0.05                                   # quanto a faixa anda por passo do timer (uma volta a cada ~2,4 s)

type Region = tuple[QRectF, str]                   # um lado da barra (preenchido ou trilho) e a cor a usar nele


class ProgressBarDelegate(QStyledItemDelegate):
    """A coluna "%" da fila como barra na cor do estado, com o número por cima."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.phase = 0.0                           # posição da faixa do "rodando", de 0 a 1

    def advance(self) -> None:
        """Um passo da faixa animada (o timer da fila chama e repinta a coluna)."""
        self.phase = (self.phase + BAND_STEP) % 1.0

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)    # fundo e seleção, sem o texto
        value = index.data(PROGRESS_ROLE)
        if value is None:
            return
        status = str(index.data(STATUS_ROLE) or "")
        colors = bar_colors(status, opt.palette.color(QPalette.ColorRole.Base).name(), opt.palette.color(QPalette.ColorRole.Text).name())
        bar = QRectF(opt.rect.adjusted(MARGIN_X, MARGIN_Y, -MARGIN_X, -MARGIN_Y))
        fill = QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, float(value))), bar.height())
        rest = QRectF(fill.right(), bar.top(), bar.right() - fill.right(), bar.height())
        painter.save()
        painter.fillRect(bar, QColor(colors.track))
        painter.fillRect(fill, QColor(colors.fill))
        if colors.hatch:
            painter.fillRect(fill, QBrush(QColor(colors.hatch), Qt.BrushStyle.BDiagPattern))
        if status == JobStatus.RUNNING:
            self._paint_band(painter, bar, ((fill, colors.band), (rest, colors.track_band)))
        if text:
            self._paint_text(painter, bar, text, opt.font, ((fill, colors.text), (rest, colors.track_text)))
        painter.restore()

    def _paint_band(self, painter: QPainter, bar: QRectF, regions: Sequence[Region]) -> None:
        """A faixa do "rodando": um brilho que atravessa a barra; clareia sob texto preto e escurece sob texto branco, então o
        contraste do número só sobe onde ela passa."""
        width = max(BAND_MIN_PX, bar.width() * BAND_SHARE)
        x = bar.left() - width + (bar.width() + width) * self.phase
        strip = QRectF(x, bar.top(), width, bar.height())
        for clip, color in regions:
            if clip.width() <= 0:
                continue
            glow, clear = QColor(color), QColor(color)
            glow.setAlphaF(BAND_OPACITY)
            clear.setAlphaF(0.0)
            gradient = QLinearGradient(x, 0.0, x + width, 0.0)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(0.5, glow)
            gradient.setColorAt(1.0, clear)
            painter.save()
            painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
            painter.fillRect(strip, QBrush(gradient))
            painter.restore()

    @staticmethod
    def _paint_text(painter: QPainter, bar: QRectF, text: str, font: QFont, regions: Sequence[Region]) -> None:
        """O número centrado na barra, desenhado uma vez por lado com a cor daquele lado."""
        painter.setFont(font)
        for clip, color in regions:
            if clip.width() <= 0:
                continue
            painter.save()
            painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
            painter.setPen(QColor(color))
            painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
