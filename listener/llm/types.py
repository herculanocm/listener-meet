"""Tipos da camada de LLM (F3, `specs/llm-contrato.md`): tudo `frozen, slots`, enumerações `StrEnum`. Nenhum SDK entra aqui.
Os estados do domínio que o banco guarda (`SummaryKind`, `ActionStatus`, `DecisionSource`...) moram em `model.py` e são
reexportados para quem só conhece esta camada."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from ..model import ActionStatus, ChatRole, DecisionSource, LlmCallStatus, SummaryKind, SummaryStatus

__all__ = ["LLM_JOB_TASKS", "TASK_CLASS", "TASK_REASONING", "ActionStatus", "AudioPart", "Capability", "ChatRole", "Completion",
           "ConnectionTest", "DecisionSource", "EmbedTask", "Embeddings", "LlmCallStatus", "LlmTask", "Message", "ModelClass", "ModelInfo",
           "ProviderKind", "Reasoning", "Request", "Role", "SummaryKind", "SummaryStatus", "Usage"]


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Capability(StrEnum):
    TEXT = "text"
    JSON = "json"
    AUDIO_IN = "audio_in"
    EMBEDDINGS = "embeddings"
    STREAMING = "streaming"
    COUNT_TOKENS = "count_tokens"
    CACHE = "cache"


class LlmTask(StrEnum):
    GLOSSARY = "glossary"
    IDENTIFY = "identify"
    MINUTES = "minutes"
    EXECUTIVE = "executive"
    PRE_SUMMARY = "pre_summary"
    TITLE = "title"
    REVISE = "revise"
    TRANSCRIBE = "transcribe"
    CHAT = "chat"
    TEST = "test"
    PARTIAL = "partial"            # F4: resumo parcial ao vivo
    RAG_EMBED = "rag_embed"        # F5: embeddings do índice (um registro em `llm_calls` por lote)
    RAG_CHAT = "rag_chat"          # F5: chat sobre todas as reuniões


class Reasoning(StrEnum):
    NONE = "none"
    LOW = "low"
    HIGH = "high"


class ProviderKind(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"                # F5.1: só embeddings (e5-small na CPU, `llm/local_embed.py`), sem chave nem custo


class EmbedTask(StrEnum):
    DOCUMENT = "document"
    QUERY = "query"


class ModelClass(StrEnum):
    FAST = "fast"
    STRONG = "strong"


# Classe de modelo e raciocínio por tarefa (contrato "Prompts"; F3A-45): rápido nas leves, forte nas que decidem
TASK_CLASS: dict[LlmTask, ModelClass] = {
    LlmTask.GLOSSARY: ModelClass.FAST, LlmTask.TITLE: ModelClass.FAST, LlmTask.PRE_SUMMARY: ModelClass.FAST,
    LlmTask.REVISE: ModelClass.FAST, LlmTask.TRANSCRIBE: ModelClass.FAST, LlmTask.CHAT: ModelClass.FAST, LlmTask.TEST: ModelClass.FAST,
    LlmTask.PARTIAL: ModelClass.FAST, LlmTask.RAG_EMBED: ModelClass.FAST, LlmTask.RAG_CHAT: ModelClass.FAST,   # F5 Q11: o rápido
    LlmTask.IDENTIFY: ModelClass.STRONG, LlmTask.MINUTES: ModelClass.STRONG, LlmTask.EXECUTIVE: ModelClass.STRONG,
}
TASK_REASONING: dict[LlmTask, Reasoning] = {t: (Reasoning.LOW if c == ModelClass.STRONG else Reasoning.NONE) for t, c in TASK_CLASS.items()}
# as tarefas do job `llm` da reunião (a estimativa da fila mede só elas, não os vetores nem o chat, R5-05)
LLM_JOB_TASKS: tuple[LlmTask, ...] = (LlmTask.IDENTIFY, LlmTask.MINUTES, LlmTask.EXECUTIVE, LlmTask.PRE_SUMMARY, LlmTask.TITLE,
                                      LlmTask.REVISE)


BYTES_PER_SECOND = {"audio/wav": 32_000, "audio/x-wav": 32_000, "audio/flac": 12_000, "audio/mpeg": 16_000, "audio/mp3": 16_000}


@dataclass(frozen=True, slots=True)
class AudioPart:
    data: bytes
    mime: str                      # "audio/flac", "audio/wav"
    label: str                     # "voz SPK_3, trecho 1"

    @property
    def seconds(self) -> float:
        """Duração estimada pelo tamanho (16 kHz mono: WAV 32 kB/s; FLAC ≈ 12 kB/s [exp]); só para custo prévio e o fake."""
        return len(self.data) / BYTES_PER_SECOND.get(self.mime, 12_000)


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    text: str
    audio: tuple[AudioPart, ...] = ()


@dataclass(frozen=True, slots=True)
class Request:
    task: LlmTask
    model: str
    messages: tuple[Message, ...]
    json_schema: Mapping[str, Any] | None = None
    temperature: float = 0.2
    max_output_tokens: int = 8192
    reasoning: Reasoning = Reasoning.NONE
    timeout_s: float = 120.0
    cache_prefix: bool = False     # a primeira mensagem de usuário é um prefixo estável (transcrição): cache dos provedores

    @property
    def text_chars(self) -> int:
        return sum(len(m.text) for m in self.messages)

    @property
    def audio_seconds(self) -> float:
        """Estimativa da duração do áudio anexado pelo tamanho e pelo tipo; só para custo prévio."""
        return sum(a.seconds for m in self.messages for a in m.audio)

    def with_messages(self, messages: tuple[Message, ...]) -> Request:
        return Request(self.task, self.model, messages, self.json_schema, self.temperature, self.max_output_tokens, self.reasoning,
                       self.timeout_s, self.cache_prefix)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    audio_tokens: int = 0
    cache_write_tokens: int = 0    # parte de `input_tokens` gravada no cache (a Anthropic cobra 1,25 vez a entrada, F3.1 R3-10)

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.cached_tokens + other.cached_tokens, self.audio_tokens + other.audio_tokens,
                     self.cache_write_tokens + other.cache_write_tokens)


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    data: Mapping[str, Any] | None
    usage: Usage
    model: str
    latency_ms: int
    finish: str = "stop"           # stop, length, blocked, tool, unknown
    raw_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    display_name: str
    input_limit: int
    output_limit: int
    capabilities: frozenset[Capability]
    price_ref: str = ""            # família de preço em `llm_prices` (ex.: gemini-2.5-flash)

    def has(self, cap: Capability) -> bool:
        return cap in self.capabilities


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    ok: bool
    latency_ms: int
    model: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Embeddings:
    model: str
    dim: int
    vectors: np.ndarray = field(repr=False)     # float32 (n, dim)
    usage: Usage = Usage()
