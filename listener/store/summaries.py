"""Ata, resumo executivo e pré-resumo (`summaries`, F3 item 6; o título fica em `meetings.title_llm`). "Gerar de novo"
insere uma linha `atual` e marca a anterior `superada` com as ações dela, tudo em `revisions`; nada se apaga (CP3).
`text_llm` é o Markdown gerado pelo projeto do `data_json`; `text_edited` é a edição humana."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict, cast

from ..model import ActionStatus, SummaryKind, SummaryStatus
from .core import Core, _now


def _rev(core: Core, entity: str, entity_id: int, field: str, before: Any, after: Any, author: str) -> None:
    core.insert("INSERT INTO revisions(entity, entity_id, field, before, after, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entity, entity_id, field, None if before is None else str(before), None if after is None else str(after), author, _now()))


class SummaryRow(TypedDict, total=False):
    id: int
    meeting_id: int
    kind: str
    t_until_ms: int | None
    text_llm: str | None
    text_edited: str | None
    provider: str | None
    model: str | None
    created_at: str | None
    data_json: str | None
    prompt_version: str | None
    job_id: int | None
    call_id: int | None
    status: str | None
    language: str | None


@dataclass(frozen=True, slots=True)
class SummaryInsert:
    meeting_id: int
    kind: SummaryKind
    data_json: str | None
    text_llm: str
    provider: str
    model: str
    prompt_version: str
    job_id: int | None
    call_id: int | None
    language: str | None
    status: SummaryStatus = SummaryStatus.CURRENT
    t_until_ms: int | None = None      # F4: o resumo parcial cobre a reunião até aqui (fim da última legenda usada)


class SummariesRepo:
    def __init__(self, core: Core):
        self.core = core

    def insert(self, row: SummaryInsert, author: str = "llm") -> int:
        """Nova versão `atual`; a anterior da mesma espécie vira `superada` (e as ações dela) com `revisions`."""
        with self.core.transaction():
            if row.status == SummaryStatus.CURRENT:
                for old in self.core.q("SELECT id FROM summaries WHERE meeting_id = ? AND kind = ? AND status = ?",
                                       (row.meeting_id, str(row.kind), SummaryStatus.CURRENT)):
                    self._supersede(int(old["id"]), author)
            return self.core.insert(
                "INSERT INTO summaries(meeting_id, kind, data_json, text_llm, provider, model, prompt_version, job_id, call_id, language, "
                "status, t_until_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.meeting_id, str(row.kind), row.data_json, row.text_llm, row.provider, row.model, row.prompt_version, row.job_id,
                 row.call_id, row.language, str(row.status), None if row.t_until_ms is None else int(row.t_until_ms), _now()))

    def _supersede(self, summary_id: int, author: str) -> None:
        self.core.execute("UPDATE summaries SET status = ? WHERE id = ?", (SummaryStatus.SUPERSEDED, summary_id))
        _rev(self.core, "summary", summary_id, "status", SummaryStatus.CURRENT, SummaryStatus.SUPERSEDED, author)
        self.core.execute("UPDATE action_items SET status = ? WHERE summary_id = ? AND status != ?",
                          (ActionStatus.SUPERSEDED, summary_id, ActionStatus.SUPERSEDED))

    def current(self, meeting_id: int, kind: SummaryKind | str) -> SummaryRow | None:
        rows = self.core.q("SELECT * FROM summaries WHERE meeting_id = ? AND kind = ? AND status = ? ORDER BY id DESC LIMIT 1",
                           (meeting_id, str(kind), SummaryStatus.CURRENT))
        return cast(SummaryRow, dict(rows[0])) if rows else None

    def current_all(self, meeting_id: int) -> dict[str, SummaryRow]:
        rows = self.core.q("SELECT * FROM summaries WHERE meeting_id = ? AND status = ? ORDER BY id", (meeting_id, SummaryStatus.CURRENT))
        return {str(r["kind"]): cast(SummaryRow, dict(r)) for r in rows}

    def get(self, summary_id: int) -> SummaryRow | None:
        rows = self.core.q("SELECT * FROM summaries WHERE id = ?", (summary_id,))
        return cast(SummaryRow, dict(rows[0])) if rows else None

    def list_for_meeting(self, meeting_id: int) -> list[SummaryRow]:
        return [cast(SummaryRow, dict(r)) for r in self.core.q("SELECT * FROM summaries WHERE meeting_id = ? ORDER BY id", (meeting_id,))]

    def has_current(self, meeting_id: int, kind: SummaryKind | str, job_id: int | None = None) -> bool:
        """Idempotência por tarefa: o runner pula a espécie que já tem linha `atual` deste job pai."""
        if job_id is None:
            return self.current(meeting_id, kind) is not None
        return bool(self.core.q("SELECT 1 FROM summaries WHERE meeting_id = ? AND kind = ? AND status = ? AND job_id = ? LIMIT 1",
                                (meeting_id, str(kind), SummaryStatus.CURRENT, job_id)))

    def set_text_edited(self, summary_id: int, text: str | None, author: str = "usuario") -> bool:
        """Edição humana do Markdown (`text_edited`); a anterior fica em `revisions`. Texto igual ao atual não grava."""
        row = self.get(summary_id)
        if row is None:
            return False
        before = row.get("text_edited")
        if (before or "") == (text or ""):
            return False
        with self.core.transaction():
            self.core.execute("UPDATE summaries SET text_edited = ? WHERE id = ?", (text, summary_id))
            _rev(self.core, "summary", summary_id, "text_edited", before, text, author)
        return True

    def display_text(self, row: SummaryRow | None) -> str:
        return str((row or {}).get("text_edited") or (row or {}).get("text_llm") or "")

    def stale_after_reprocess(self, meeting_id: int, current_job_id: int | None) -> list[str]:
        """Espécies com linha `atual` gerada por um job anterior ao vigente: "gerados antes do reprocessamento" (P7)."""
        if current_job_id is None:
            return []
        rows = self.core.q("SELECT kind FROM summaries WHERE meeting_id = ? AND status = ? AND job_id IS NOT NULL AND job_id != ? "
                           "ORDER BY id", (meeting_id, SummaryStatus.CURRENT, current_job_id))
        return [str(r["kind"]) for r in rows]

    @staticmethod
    def to_dict(row: SummaryRow | None) -> dict[str, Any]:
        return dict(row) if row else {}
