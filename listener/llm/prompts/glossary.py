"""Glossário do warm-up (handoff §4, F3 item 4): termos prováveis (siglas, produtos, nomes) para o `initial_prompt` do
Whisper e o mapeamento dos participantes que o casamento local não resolveu. F5.6 L2 (A-24): as instruções (que termos
procurar) são do usuário; o limite de 40 termos e o casamento dos participantes com as pessoas cadastradas ficam."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import Person, language_instruction, system_text

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["terms", "people"],
    "properties": {"terms": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 60}},
                   "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "person_id"],
                                                         "properties": {"name": {"type": "string", "maxLength": 80},
                                                                        "person_id": {"type": ["integer", "null"]}}}}},
}
INSTRUCTIONS = ("Antes de uma reunião ser gravada, prepare um glossário curto para o reconhecedor de fala: nomes próprios, "
                "siglas, nomes de produtos e de empresas, termos técnicos que provavelmente serão ditos, tirados do título, "
                "dos participantes e do glossário do próprio usuário.")
CONTRACT = ("Até 40 termos, os mais prováveis primeiro, sem repetir, sem palavras comuns. Também case cada nome de "
            "participante não resolvido com uma das pessoas conhecidas listadas quando for claramente a mesma pessoa "
            "(apelido, primeiro nome, erro de digitação); senão, person_id null.")


@dataclass(frozen=True, slots=True)
class GlossaryCtx:
    title: str | None
    participants_text: str | None
    unresolved: Sequence[str]
    known: Sequence[Person]
    fixed_glossary: str
    output_language: str


def build(ctx: GlossaryCtx, instructions: str | None = None) -> tuple[Message, ...]:
    known = "; ".join(p.render() for p in ctx.known) or "(nenhuma)"
    body = "\n".join(x for x in (f"Título: {ctx.title or '(nenhum)'}",
                                 f"Participantes como digitados: {ctx.participants_text or '(nenhum)'}",
                                 f"Nomes de participantes não resolvidos: {', '.join(ctx.unresolved) or '(nenhum)'}",
                                 f"Pessoas conhecidas: {known}", f"Glossário do usuário: {ctx.fixed_glossary or '(nenhum)'}"))
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.output_language)
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}"), Message(Role.USER, body))
