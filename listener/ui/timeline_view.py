"""O desenho da linha do tempo da reunião (F5.1 item 5, U-008; A-19): o eixo em mm:ss em cima, os rótulos das faixas à
esquerda e as faixas com as falas como barras, os marcadores como traços (com a bandeira no eixo) e as pausas da gravação
hachuradas; o cursor do som que toca por cima de tudo. Ocupa a largura; `Ctrl`+roda amplia até 20 vezes ancorado no mouse
(Q1), com rolagem horizontal; com muitas pessoas, rolagem vertical. O clique toca do instante exato; num marcador, do
marcador menos 150 ms. A dica é texto puro (nome ou fala com "<b>" não vira HTML, R7-07). A pintura cobre só o
retângulo do evento, com `searchsorted` nas faixas, e a barra tem 2 px lógicos no mínimo: as mais finas que se tocam
viram uma (milhares de falas sem travar). Ampliada, a vista vira a página atrás do cursor, salvo nos 5 s depois de o
usuário rolar ou ampliar. A montagem das faixas e a aba ficam em `ui/timeline.py`."""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import numpy as np
from PySide6 import QtGui
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QHelpEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen, QPolygonF, QResizeEvent,
                           QWheelEvent)
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from ..llm.prompts.common import stamp
from ..pipeline.review import PLAY_LEAD_MS
from ..rag.export import fmt_duration

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow

AXIS_H, LANE_GAP = 24, 6         # px lógicos: régua em cima, faixas embaixo
LANE_H_MIN, LANE_H_MAX = 22, 34    # a faixa cresce até isto com poucas pessoas (alvo maior para o clique)
LABEL_PAD, LABEL_MIN_W, LABEL_MAX_W = 8, 64, 180
BAR_MIN_PX = 2.0                 # A-19: a barra mais fina; as vizinhas que se tocam viram uma só
ZOOM_MAX, ZOOM_STEP = 20.0, 1.25     # Q1: até 20 vezes, 1,25 vez por dente da roda
TICK_MIN_PX = 90                 # rótulos do eixo a pelo menos isto um do outro
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
MARKER_HIT_PX, BAR_HIT_PX = 4, 3     # folga do clique no marcador e da dica na barra
FOLLOW_PAUSE_S = 5.0             # A-19 e Q2: depois de o usuário rolar ou ampliar, 5 s sem seguir o cursor
FOLLOW_MARGIN_PX, FOLLOW_LEAD = 24, 0.1      # vira a página perto da borda; o cursor volta a 10 % da largura
WHEEL_PX = 60                    # rolagem horizontal por dente da roda
TIP_CHARS = 140
LANE_COLORS = ("#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac")
CURSOR_COLOR, MARKER_COLOR = "#e74c3c", "#e67e22"
EMPTY_TEXT = "Sem falas nesta reunião."


# ---------------------------------------------------------------- funções puras
@dataclass(frozen=True, slots=True, eq=False)
class Lane:
    """Uma faixa: as falas de uma pessoa (ou de uma voz sem pessoa) em ordem de início."""
    label: str                   # "Você", o nome ou o rótulo da voz
    title: str                   # a dica: "Nome — Cargo"
    rows: tuple[int, ...]        # índices nas falas da transcrição
    starts: np.ndarray           # ms, crescente
    ends: np.ndarray
    reach: np.ndarray            # o maior fim até cada fala (crescente): a primeira barra visível sai por `searchsorted`
    speech_ms: int
    mine: bool = False


def visible_slice(lane: Lane, t0: float, t1: float) -> tuple[int, int]:
    """[lo, hi) das falas da faixa que cruzam [t0, t1)."""
    lo = int(np.searchsorted(lane.reach, t0, side="right"))
    return lo, max(lo, int(np.searchsorted(lane.starts, t1, side="left")))


def bar_at(lane: Lane, t: float, tol: float) -> int | None:
    """A fala da faixa sob `t`, com folga `tol` (ms: barra fina também acha); a de início mais tarde."""
    j = int(np.searchsorted(lane.starts, t + tol, side="right")) - 1
    while j >= 0 and lane.reach[j] >= t - tol:
        if lane.ends[j] >= t - tol:
            return j
        j -= 1
    return None


