"""Pré-resumo (até 600 caracteres) a partir da ata validada: o que aparece no cabeçalho da revisão e na lista de reuniões."""
from __future__ import annotations

from ..types import Message
from .common import VOICE_LABELS, header, system, user
from .executive import SummaryCtx

PROMPT_VERSION = "1.2"                  # U-041 (A-10): o cabeçalho lista os falantes; F5.5 (A-20): "Voz 2" sem tradução
MAX_CHARS = 600
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": MAX_CHARS}}}
SYSTEM = ("You write a very short summary of a meeting (two or three sentences) stating the subject and the main outcome, using "
          f"only the minutes given. Hard limit: {MAX_CHARS - 100} characters. Plain prose, no bullet points. {VOICE_LABELS}")


def build(ctx: SummaryCtx) -> tuple[Message, ...]:
    return (system(SYSTEM, ctx.meeting), user(header(ctx.meeting), f"Minutes (JSON):\n{ctx.minutes_json}"))
