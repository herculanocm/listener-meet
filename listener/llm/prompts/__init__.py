"""Prompts e esquemas do projeto, um módulo por tarefa (`PROMPT_VERSION`, `SCHEMA`, `build(ctx)`); mudar o texto de um prompt
sobe a versão (regra de revisão de diff). `summaries.prompt_version` e `llm_calls.prompt_version` gravam `"minutes/1.0"`."""
from __future__ import annotations

from types import ModuleType

from ..types import LlmTask
from . import chat, executive, glossary, identify, minutes, pre_summary, revise, test, title, transcribe

MODULES: dict[LlmTask, ModuleType] = {
    LlmTask.GLOSSARY: glossary, LlmTask.IDENTIFY: identify, LlmTask.MINUTES: minutes, LlmTask.EXECUTIVE: executive,
    LlmTask.PRE_SUMMARY: pre_summary, LlmTask.TITLE: title, LlmTask.REVISE: revise, LlmTask.TRANSCRIBE: transcribe, LlmTask.CHAT: chat,
    LlmTask.TEST: test,
}


def prompt_version(task: LlmTask) -> str:
    return f"{task}/{MODULES[task].PROMPT_VERSION}"


def schema_for(task: LlmTask) -> dict | None:
    return MODULES[task].SCHEMA
