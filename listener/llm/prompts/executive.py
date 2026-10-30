"""Resumo executivo (até 1500 caracteres) a partir da ata validada, não da transcrição (F3A-35). F5.6 L2 (A-24): as
instruções (para quem e com o quê) são do usuário; só a ata como fonte, o limite e os rótulos de voz ficam."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import Message
from .common import VOICE_LABELS, MeetingCtx, header, system, system_text, user

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.2: o cabeçalho lista os falantes e "Voz 2" sem tradução
MAX_CHARS = 1500
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": MAX_CHARS}}}
INSTRUCTIONS = ("Escreva o resumo executivo de uma reunião para quem não estava nela: do que tratou, o que foi decidido, o "
                "que ficou pendente e os próximos passos principais, em um ou dois parágrafos curtos de prosa corrida (sem "
                "tópicos, sem títulos).")
CONTRACT = f"Use só a ata dada. Limite rígido: {MAX_CHARS - 200} caracteres. {VOICE_LABELS}"


@dataclass(frozen=True, slots=True)
class SummaryCtx:
    meeting: MeetingCtx
    minutes_json: str


def build(ctx: SummaryCtx, instructions: str | None = None) -> tuple[Message, ...]:
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"Ata (JSON):\n{ctx.minutes_json}"))
