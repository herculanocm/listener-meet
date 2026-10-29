"""Chat sobre todas as reuniões (F5 item 11, F5A-24): os trechos que a busca híbrida achou, agrupados por reunião, cada uma
com um apelido (`[R1]`, `[R2]`, na ordem de relevância) e o cabeçalho `[R1] título — AAAA-MM-DD — participantes`; depois, as
falas `#<segment_id> [mm:ss] Nome — Cargo: texto` dos segmentos que o trecho cobre agora (escolhidos pelo tempo, porque o
reprocessamento troca os ids, F5A-06) e uma linha por item de ata, ação, executivo ou marca da reunião com o tipo, o
`[mm:ss]` quando há tempo e o texto; o histórico curto da conversa (as citações das respostas anteriores já legíveis: os
apelidos valem só para a pergunta atual); a pergunta por último. Resposta em texto livre citando `[R1 12:03]`; o projeto
liga cada citação à linha enviada mais próxima, até 5 s (`rag/citations.py`)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import VOICE_LABELS, language_instruction, stamp

PROMPT_VERSION = "1.1"                  # F5.5 (A-20): "Voz 2" sem tradução
SCHEMA = None
HISTORY_TURNS = 8
SYSTEM = ("You answer questions about the user's past meetings using only the excerpts below, grouped by meeting. Each meeting "
          "has an alias such as [R1], valid only for the current question. Cite every statement as [R<n> mm:ss], taking the alias "
          "and the time from the excerpt lines you rely on (for example: 'o prazo foi fechado para sexta [R1 12:03]'); put several "
          "citations in one bracket separated by ';' ([R1 12:03; R2 05:10]) and cite [R<n>] alone when the line has no time. "
          "Never invent aliases, times or facts. If the excerpts do not answer the question, say plainly that you did not find it "
          f"in the meetings. Be concise. {VOICE_LABELS}")
NO_EXCERPTS = "(no excerpts were found for this question)"
KIND_NAMES = {"transcricao": "transcript", "decisao": "decision", "destaque": "highlight", "pendencia": "open point",
              "proximo_passo": "next step", "ata": "minutes", "acao": "action item", "executivo": "executive summary",
              "reuniao": "meeting info"}


@dataclass(frozen=True, slots=True)
class ExcerptLine:
    t_ms: int | None
    text: str
    speaker: str = ""                    # "Nome — Cargo" nas falas
    segment_id: int | None = None        # só nas falas (o segmento vigente)
    kind: str = "transcricao"            # `ChunkKind`

    def render(self) -> str:
        if self.segment_id is not None and self.t_ms is not None:
            return f"#{self.segment_id} [{stamp(self.t_ms)}] {self.speaker}: {self.text}"
        when = f" [{stamp(self.t_ms)}]" if self.t_ms is not None else ""
        return f"{KIND_NAMES.get(self.kind, self.kind)}{when}: {self.text}"


@dataclass(frozen=True, slots=True)
class MeetingBlock:
    alias: str                           # "R1"
    meeting_id: int
    title: str
    date: str                            # AAAA-MM-DD
    participants: str
    lines: tuple[ExcerptLine, ...]

    def render(self) -> str:
        people = self.participants or "(participantes não identificados)"
        head = f"[{self.alias}] {self.title or '(sem título)'} — {self.date or '?'} — {people}"
        return "\n".join([head, *(ln.render() for ln in self.lines)])


@dataclass(frozen=True, slots=True)
class RagChatCtx:
    blocks: Sequence[MeetingBlock]
    history: Sequence[tuple[str, str]]   # (role, text) das últimas mensagens da conversa
    question: str
    output_language: str


def render_blocks(blocks: Sequence[MeetingBlock]) -> str:
    return "\n\n".join(b.render() for b in blocks) or NO_EXCERPTS


def build(ctx: RagChatCtx) -> tuple[Message, ...]:
    excerpts = Message(Role.USER, "Excerpts from the user's meetings (aliases are valid only for this question; transcript lines are "
                                  f"#segment_id [mm:ss] speaker: text):\n\n{render_blocks(ctx.blocks)}")
    ack = Message(Role.ASSISTANT, "Understood. I will answer only from these excerpts, citing [R<n> mm:ss].")
    history = tuple(Message(Role.ASSISTANT if role == "assistant" else Role.USER, text) for role, text in ctx.history[-HISTORY_TURNS:])
    return (Message(Role.SYSTEM, f"{SYSTEM}\n{language_instruction(ctx.output_language)}"), excerpts, ack, *history,
            Message(Role.USER, ctx.question))
