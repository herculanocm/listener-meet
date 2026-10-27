"""Chat sobre uma reunião (F3 item 10): transcrição como prefixo estável (cache dos provedores), histórico curto, pergunta por
último; citações `[mm:ss]` da transcrição enviada, reescritas pelo projeto para o segmento mais próximo."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import Line, MeetingCtx, header, language_instruction, render_lines

PROMPT_VERSION = "1.1"                  # U-041 (A-10): o cabeçalho lista os falantes identificados
SCHEMA = None
SYSTEM = ("You answer questions about one meeting using only its transcript below. Quote the moment you rely on as [mm:ss] taken "
          "from the transcript lines (for example: 'o prazo foi fechado para sexta [12:03]'). If the transcript does not answer "
          "the question, say so. Be concise.")


@dataclass(frozen=True, slots=True)
class ChatCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]
    history: Sequence[tuple[str, str]]     # (role, text) das últimas trocas
    question: str


def build(ctx: ChatCtx) -> tuple[Message, ...]:
    prefix = Message(Role.USER, f"{header(ctx.meeting)}\n\nTranscript (#segment_id [mm:ss] speaker: text):\n{render_lines(ctx.lines)}")
    ack = Message(Role.ASSISTANT, "Understood. I will answer questions about this meeting citing [mm:ss] from the transcript.")
    history = tuple(Message(Role.ASSISTANT if role == "assistant" else Role.USER, text) for role, text in ctx.history[-8:])
    return (Message(Role.SYSTEM, f"{SYSTEM}\n{language_instruction(ctx.meeting.output_language)}"), prefix, ack, *history,
            Message(Role.USER, ctx.question))
