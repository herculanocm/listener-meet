"""Transcrição revisada (F3 item 8): blocos de 60 segmentos com 5 de contexto; a LLM corrige pontuação, capitalização, nomes
próprios, siglas e termos do glossário, sem mudar o sentido nem traduzir. Saída como lista `{segment_id, text}` restrita
aos ids do bloco (o subconjunto estrito não aceita chaves dinâmicas: desvio do contrato registrado na spec). F5.6 L2 (A-24):
as instruções (o que corrigir) são do usuário; manter palavras e sentido, não juntar nem dividir segmentos e o formato ficam."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import Line, MeetingCtx, header, render_lines, system, system_text, user

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.1: o cabeçalho lista os falantes identificados
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["revisions"],
    "properties": {"revisions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["segment_id", "text"],
        "properties": {"segment_id": {"type": "integer"}, "text": {"type": "string", "maxLength": 2000}}}}},
}
INSTRUCTIONS = ("Revise de leve uma transcrição automática de fala: corrija pontuação, capitalização, erros evidentes de "
                "reconhecimento, nomes próprios, siglas e termos do glossário, e tire os cacoetes (hum, né, tipo) quando não "
                "acrescentam nada.")
CONTRACT = ("Mantenha as palavras, o sentido, os números, as datas e o idioma de quem fala; não resuma, não traduza, não "
            "junte nem divida segmentos. Devolva uma entrada por segmento a editar, com o mesmo segment_id; os segmentos sem "
            "mudança podem ficar de fora. Os segmentos de contexto estão marcados como tal e não entram na resposta.")


@dataclass(frozen=True, slots=True)
class ReviseCtx:
    meeting: MeetingCtx
    before: Sequence[Line]        # contexto (só leitura)
    block: Sequence[Line]         # os segmentos a revisar
    after: Sequence[Line]


def build(ctx: ReviseCtx, instructions: str | None = None) -> tuple[Message, ...]:
    parts = [header(ctx.meeting)]
    if ctx.before:
        parts.append(f"Contexto antes (não devolver):\n{render_lines(ctx.before)}")
    parts.append(f"Segmentos a editar (#segment_id [mm:ss] falante: texto):\n{render_lines(ctx.block)}")
    if ctx.after:
        parts.append(f"Contexto depois (não devolver):\n{render_lines(ctx.after)}")
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting), user(*parts))
