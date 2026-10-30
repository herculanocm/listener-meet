"""F5.6 L2 (A-31): o que vai junto das instruções em cada tarefa, para a página "Prompts" mostrar só leitura. As mensagens
saem do próprio `build` de cada módulo (o que muda no código aparece aqui sozinho), com marcadores no lugar dos dados
("{transcrição}", "{falantes identificados}") e `MARK` onde entram as instruções; depois, o formato da resposta (o esquema
JSON ou texto livre). Na ata, também a consolidação do modo em blocos. Sem Qt; montada só quando a tarefa é aberta."""
from __future__ import annotations

import json
from typing import Any

from ..types import AudioPart, LlmTask, Message, Role
from . import build, chat, executive, glossary, identify, minutes, partial, rag_chat, revise, schema_for, title, transcribe
from .common import Line, Marker, MeetingCtx, Person
from .rag_chat import MeetingBlock

MARK = "{suas instruções}"
ROLES = {Role.SYSTEM: "Sistema", Role.USER: "Usuário", Role.ASSISTANT: "Modelo"}
MARKER_SHOWN = 'marcador 0 em [00:00]: "{nota}"'    # o marcador da reunião de exemplo, trocado por um marcador só
REDUCE_NOTE = "Ata longa, em blocos: cada bloco recebe o pedido acima; depois, a consolidação recebe:"


class _Line(Line):
    """Uma fala que se escreve como o marcador: o bloco inteiro da transcrição vira uma linha."""
    __slots__ = ()

    def render(self) -> str:
        return self.text


class _Person(Person):
    __slots__ = ()

    def render(self) -> str:
        return self.name


class _Block(MeetingBlock):
    __slots__ = ()

    def render(self) -> str:
        return self.title


def _lines(mark: str) -> tuple[Line, ...]:
    return (_Line(0, 0, "", mark),)


def _meeting() -> MeetingCtx:
    return MeetingCtx("{título}", "{data}", "{participantes digitados}", (_Person(0, "{participantes com perfil}"),),
                      (Marker(0, 0, "{nota}"),), "{idioma da reunião}", glossary="{glossário}",
                      speakers=(_Person(0, "{falantes identificados}"),))


def _context(task: LlmTask) -> Any:
    m, people = _meeting(), (_Person(0, "{pessoas cadastradas}"),)
    summary = executive.SummaryCtx(m, "{ata em JSON}")
    contexts: dict[LlmTask, Any] = {
        LlmTask.IDENTIFY: identify.IdentifyCtx(m, _lines("{transcrição}"), ("{vozes sem nome}",), people),
        LlmTask.REVISE: revise.ReviseCtx(m, _lines("{falas de contexto antes}"), _lines("{falas a revisar}"),
                                         _lines("{falas de contexto depois}")),
        LlmTask.TITLE: title.TitleCtx(m, "{pré-resumo, contexto da ata ou falas de abertura}"),
        LlmTask.PRE_SUMMARY: summary, LlmTask.EXECUTIVE: summary,
        LlmTask.MINUTES: minutes.MinutesCtx(m, _lines("{transcrição}"), people),
        LlmTask.PARTIAL: partial.PartialCtx(m, "{resumo parcial anterior em JSON}", _lines("{legendas novas}")),
        LlmTask.CHAT: chat.ChatCtx(m, _lines("{transcrição}"), (("user", "{perguntas e respostas anteriores}"),), "{pergunta}"),
        LlmTask.RAG_CHAT: rag_chat.RagChatCtx((_Block("R1", 0, "{trechos achados pela busca, por reunião}", "", "", ()),),
                                              (("user", "{perguntas e respostas anteriores}"),), "{pergunta}", "{idioma padrão}"),
        LlmTask.GLOSSARY: glossary.GlossaryCtx("{título}", "{participantes digitados}", ("{nomes não resolvidos}",), people,
                                               "{glossário fixo}", "{idioma da reunião}"),
        LlmTask.TRANSCRIBE: transcribe.TranscribeCtx(AudioPart(b"", "audio/flac", "{áudio da janela}"), "{idioma da reunião}",
                                                     "{termos do glossário}"),
    }
    return contexts[task]


def render(messages: tuple[Message, ...]) -> list[str]:
    """Cada mensagem com o papel em PT-BR ("[Sistema]") e o áudio anexado numa linha."""
    out = []
    for m in messages:
        audio = "".join(f"\n[+ áudio: {a.label}]" for a in m.audio)
        out.append(f"[{ROLES[m.role]}]\n{m.text}{audio}")
    return out


def preview(task: LlmTask) -> str:
    """O pedido inteiro de uma tarefa com marcadores, as instruções em `MARK` e o formato da resposta no fim."""
    parts = render(build(task, _context(task), MARK))
    if task == LlmTask.MINUTES:
        notes = minutes.ReduceCtx(_meeting(), ("{notas de cada bloco em JSON}",), (_Person(0, "{pessoas cadastradas}"),))
        parts += [REDUCE_NOTE, *render(build(task, notes, MARK, reduce=True))]
    schema = schema_for(task)
    parts.append("[Formato da resposta]\n" + (json.dumps(schema, ensure_ascii=False) if schema else "Texto livre."))
    return "\n\n".join(parts).replace(MARKER_SHOWN, "{marcadores com a nota}")


__all__ = ["MARK", "preview", "render"]
