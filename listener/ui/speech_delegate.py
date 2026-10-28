"""U-081 (F5.4 L4): a fala na transcrição da reunião e a legenda do Ao vivo. Duas colunas: à esquerda o [mm:ss] em legenda
(12 px, no secundário) e o ponto na cor do falante (o mesmo `speaker` da faixa dele na linha do tempo); à direita o falante
em negrito, o cargo no secundário e o texto, que quebra embaixo do nome. No Ao vivo, a legenda que virou marcador leva a
bandeira à esquerda do tempo.

O item diz o que mostrar por papéis (`STAMP_ROLE` e os outros); o `DisplayRole` segue com a linha inteira, para o leitor de
tela e os testes. Sem `dataChanged` e sem folha: o fundo (a seleção, o passar do mouse, a fala que toca) vem do
`CE_ItemViewItem` do estilo com o texto vazio, e as cores, dos tokens da janela na hora da pintura. A altura de cada item sai
de um `QTextLayout` com a largura da lista, guardada por texto e largura (milhares de falas não refazem a conta a cada
pintura; a conta só volta quando a largura muda)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ..llm.prompts.common import stamp
from . import icons
from .theme import Tone, font_for, qcolor, surface_tokens

if TYPE_CHECKING:
    from .theme import Tokens

STAMP_ROLE = Qt.ItemDataRole.UserRole + 0x5301      # int: o início em ms
SPEAKER_ROLE = Qt.ItemDataRole.UserRole + 0x5302    # str: o nome ("" sem falante)
ROLE_ROLE = Qt.ItemDataRole.UserRole + 0x5303       # str: o cargo ("" sem cargo)
TEXT_ROLE = Qt.ItemDataRole.UserRole + 0x5304       # str: o que foi dito
COLOR_ROLE = Qt.ItemDataRole.UserRole + 0x5305      # int: o índice do falante nos tokens `speaker` (None: sem ponto)
FLAG_ROLE = Qt.ItemDataRole.UserRole + 0x5306       # bool: a legenda virou marcador (Ao vivo)

PAD_X, PAD_Y = 8, 6               # px lógicos dentro do item
GAP = 8                           # entre o tempo, o ponto e o texto
DOT = 8                           # o diâmetro do ponto do falante
FLAG = 14                         # a bandeira da legenda marcada
STAMP_SAMPLE = "000:00"           # a coluna do tempo cabe "mm:ss" até 999 min
CACHE_MAX = 20_000                # alturas guardadas (texto, largura); passou disto, recomeça


class SpeechDelegate(QStyledItemDelegate):
    """O desenho de uma fala (ou legenda). `flags` reserva a coluna da bandeira (Ao vivo)."""

    def __init__(self, parent: QWidget | None = None, *, flags: bool = False) -> None:
        super().__init__(parent)
        self.flags = flags
        self.caption = font_for("caption")
        self.strong = font_for("body_strong")
        self._caption_fm = QFontMetrics(self.caption)
        self.stamp_w = self._caption_fm.horizontalAdvance(STAMP_SAMPLE)
        self._gutter = PAD_X + (FLAG + GAP // 2 if flags else 0) + self.stamp_w + GAP + DOT + GAP
        self._heights: dict[tuple[str, str, str, int], int] = {}
        self._wrap = QTextOption()
        self._wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._strong_fmt = _format(self.strong, None)         # a medida não usa cor: um formato só, feito uma vez

    # ---------------------------------------------------------------- geometria
    def gutter(self) -> int:
        """A largura da coluna da esquerda (a bandeira, o tempo e o ponto), medida uma vez."""
        return self._gutter

    def _layout(self, body: QFont, name: str, role: str, text: str, width: int, t: Tokens | None) -> QTextLayout:
        """O nome em negrito, o cargo no secundário e o texto, quebrando em `width` (com `t`, as cores dos tokens)."""
        head = f"{name} — {role}" if name and role else name
        full = f"{head}  {text}" if head else text
        layout = QTextLayout(full, body)
        layout.setTextOption(self._wrap)
        if name:
            ranges = [_range(0, len(name), self._strong_fmt if t is None else _format(self.strong, t.text))]
            if role:
                ranges.append(_range(len(name), len(head) - len(name), _format(None, t.secondary if t is not None else None)))
            layout.setFormats(ranges)
        layout.beginLayout()
        y = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            line.setPosition(QPointF(0, y))
            y += line.height()
        layout.endLayout()
        return layout

    def _text_width(self, item_width: int) -> int:
        return max(40, item_width - self._gutter - PAD_X)

    @staticmethod
    def _item_width(option: QStyleOptionViewItem) -> int:
        """Na medida, a largura do item: a que a lista dá ou, quando pergunta sem ela (largura 0), a da área visível (a mesma
        chave do cálculo de verdade, sem conta a mais), menos o espaçamento da lista dos dois lados."""
        view, full = option.widget, option.rect.width()
        if full <= 0:
            full = view.viewport().width() if isinstance(view, QAbstractScrollArea) else 400
        return full - 2 * view.spacing() if isinstance(view, QListView) else full

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        name, role, text = (str(index.data(r) or "") for r in (SPEAKER_ROLE, ROLE_ROLE, TEXT_ROLE))
        item_width = self._item_width(option)
        width = self._text_width(item_width)
        key = (name, role, text, width)
        height = self._heights.get(key)
        if height is None:
            layout = self._layout(option.font, name, role, text, width, None)
            height = round(layout.boundingRect().height())
            if len(self._heights) >= CACHE_MAX:
                self._heights.clear()
            self._heights[key] = height
        return QSize(item_width, max(height, self._caption_fm.height(), DOT) + 2 * PAD_Y)

    # ---------------------------------------------------------------- pintura
    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)    # fundo, seleção e a fala que toca
        t = surface_tokens(opt.widget)
        rect = opt.rect
        top = rect.top() + PAD_Y
        painter.save()
        x = rect.left() + PAD_X
        if self.flags:
            if index.data(FLAG_ROLE):
                dpr = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
                painter.drawPixmap(QPointF(x, top + 1), icons.pixmap("flag", t.tone(Tone.ACCENT), FLAG, dpr))
            x += FLAG + GAP // 2
        cap_fm, stamp_w = self._caption_fm, self.stamp_w
        painter.setFont(self.caption)
        painter.setPen(qcolor(t.secondary))
        body_ascent = QFontMetrics(opt.font).ascent()
        base = top + body_ascent                            # o tempo na mesma linha de base da primeira linha do texto
        painter.drawText(QRectF(x, base - cap_fm.ascent(), stamp_w, cap_fm.height()),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), stamp(int(index.data(STAMP_ROLE) or 0)))
        x += stamp_w + GAP
        color = index.data(COLOR_ROLE)
        if color is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.speaker(int(color))))
            center_y = base - QFontMetrics(opt.font).xHeight() / 2
            painter.drawEllipse(QPointF(x + DOT / 2, center_y), DOT / 2, DOT / 2)
        name, role, text = (str(index.data(r) or "") for r in (SPEAKER_ROLE, ROLE_ROLE, TEXT_ROLE))
        painter.setPen(qcolor(t.text))
        layout = self._layout(opt.font, name, role, text, self._text_width(rect.width()), t)
        layout.draw(painter, QPointF(rect.left() + self.gutter(), top))
        painter.restore()

    def clear(self) -> None:
        """As alturas guardadas saem (a lista trocou de fonte; o texto e a largura já entram na chave)."""
        self._heights.clear()


def _format(font: QFont | None, color: str | None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if font is not None:
        fmt.setFont(font)
    if color is not None:
        fmt.setForeground(qcolor(color))
    return fmt


def _range(start: int, length: int, fmt: QTextCharFormat) -> QTextLayout.FormatRange:
    r = QTextLayout.FormatRange()
    r.start, r.length, r.format = start, length, fmt
    return r


def speech_roles(item: Any, t_ms: int, name: str, role: str, text: str, color: int | None) -> None:
    """Os papéis de uma linha num `QListWidgetItem` (o Ao vivo; a transcrição os dá pelo modelo)."""
    for key, value in ((STAMP_ROLE, int(t_ms)), (SPEAKER_ROLE, name), (ROLE_ROLE, role), (TEXT_ROLE, text), (COLOR_ROLE, color)):
        item.setData(key, value)


__all__ = ["COLOR_ROLE", "FLAG_ROLE", "ROLE_ROLE", "SPEAKER_ROLE", "STAMP_ROLE", "TEXT_ROLE", "SpeechDelegate", "speech_roles"]
