"""Chat sobre uma reunião (F3 item 10): transcrição como prefixo estável (cache dos provedores), histórico curto, pergunta por
último; citações `[mm:ss]` da transcrição enviada, reescritas pelo projeto para o segmento mais próximo. F5.6 L2 (A-24): as
instruções (o papel e o estilo) são do usuário; citar `[mm:ss]`, dizer quando a transcrição não responde e os rótulos de voz
ficam. Versão 1.3: o "Be concise." veio para as instruções (antes ficava entre as regras de citação), para o estilo ser do
usuário sem contradizer o contrato."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import VOICE_LABELS, Line, MeetingCtx, header, language_instruction, render_lines, system_text

PROMPT_VERSION = "1.3"                  # F5.6 L2: "Be concise." nas instruções (editáveis), antes da regra de citação
SCHEMA = None
INSTRUCTIONS = "You answer questions about one meeting using only its transcript below. Be concise."
CONTRACT = ("Quote the moment you rely on as [mm:ss] taken from the transcript lines (for example: 'o prazo foi fechado para "
            f"sexta [12:03]'). If the transcript does not answer the question, say so. {VOICE_LABELS}")


@dataclass(frozen=True, slots=True)
class ChatCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]
    history: Sequence[tuple[str, str]]     # (role, text) das últimas trocas
    question: str


def build(ctx: ChatCtx, instructions: str | None = None) -> tuple[Message, ...]:
    prefix = Message(Role.USER, f"{header(ctx.meeting)}\n\nTranscript (#segment_id [mm:ss] speaker: text):\n{render_lines(ctx.lines)}")
    ack = Message(Role.ASSISTANT, "Understood. I will answer questions about this meeting citing [mm:ss] from the transcript.")
    history = tuple(Message(Role.ASSISTANT if role == "assistant" else Role.USER, text) for role, text in ctx.history[-8:])
    language = ctx.meeting.output_language
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, language)
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(language)}"), prefix, ack, *history, Message(Role.USER, ctx.question))
