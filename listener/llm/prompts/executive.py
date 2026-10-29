"""Resumo executivo (até 1500 caracteres) a partir da ata validada, não da transcrição (F3A-35). F5.6 L2 (A-24): as
instruções (para quem e com o quê) são do usuário; só a ata como fonte, o limite e os rótulos de voz ficam."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import Message
from .common import VOICE_LABELS, MeetingCtx, header, system, system_text, user

PROMPT_VERSION = "1.2"                  # U-041 (A-10): o cabeçalho lista os falantes; F5.5 (A-20): "Voz 2" sem tradução
MAX_CHARS = 1500
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": MAX_CHARS}}}
INSTRUCTIONS = ("You write the executive summary of a meeting for someone who was not there: what it was about, what was "
                "decided, what is pending and the key next steps, in one or two short paragraphs of plain prose (no bullet points, "
                "no headings).")
CONTRACT = f"Use only the minutes given. Hard limit: {MAX_CHARS - 200} characters. {VOICE_LABELS}"


@dataclass(frozen=True, slots=True)
class SummaryCtx:
    meeting: MeetingCtx
    minutes_json: str


def build(ctx: SummaryCtx, instructions: str | None = None) -> tuple[Message, ...]:
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"Minutes (JSON):\n{ctx.minutes_json}"))
