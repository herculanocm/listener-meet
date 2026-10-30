"""Chat sobre uma reunião (F3 item 10): transcrição como prefixo estável (cache dos provedores), histórico curto, pergunta por
último; citações `[mm:ss]` da transcrição enviada, reescritas pelo projeto para o segmento mais próximo. F5.6 L2 (A-24): as
instruções (o papel e o estilo) são do usuário; citar `[mm:ss]`, dizer quando a transcrição não responde e os rótulos de voz
ficam. Versão 1.3: o "Seja conciso." veio para as instruções (antes ficava entre as regras de citação), para o estilo ser do
usuário sem contradizer o contrato. Versão 2.0 (D-071): o prompt inteiro em pt-BR."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import VOICE_LABELS, Line, MeetingCtx, header, language_instruction, render_lines, system_text

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.3: "Seja conciso." nas instruções (editáveis)
SCHEMA = None
INSTRUCTIONS = "Responda perguntas sobre uma reunião usando só a transcrição dela, abaixo. Seja conciso."
CONTRACT = ("Cite o momento em que se apoia como [mm:ss], tirado das linhas da transcrição (por exemplo: 'o prazo foi "
            f"fechado para sexta [12:03]'). Se a transcrição não responde à pergunta, diga isso. {VOICE_LABELS}")


@dataclass(frozen=True, slots=True)
class ChatCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]
    history: Sequence[tuple[str, str]]     # (role, text) das últimas trocas
    question: str


def build(ctx: ChatCtx, instructions: str | None = None) -> tuple[Message, ...]:
    prefix = Message(Role.USER, f"{header(ctx.meeting)}\n\nTranscrição (#segment_id [mm:ss] falante: texto):\n"
                                f"{render_lines(ctx.lines)}")
    ack = Message(Role.ASSISTANT, "Entendido. Vou responder perguntas sobre esta reunião citando [mm:ss] da transcrição.")
    history = tuple(Message(Role.ASSISTANT if role == "assistant" else Role.USER, text) for role, text in ctx.history[-8:])
    language = ctx.meeting.output_language
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, language)
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(language)}"), prefix, ack, *history,
            Message(Role.USER, ctx.question))
