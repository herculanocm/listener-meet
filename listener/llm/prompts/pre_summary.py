"""Pré-resumo (até 600 caracteres) a partir da ata validada: o que aparece no cabeçalho da revisão e na lista de reuniões.
F5.6 L2 (A-24): as instruções são do usuário; o limite, a prosa sem tópicos e os rótulos de voz ficam."""
from __future__ import annotations

from ..types import Message
from .common import VOICE_LABELS, header, system, system_text, user
from .executive import SummaryCtx

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.2: o cabeçalho lista os falantes e "Voz 2" sem tradução
MAX_CHARS = 600
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": MAX_CHARS}}}
INSTRUCTIONS = ("Escreva um resumo bem curto de uma reunião (duas ou três frases) dizendo o assunto e o resultado principal, "
                "usando só a ata dada.")
CONTRACT = f"Limite rígido: {MAX_CHARS - 100} caracteres. Prosa corrida, sem tópicos. {VOICE_LABELS}"


def build(ctx: SummaryCtx, instructions: str | None = None) -> tuple[Message, ...]:
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"Ata (JSON):\n{ctx.minutes_json}"))
