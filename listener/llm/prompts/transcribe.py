"""Transcrição final pela LLM com áudio (D-020, F3 item 9): falas com tempos relativos ao início da janela; o projeto
interpola as palavras (`LLMAudioTranscriber`). F5.6 L2 (A-24): as instruções (como transcrever) são do usuário; as falas
com os tempos, o idioma em duas letras e o "sem tradução" ficam; `{idioma}` vira o idioma pedido ou "o idioma original"."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import AudioPart, Message, Role
from .common import system_text

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["language", "utterances"],
    "properties": {"language": {"type": "string", "maxLength": 10},
                   "utterances": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["start_s", "end_s", "text"],
                       "properties": {"start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
                                      "text": {"type": "string", "maxLength": 2000}}}}},
}
INSTRUCTIONS = "Transcreva literalmente o áudio de reunião anexado, no idioma original dele."
CONTRACT = ("Divida em falas nas trocas de falante e nas pausas naturais (cada uma com uns 15 segundos no máximo), com "
            "start_s e end_s em segundos relativos ao começo do áudio. Mantenha os números em algarismos, mantenha nomes e "
            "siglas como foram ditos, sem comentários, sem tradução. Devolva language como código de duas letras.")


@dataclass(frozen=True, slots=True)
class TranscribeCtx:
    audio: AudioPart
    language: str | None
    initial_prompt: str


def build(ctx: TranscribeCtx, instructions: str | None = None) -> tuple[Message, ...]:
    hints = []
    if ctx.language:
        hints.append(f"O idioma é {ctx.language}.")
    if ctx.initial_prompt:
        hints.append(f"Nomes e termos prováveis: {ctx.initial_prompt}")
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.language)
    return (Message(Role.SYSTEM, text), Message(Role.USER, " ".join(hints) or "Transcreva o áudio.", (ctx.audio,)))
