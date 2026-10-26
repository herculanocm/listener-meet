"""Linha do tempo da reunião aberta (F5.1 itens 5 e 6; U-008, U-025; A-16, A-19): as faixas, as contas puras do desenho e
onde está o som desta reunião. O desenho e a aba "Linha do tempo" ficam em `ui/timeline_view.py`.

`build_lanes` faz uma faixa por pessoa das falas ("Você" primeiro, as outras por tempo de fala; voz sem pessoa fica com o
rótulo dela, A-16). As funções puras dão as falas visíveis por `searchsorted`, a barra sob o mouse, as barras de 2 px
lógicos no mínimo juntadas quando se tocam, o passo do eixo e o começo da fala para a dica.

`PlaybackFollower` diz onde está o som desta reunião (U-025): pausado, fica; parado, com erro ou com outra reunião tocando,
None. Entre as leituras de 100 ms do player a posição é extrapolada pelo relógio, só depois de ela andar no ritmo do áudio
(o começo de um som e o ±10 s ficam parados até o áudio sair) e até 0,4 s além da última lida. A posição do motor anda em
degraus de um bloco (0,25 s) e a leitura ainda atrasa até 100 ms: sem extrapolar, o cursor da linha do tempo chegaria a
~350 ms de atraso e andaria aos saltos, que é onde a extrapolação vale; o destaque da Transcrição, que já olha 150 ms à
frente, ficaria em ~200 ms. Os dois seguem o mesmo sinal."""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .player import PAUSED, PLAYING, PlayerState

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow
    from .player import Player

BAR_MIN_PX = 2.0                 # A-19: a barra mais fina; as vizinhas que se tocam viram uma só
TICK_MIN_PX = 90                 # rótulos do eixo a pelo menos isto um do outro
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
TIP_CHARS = 140
FOLLOW_MS = 50                   # enquanto toca, o cursor e o destaque andam a cada 50 ms
EXTRAP_MAX_MS = 400              # um bloco de 0,25 s mais a leitura de 100 ms, com folga
ADVANCE_SLACK_MS = 300           # "andou no ritmo do áudio": o passo cabe no tempo passado mais isto
YOU = "Você"


# ---------------------------------------------------------------- faixas e contas puras
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


def build_lanes(rows: Sequence[TranscriptRow], me_id: int | None) -> list[Lane]:
    """Uma faixa por pessoa (os canais dela juntos) e uma por voz sem pessoa (por canal e rótulo; o canal entra no rótulo
    quando o mesmo rótulo aparece em dois canais). "Você" (a pessoa do usuário) primeiro; as outras por tempo de fala."""
    groups: dict[tuple[Any, ...], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(("p", r.person_id) if r.person_id is not None else ("v", r.channel, r.name), []).append(i)
    voices = Counter(key[2] for key in groups if key[0] == "v")
    lanes: list[Lane] = []
    for key, idx in groups.items():
        idx.sort(key=lambda i: rows[i].t_start_ms)
        first = rows[idx[0]]
        mine = key[0] == "p" and me_id is not None and key[1] == me_id
        label = YOU if mine else (f"{first.name} (canal {first.channel})" if key[0] == "v" and voices[first.name] > 1 else first.name)
        starts = np.array([rows[i].t_start_ms for i in idx], np.int64)
        ends = np.maximum(np.array([rows[i].t_end_ms for i in idx], np.int64), starts)
        lanes.append(Lane(label, f"{first.name} — {first.role}" if first.role else first.name, tuple(idx), starts, ends,
                          np.maximum.accumulate(ends), int((ends - starts).sum()), mine))
    lanes.sort(key=lambda ln: (not ln.mine, -ln.speech_ms, int(ln.starts[0]), ln.label))
    return lanes


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


def follow_position(t_ms: int, seen_at: float, now: float, advancing: bool) -> int:
    """A posição mostrada: a última lida mais o tempo passado desde que ela mudou, até `EXTRAP_MAX_MS`, quando o som anda."""
    return t_ms + min(EXTRAP_MAX_MS, max(0, round((now - seen_at) * 1000))) if advancing else t_ms


# ---------------------------------------------------------------- onde está o som (U-025)
class PlaybackFollower(QObject):
    """A posição do som desta reunião (`moved`: ms da reunião, ou None), a cada 50 ms enquanto toca."""
    moved = Signal(object)

    def __init__(self, player: Player, meeting_id: int, parent: QObject | None = None, *, clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        self.meeting_id = meeting_id
        self.clock = clock
        self.position: int | None = None
        self._seen: tuple[int, float] | None = None       # (posição lida, quando ela mudou)
        self._advancing = False
        self._timer = QTimer(self)
        self._timer.setInterval(FOLLOW_MS)
        self._timer.timeout.connect(self._tick)
        player.state_changed.connect(self.on_state)
        player.position_changed.connect(self.on_state)
        self.on_state(player.state())

    def on_state(self, st: object) -> None:
        if not isinstance(st, PlayerState):
            return
        if st.meeting_id != self.meeting_id or st.state not in (PLAYING, PAUSED):
            self._halt(None)                              # parado, erro ou outra reunião: o destaque some
        elif st.state == PAUSED:
            self._halt(st.t_ms)                           # pausado: fica onde parou
        else:
            now = self.clock()
            if self._seen is None or st.t_ms != self._seen[0]:
                step = st.t_ms - self._seen[0] if self._seen is not None else -1
                elapsed = (now - self._seen[1]) * 1000 if self._seen is not None else 0.0
                self._advancing = 0 < step <= elapsed + ADVANCE_SLACK_MS     # andou no ritmo do áudio, não saltou
                self._seen = (st.t_ms, now)
            if not self._timer.isActive():
                self._timer.start()
            self._tick()

    def _halt(self, t_ms: int | None) -> None:
        self._timer.stop()
        self._seen, self._advancing = None, False
        self._emit(t_ms)

    def _tick(self) -> None:
        if self._seen is not None:
            self._emit(follow_position(*self._seen, self.clock(), self._advancing))

    def _emit(self, t_ms: int | None) -> None:
        if t_ms != self.position:
            self.position = t_ms
            self.moved.emit(t_ms)


__all__ = ["Lane", "PlaybackFollower", "bar_at", "build_lanes", "follow_position", "merge_spans", "opening", "tick_step_ms",
           "visible_slice"]
