"""Transcrição revisada (F3 item 8): blocos de 60 segmentos com 5 de contexto; a LLM corrige pontuação, capitalização, nomes
próprios, siglas e termos do glossário, sem mudar o sentido nem traduzir. Saída como lista `{segment_id, text}` restrita
aos ids do bloco (o subconjunto estrito não aceita chaves dinâmicas: desvio do contrato registrado na spec)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import Line, MeetingCtx, header, render_lines, system, user

PROMPT_VERSION = "1.1"                  # U-041 (A-10): o cabeçalho lista os falantes identificados
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["revisions"],
    "properties": {"revisions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["segment_id", "text"],
        "properties": {"segment_id": {"type": "integer"}, "text": {"type": "string", "maxLength": 2000}}}}},
}
SYSTEM = ("You lightly edit an automatic speech transcript: fix punctuation, capitalization, obvious recognition errors, proper "
          "names, acronyms and glossary terms, and remove fillers (hum, né, tipo) when they add nothing. Keep the speaker's words, "
          "meaning, numbers, dates and language; do not summarize, do not translate, do not merge or split segments. Return one "
          "entry per segment to edit, with the same segment_id; segments that need no change may be omitted. Context segments "
          "are marked as such and must not be returned.")


@dataclass(frozen=True, slots=True)
class ReviseCtx:
    meeting: MeetingCtx
    before: Sequence[Line]        # contexto (só leitura)
    block: Sequence[Line]         # os segmentos a revisar
    after: Sequence[Line]


def build(ctx: ReviseCtx) -> tuple[Message, ...]:
    parts = [header(ctx.meeting)]
    if ctx.before:
        parts.append(f"Context before (do not return):\n{render_lines(ctx.before)}")
    parts.append(f"Segments to edit (#segment_id [mm:ss] speaker: text):\n{render_lines(ctx.block)}")
    if ctx.after:
        parts.append(f"Context after (do not return):\n{render_lines(ctx.after)}")
    return (system(SYSTEM, ctx.meeting), user(*parts))
