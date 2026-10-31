"""Histórico do chat (`chat_messages`). F3 item 10: o chat sobre uma reunião, pergunta e resposta com provedor, modelo,
citações `[{"t_ms", "segment_id"}]` reescritas pelo projeto e custo. F5 (v8): o chat sobre todas as reuniões guarda as
mensagens com `meeting_id` nulo numa conversa (`chat_threads`, "nova conversa", Q3), citações `[{alias, meeting_id, t_ms,
segment_id}]` e a avaliação "citação certa?" (`rating`: 1, 0 ou nulo, U-027). F5.10 (v9): a resposta de "Perguntar à
base" guarda os passos em `trace_json` (consultas, linhas, tempo e a categoria do erro; nunca os dados). Apagar, só pelo
usuário (CP3)."""
from __future__ import annotations

import json
from typing import Any, TypedDict, cast

from ..model import ChatRole
from .core import Core, _now, json_default


class ChatRow(TypedDict, total=False):
    id: int
    meeting_id: int | None           # nulo = chat sobre todas as reuniões (F5)
    thread_id: int | None            # conversa do chat global (F5)
    role: str
    text: str
    provider: str | None
    model: str | None
    citations_json: str | None
    cost_usd: float | None
    rating: int | None               # U-027: 1 citação certa, 0 errada, nulo sem avaliação
    trace_json: str | None           # F5.10 (v9): os passos de "Perguntar à base"
    created_at: str


class ThreadRow(TypedDict, total=False):
    id: int
    title: str | None
    created_at: str
    messages: int
    last_at: str | None


class ChatRepo:
    def __init__(self, core: Core):
        self.core = core

    def add(self, meeting_id: int | None, role: ChatRole, text: str, provider: str | None = None, model: str | None = None,
            citations: list[dict[str, Any]] | None = None, cost_usd: float | None = None, *, thread_id: int | None = None,
            trace: dict[str, Any] | None = None) -> int:
        payload = json.dumps(citations, ensure_ascii=False, default=json_default) if citations is not None else None
        steps = json.dumps(trace, ensure_ascii=False, default=json_default) if trace is not None else None
        return self.core.insert("INSERT INTO chat_messages(meeting_id, thread_id, role, text, provider, model, citations_json, cost_usd, "
                                "trace_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (meeting_id, thread_id, str(role), text, provider, model, payload, cost_usd, steps, _now()))

    def get(self, message_id: int) -> ChatRow | None:
        rows = self.core.q("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        return cast(ChatRow, dict(rows[0])) if rows else None

    def list_for_meeting(self, meeting_id: int, limit: int = 200) -> list[ChatRow]:
        rows = self.core.q("SELECT * FROM (SELECT * FROM chat_messages WHERE meeting_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                           (meeting_id, int(limit)))
        return [cast(ChatRow, dict(r)) for r in rows]

    def clear(self, meeting_id: int) -> int:
        """Só por ação do usuário (CP3): limpa a conversa de uma reunião."""
        return self.core.execute("DELETE FROM chat_messages WHERE meeting_id = ?", (meeting_id,))

    # ---------------------------------------------------------------- conversas do chat global (F5, Q3)
    def create_thread(self, title: str | None) -> int:
        """"Nova conversa": o título é a primeira pergunta (cortada pela tela)."""
        return self.core.insert("INSERT INTO chat_threads(title, created_at) VALUES (?, ?)", (title, _now()))

    def get_thread(self, thread_id: int) -> ThreadRow | None:
        rows = self.core.q("SELECT * FROM chat_threads WHERE id = ?", (thread_id,))
        return cast(ThreadRow, dict(rows[0])) if rows else None

    def list_threads(self, limit: int = 100) -> list[ThreadRow]:
        """As conversas mais recentes primeiro (pela última mensagem), com quantas mensagens têm."""
        rows = self.core.q("SELECT t.*, COUNT(m.id) AS messages, MAX(m.created_at) AS last_at FROM chat_threads t "
                           "LEFT JOIN chat_messages m ON m.thread_id = t.id GROUP BY t.id "
                           "ORDER BY COALESCE(MAX(m.id), 0) DESC, t.id DESC LIMIT ?", (int(limit),))
        return [cast(ThreadRow, dict(r)) for r in rows]

    def list_for_thread(self, thread_id: int, limit: int = 200) -> list[ChatRow]:
        rows = self.core.q("SELECT * FROM (SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                           (thread_id, int(limit)))
        return [cast(ChatRow, dict(r)) for r in rows]

    def set_rating(self, message_id: int, rating: int | None) -> bool:
        """U-027 "citação certa?": 1 sim, 0 não, None desfaz."""
        if rating not in (None, 0, 1):
            raise ValueError(f"avaliação inválida: {rating!r}")
        return self.core.execute("UPDATE chat_messages SET rating = ? WHERE id = ?", (rating, message_id)) > 0
