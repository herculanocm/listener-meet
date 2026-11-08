"""A tela do vídeo da reunião (F5.19, item 3): uma `Screen` desenhada em RGB por QPainter num QImage, nos tokens escuros.

No topo, o título (cortado com "…") e a data, com um traço embaixo. Embaixo, presa ao pé, a legenda atual: o ponto na cor
do falante (U-081), o nome em negrito, o cargo no secundário e o texto em letra grande, que encolhe até caber em
`Look.lines` linhas. Acima dela, a anterior, menor e apagada (sem apagar se ainda fala); do mesmo falante, ela vira o
começo do mesmo parágrafo, debaixo de um cabeçalho só. Sem espaço para as duas, só a atual.

O desenhista é montado na thread da interface (fontes, cores e o fundo com o topo) e usado só pela tarefa depois: o Qt
permite QPainter num QImage fora da thread da interface (sonda de 07/11). A medida do texto fica guardada por texto."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPainter

from .media_cues import Cue, Screen
from .theme import make_font, qcolor
from .tokens import DARK, FONTS, FontToken, Tokens

WIDTH, HEIGHT = 1280, 720
WRAP = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
ANYWHERE = int(Qt.TextFlag.TextWrapAnywhere | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)   # palavra maior que a linha
LEFT_MID = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
RIGHT_MID = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
FIT_CACHE = 512                  # medidas guardadas (texto e tamanho); passou disso, recomeça


@dataclass(frozen=True, slots=True)
class Look:
    """As medidas da tela em px (1280 x 720)."""
    margin: int = 64
    top_h: int = 104             # a faixa do título; o traço fica na base dela
    bottom: int = 64
    title_px: int = 28
    date_px: int = 22
    name_px: int = 30
    text_px: int = 42
    min_text_px: int = 28
    context_name_px: int = 24
    context_text_px: int = 32
    lines: int = 3
    gap: int = 28                # entre a anterior e a atual
    header_gap: int = 6          # entre o cabeçalho e o texto
    dot: int = 14
    dim: float = 0.5             # a anterior que já terminou
    live: float = 0.85           # a anterior que ainda fala


@dataclass(frozen=True, slots=True)
class _Text:
    font: QFont
    height: int
    flags: int = WRAP


class FrameRenderer:
    """`render(screen)`: a tela em RGB (`numpy` H x W x 3, uint8)."""

    def __init__(self, title: str, date: str, cues: Sequence[Cue], *, size: tuple[int, int] = (WIDTH, HEIGHT),
                 tokens: Tokens = DARK, look: Look | None = None):
        self.cues = cues
        self.width, self.height = size
        self.t = tokens
        self.look = look or Look()
        lk = self.look
        self.text_w = self.width - 2 * lk.margin
        self._fits: dict[tuple[str, int], _Text] = {}
        self._fonts: dict[tuple[int, bool], QFont] = {}
        self.base = self._background(title, date)

    # ---------------------------------------------------------------- fontes e medidas
    def font(self, px: int, strong: bool = False) -> QFont:
        key = (px, strong)
        if key not in self._fonts:
            token = FONTS.body_strong if strong else FONTS.body
            self._fonts[key] = make_font(FontToken(token.family, px, token.semibold))
        return self._fonts[key]

    def fit(self, text: str, px: int) -> _Text:
        """A maior letra de `px` para baixo (de 2 em 2, até `min_text_px`) em que o texto cabe em `lines` linhas."""
        key = (text, px)
        hit = self._fits.get(key)
        if hit is not None:
            return hit
        lk = self.look
        size = px
        while True:
            font = self.font(size)
            fm = QFontMetrics(font)
            box = fm.boundingRect(QRect(0, 0, self.text_w, 100_000), WRAP, text)
            flags = WRAP if box.width() <= self.text_w else ANYWHERE   # uma palavra maior que a linha quebra em qualquer ponto
            height = box.height() if flags == WRAP else fm.boundingRect(QRect(0, 0, self.text_w, 100_000), flags, text).height()
            if height <= lk.lines * fm.lineSpacing() or size <= lk.min_text_px:
                break
            size -= 2
        if len(self._fits) >= FIT_CACHE:
            self._fits.clear()
        self._fits[key] = out = _Text(font, min(height, lk.lines * fm.lineSpacing()), flags)
        return out

    def header_h(self, px: int) -> int:
        return QFontMetrics(self.font(px, True)).height()

    # ---------------------------------------------------------------- desenho
    def _background(self, title: str, date: str) -> QImage:
        """O fundo com o título, a data e o traço, desenhado uma vez (cada tela parte de uma cópia)."""
        lk, t = self.look, self.t
        img = QImage(self.width, self.height, QImage.Format.Format_RGB888)
        img.fill(qcolor(t.window))
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        date_font = self.font(lk.date_px)
        date_w = QFontMetrics(date_font).horizontalAdvance(date) if date else 0
        title_font = make_font(FontToken(FONTS.subtitle.family, lk.title_px, True))
        room = self.text_w - date_w - (lk.margin // 2 if date else 0)
        bar = QRectF(lk.margin, 0, self.text_w, lk.top_h)
        p.setFont(title_font)
        p.setPen(qcolor(t.text))
        p.drawText(bar, LEFT_MID,
                   QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, max(0, room)))
        if date:
            p.setFont(date_font)
            p.setPen(qcolor(t.secondary))
            p.drawText(bar, RIGHT_MID, date)
        p.setPen(qcolor(t.stroke))
        p.drawLine(lk.margin, lk.top_h, self.width - lk.margin, lk.top_h)
        p.end()
        return img

    def render(self, screen: Screen) -> np.ndarray:
        img = self.base.copy()
        if screen.current is not None:
            p = QPainter(img)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            self._paint(p, screen)
            p.end()
        return to_rgb(img)

    def _paint(self, p: QPainter, screen: Screen) -> None:
        lk = self.look
        if screen.current is None:
            return
        cur = self.cues[screen.current]
        prev = self.cues[screen.previous] if screen.previous is not None else None
        bottom = self.height - lk.bottom
        room = bottom - lk.top_h - lk.gap
        body = self.fit(cur.text, lk.text_px)
        if prev is not None and prev.speaker == cur.speaker:
            before = self.fit(prev.text, lk.text_px)
            head = self.header_h(lk.name_px)
            if head + lk.header_gap + before.height + body.height <= room:
                y = bottom - body.height
                self._text(p, cur.text, body, y, 1.0)
                y -= before.height
                self._text(p, prev.text, before, y, lk.live if screen.previous_live else lk.dim)
                self._header(p, cur, lk.name_px, y - lk.header_gap - head, 1.0)
                return
            prev = None
        top = self._block(p, cur, lk.name_px, body, bottom, 1.0)
        if prev is not None:
            ctx = self.fit(prev.text, lk.context_text_px)
            need = self.header_h(lk.context_name_px) + lk.header_gap + ctx.height
            if top - lk.gap - need >= lk.top_h + lk.gap:
                self._block(p, prev, lk.context_name_px, ctx, top - lk.gap, lk.live if screen.previous_live else lk.dim)

    def _block(self, p: QPainter, cue: Cue, name_px: int, body: _Text, bottom: int, alpha: float) -> int:
        """Uma legenda presa a `bottom` (cabeçalho e texto); devolve o topo dela."""
        y = bottom - body.height
        self._text(p, cue.text, body, y, alpha)
        head = self.header_h(name_px)
        top = y - self.look.header_gap - head
        self._header(p, cue, name_px, top, alpha)
        return top

    def _text(self, p: QPainter, text: str, fitted: _Text, y: int, alpha: float) -> None:
        p.setFont(fitted.font)
        p.setPen(qcolor(self.t.text, alpha))
        p.drawText(QRectF(self.look.margin, y, self.text_w, fitted.height), fitted.flags, text)

    def _header(self, p: QPainter, cue: Cue, px: int, y: int, alpha: float) -> None:
        """O ponto na cor do falante, o nome em negrito e, depois de um "·", o cargo no secundário (cortado com "…")."""
        lk, t = self.look, self.t
        strong, plain = self.font(px, True), self.font(px)
        fm = QFontMetrics(strong)
        h = fm.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(t.speaker(cue.color), alpha))
        p.drawEllipse(QRectF(lk.margin, y + (h - lk.dot) / 2, lk.dot, lk.dot))
        x = lk.margin + lk.dot + 12
        name = fm.elidedText(cue.name, Qt.TextElideMode.ElideRight, self.width - lk.margin - x)
        p.setFont(strong)
        p.setPen(qcolor(t.text, alpha))
        p.drawText(QRectF(x, y, self.width - lk.margin - x, h), LEFT_MID, name)
        if not cue.role:
            return
        x += fm.horizontalAdvance(name)
        role = f"  ·  {cue.role}"
        room = self.width - lk.margin - x
        if room > 0:
            p.setFont(plain)
            p.setPen(qcolor(t.secondary, alpha))
            p.drawText(QRectF(x, y, room, h), LEFT_MID, QFontMetrics(plain).elidedText(role, Qt.TextElideMode.ElideRight, room))


def to_rgb(img: QImage) -> np.ndarray:
    """Os pixels de um QImage RGB888 em `numpy` (H x W x 3), sem o enchimento de cada linha."""
    w, h = img.width(), img.height()
    raw = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    return raw[:, : w * 3].reshape(h, w, 3).copy()


__all__ = ["HEIGHT", "WIDTH", "FrameRenderer", "Look", "to_rgb"]
