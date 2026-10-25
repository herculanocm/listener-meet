"""Título (até 80 caracteres) a partir do pré-resumo: uma chamada de 200 tokens no modelo rápido."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import Message
from .common import MeetingCtx, header, system, user

PROMPT_VERSION = "1.0"
MAX_CHARS = 80
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["title"],
                "properties": {"title": {"type": "string", "maxLength": MAX_CHARS}}}
SYSTEM = ("You propose a short, specific title for a meeting from its summary: subject plus the main outcome when there is one, "
          f"at most {MAX_CHARS - 20} characters, no quotes, no trailing period. Example in Brazilian Portuguese: "
          '"Orçamento 2027: corte de 8% aprovado".')


@dataclass(frozen=True, slots=True)
class TitleCtx:
    meeting: MeetingCtx
    summary: str


def build(ctx: TitleCtx) -> tuple[Message, ...]:
    return (system(SYSTEM, ctx.meeting), user(header(ctx.meeting), f"Summary:\n{ctx.summary}"))
