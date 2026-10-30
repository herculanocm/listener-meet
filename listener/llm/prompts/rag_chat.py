"""Chat sobre todas as reuniões (F5 item 11, F5A-24): os trechos que a busca híbrida achou, agrupados por reunião, cada uma
com um apelido (`[R1]`, `[R2]`, na ordem de relevância) e o cabeçalho `[R1] título — AAAA-MM-DD — participantes`; depois, as
falas `#<segment_id> [mm:ss] Nome — Cargo: texto` dos segmentos que o trecho cobre agora (escolhidos pelo tempo, porque o
reprocessamento troca os ids, F5A-06) e uma linha por item de ata, ação, executivo ou marca da reunião com o tipo, o
`[mm:ss]` quando há tempo e o texto; o histórico curto da conversa (as citações das respostas anteriores já legíveis: os
apelidos valem só para a pergunta atual); a pergunta por último. Resposta em texto livre citando `[R1 12:03]`; o projeto
liga cada citação à linha enviada mais próxima, até 5 s (`rag/citations.py`). F5.6 L2 (A-24): as instruções (o papel e o
estilo) são do usuário; os apelidos, as citações, não inventar e dizer quando não achou ficam. Versão 1.2: o "Seja conciso."
veio para as instruções (antes ficava no meio do contrato), para o estilo ser do usuário sem contradizer o contrato.
Versão 2.0 (D-071): o prompt inteiro em pt-BR."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import VOICE_LABELS, language_instruction, stamp, system_text

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.2: "Seja conciso." nas instruções e "Voz 2" sem tradução
SCHEMA = None
HISTORY_TURNS = 8
INSTRUCTIONS = ("Responda perguntas sobre as reuniões passadas do usuário usando só os trechos abaixo, agrupados por reunião. "
                "Seja conciso.")
CONTRACT = ("Cada reunião tem um apelido como [R1], válido só para a pergunta atual. Cite toda afirmação como [R<n> mm:ss], "
            "tirando o apelido e o tempo das linhas dos trechos em que se apoia (por exemplo: 'o prazo foi fechado para sexta "
            "[R1 12:03]'); ponha várias citações num colchete só, separadas por ';' ([R1 12:03; R2 05:10]), e cite [R<n>] "
            "sozinho quando a linha não tem tempo. Nunca invente apelidos, tempos nem fatos. Se os trechos não respondem à "
            f"pergunta, diga claramente que não encontrou isso nas reuniões. {VOICE_LABELS}")
NO_EXCERPTS = "(nenhum trecho foi encontrado para esta pergunta)"
KIND_NAMES = {"transcricao": "transcrição", "decisao": "decisão", "destaque": "destaque", "pendencia": "pendência",
              "proximo_passo": "próximo passo", "ata": "ata", "acao": "ação", "executivo": "resumo executivo",
              "reuniao": "dados da reunião"}


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


def build(ctx: RagChatCtx, instructions: str | None = None) -> tuple[Message, ...]:
    excerpts = Message(Role.USER, "Trechos das reuniões do usuário (os apelidos valem só para esta pergunta; as linhas da "
                                  f"transcrição são #segment_id [mm:ss] falante: texto):\n\n{render_blocks(ctx.blocks)}")
    ack = Message(Role.ASSISTANT, "Entendido. Vou responder só a partir destes trechos, citando [R<n> mm:ss].")
    history = tuple(Message(Role.ASSISTANT if role == "assistant" else Role.USER, text) for role, text in ctx.history[-HISTORY_TURNS:])
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.output_language)
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}"), excerpts, ack, *history,
            Message(Role.USER, ctx.question))
