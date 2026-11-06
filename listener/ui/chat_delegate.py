"""F5.17 (U-141): a fala da bandeja de legendas como num chat.

- As falas dos outros à esquerda, num balão no fundo da janela com borda. Na primeira de uma sequência vem o cabeçalho: o
  ponto na cor do falante (U-081), o nome em negrito, o cargo no secundário e o [mm:ss]; sem espaço, o cargo sai antes e
  o nome é cortado. Sem nome, só o [mm:ss].
- As suas à direita, no fundo de destaque (`selected`; no contraste alto, o texto do destaque), com o [mm:ss] em cima e
  sem o nome.
- A legenda que virou marcador leva a bandeira ao lado do balão (U-144): na coluna do ponto, ou à esquerda do seu balão.

A altura vem do item (`SizeHintRole`, `ui/caption_list.py`): `item_size` mede o texto uma vez por texto e largura, como o
`SpeechDelegate` (a quebra do C++, `boundingRect` com a quebra por palavra; a palavra que não cabe na linha vai para o
`QTextLayout`, que quebra em qualquer ponto). A pintura usa as mesmas opções na mesma largura: o texto quebra igual, e só o
balão encolhe até a linha mais larga. Sem `sizeHint` aqui: o layout fica no C++ (D-094)."""
from __future__ import annotations

import math
from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ..llm.prompts.common import stamp
from . import icons
from .speech_delegate import COLOR_ROLE, FLAG_ROLE, ROLE_ROLE, SPEAKER_ROLE, STAMP_ROLE, TEXT_ROLE, WORD_FLAGS, _flat, _lay_lines
from .theme import Tokens, Tone, font_for, qcolor, surface_tokens

MINE_ROLE = Qt.ItemDataRole.UserRole + 0x5311      # bool: a fala é sua (à direita, sem o nome)
HEAD_ROLE = Qt.ItemDataRole.UserRole + 0x5312      # bool: o cabeçalho aparece (a primeira fala da sequência)

PAD_X, PAD_Y = 8, 3               # a margem do item dos lados e a metade do espaço entre falas
GUTTER = 16                       # a coluna do ponto (e da bandeira) à esquerda dos balões dos outros
BUBBLE_X, BUBBLE_Y = 10, 6        # dentro do balão
RADIUS = 8
DOT = 8
FLAG = 14
GAP = 6                           # entre o nome e a hora no cabeçalho; entre o balão e a bandeira
HEAD_GAP = 2                      # entre o cabeçalho e o balão
SHARE = 0.85                      # o balão ocupa até 85 % da largura útil
CACHE_MAX = 20_000                # medidas guardadas (texto, largura); passou disto, recomeça
TALL = 1 << 20

type Measure = tuple[int, int, bool]   # a altura do texto, a largura da linha mais larga e a quebra em qualquer ponto


