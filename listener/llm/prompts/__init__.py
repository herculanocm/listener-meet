"""Prompts e esquemas do projeto, um módulo por tarefa (`PROMPT_VERSION`, `SCHEMA`, `build(ctx)`); mudar o texto de um prompt
sobe a versão (regra de revisão de diff). `summaries.prompt_version` e `llm_calls.prompt_version` gravam `"minutes/1.0"`.
F5.6 L2 (A-24 a A-28): as tarefas de `EDITABLE` separam `INSTRUCTIONS` (editáveis nas Configurações) de `CONTRACT`
(fixo) e recebem `build(ctx, instructions)`; com instruções do usuário, a versão gravada leva a marca `+u<hash>` do texto
(`minutes/1.2+u3fa9c1`), nunca o texto. F5.10: `rag_sql` e `rag_answer` ("Perguntar à base") entram na página e o `rag_chat`
sai dela (o chat do Histórico passa a ser o agente; o módulo segue para o chat global antigo)."""
from __future__ import annotations

from types import ModuleType
from typing import Any

from ...config.prompts import text_hash
from ..types import LlmTask, Message
from . import (chat, executive, glossary, identify, minutes, partial, pre_summary, rag_answer, rag_chat, rag_sql, revise, test, title,
               transcribe)

MODULES: dict[LlmTask, ModuleType] = {
    LlmTask.GLOSSARY: glossary, LlmTask.IDENTIFY: identify, LlmTask.MINUTES: minutes, LlmTask.EXECUTIVE: executive,
    LlmTask.PRE_SUMMARY: pre_summary, LlmTask.TITLE: title, LlmTask.REVISE: revise, LlmTask.TRANSCRIBE: transcribe, LlmTask.CHAT: chat,
    LlmTask.TEST: test, LlmTask.PARTIAL: partial, LlmTask.RAG_CHAT: rag_chat, LlmTask.RAG_SQL: rag_sql, LlmTask.RAG_ANSWER: rag_answer,
}
# A-25: as tarefas com instruções editáveis, na ordem da página "Prompts" (o `test` fica fora; F5.10: o `rag_chat` também)
EDITABLE: tuple[LlmTask, ...] = (LlmTask.IDENTIFY, LlmTask.REVISE, LlmTask.TITLE, LlmTask.PRE_SUMMARY, LlmTask.MINUTES,
                                 LlmTask.EXECUTIVE, LlmTask.PARTIAL, LlmTask.CHAT, LlmTask.RAG_SQL, LlmTask.RAG_ANSWER,
                                 LlmTask.GLOSSARY, LlmTask.TRANSCRIBE)


def prompt_version(task: LlmTask, instructions: str | None = None) -> str:
    """`minutes/1.2`; com instruções do usuário, `minutes/1.2+u3fa9c1` (o hash do texto sem os espaços das pontas, A-28)."""
    base = f"{task}/{MODULES[task].PROMPT_VERSION}"
    custom = (instructions or "").strip()
    return f"{base}+u{text_hash(custom)}" if custom else base


def schema_for(task: LlmTask) -> dict | None:
    return MODULES[task].SCHEMA


def default_instructions(task: LlmTask) -> str:
    """As instruções do app de uma tarefa editável (o que "Restaurar padrão" põe de volta)."""
    return str(MODULES[task].INSTRUCTIONS)


def build(task: LlmTask, ctx: Any, instructions: str | None = None, *, reduce: bool = False) -> tuple[Message, ...]:
    """O prompt de uma tarefa com as instruções dadas (None = as do app); `reduce`, a consolidação da ata em blocos."""
    module = MODULES[task]
    return (module.build_reduce if reduce else module.build)(ctx, instructions)
