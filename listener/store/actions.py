"""Ações da ata (`action_items`, F3 item 6): uma linha por ação com `summary_id`, `segment_id` da evidência, dono casado com
`people` quando houver, prazo absoluto devolvido pela LLM e `status` (`ActionStatus`). Mudanças humanas vão para
`revisions`; reprocessar religa `segment_id` pelo tempo (F3 item 8)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict, cast

from ..model import ActionStatus
from .core import Core, _now


def _rev(core: Core, entity: str, entity_id: int, field: str, before: Any, after: Any, author: str) -> None:
    core.insert("INSERT INTO revisions(entity, entity_id, field, before, after, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entity, entity_id, field, None if before is None else str(before), None if after is None else str(after), author, _now()))


class ActionRow(TypedDict, total=False):
    id: int
    meeting_id: int
    text: str | None
    owner_person_id: int | None
    owner_text: str | None
    due: str | None
    t_ms: int | None
    status: str | None
    summary_id: int | None
    segment_id: int | None
    job_id: int | None
    created_at: str | None


@dataclass(frozen=True, slots=True)
class ActionInsert:
    meeting_id: int
    summary_id: int
    text: str
    owner_text: str | None
    owner_person_id: int | None
    due: str | None                   # AAAA-MM-DD (texto livre do prazo fica em `text` quando a LLM não resolveu)
    t_ms: int | None
    segment_id: int | None
    job_id: int | None
    status: ActionStatus = ActionStatus.OPEN


class ActionsRepo:
    def __init__(self, core: Core):
        self.core = core

    def insert_many(self, rows: list[ActionInsert]) -> list[int]:
        sql = ("INSERT INTO action_items(meeting_id, summary_id, text, owner_text, owner_person_id, due, t_ms, segment_id, job_id, "
               "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        with self.core.transaction():
            return [self.core.insert(sql, (a.meeting_id, a.summary_id, a.text, a.owner_text, a.owner_person_id, a.due, a.t_ms, a.segment_id,
                                           a.job_id, str(a.status), _now())) for a in rows]

    def list_for_meeting(self, meeting_id: int, summary_id: int | None = None, include_superseded: bool = False) -> list[ActionRow]:
        sql = "SELECT * FROM action_items WHERE meeting_id = ?"
        params: list[Any] = [meeting_id]
        if summary_id is not None:
            sql += " AND summary_id = ?"
            params.append(summary_id)
        if not include_superseded:
            sql += " AND (status IS NULL OR status != ?)"
            params.append(str(ActionStatus.SUPERSEDED))
        return [cast(ActionRow, dict(r)) for r in self.core.q(sql + " ORDER BY t_ms, id", tuple(params))]

    def get(self, action_id: int) -> ActionRow | None:
        rows = self.core.q("SELECT * FROM action_items WHERE id = ?", (action_id,))
        return cast(ActionRow, dict(rows[0])) if rows else None

    def set_status(self, action_id: int, status: ActionStatus, author: str = "usuario") -> bool:
        row = self.get(action_id)
        if row is None or str(row.get("status")) == str(status):
            return False
        with self.core.transaction():
            self.core.execute("UPDATE action_items SET status = ? WHERE id = ?", (str(status), action_id))
            _rev(self.core, "action_item", action_id, "status", row.get("status"), str(status), author)
        return True

    def set_text(self, action_id: int, text: str, author: str = "usuario") -> bool:
        row = self.get(action_id)
        if row is None or (row.get("text") or "") == text:
            return False
        with self.core.transaction():
            self.core.execute("UPDATE action_items SET text = ? WHERE id = ?", (text, action_id))
            _rev(self.core, "action_item", action_id, "text", row.get("text"), text, author)
        return True

    def relink_segments(self, meeting_id: int, new_job_id: int) -> int:
        """Reprocessamento: `segment_id` passa ao segmento do job novo mais próximo em `t_ms` (mesmo canal quando houver)."""
        rows = self.core.q("SELECT a.id, a.t_ms, s.channel FROM action_items a LEFT JOIN segments s ON s.id = a.segment_id "
                           "WHERE a.meeting_id = ? AND a.t_ms IS NOT NULL", (meeting_id,))
        n = 0
        with self.core.transaction():
            for r in rows:
                if r["channel"] is None:
                    cand = self.core.q("SELECT id FROM segments WHERE meeting_id = ? AND job_id = ? ORDER BY ABS(t_start_ms - ?) LIMIT 1",
                                       (meeting_id, new_job_id, int(r["t_ms"])))
                else:
                    cand = self.core.q("SELECT id FROM segments WHERE meeting_id = ? AND job_id = ? AND channel = ? "
                                       "ORDER BY ABS(t_start_ms - ?) LIMIT 1", (meeting_id, new_job_id, r["channel"], int(r["t_ms"])))
                if cand:
                    n += self.core.execute("UPDATE action_items SET segment_id = ? WHERE id = ?", (int(cand[0]["id"]), int(r["id"])))
        return n
