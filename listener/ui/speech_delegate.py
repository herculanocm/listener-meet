"""U-081 (F5.4 L4): a fala na transcrição da reunião e a legenda do Ao vivo. Duas colunas: à esquerda o [mm:ss] em legenda
(12 px, no secundário) e o ponto na cor do falante (o mesmo `speaker` da faixa dele na linha do tempo); à direita o falante
em negrito, o cargo no secundário e o texto, que quebra embaixo do nome. No Ao vivo, a legenda que virou marcador leva a
bandeira à esquerda do tempo.

O item diz o que mostrar por papéis (`STAMP_ROLE` e os outros); o `DisplayRole` segue com a linha inteira, para o leitor de
tela e os testes. Sem `dataChanged` e sem folha: o fundo (a seleção, o passar do mouse, a fala que toca) vem do
`CE_ItemViewItem` do estilo com o texto vazio, e as cores, dos tokens da janela na hora da pintura (no contraste alto, a
linha selecionada leva o texto do destaque, R-03).

A altura (revisão da F5.4, R-06): o número de linhas sai do C++ (`QFontMetrics.boundingRect` com a quebra por palavra), uma
chamada por fala; o `QTextLayout` montado em Python por fala e por largura deixava o redimensionar ~60 % mais lento numa
transcrição de 3.000 falas. O nome em negrito fica fora da medida: o texto começa numa tabulação na largura do cabeçalho (o
nome, o cargo e os dois espaços) e a pintura usa as mesmas opções (a mesma quebra da medida); o cabeçalho é pintado à parte
na primeira linha. Quando o cabeçalho não cabe na linha ou uma palavra não cabe numa linha, o desenho de antes (um
`QTextLayout` com o nome e o texto juntos, que quebra o cabeçalho e corta a palavra longa). As alturas ficam guardadas por
texto e largura (a conta só volta quando a largura muda)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QFontMetricsF, QPainter, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ..llm.prompts.common import stamp
from . import icons
from .theme import Tone, font_for, qcolor, surface_tokens

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
SEP = "  "                        # entre o cabeçalho e o texto
EXACT = -1                        # a medida no desenho de antes (o cabeçalho ou uma palavra não cabe na linha)
TALL = 1 << 20                    # a altura livre da medida
WORD_FLAGS = int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
FLAT = str.maketrans({"\n": " ", "\r": " ", "\t": " ", "\x9c": " "})   # o que o `qt_format_text` trataria diferente da pintura
FLAT_CHARS = tuple(map(chr, FLAT))

type Measure = tuple[int, int]    # a altura do texto e onde ele começa na primeira linha (`EXACT`: o desenho de antes)


@dataclass(frozen=True, slots=True)
class _FontFacts:
    """As medidas do corpo de uma fonte: a altura de uma linha e o passo entre linhas na medida do C++ (que soma o
    `leading`) e a altura de uma linha na pintura (o `QTextLayout`, sem o `leading`), como antes."""
    font: QFont
    metrics: QFontMetrics
    one: int
    step: int
    line_h: float


class SpeechDelegate(QStyledItemDelegate):
    """O desenho de uma fala (ou legenda). `flags` reserva a coluna da bandeira (Ao vivo)."""

    def __init__(self, parent: QWidget | None = None, *, flags: bool = False) -> None:
        super().__init__(parent)
        self.flags = flags
        self.caption = font_for("caption")
        self.strong = font_for("body_strong")
        self._caption_fm = QFontMetrics(self.caption)
        self._strong_fm = QFontMetricsF(self.strong)
        self.stamp_w = self._caption_fm.horizontalAdvance(STAMP_SAMPLE)
        self._gutter = PAD_X + (FLAG + GAP // 2 if flags else 0) + self.stamp_w + GAP + DOT + GAP
        self._heights: dict[tuple[str, str, str, int], Measure] = {}
        self._indents: dict[tuple[str, str], int] = {}
        self._facts: _FontFacts | None = None
        self._wrap = QTextOption()
        self._wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._strong_fmt = _format(self.strong, None)         # a medida não usa cor: um formato só, feito uma vez

    # ---------------------------------------------------------------- geometria
    def gutter(self) -> int:
        """A largura da coluna da esquerda (a bandeira, o tempo e o ponto), medida uma vez."""
        return self._gutter

    def _font_facts(self, body: QFont) -> _FontFacts:
        if self._facts is None or self._facts.font != body:
            fm = QFontMetrics(body)
            one = fm.boundingRect(QRect(0, 0, TALL, TALL), WORD_FLAGS, "x").height()
            step = fm.boundingRect(QRect(0, 0, TALL, TALL), WORD_FLAGS, "x\nx").height() - one
            probe = QTextLayout("x", body)
            _lay_lines(probe, TALL)
            line_h = probe.lineAt(0).height()
            self._facts = _FontFacts(QFont(body), fm, one, max(1, step), line_h)
        return self._facts

    def _indent(self, body: QFont, name: str, role: str) -> int:
        """Onde o texto começa na primeira linha: o nome em negrito, o " — cargo" e os dois espaços, para cima no px inteiro
        (a tabulação do Qt é em px inteiros); 0 sem nome. Guardado por falante (são poucos)."""
        if not name:
            return 0
        key = (name, role)
        found = self._indents.get(key)
        if found is None:
            tail = f" — {role}{SEP}" if role else SEP
            found = math.ceil(self._strong_fm.horizontalAdvance(name) + QFontMetricsF(body).horizontalAdvance(tail))
            self._indents[key] = found
        return found

    def _measure(self, body: QFont, name: str, role: str, text: str, width: int) -> Measure:
        """A altura do texto em `width` e onde ele começa (`EXACT`: no desenho de antes), guardadas por texto e largura."""
        key = (name, role, text, width)
        found = self._heights.get(key)
        if found is not None:
            return found
        facts = self._font_facts(body)
        indent = self._indent(body, name, role)
        flat = _flat(text)
        found = (0, EXACT)
        if flat and indent <= width:
            room = QRect(0, 0, width, TALL)
            r = (facts.metrics.boundingRect(room, WORD_FLAGS, "\t" + flat, 0, [indent]) if indent else
                 facts.metrics.boundingRect(room, WORD_FLAGS, flat))
            if r.width() <= width:                       # nenhuma palavra passou da linha
                lines = 1 + round((r.height() - facts.one) / facts.step)
                found = (round(lines * facts.line_h), indent)
        if found[1] == EXACT:
            found = (round(self._layout(body, name, role, text, width).boundingRect().height()), EXACT)
        if len(self._heights) >= CACHE_MAX:
            self._heights.clear()
        self._heights[key] = found
        return found

    def _layout(self, body: QFont, name: str, role: str, text: str, width: int, ink: tuple[str, str] | None = None) -> QTextLayout:
        """O desenho de antes: o nome em negrito, o cargo no secundário e o texto, quebrando em `width` (com `ink`, as cores
        do texto e do secundário)."""
        head = f"{name} — {role}" if name and role else name
        full = f"{head}{SEP}{text}" if head else text
        layout = QTextLayout(full, body)
        layout.setTextOption(self._wrap)
        if name:
            ranges = [_range(0, len(name), self._strong_fmt if ink is None else _format(self.strong, ink[0]))]
            if role:
                ranges.append(_range(len(name), len(head) - len(name), _format(None, ink[1] if ink is not None else None)))
            layout.setFormats(ranges)
        _lay_lines(layout, width)
        return layout

    def body_layout(self, body: QFont, text: str, indent: int, width: int) -> QTextLayout:
        """A pintura do texto com as opções da medida: a tabulação na largura do cabeçalho e a quebra por palavra."""
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(Qt.AlignmentFlag.AlignLeft)
        option.setTextDirection(Qt.LayoutDirection.LeftToRight)
        if indent:
            option.setTabArray([float(indent)])
        flat = _flat(text)
        layout = QTextLayout(("\t" + flat) if indent else flat, body)
        layout.setTextOption(option)
        _lay_lines(layout, width)
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
        height = self._measure(option.font, name, role, text, self._text_width(item_width))[0]
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
        selected = bool(opt.state & QStyle.StateFlag.State_Selected) and opt.backgroundBrush.style() == Qt.BrushStyle.NoBrush
        text_ink, note_ink = t.on_selected(t.text, selected), t.on_selected(t.secondary, selected)   # R-03
        rect = opt.rect
        top = rect.top() + PAD_Y
        painter.save()
        x = rect.left() + PAD_X
        if self.flags:
            if index.data(FLAG_ROLE):
                dpr = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
                painter.drawPixmap(QPointF(x, top + 1), icons.pixmap("flag", t.on_selected(t.tone(Tone.ACCENT), selected), FLAG, dpr))
            x += FLAG + GAP // 2
        cap_fm, stamp_w = self._caption_fm, self.stamp_w
        painter.setFont(self.caption)
        painter.setPen(qcolor(note_ink))
        body_ascent = QFontMetrics(opt.font).ascent()
        base = top + body_ascent                            # o tempo na mesma linha de base da primeira linha do texto
        painter.drawText(QRectF(x, base - cap_fm.ascent(), stamp_w, cap_fm.height()),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), stamp(int(index.data(STAMP_ROLE) or 0)))
        x += stamp_w + GAP
        color = index.data(COLOR_ROLE)
        if color is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.on_selected(t.speaker(int(color)), selected)))
            center_y = base - QFontMetrics(opt.font).xHeight() / 2
            painter.drawEllipse(QPointF(x + DOT / 2, center_y), DOT / 2, DOT / 2)
        self._paint_text(painter, opt.font, index, QPointF(rect.left() + self.gutter(), top), self._text_width(rect.width()),
                         (text_ink, note_ink))
        painter.restore()

    def _paint_text(self, painter: QPainter, body: QFont, index: QModelIndex | QPersistentModelIndex, at: QPointF, width: int,
                    ink: tuple[str, str]) -> None:
        """O cabeçalho e o texto: com a tabulação (o cabeçalho pintado à parte na primeira linha) ou no desenho de antes."""
        name, role, text = (str(index.data(r) or "") for r in (SPEAKER_ROLE, ROLE_ROLE, TEXT_ROLE))
        indent = self._measure(body, name, role, text, width)[1]
        painter.setPen(qcolor(ink[0]))
        if indent == EXACT:
            self._layout(body, name, role, text, width, ink).draw(painter, at)
            return
        layout = self.body_layout(body, text, indent, width)
        if name:
            baseline = at.y() + layout.lineAt(0).ascent()
            painter.setFont(self.strong)
            painter.drawText(QPointF(at.x(), baseline), name)
            if role:
                painter.setFont(body)
                painter.setPen(qcolor(ink[1]))
                painter.drawText(QPointF(at.x() + self._strong_fm.horizontalAdvance(name), baseline), f" — {role}")
                painter.setPen(qcolor(ink[0]))
        layout.draw(painter, at)

    def clear(self) -> None:
        """As alturas guardadas saem (a lista trocou de fonte; o texto e a largura já entram na chave)."""
        self._heights.clear()
        self._indents.clear()
        self._facts = None


def _flat(text: str) -> str:
    """A quebra de linha, a tabulação e o U+009C do texto viram espaço (o `qt_format_text` da medida os trataria diferente
    da pintura); a checagem antes da troca custa uma fração dela (3.000 falas: 0,4 contra 32 ms)."""
    if any(c in text for c in FLAT_CHARS):
        return text.translate(FLAT)
    return text


def _lay_lines(layout: QTextLayout, width: int) -> None:
    """As linhas de `layout` em `width`, uma embaixo da outra (sem o `leading`, como sempre foi)."""
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


__all__ = ["COLOR_ROLE", "EXACT", "FLAG_ROLE", "ROLE_ROLE", "SPEAKER_ROLE", "STAMP_ROLE", "TEXT_ROLE", "SpeechDelegate", "speech_roles"]
