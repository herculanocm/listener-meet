"""Resumo executivo (até 1500 caracteres) a partir da ata validada, não da transcrição (F3A-35)."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import Message
from .common import MeetingCtx, header, system, user

PROMPT_VERSION = "1.1"                  # U-041 (A-10): o cabeçalho lista os falantes identificados
MAX_CHARS = 1500
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": MAX_CHARS}}}
SYSTEM = ("You write the executive summary of a meeting for someone who was not there: what it was about, what was decided, what "
          "is pending and the key next steps, in one or two short paragraphs of plain prose (no bullet points, no headings). Use "
          f"only the minutes given. Hard limit: {MAX_CHARS - 200} characters.")


@dataclass(frozen=True, slots=True)
class SummaryCtx:
    meeting: MeetingCtx
    minutes_json: str


def build(ctx: SummaryCtx) -> tuple[Message, ...]:
    return (system(SYSTEM, ctx.meeting), user(header(ctx.meeting), f"Minutes (JSON):\n{ctx.minutes_json}"))
