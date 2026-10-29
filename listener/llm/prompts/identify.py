"""Identidade por contexto (handoff §4, F3 item 5, CP11): só vozes pendentes (`SPK_n`) entram; a LLM sugere `person_id` da
lista de candidatos ou `new_name`, com evidência `{segment_id, quote}` que o projeto confere contra o segmento."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import Line, MeetingCtx, Person, header, system, text_block, user

PROMPT_VERSION = "1.1"                  # U-041 (A-10): o cabeçalho lista os falantes identificados
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["speakers"],
    "properties": {"speakers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["cluster", "person_id", "new_name", "confidence", "evidence"],
        "properties": {
            "cluster": {"type": "string"},
            "person_id": {"type": ["integer", "null"]},
            "new_name": {"type": ["string", "null"], "maxLength": 80},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "array", "maxItems": 5, "items": {
                "type": "object", "additionalProperties": False, "required": ["segment_id", "quote"],
                "properties": {"segment_id": {"type": "integer"}, "quote": {"type": "string", "maxLength": 200}}}}}}}},
}
SYSTEM = ("You identify who the unnamed speakers of a meeting transcript are, using only textual clues: people addressing each "
          "other by name, self-introductions, roles mentioned, who answers to whom. Speakers labelled SPK_n are unresolved; the "
          "others are already known. For each unresolved speaker return either the person_id of a listed candidate or a new_name "
          "(never both), a confidence between 0 and 1, and up to 5 pieces of evidence, each with the segment_id and a short quote "
          "copied verbatim from that segment. If there is no clue, return person_id null, new_name null and confidence 0. Never "
          "invent names.")


@dataclass(frozen=True, slots=True)
class IdentifyCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]             # `Context.identify_lines` (F5.5, A-14): a voz pendente pelo rótulo, como antes
    pending: Sequence[str]            # rótulos SPK_n por decidir (as outras vozes sem pessoa vêm como "Voz 2")
    candidates: Sequence[Person]      # pessoas cadastradas (id, nome, cargo, empresa)


def build(ctx: IdentifyCtx) -> tuple[Message, ...]:
    cands = "; ".join(p.render() for p in ctx.candidates) or "(none)"
    return (system(SYSTEM, ctx.meeting),
            user(header(ctx.meeting), f"Unresolved speakers: {', '.join(ctx.pending)}",
                 f"Candidates (person_id) name — role, company: {cands}", text_block("Transcript", ctx.lines),
                 'Example of one speaker entry: {"cluster": "SPK_2", "person_id": 7, "new_name": null, "confidence": 0.8, '
                 '"evidence": [{"segment_id": 143, "quote": "valeu, Rodrigo"}]}'))
