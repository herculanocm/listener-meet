"""Repositório dos pedaços FLAC (`audio_chunks` em cada arquivo de áudio): o arquivo é resolvido pela reunião (M-19).
Órfãos (pedaços de reunião que não existe mais) são reportados, nunca apagados (CP3, M-18)."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, TypedDict, cast

from ..log import get_logger
from .core import Core, _now

if TYPE_CHECKING:
    from ..capture.sinks import EncodedChunk

log = get_logger("store.audio")


class ChunkMeta(TypedDict, total=False):
    id: int
    meeting_id: int
    channel: str
    idx: int
    t_start_ms: int
    frames: int
    sample_rate: int
    gap_frames: int
    bytes: int


class ChunkData(TypedDict):
    idx: int
    t_start_ms: int
    frames: int
    sample_rate: int
    flac: bytes


class ChannelStats(TypedDict):
    channel: str
    n: int
    frames: int
    gaps: int
    bytes: int
    t0: int
    t1: int
    sr: int


class ContinuityIssue(TypedDict):
    idx: int
    expected_ms: int
    got_ms: int
    delta_ms: int


class AudioRepo:
    def __init__(self, core: Core):
        self.core = core

    def _schema(self, meeting_id: int) -> str | None:
        return self.core.audio_schema_for_meeting(meeting_id)

    def insert_chunk(self, meeting_id: int, channel: str, idx: int, t_start_ms: int, frames: int,
                     sample_rate: int, gap_frames: int, flac: bytes) -> int:
        s = self._schema(meeting_id)
        if s is None:
            raise sqlite3.OperationalError(f"reunião {meeting_id} não tem arquivo de áudio")
        return self.core.insert(
            f"INSERT INTO {s}.audio_chunks(meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, flac, created_at) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (meeting_id, channel, idx, int(t_start_ms), int(frames), int(sample_rate), int(gap_frames), sqlite3.Binary(flac), _now()))

    def write_chunk(self, chunk: EncodedChunk) -> None:
        self.insert_chunk(chunk.meeting_id, chunk.channel, chunk.idx, chunk.t_start_ms, chunk.frames, chunk.sample_rate,
                          chunk.gap_frames, chunk.flac)

    def list_chunks(self, meeting_id: int, channel: str | None = None) -> list[ChunkMeta]:
        s = self._schema(meeting_id)
        if s is None:
            return []
        cols = "id, meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, length(flac) AS bytes"
        if channel:
            rows = self.core.q(f"SELECT {cols} FROM {s}.audio_chunks WHERE meeting_id = ? AND channel = ? ORDER BY idx",  # noqa: S608
                               (meeting_id, channel))
        else:
            rows = self.core.q(f"SELECT {cols} FROM {s}.audio_chunks WHERE meeting_id = ? ORDER BY channel, idx", (meeting_id,))  # noqa: S608
        return [cast(ChunkMeta, dict(r)) for r in rows]

    def get_chunks_range(self, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> list[ChunkData]:
        s = self._schema(meeting_id)
        if s is None:
            return []
        rows = self.core.q(f"SELECT idx, t_start_ms, frames, sample_rate, flac FROM {s}.audio_chunks "  # noqa: S608
                           "WHERE meeting_id = ? AND channel = ? AND t_start_ms < ? AND (t_start_ms + frames * 1000 / sample_rate) > ? "
                           "ORDER BY idx", (meeting_id, channel, int(t1_ms), int(t0_ms)))
        return [ChunkData(idx=r["idx"], t_start_ms=r["t_start_ms"], frames=r["frames"], sample_rate=r["sample_rate"], flac=r["flac"])
                for r in rows]

    def get_chunk(self, meeting_id: int, channel: str, idx: int) -> ChunkData | None:
        """Um pedaço só, com o FLAC (o player decodifica cada pedaço uma vez e corta janelas curtas dele, F5 R7-02)."""
        s = self._schema(meeting_id)
        if s is None:
            return None
        rows = self.core.q(f"SELECT idx, t_start_ms, frames, sample_rate, flac FROM {s}.audio_chunks "  # noqa: S608
                           "WHERE meeting_id = ? AND channel = ? AND idx = ? LIMIT 1", (meeting_id, channel, int(idx)))
        if not rows:
            return None
        r = rows[0]
        return ChunkData(idx=r["idx"], t_start_ms=r["t_start_ms"], frames=r["frames"], sample_rate=r["sample_rate"], flac=r["flac"])

    def chunk_stats(self, meeting_id: int) -> dict[str, ChannelStats]:
        s = self._schema(meeting_id)
        if s is None:
            return {}
        out: dict[str, ChannelStats] = {}
        for r in self.core.q(f"SELECT channel, COUNT(*) AS n, SUM(frames) AS frames, SUM(gap_frames) AS gaps, SUM(length(flac)) AS bytes, "  # noqa: S608
                             f"MIN(t_start_ms) AS t0, MAX(t_start_ms + frames * 1000 / sample_rate) AS t1, MAX(sample_rate) AS sr "
                             f"FROM {s}.audio_chunks WHERE meeting_id = ? GROUP BY channel", (meeting_id,)):
            out[r["channel"]] = cast(ChannelStats, dict(r))
        return out

    def covered_ms(self, meeting_id: int) -> int | None:
        """Fim do último pedaço (ms): duração de uma reunião interrompida (M-05)."""
        s = self._schema(meeting_id)
        if s is None:
            return None
        row = self.core.q(f"SELECT MAX(t_start_ms + frames * 1000 / sample_rate) AS t1 FROM {s}.audio_chunks WHERE meeting_id = ?",  # noqa: S608
                          (meeting_id,))
        return int(row[0]["t1"]) if row and row[0]["t1"] is not None else None

    def continuity(self, meeting_id: int, channel: str, tolerance_ms: int = 50) -> list[ContinuityIssue]:
        """Buracos ou sobreposições entre pedaços consecutivos (para o gate G1)."""
        issues: list[ContinuityIssue] = []
        prev_end: int | None = None
        for c in self.list_chunks(meeting_id, channel):
            if prev_end is not None and abs(c["t_start_ms"] - prev_end) > tolerance_ms:
                issues.append(ContinuityIssue(idx=c["idx"], expected_ms=prev_end, got_ms=c["t_start_ms"],
                                              delta_ms=c["t_start_ms"] - prev_end))
            prev_end = c["t_start_ms"] + round(c["frames"] * 1000 / c["sample_rate"])
        return issues

    def delete_chunks(self, meeting_id: int) -> int:
        s = self._schema(meeting_id)
        if s is None:
            return 0
        return self.core.execute(f"DELETE FROM {s}.audio_chunks WHERE meeting_id = ?", (meeting_id,))  # noqa: S608

    def orphan_report(self) -> dict[str, list[int]]:
        """Por arquivo de áudio, reuniões com pedaços mas sem linha em `meetings`. Só reporta (CP3)."""
        out: dict[str, list[int]] = {}
        for file in self.core.audio_files():
            try:
                s = self.core.attach(file)
                rows = self.core.q(f"SELECT DISTINCT meeting_id FROM {s}.audio_chunks "  # noqa: S608
                                   "WHERE meeting_id NOT IN (SELECT id FROM main.meetings) ORDER BY meeting_id")
            except (sqlite3.Error, RuntimeError, FileNotFoundError) as e:
                log.warning("não consegui varrer %s: %s", file, e)
                continue
            ids = [int(r["meeting_id"]) for r in rows]
            if ids:
                out[file] = ids
        return out

    def db_sizes(self) -> dict[str, Any]:
        return self.core.db_sizes()
