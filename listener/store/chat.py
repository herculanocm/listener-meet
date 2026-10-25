"""Histórico do chat sobre uma reunião (`chat_messages`, F3 item 10): pergunta e resposta com provedor, modelo, citações
`[{"t_ms", "segment_id"}]` reescritas pelo projeto e custo. Chat sobre todas as reuniões é F5."""
from __future__ import annotations

import json
from typing import Any, TypedDict, cast

from ..model import ChatRole
from .core import Core, _now, json_default


class ChatRow(TypedDict, total=False):
    id: int
    meeting_id: int
    role: str
    text: str
    provider: str | None
    model: str | None
    citations_json: str | None
    cost_usd: float | None
    created_at: str


class ChatRepo:
    def __init__(self, core: Core):
        self.core = core

    def add(self, meeting_id: int, role: ChatRole, text: str, provider: str | None = None, model: str | None = None,
            citations: list[dict[str, Any]] | None = None, cost_usd: float | None = None) -> int:
        payload = json.dumps(citations, ensure_ascii=False, default=json_default) if citations is not None else None
        return self.core.insert("INSERT INTO chat_messages(meeting_id, role, text, provider, model, citations_json, cost_usd, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (meeting_id, str(role), text, provider, model, payload, cost_usd, _now()))

    def list_for_meeting(self, meeting_id: int, limit: int = 200) -> list[ChatRow]:
        rows = self.core.q("SELECT * FROM (SELECT * FROM chat_messages WHERE meeting_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                           (meeting_id, int(limit)))
        return [cast(ChatRow, dict(r)) for r in rows]

    def clear(self, meeting_id: int) -> int:
        """Só por ação do usuário (CP3): limpa a conversa de uma reunião."""
        return self.core.execute("DELETE FROM chat_messages WHERE meeting_id = ?", (meeting_id,))
