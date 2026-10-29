"""Glossário do warm-up (handoff §4, F3 item 4): termos prováveis (siglas, produtos, nomes) para o `initial_prompt` do
Whisper e o mapeamento dos participantes que o casamento local não resolveu. F5.6 L2 (A-24): as instruções (que termos
procurar) são do usuário; o limite de 40 termos e o casamento dos participantes com as pessoas cadastradas ficam."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import Person, language_instruction, system_text

PROMPT_VERSION = "1.0"
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["terms", "people"],
    "properties": {"terms": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 60}},
                   "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "person_id"],
                                                         "properties": {"name": {"type": "string", "maxLength": 80},
                                                                        "person_id": {"type": ["integer", "null"]}}}}},
}
INSTRUCTIONS = ("Before a meeting is recorded, you prepare a short glossary for the speech recognizer: proper names, acronyms, "
                "product and company names, technical terms likely to be said, derived from the title, the participants and the "
                "user's own glossary.")
CONTRACT = ("Up to 40 terms, most likely first, no duplicates, no common words. Also match each unresolved participant name to one "
            "of the listed known people when it is clearly the same person (nickname, first name, typo); otherwise person_id null.")


@dataclass(frozen=True, slots=True)
class GlossaryCtx:
    title: str | None
    participants_text: str | None
    unresolved: Sequence[str]
    known: Sequence[Person]
    fixed_glossary: str
    output_language: str


def build(ctx: GlossaryCtx, instructions: str | None = None) -> tuple[Message, ...]:
    known = "; ".join(p.render() for p in ctx.known) or "(none)"
    body = "\n".join(x for x in (f"Title: {ctx.title or '(none)'}", f"Participants as typed: {ctx.participants_text or '(none)'}",
                                 f"Unresolved participant names: {', '.join(ctx.unresolved) or '(none)'}", f"Known people: {known}",
                                 f"User glossary: {ctx.fixed_glossary or '(none)'}"))
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.output_language)
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}"), Message(Role.USER, body))
