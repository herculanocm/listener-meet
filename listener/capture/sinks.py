"""Fan-out da captura (F1.2 M-17): o gravador publica pedaços codificados e eventos para quem implementar estes Protocols.
`Store` implementa os dois (SQLite); a F4 liga um `LiveSink`, a F2 um `JobScheduler`; os testes usam `MemorySink`.
O pacote `capture` não importa `store`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..model import EventType, MeetingStatus


@dataclass(frozen=True, slots=True)
class EncodedChunk:
    meeting_id: int
    channel: str
    idx: int
    t_start_ms: int
    frames: int
    sample_rate: int
    gap_frames: int
    flac: bytes

    @property
    def key(self) -> str:
        return f"{self.channel}{self.idx}"


class ChunkSink(Protocol):
    def write_chunk(self, chunk: EncodedChunk) -> None: ...


class EventSink(Protocol):
    def add_event(self, meeting_id: int | None, t_ms: int | None, type_: EventType | str, data: dict[str, Any] | None = None) -> int: ...
    def add_marker(self, meeting_id: int, t_ms: int, note: str = "") -> int: ...
    def finish_meeting(self, meeting_id: int, duration_ms: int, status: MeetingStatus = ...) -> None: ...
    def delete_meeting(self, meeting_id: int) -> None: ...


class RecorderSink(ChunkSink, EventSink, Protocol):
    """O destino principal do gravador: pedaços com nova tentativa, eventos e ciclo da reunião. `Store` serve."""
    def close_thread(self) -> None: ...


class MemorySink:
    """Guarda tudo em memória: testes sem banco e medições."""

    def __init__(self, fail_times: int = 0) -> None:
        self.chunks: list[EncodedChunk] = []
        self.events: list[tuple[int | None, int | None, str, dict[str, Any]]] = []
        self.markers: list[tuple[int, int, str]] = []
        self.finished: list[tuple[int, int, str]] = []
        self.deleted: list[int] = []
        self.fail_times = fail_times      # próximas N gravações falham com OperationalError (simula banco preso)
        self.attempts = 0

    def write_chunk(self, chunk: EncodedChunk) -> None:
        import sqlite3

        self.attempts += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise sqlite3.OperationalError("database is locked")
        self.chunks.append(chunk)

    def add_event(self, meeting_id: int | None, t_ms: int | None, type_: EventType | str, data: dict[str, Any] | None = None) -> int:
        self.events.append((meeting_id, t_ms, str(type_), dict(data or {})))
        return len(self.events)

    def add_marker(self, meeting_id: int, t_ms: int, note: str = "") -> int:
        self.markers.append((meeting_id, t_ms, note))
        return len(self.markers)

    def finish_meeting(self, meeting_id: int, duration_ms: int, status: MeetingStatus = MeetingStatus.RECORDED) -> None:
        self.finished.append((meeting_id, duration_ms, str(status)))

    def delete_meeting(self, meeting_id: int) -> None:
        self.deleted.append(meeting_id)

    def close_thread(self) -> None:
        pass
