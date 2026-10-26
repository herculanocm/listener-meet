"""Linha do tempo da reunião aberta (F5.1 itens 5 e 6; U-008, U-025; A-15, A-16, A-19) e onde está o som desta reunião.

`TimelineTab` é a aba "Linha do tempo", entre Marcadores e Chat: uma faixa por pessoa das falas ("Você" primeiro, as
outras por tempo de fala; voz sem pessoa fica com o rótulo dela, A-16), desenhada por `TimelineView` (`ui/timeline_view.py`:
barras, marcadores, pausas hachuradas, eixo em mm:ss, `Ctrl`+roda, clique que toca). Lê as falas da aba Transcrição
(`transcript.model.rows`, as mesmas na mesma ordem), os marcadores e as pausas da gravação (`pause_intervals`) a cada
recarga (A-15). Sem falas, o aviso "sem falas"; sem player, o clique avisa.

`PlaybackFollower` diz onde está o som desta reunião (U-025): entre as leituras de 100 ms do player a posição é
extrapolada pelo relógio (a do motor anda em degraus de um bloco, 0,25 s, e a leitura ainda atrasa até 100 ms: sem isto o
destaque chegaria a 350 ms de atraso), só depois de ela andar no ritmo do áudio (o começo de um som e o ±10 s ficam
parados até o áudio sair) e até 0,4 s além da última lida; pausado, fica; parado, com erro ou com outra reunião tocando,
None. O cursor daqui e o destaque da Transcrição seguem o mesmo sinal."""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .player import PAUSED, PLAYING, PlayerState, pause_intervals
from .timeline_view import EMPTY_TEXT, ZOOM_MAX, Lane, TimelineView

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow
    from ..store import Store
    from .player import Player

FOLLOW_MS = 50                   # enquanto toca, o cursor e o destaque andam a cada 50 ms
EXTRAP_MAX_MS = 400              # um bloco de 0,25 s mais a leitura de 100 ms, com folga
ADVANCE_SLACK_MS = 300           # "andou no ritmo do áudio": o passo cabe no tempo passado mais isto
YOU = "Você"
NO_PLAYER_TEXT = "Reprodução indisponível nesta janela."


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


def follow_position(t_ms: int, seen_at: float, now: float, advancing: bool) -> int:
    """A posição mostrada: a última lida mais o tempo passado desde que ela mudou, até `EXTRAP_MAX_MS`, quando o som anda."""
    return t_ms + min(EXTRAP_MAX_MS, max(0, round((now - seen_at) * 1000))) if advancing else t_ms


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


class TimelineTab(QWidget):
    """A aba "Linha do tempo" (A-15): a vista e a linha de aviso; `reload()` relê falas, marcadores, pausas e quem é você."""

    def __init__(self, store: Store, meeting_id: int, *, rows: Callable[[], Sequence[TranscriptRow]],
                 play: Callable[[int], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.rows_fn = rows
        self.play_fn = play
        self.view = TimelineView(self._play)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #888; font-size: 11px;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.note)
        self.reload()

    def reload(self) -> None:
        rows = list(self.rows_fn())
        m = self.store.get_meeting(self.meeting_id) or {}
        me = self.store.get_me()
        markers = [(int(k["t_ms"]), str(k.get("note") or "")) for k in self.store.list_markers(self.meeting_id)]
        spans = pause_intervals(self.store.list_events(self.meeting_id))
        duration = max([int(m.get("duration_ms") or 0), *(r.t_end_ms for r in rows), *(t for t, _ in markers),
                        *(a if b is None else b for a, b in spans)])
        lanes = build_lanes(rows, int(me["id"]) if me else None)
        self.view.set_data(rows, lanes, markers, [(a, duration if b is None else b) for a, b in spans], duration)
        self.note.setText(f"{len(lanes)} falante(s), {len(rows)} fala(s). Clique para ouvir daquele ponto; Ctrl + roda do mouse "
                          f"amplia até {ZOOM_MAX:.0f} vezes." if rows else EMPTY_TEXT)

    def set_cursor(self, t_ms: int | None) -> None:
        self.view.set_cursor(t_ms)

    def _play(self, t_ms: int) -> None:
        if self.play_fn is None:
            self.note.setText(NO_PLAYER_TEXT)
            return
        self.play_fn(t_ms)


__all__ = ["Lane", "PlaybackFollower", "TimelineTab", "TimelineView", "build_lanes", "follow_position"]
