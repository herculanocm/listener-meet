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

PROMPT_VERSION = "1.0"
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
INSTRUCTIONS = "You keep a running summary of a meeting that is still happening, written from live captions."
CONTRACT = ("Captions are automatic: they may have recognition errors, and the speaker label may be wrong or just 'Participante'. "
            "You receive the previous running summary (JSON) and the captions since then. Return the updated summary of the whole "
            f"meeting so far, not only of the new part: 'summary' in plain prose, at most {MAX_SUMMARY - 200} characters; "
            "'decisions' are possible decisions taken so far; 'actions' are possible action items so far, with 'owner' as said in "
            "the meeting or null. Keep the items of the previous summary, with their caption_id, unless the new captions "
            f"contradict them; never repeat an item; at most {MAX_ITEMS} items per list, the most relevant ones. Every item cites "
            "in 'caption_id' the caption that supports it (the number after '#'); null only when no caption supports it. Report "
            "only what was said; these are possible decisions and actions, not final ones. Example item in Brazilian Portuguese: "
            '{"text": "Cortar 8% do orçamento de marketing", "caption_id": 41}.')


@dataclass(frozen=True, slots=True)
class PartialCtx:
    meeting: MeetingCtx
    previous_json: str | None          # parcial anterior (resumo, decisões e ações com `caption_id`); None no primeiro
    lines: Sequence[Line]              # legendas novas: `segment_id` aqui é o id da legenda


def build(ctx: PartialCtx, instructions: str | None = None) -> tuple[Message, ...]:
    previous = ctx.previous_json or "(none: this is the first running summary of the meeting)"
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting),
            user(header(ctx.meeting), f"Previous running summary (JSON):\n{previous}",
                 f"New captions (format: #caption_id [mm:ss] speaker: text):\n{render_lines(ctx.lines)}"))
