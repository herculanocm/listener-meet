"""Legendas ao vivo (`captions`, F4 item 6): gravadas pelo processo `--live` (papel WORKER) uma a uma, nunca apagadas
(CP3); a passagem final da F2 só as substitui na exibição. `label` é o texto mostrado ("Ana — CFO", "Participante" ou o
usuário no canal L); `confidence` é o cosseno do nome escolhido (nulo sem embedding); `model` é o Whisper que transcreveu
(mostra a degradação); `latency_ms` = agora - (início da sessão + fim da janela), medida no mesmo relógio da captura."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypedDict, cast

from .core import Core, _now


class CaptionRow(TypedDict, total=False):
    id: int
    meeting_id: int
    t_start_ms: int
    t_end_ms: int
    text: str
    person_id: int | None
    created_at: str
    channel: str | None
    confidence: float | None
    label: str | None
    model: str | None
    latency_ms: int | None


@dataclass(frozen=True, slots=True)
class CaptionInsert:
    meeting_id: int
    channel: str
    t_start_ms: int
    t_end_ms: int
    text: str
    person_id: int | None = None
    label: str | None = None
    confidence: float | None = None
    model: str | None = None
    latency_ms: int | None = None


class CaptionsRepo:
    def __init__(self, core: Core):
        self.core = core

    def insert(self, row: CaptionInsert) -> int:
        return self.core.insert(
            "INSERT INTO captions(meeting_id, channel, t_start_ms, t_end_ms, text, person_id, label, confidence, model, latency_ms, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.meeting_id, row.channel, int(row.t_start_ms), int(row.t_end_ms), row.text, row.person_id, row.label,
             None if row.confidence is None else float(row.confidence), row.model,
             None if row.latency_ms is None else int(row.latency_ms), _now()))

    def list_for_meeting(self, meeting_id: int, after_id: int = 0, limit: int | None = None) -> list[CaptionRow]:
        """Em ordem de tempo; `after_id` traz só as novas (resumo parcial e janela "Ao vivo" leem por incremento)."""
        sql = "SELECT * FROM captions WHERE meeting_id = ? AND id > ? ORDER BY t_start_ms, id"
        params: tuple[int, ...] = (meeting_id, int(after_id))
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, int(limit))
        return [cast(CaptionRow, dict(r)) for r in self.core.q(sql, params)]

    def count(self, meeting_id: int) -> int:
        return int(self.core.q("SELECT COUNT(*) FROM captions WHERE meeting_id = ?", (meeting_id,))[0][0])

    def last_id(self, meeting_id: int) -> int:
        """Maior id das legendas da reunião (0 sem legenda): o resumo parcial só roda com legendas novas (F4 L5)."""
        return int(self.core.q("SELECT COALESCE(MAX(id), 0) FROM captions WHERE meeting_id = ?", (meeting_id,))[0][0])

    def start_times(self, meeting_id: int, ids: Iterable[int]) -> dict[int, int]:
        """id -> `t_start_ms` das legendas pedidas que são desta reunião (o `caption_id` que a LLM citou vira `t_ms`)."""
        wanted = sorted({int(i) for i in ids})
        if not wanted:
            return {}
        marks = ",".join("?" * len(wanted))
        rows = self.core.q(f"SELECT id, t_start_ms FROM captions WHERE meeting_id = ? AND id IN ({marks})",  # noqa: S608
                           (meeting_id, *wanted))
        return {int(r["id"]): int(r["t_start_ms"] or 0) for r in rows}