class ChatDelegate(QStyledItemDelegate):
    """O balão de uma fala da bandeja. Lê os papéis do `SpeechDelegate` e mais dois: `MINE_ROLE` e `HEAD_ROLE`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.body = font_for("body")
        self.strong = font_for("body_strong")
        self.caption = font_for("caption")
        self.body_fm = QFontMetrics(self.body)
        self._strong_fm = QFontMetrics(self.strong)
        self._caption_fm = QFontMetrics(self.caption)
        self.head_h = max(self.body_fm.height(), self._strong_fm.height(), self._caption_fm.height())
        self._cache: dict[tuple[str, int], Measure] = {}
        self._wrap = QTextOption()
        self._wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    # ---------------------------------------------------------------- medida
    @staticmethod
    def text_width(item_width: int) -> int:
        """A largura do texto no balão mais largo: 85 % do que sobra depois das margens e da coluna do ponto."""
        return max(40, int((item_width - 2 * PAD_X - GUTTER) * SHARE) - 2 * BUBBLE_X)

    def measure(self, text: str, width: int) -> Measure:
        key = (text, width)
        found = self._cache.get(key)
        if found is not None:
            return found
        r = self.body_fm.boundingRect(QRect(0, 0, width, TALL), WORD_FLAGS, text)
        if r.width() <= width:                            # nenhuma palavra passou da linha: a quebra do C++
            found = (r.height(), r.width(), False)
        else:
            layout = self._layout(text, width)
            widest = max((math.ceil(layout.lineAt(i).naturalTextWidth()) for i in range(layout.lineCount())), default=0)
            found = (math.ceil(layout.boundingRect().height()), min(width, widest), True)
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[key] = found
        return found

    def _layout(self, text: str, width: int) -> QTextLayout:
        layout = QTextLayout(text, self.body)
        layout.setTextOption(self._wrap)
        _lay_lines(layout, width)
        return layout

    def item_size(self, text: str, head: bool, item_width: int) -> QSize:
        """O tamanho de uma fala na largura do item (vai para o `SizeHintRole`)."""
        height = self.measure(_flat(text) or " ", self.text_width(item_width))[0]
        return QSize(item_width, 2 * PAD_Y + (self.head_h + HEAD_GAP if head else 0) + height + 2 * BUBBLE_Y)

    # ---------------------------------------------------------------- pintura
    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = surface_tokens(option.widget)
        rect = option.rect
        mine = bool(index.data(MINE_ROLE))
        text = _flat(str(index.data(TEXT_ROLE) or "")) or " "
        width = self.text_width(rect.width())
        text_h, widest, exact = self.measure(text, width)
        bubble_w = widest + 2 * BUBBLE_X
        left, right = rect.left() + PAD_X + GUTTER, rect.left() + rect.width() - PAD_X
        x, y = (right - bubble_w if mine else left), rect.top() + PAD_Y
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if index.data(HEAD_ROLE):
            self._paint_head(painter, t, index, mine, (left, right), y)
            y += self.head_h + HEAD_GAP
        painter.setPen(Qt.PenStyle.NoPen if mine else QPen(qcolor(t.stroke), 1))
        painter.setBrush(qcolor(t.selected if mine else t.window))
        painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, bubble_w - 1, text_h + 2 * BUBBLE_Y - 1), RADIUS, RADIUS)
        painter.setPen(qcolor(t.on_selected(t.text, mine)))
        painter.setFont(self.body)
        if exact:
            self._layout(text, width).draw(painter, QPointF(x + BUBBLE_X, y + BUBBLE_Y))
        else:
            painter.drawText(QRect(x + BUBBLE_X, y + BUBBLE_Y, width, text_h), WORD_FLAGS, text)
        if index.data(FLAG_ROLE):
            device = painter.device()
            dpr = device.devicePixelRatioF() if device is not None else 1.0
            fx = x - GAP - FLAG if mine else rect.left() + PAD_X + (GUTTER - FLAG) // 2
            painter.drawPixmap(QPointF(fx, y + BUBBLE_Y + 1), icons.pixmap("flag", t.tone(Tone.ACCENT), FLAG, dpr))
        painter.restore()

    def _paint_head(self, painter: QPainter, t: Tokens, index: QModelIndex | QPersistentModelIndex, mine: bool,
                    span: tuple[int, int], top: int) -> None:
        """O cabeçalho numa linha: o seu, só o [mm:ss] à direita; o dos outros, o ponto, o nome, o cargo e o [mm:ss]."""
        left, right = span
        when = f"[{stamp(int(index.data(STAMP_ROLE) or 0))}]"
        when_w = self._caption_fm.horizontalAdvance(when)
        base = top + self._strong_fm.ascent()
        name, job = (str(index.data(r) or "") for r in (SPEAKER_ROLE, ROLE_ROLE))
        if mine or not name:
            painter.setFont(self.caption)
            painter.setPen(qcolor(t.secondary))
            painter.drawText(QPointF(right - when_w if mine else left, base), when)
            return
        color = index.data(COLOR_ROLE)
        if color is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.speaker(int(color))))
            painter.drawEllipse(QPointF(left - GUTTER / 2, base - self.body_fm.xHeight() / 2), DOT / 2, DOT / 2)
        room = right - left - GAP - when_w
        tail = f" — {job}" if job else ""
        name_w, tail_w = self._strong_fm.horizontalAdvance(name), self.body_fm.horizontalAdvance(tail)
        if name_w + tail_w > room:                       # sem espaço: o cargo sai primeiro, depois o nome é cortado
            tail, tail_w = "", 0
            if name_w > room:
                name = self._strong_fm.elidedText(name, Qt.TextElideMode.ElideRight, max(0, room))
                name_w = self._strong_fm.horizontalAdvance(name)
        painter.setFont(self.strong)
        painter.setPen(qcolor(t.text))
        painter.drawText(QPointF(left, base), name)
        if tail:
            painter.setFont(self.body)
            painter.setPen(qcolor(t.secondary))
            painter.drawText(QPointF(left + name_w, base), tail)
        painter.setFont(self.caption)
        painter.setPen(qcolor(t.secondary))
        painter.drawText(QPointF(left + name_w + tail_w + GAP, base), when)

    def clear(self) -> None:
        """As medidas guardadas saem (a fonte mudou; o texto e a largura já entram na chave)."""
        self._cache.clear()


__all__ = ["BUBBLE_X", "BUBBLE_Y", "GUTTER", "HEAD_ROLE", "MINE_ROLE", "PAD_X", "PAD_Y", "ChatDelegate"]