def merge_spans(x0: Sequence[float], x1: Sequence[float], min_w: float = BAR_MIN_PX) -> list[tuple[float, float]]:
    """As barras em px, cada uma com `min_w` no mínimo, e as que se tocam juntas (milhares de falas viram poucos retângulos)."""
    out: list[tuple[float, float]] = []
    for a, end in zip(x0, x1, strict=True):
        b = max(end, a + min_w)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def tick_step_ms(ms_per_px: float, min_px: float = TICK_MIN_PX) -> int:
    """O passo do eixo: o menor da lista com os rótulos a pelo menos `min_px` um do outro."""
    for s in TICK_STEPS_S:
        if s * 1000 / max(ms_per_px, 1e-9) >= min_px:
            return s * 1000
    return TICK_STEPS_S[-1] * 1000


def opening(text: str, limit: int = TIP_CHARS) -> str:
    """O começo da fala para a dica, cortado numa palavra."""
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[:limit].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- a vista
class TimelineView(QAbstractScrollArea):
    """O eixo em cima, os rótulos à esquerda e as faixas, com rolagem horizontal (ampliada) e vertical (muitas pessoas).
    `play(t_ms)` recebe o ponto do clique; `set_cursor` move o cursor do som."""

    def __init__(self, play: Callable[[int], Any], parent: QWidget | None = None, *, clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        self.play = play
        self.clock = clock
        self.rows: Sequence[TranscriptRow] = ()
        self.lanes: list[Lane] = []
        self.markers: list[tuple[int, str]] = []
        self.pauses: list[tuple[int, int]] = []
        self.duration_ms = 0
        self.zoom = 1.0
        self.cursor_ms: int | None = None
        self.played: deque[int] = deque(maxlen=100)       # instrumentação dos testes: os pontos pedidos ao player
        self._label_w = LABEL_MIN_W
        self._lane_h = LANE_H_MIN
        self._track_seen = 1.0
        self._precise = 0.0                               # o deslocamento exato pedido por último (a barra guarda inteiros)
        self._user_scroll_at = float("-inf")
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.horizontalScrollBar().actionTriggered.connect(self.user_scrolled)
        self.setMinimumHeight(AXIS_H + 3 * (LANE_H_MIN + LANE_GAP))

    def set_data(self, rows: Sequence[TranscriptRow], lanes: list[Lane], markers: list[tuple[int, str]],
                 pauses: list[tuple[int, int]], duration_ms: int) -> None:
        self.rows, self.lanes, self.markers, self.pauses, self.duration_ms = rows, lanes, markers, pauses, max(0, duration_ms)
        widths = [self.fontMetrics().horizontalAdvance(ln.label) for ln in lanes]
        self._label_w = min(LABEL_MAX_W, max(LABEL_MIN_W, max(widths, default=0) + 2 * LABEL_PAD))
        self._update_bars()
        self.viewport().update()

    # ---------------------------------------------------------------- geometria
    def _track(self) -> float:
        return max(1.0, float(self.viewport().width() - self._label_w))

    def _scale(self) -> float:
        """px por ms."""
        return self._track() * self.zoom / max(1, self.duration_ms)

    def _offset(self) -> float:
        """O deslocamento horizontal em px: o exato pedido por último enquanto a barra estiver nele (sem o arredondamento,
        ampliar várias vezes seguidas não escorrega o instante sob o mouse); depois de o usuário mexer, o da barra."""
        value = self.horizontalScrollBar().value()
        return self._precise if round(self._precise) == value else float(value)

    def _set_offset(self, value: float) -> None:
        bar = self.horizontalScrollBar()
        self._precise = min(max(0.0, value), float(bar.maximum()))
        bar.setValue(round(self._precise))

    def x_of(self, t_ms: float) -> float:
        return self._label_w + t_ms * self._scale() - self._offset()

    def t_of(self, x: float) -> int:
        t = (x - self._label_w + self._offset()) / self._scale()
        return round(min(max(t, 0.0), float(self.duration_ms)))

    def _lane_top(self, i: int) -> int:
        return AXIS_H + LANE_GAP + i * (self._lane_h + LANE_GAP) - self.verticalScrollBar().value()

    def lane_at(self, y: float) -> int | None:
        rel = y - AXIS_H - LANE_GAP + self.verticalScrollBar().value()
        i, off = divmod(rel, self._lane_h + LANE_GAP)
        return int(i) if rel >= 0 and off <= self._lane_h and int(i) < len(self.lanes) else None

    def marker_at(self, x: float) -> tuple[int, str] | None:
        near = [(abs(self.x_of(t) - x), t, note) for t, note in self.markers]
        best = min(near, default=None)
        return (best[1], best[2]) if best is not None and best[0] <= MARKER_HIT_PX else None

    def _update_bars(self) -> None:
        track = self._track()
        self._track_seen = track
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        room = (self.viewport().height() - AXIS_H - LANE_GAP) // max(1, len(self.lanes)) - LANE_GAP
        self._lane_h = max(LANE_H_MIN, min(LANE_H_MAX, room))
        h.setRange(0, max(0, math.ceil(track * self.zoom - track)))
        h.setPageStep(int(track))
        h.setSingleStep(max(1, int(track / 20)))
        v.setRange(0, max(0, AXIS_H + LANE_GAP + len(self.lanes) * (self._lane_h + LANE_GAP) - self.viewport().height()))
        v.setPageStep(max(1, self.viewport().height() - AXIS_H))
        v.setSingleStep(self._lane_h + LANE_GAP)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """A janela ou a vista mudou de tamanho (também quando uma barra de rolagem aparece): o começo da vista fica no lugar."""
        left = self._offset() / (self._track_seen * self.zoom)
        super().resizeEvent(event)
        self._update_bars()
        self._set_offset(left * self._track() * self.zoom)

    # ---------------------------------------------------------------- ampliar, rolar e seguir o cursor
    def user_scrolled(self, *_args: object) -> None:
        self._user_scroll_at = self.clock()

    def zoom_at(self, x: float, factor: float) -> None:
        """Amplia (ou reduz) mantendo sob `x` o instante que estava lá (A-19), de 1 a 20 vezes (Q1)."""
        zoom = min(ZOOM_MAX, max(1.0, self.zoom * factor))
        if zoom == self.zoom or self.duration_ms <= 0:
            return
        x = max(float(self._label_w), x)
        t = (x - self._label_w + self._offset()) / self._scale()
        self.zoom = zoom
        self._update_bars()
        self._set_offset(t * self._scale() - (x - self._label_w))
        self.user_scrolled()
        self.viewport().update()

    def set_cursor(self, t_ms: int | None) -> None:
        old, self.cursor_ms = self.cursor_ms, t_ms
        if old == t_ms:
            return
        bar = self.horizontalScrollBar()
        if t_ms is not None and bar.maximum() > 0 and self.clock() - self._user_scroll_at >= FOLLOW_PAUSE_S:
            x = self.x_of(t_ms)
            if not self._label_w <= x <= self.viewport().width() - FOLLOW_MARGIN_PX:      # vira a página atrás do cursor
                self._set_offset(t_ms * self._scale() - FOLLOW_LEAD * self._track())
        for t in (old, t_ms):
            if t is not None:
                self.viewport().update(QRect(int(self.x_of(t)) - 7, 0, 15, self.viewport().height()))

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        dy = event.angleDelta().y()
        mods = event.modifiers()
        bar = self.horizontalScrollBar()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if dy:
                self.zoom_at(event.position().x(), ZOOM_STEP ** (dy / 120))
            event.accept()
        elif dy and bar.maximum() and (mods & Qt.KeyboardModifier.ShiftModifier or not self.verticalScrollBar().maximum()):
            bar.setValue(bar.value() - round(dy / 120 * WHEEL_PX))      # para baixo anda para a frente no tempo
            self.user_scrolled()
            event.accept()
        else:
            super().wheelEvent(event)

    # ---------------------------------------------------------------- clique e dica
    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if event.button() != Qt.MouseButton.LeftButton or self.duration_ms <= 0 or x < self._label_w:
            super().mousePressEvent(event)
            return
        marker = self.marker_at(x)
        t = max(0, marker[0] - PLAY_LEAD_MS) if marker is not None else self.t_of(x)     # A-19
        self.played.append(t)
        self.play(t)
        event.accept()

    def tooltip_at(self, x: float, y: float) -> str:
        """A dica em texto puro: o marcador, a fala (falante, tempo e começo), a pausa ou o instante."""
        if self.duration_ms <= 0:
            return ""
        lane_i = self.lane_at(y)
        if x < self._label_w:
            lane = self.lanes[lane_i] if lane_i is not None else None
            return f"{lane.title} · {fmt_duration(lane.speech_ms)} de fala" if lane is not None else ""
        t = self.t_of(x)
        marker = self.marker_at(x)
        if marker is not None:
            return f"Marcador [{stamp(marker[0])}]: {marker[1] or '(sem nota)'}\nclique para ouvir deste ponto"
        if lane_i is not None:
            lane = self.lanes[lane_i]
            j = bar_at(lane, t, BAR_HIT_PX / self._scale())
            if j is not None:
                row = self.rows[lane.rows[j]]
                return f"{lane.title} · {stamp(row.t_start_ms)} a {stamp(row.t_end_ms)}\n{opening(row.shown_text)}"
        pause = next(((a, b) for a, b in self.pauses if a <= t < b), None)
        if pause is not None:
            return f"Gravação pausada de {stamp(pause[0])} a {stamp(pause[1])}"
        return f"{stamp(t)} · clique para ouvir daqui"

    @override
    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            text = self.tooltip_at(event.pos().x(), event.pos().y())
            if text:     # texto do usuário (nome, fala, nota): parágrafo com o HTML escapado (R7-07, R8-02)
                QToolTip.showText(event.globalPos(), QtGui.Qt.convertFromPlainText(text, Qt.WhiteSpaceMode.WhiteSpaceNormal),
                                  self.viewport())
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().viewportEvent(event)

    # ---------------------------------------------------------------- pintura (só o retângulo do evento, A-19)
    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self.viewport())
        rect = event.rect()
        p.fillRect(rect, self.palette().base())
        if self.duration_ms > 0:
            pad = BAR_MIN_PX / self._scale()
            origin = self._offset() - self._label_w
            t0, t1 = (rect.left() + origin) / self._scale() - pad, (rect.right() + 1 + origin) / self._scale() + pad
            p.save()
            p.setClipRect(QRect(self._label_w, AXIS_H, self.viewport().width(), self.viewport().height()).intersected(rect))
            self._paint_lanes(p, rect, t0, t1)
            self._paint_spans(p, t0, t1, AXIS_H)
            p.restore()
            self._paint_axis(p, rect, t0, t1)
            self._paint_cursor(p)
        self._paint_labels(p, rect)
        p.end()

    def _paint_lanes(self, p: QPainter, rect: QRect, t0: float, t1: float) -> None:
        scale, origin = self._scale(), self._label_w - self._offset()
        for i, lane in enumerate(self.lanes):
            top = self._lane_top(i)
            if top > rect.bottom() or top + self._lane_h < rect.top():
                continue
            p.fillRect(QRect(self._label_w, top, self.viewport().width(), self._lane_h), self.palette().alternateBase())
            lo, hi = visible_slice(lane, t0, t1)
            color = QColor(LANE_COLORS[i % len(LANE_COLORS)])
            x0, x1 = (origin + lane.starts[lo:hi] * scale).tolist(), (origin + lane.ends[lo:hi] * scale).tolist()
            for a, b in merge_spans(x0, x1):
                p.fillRect(QRectF(a, top + 2, b - a, self._lane_h - 4), color)
        if not self.lanes:
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(QRect(self._label_w, AXIS_H, int(self._track()), self.viewport().height() - AXIS_H),
                       Qt.AlignmentFlag.AlignCenter, EMPTY_TEXT)

    def _paint_spans(self, p: QPainter, t0: float, t1: float, top: int) -> None:
        """As pausas da gravação hachuradas e os traços dos marcadores, de `top` até embaixo."""
        height = self.viewport().height()
        hatch = QColor(self.palette().color(QPalette.ColorRole.Text))
        hatch.setAlpha(110)
        for a, b in self.pauses:
            if b >= t0 and a <= t1:
                x = self.x_of(a)
                p.fillRect(QRectF(x, top, max(1.0, self.x_of(b) - x), height), QBrush(hatch, Qt.BrushStyle.BDiagPattern))
        p.setPen(QPen(QColor(MARKER_COLOR), 1))
        for t, _note in self.markers:
            if t0 <= t <= t1:
                p.drawLine(QPointF(self.x_of(t), top), QPointF(self.x_of(t), height))

    def _paint_axis(self, p: QPainter, rect: QRect, t0: float, t1: float) -> None:
        if rect.top() > AXIS_H:
            return
        pal = self.palette()
        band = QRect(self._label_w, 0, self.viewport().width(), AXIS_H)
        p.save()
        p.setClipRect(band)
        p.fillRect(band, pal.window())
        self._paint_spans(p, t0, t1, AXIS_H - 6)                 # a pausa e o marcador também na régua
        step = tick_step_ms(1 / self._scale())
        p.setPen(pal.color(QPalette.ColorRole.WindowText))
        t = max(0, math.floor(t0 / step)) * step
        while t <= min(t1, self.duration_ms):
            x = self.x_of(t)
            p.drawLine(QPointF(x, AXIS_H - 5), QPointF(x, AXIS_H))
            p.drawText(QPointF(x + 3, AXIS_H - 10), stamp(t))
            t += step
        p.setBrush(QColor(MARKER_COLOR))
        for mt, _note in self.markers:
            if t0 <= mt <= t1:
                x = self.x_of(mt)
                p.drawPolygon(QPolygonF([QPointF(x - 4, AXIS_H - 7), QPointF(x + 4, AXIS_H - 7), QPointF(x, AXIS_H - 1)]))
        p.restore()
        p.setPen(pal.color(QPalette.ColorRole.Mid))
        p.drawLine(self._label_w, AXIS_H, self.viewport().width(), AXIS_H)

    def _paint_cursor(self, p: QPainter) -> None:
        if self.cursor_ms is None:
            return
        x = self.x_of(self.cursor_ms)
        if self._label_w - 1 <= x <= self.viewport().width() + 1:
            p.setPen(QPen(QColor(CURSOR_COLOR), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.viewport().height()))
            p.setBrush(QColor(CURSOR_COLOR))
            p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)]))

    def _paint_labels(self, p: QPainter, rect: QRect) -> None:
        if rect.left() > self._label_w:
            return
        pal = self.palette()
        p.fillRect(QRect(0, 0, self._label_w, self.viewport().height()), pal.window())
        p.setPen(pal.color(QPalette.ColorRole.WindowText))
        fm = self.fontMetrics()
        for i, lane in enumerate(self.lanes):
            top = self._lane_top(i)
            if top + self._lane_h > AXIS_H:
                p.drawText(QRect(LABEL_PAD, top, self._label_w - 2 * LABEL_PAD, self._lane_h), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(lane.label, Qt.TextElideMode.ElideRight, self._label_w - 2 * LABEL_PAD))
        p.fillRect(QRect(0, 0, self._label_w, AXIS_H), pal.window())
        p.setPen(pal.color(QPalette.ColorRole.Mid))
        p.drawLine(self._label_w - 1, 0, self._label_w - 1, self.viewport().height())


__all__ = ["EMPTY_TEXT", "Lane", "TimelineView", "bar_at", "merge_spans", "opening", "tick_step_ms", "visible_slice"]
