"""Ata (F3 item 6): decisões, ações com dono e prazo absoluto, destaques dos marcadores com as notas do usuário, pendências e
próximos passos, tudo com `segment_ids` de evidência. O projeto renderiza o Markdown (`llm/render.py`). F5.6 L2 (A-24): as
instruções (o papel e o que relatar) são do usuário; o contrato (a evidência de cada item, os campos das ações, dos destaques
e do contexto, os rótulos de voz) fica. A redução do modo hierárquico recebe as mesmas instruções do usuário antes do contrato
dela; com as do app, a redução sai como antes."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import VOICE_LABELS, Line, MeetingCtx, Person, compose, header, ids_schema, own, system, system_text, text_block, user

PROMPT_VERSION = "1.2"                  # U-041 (A-10): o cabeçalho lista os falantes; F5.5 (A-20): "Voz 2" sem tradução


def _item(extra: dict | None = None) -> dict:
    props = {"text": {"type": "string", "maxLength": 600}, "segment_ids": ids_schema(), **(extra or {})}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["context", "decisions", "actions", "highlights", "open_points", "next_steps"],
    "properties": {
        "context": {"type": "string", "maxLength": 1200},
        "decisions": {"type": "array", "items": _item()},
        "actions": {"type": "array", "items": _item({
            "owner_text": {"type": ["string", "null"], "maxLength": 80}, "owner_person_id": {"type": ["integer", "null"]},
            "due_text": {"type": ["string", "null"], "maxLength": 80}, "due_date": {"type": ["string", "null"], "maxLength": 10}})},
        "highlights": {"type": "array", "items": _item({"marker_id": {"type": "integer"}})},
        "open_points": {"type": "array", "items": _item()},
        "next_steps": {"type": "array", "items": _item()},
    },
}
INSTRUCTIONS = "You write meeting minutes from a transcript. Report only what was actually said."
CONTRACT = ("Every decision, action, highlight, open point and next step must cite the segment_ids that support it (the numbers "
            "after '#'); use an empty list only when there is truly no supporting segment. Actions: owner_text is the name as "
            "said in the meeting; owner_person_id only when the owner is one of the listed people; due_text is the deadline as "
            "said; due_date is that deadline as an absolute YYYY-MM-DD computed from the meeting date, or null. Highlights: one "
            "entry per user marker, describing what was being discussed around that time and taking the user's note into "
            f"account. Context: two to four sentences on what the meeting was about. {VOICE_LABELS}")
REDUCE_SYSTEM = ("You consolidate structured notes taken from consecutive blocks of one long meeting into the final minutes. Merge "
                 "duplicates, keep the segment_ids from the notes, keep the chronological order and do not add anything that is not "
                 f"in the notes. {VOICE_LABELS}")


@dataclass(frozen=True, slots=True)
class MinutesCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]
    people: Sequence[Person]          # pessoas conhecidas (para `owner_person_id`)
    block: tuple[int, int] | None = None     # (índice, total) no modo hierárquico


def build(ctx: MinutesCtx, instructions: str | None = None) -> tuple[Message, ...]:
    people = "; ".join(p.render() for p in ctx.people) or "(none)"
    block = f"This is block {ctx.block[0] + 1} of {ctx.block[1]} of the meeting; write notes for this block only." if ctx.block else ""
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting),
            user(header(ctx.meeting), f"Known people (person_id) name — role, company: {people}", block,
                 text_block("Transcript", ctx.lines)))


@dataclass(frozen=True, slots=True)
class ReduceCtx:
    meeting: MeetingCtx
    notes_json: Sequence[str]         # JSON das notas por bloco, na ordem
    people: Sequence[Person]


def build_reduce(ctx: ReduceCtx, instructions: str | None = None) -> tuple[Message, ...]:
    """A consolidação das notas dos blocos: o contrato da redução; com instruções do usuário, elas vêm antes dele (A-24)."""
    people = "; ".join(p.render() for p in ctx.people) or "(none)"
    notes = "\n\n".join(f"Block {i + 1} notes:\n{n}" for i, n in enumerate(ctx.notes_json))
    mine = own(INSTRUCTIONS, instructions, ctx.meeting.output_language)
    text = REDUCE_SYSTEM if mine == INSTRUCTIONS else compose(mine, REDUCE_SYSTEM)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"Known people: {people}", notes))
