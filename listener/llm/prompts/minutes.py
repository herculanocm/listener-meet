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

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.2: o cabeçalho lista os falantes e "Voz 2" sem tradução


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
INSTRUCTIONS = "Escreva a ata de uma reunião a partir da transcrição. Relate só o que foi dito de fato."
CONTRACT = ("Toda decisão, ação, destaque, pendência e próximo passo precisa citar os segment_ids que a sustentam (os "
            "números depois de '#'); use uma lista vazia só quando não há mesmo segmento de apoio. Ações: owner_text é o "
            "nome como foi dito na reunião; owner_person_id só quando o responsável é uma das pessoas listadas; due_text é "
            "o prazo como foi dito; due_date é esse prazo como data absoluta AAAA-MM-DD, calculada a partir da data da "
            "reunião, ou null. Destaques: uma entrada por marcador do usuário, descrevendo o que se discutia perto daquele "
            "momento e levando em conta a nota do usuário. Contexto: duas a quatro frases sobre do que tratou a reunião. "
            f"{VOICE_LABELS}")
REDUCE_SYSTEM = ("Consolide na ata final as notas estruturadas tiradas de blocos consecutivos de uma reunião longa. Junte "
                 "as repetições, mantenha os segment_ids das notas, mantenha a ordem cronológica e não acrescente nada que "
                 f"não esteja nas notas. {VOICE_LABELS}")


@dataclass(frozen=True, slots=True)
class MinutesCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]
    people: Sequence[Person]          # pessoas conhecidas (para `owner_person_id`)
    block: tuple[int, int] | None = None     # (índice, total) no modo hierárquico


def build(ctx: MinutesCtx, instructions: str | None = None) -> tuple[Message, ...]:
    people = "; ".join(p.render() for p in ctx.people) or "(nenhuma)"
    block = (f"Este é o bloco {ctx.block[0] + 1} de {ctx.block[1]} da reunião; escreva as notas só deste bloco."
             if ctx.block else "")
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting),
            user(header(ctx.meeting), f"Pessoas conhecidas (person_id) nome — cargo, empresa: {people}", block,
                 text_block("Transcrição", ctx.lines)))


@dataclass(frozen=True, slots=True)
class ReduceCtx:
    meeting: MeetingCtx
    notes_json: Sequence[str]         # JSON das notas por bloco, na ordem
    people: Sequence[Person]


def build_reduce(ctx: ReduceCtx, instructions: str | None = None) -> tuple[Message, ...]:
    """A consolidação das notas dos blocos: o contrato da redução; com instruções do usuário, elas vêm antes dele (A-24)."""
    people = "; ".join(p.render() for p in ctx.people) or "(nenhuma)"
    notes = "\n\n".join(f"Notas do bloco {i + 1}:\n{n}" for i, n in enumerate(ctx.notes_json))
    mine = own(INSTRUCTIONS, instructions, ctx.meeting.output_language)
    text = REDUCE_SYSTEM if mine == INSTRUCTIONS else compose(mine, REDUCE_SYSTEM)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"Pessoas conhecidas: {people}", notes))
