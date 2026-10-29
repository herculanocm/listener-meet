"""Título (até 80 caracteres) a partir do pré-resumo (ou do contexto da ata) ou, sem nenhum dos dois, das falas de abertura
(F5.6 A-07: o núcleo automático roda antes de qualquer ata): uma chamada de 200 tokens no modelo rápido. F5.6 L2 (A-24): as
instruções (o que o título diz) são do usuário; o tamanho, sem aspas nem ponto final, e o exemplo ficam no contrato. Versão
1.3 (revisão do L2, R-07): as instruções terminam a frase com ponto e o contrato começa com "At most", para quem edita ler
duas frases inteiras (antes, ", at most" no meio da mesma frase)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..costs import estimate_tokens
from ..types import Message
from .common import Line, MeetingCtx, header, system, system_text, user

PROMPT_VERSION = "1.3"                  # F5.6 L2 (R-07): instruções e contrato em frases separadas; 1.2: das falas de abertura
MAX_CHARS = 80
OPENING_TOKENS = 1500                   # F5.6 (A-07): a abertura da reunião que o título lê sem resumo
SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["title"],
                "properties": {"title": {"type": "string", "maxLength": MAX_CHARS}}}
INSTRUCTIONS = ("You propose a short, specific title for a meeting from its summary or, when there is none, from the opening of "
                "its transcript: subject plus the main outcome when there is one.")
CONTRACT = (f"At most {MAX_CHARS - 20} characters, no quotes, no trailing period. Example in Brazilian Portuguese: "
            '"Orçamento 2027: corte de 8% aprovado".')
SUMMARY_LABEL = "Summary:"
OPENING_LABEL = "Opening of the transcript:"


@dataclass(frozen=True, slots=True)
class TitleCtx:
    meeting: MeetingCtx
    summary: str
    opening: bool = False               # `summary` são as falas de abertura (F5.6 A-07)


def opening(lines: Sequence[Line], max_tokens: int = OPENING_TOKENS) -> tuple[Line, ...]:
    """As falas de abertura até ~`max_tokens`, cortadas na troca de falante (a vez de quem fala no limite segue até 15 % a
    mais, como `split_blocks`); ao menos uma."""
    out: list[Line] = []
    total = 0
    for ln in lines:
        t = estimate_tokens(ln.render()) + 1
        if out and total + t > max_tokens and (out[-1].speaker != ln.speaker or total + t > max_tokens * 1.15):
            break
        out.append(ln)
        total += t
    return tuple(out)


def build(ctx: TitleCtx, instructions: str | None = None) -> tuple[Message, ...]:
    label = OPENING_LABEL if ctx.opening else SUMMARY_LABEL
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting), user(header(ctx.meeting), f"{label}\n{ctx.summary}"))
