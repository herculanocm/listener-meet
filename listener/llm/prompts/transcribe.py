"""Transcrição final pela LLM com áudio (D-020, F3 item 9): falas com tempos relativos ao início da janela; o projeto
interpola as palavras (`LLMAudioTranscriber`). F5.6 L2 (A-24): as instruções (como transcrever) são do usuário; as falas
com os tempos, o idioma em duas letras e o "sem tradução" ficam; `{idioma}` vira o idioma pedido ou "the original language"."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import AudioPart, Message, Role
from .common import system_text

PROMPT_VERSION = "1.0"
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["language", "utterances"],
    "properties": {"language": {"type": "string", "maxLength": 10},
                   "utterances": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["start_s", "end_s", "text"],
                       "properties": {"start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
                                      "text": {"type": "string", "maxLength": 2000}}}}},
}
INSTRUCTIONS = "Transcribe the attached meeting audio verbatim in its original language."
CONTRACT = ("Split it into utterances at speaker changes and natural pauses (at most about 15 seconds each), with start_s and end_s "
            "in seconds relative to the beginning of the audio. Keep numbers as digits, keep names and acronyms as said, no "
            "commentary, no translation. Return language as a two-letter code.")


@dataclass(frozen=True, slots=True)
class TranscribeCtx:
    audio: AudioPart
    language: str | None
    initial_prompt: str


def build(ctx: TranscribeCtx, instructions: str | None = None) -> tuple[Message, ...]:
    hints = []
    if ctx.language:
        hints.append(f"The language is {ctx.language}.")
    if ctx.initial_prompt:
        hints.append(f"Likely names and terms: {ctx.initial_prompt}")
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.language)
    return (Message(Role.SYSTEM, text), Message(Role.USER, " ".join(hints) or "Transcribe the audio.", (ctx.audio,)))
