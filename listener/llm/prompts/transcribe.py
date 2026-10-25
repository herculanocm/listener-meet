"""Transcrição final pela LLM com áudio (D-020, F3 item 9): falas com tempos relativos ao início da janela; o projeto
interpola as palavras (`LLMAudioTranscriber`)."""
from __future__ import annotations

from dataclasses import dataclass

from ..types import AudioPart, Message, Role

PROMPT_VERSION = "1.0"
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["language", "utterances"],
    "properties": {"language": {"type": "string", "maxLength": 10},
                   "utterances": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["start_s", "end_s", "text"],
                       "properties": {"start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
                                      "text": {"type": "string", "maxLength": 2000}}}}},
}
SYSTEM = ("Transcribe the attached meeting audio verbatim in its original language. Split it into utterances at speaker changes "
          "and natural pauses (at most about 15 seconds each), with start_s and end_s in seconds relative to the beginning of the "
          "audio. Keep numbers as digits, keep names and acronyms as said, no commentary, no translation. Return language as a "
          "two-letter code.")


@dataclass(frozen=True, slots=True)
class TranscribeCtx:
    audio: AudioPart
    language: str | None
    initial_prompt: str


def build(ctx: TranscribeCtx) -> tuple[Message, ...]:
    hints = []
    if ctx.language:
        hints.append(f"The language is {ctx.language}.")
    if ctx.initial_prompt:
        hints.append(f"Likely names and terms: {ctx.initial_prompt}")
    return (Message(Role.SYSTEM, SYSTEM), Message(Role.USER, " ".join(hints) or "Transcribe the audio.", (ctx.audio,)))
