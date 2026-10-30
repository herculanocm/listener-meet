"""Resumo parcial ao vivo (F4 item 7): o parcial anterior (JSON) e as legendas novas desde ele (`#<caption_id> [mm:ss]
rótulo: texto`, automáticas e com nomes possivelmente errados) viram o parcial atualizado da reunião inteira até ali: um
resumo curto e as possíveis decisões e ações, cada item com o `caption_id` da legenda de apoio (o projeto converte em
`t_ms`; id desconhecido vira nulo, como a evidência da F3). Modelo rápido; no idioma da reunião (F5.3 A-02). F5.6 L2 (A-24):
as instruções (o papel) são do usuário; as legendas, o parcial anterior, os campos, os limites e as citações ficam."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import Line, MeetingCtx, header, render_lines, system, system_text, user

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR
MAX_SUMMARY = 1200
MAX_ITEMS = 15


def _item(extra: dict | None = None) -> dict:
    props = {"text": {"type": "string", "maxLength": 300}, "caption_id": {"type": ["integer", "null"]}, **(extra or {})}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["summary", "decisions", "actions"],
    "properties": {
        "summary": {"type": "string", "maxLength": MAX_SUMMARY},
        "decisions": {"type": "array", "maxItems": MAX_ITEMS, "items": _item()},
        "actions": {"type": "array", "maxItems": MAX_ITEMS, "items": _item({"owner": {"type": ["string", "null"], "maxLength": 80}})},
    },
}
INSTRUCTIONS = "Mantenha o resumo parcial de uma reunião que ainda está acontecendo, escrito a partir das legendas ao vivo."
CONTRACT = ("As legendas são automáticas: podem ter erros de reconhecimento, e o rótulo do falante pode estar errado ou ser "
            "só 'Participante'. O pedido traz o resumo parcial anterior (JSON) e as legendas desde então. Devolva o resumo "
            "atualizado da reunião inteira até aqui, não só da parte nova: 'summary' em prosa corrida, com no máximo "
            f"{MAX_SUMMARY - 200} caracteres; 'decisions' são as possíveis decisões tomadas até aqui; 'actions' são as "
            "possíveis ações até aqui, com 'owner' como foi dito na reunião ou null. Mantenha os itens do resumo anterior, "
            "com o caption_id deles, a menos que as legendas novas os contradigam; nunca repita um item; no máximo "
            f"{MAX_ITEMS} itens por lista, os mais relevantes. Todo item cita em 'caption_id' a legenda que o sustenta (o "
            "número depois de '#'); null só quando nenhuma legenda o sustenta. Relate só o que foi dito; são possíveis "
            "decisões e ações, não definitivas. Exemplo de item, em português do Brasil: "
            '{"text": "Cortar 8% do orçamento de marketing", "caption_id": 41}.')


@dataclass(frozen=True, slots=True)
class PartialCtx:
    meeting: MeetingCtx
    previous_json: str | None          # parcial anterior (resumo, decisões e ações com `caption_id`); None no primeiro
    lines: Sequence[Line]              # legendas novas: `segment_id` aqui é o id da legenda


def build(ctx: PartialCtx, instructions: str | None = None) -> tuple[Message, ...]:
    previous = ctx.previous_json or "(nenhum: este é o primeiro resumo parcial da reunião)"
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting),
            user(header(ctx.meeting), f"Resumo parcial anterior (JSON):\n{previous}",
                 f"Legendas novas (formato: #caption_id [mm:ss] falante: texto):\n{render_lines(ctx.lines)}"))
