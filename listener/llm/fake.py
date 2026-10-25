"""`FakeProvider` (F3, testes sem rede e `LISTENER_LLM=fake` no worker leve): roteiro por tarefa, falhas injetáveis (429 com
`Retry-After`, timeout, JSON inválido, "request too large"), registro de todos os pedidos, streaming em pedaços,
capacidades e janela configuráveis, contagem de paralelismo (para conferir o semáforo). Sem um roteiro, uma tarefa com
esquema recebe a instância mínima válida do esquema; sem esquema, um texto fixo."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import numpy as np

from .base import LlmStream
from .models_table import PROVIDER_CAPABILITIES
from .schema import minimal_instance
from .types import (Capability, Completion, ConnectionTest, Embeddings, EmbedTask, LlmTask, ModelInfo, ProviderKind, Request,
                    Usage)

type Script = Callable[[Request], Any] | dict[str, Any] | list[Any] | str

FAKE_FAST = "fake-fast"
FAKE_STRONG = "fake-strong"


class FakeProvider:
    def __init__(self, *, kind: ProviderKind = ProviderKind.GEMINI, provider_id: str = "fake",
                 capabilities: frozenset[Capability] | None = None, input_limit: int = 1_000_000, output_limit: int = 8192,
                 scripts: dict[LlmTask, Script] | None = None, latency_ms: int = 3, delay_s: float = 0.0, embed_dim: int = 768):
        self.id = provider_id
        self.kind = kind
        self.capabilities: frozenset[Capability] = capabilities if capabilities is not None else PROVIDER_CAPABILITIES[kind]
        self.input_limit = input_limit
        self.output_limit = output_limit
        self.scripts: dict[LlmTask, Script] = dict(scripts or {})
        self.failures: list[tuple[LlmTask | None, BaseException]] = []
        self.requests: list[Request] = []
        self.embed_calls: list[tuple[int, str, int | None, str]] = []
        self.latency_ms = latency_ms
        self.delay_s = delay_s
        self.embed_dim = embed_dim
        self.in_flight = 0
        self.peak_parallel = 0
        self.closed = False
        self.cached_prefix_tokens = 0          # simula cache: pedidos com `cache_prefix` repetido devolvem `cached_tokens`

    # ---------------------------------------------------------------- roteiro
    def script(self, task: LlmTask, response: Script) -> None:
        self.scripts[task] = response

    def fail_next(self, exc: BaseException, task: LlmTask | None = None) -> None:
        """A próxima chamada da tarefa (ou de qualquer uma) levanta `exc`; cada falha é consumida uma vez (retentativa passa)."""
        self.failures.append((task, exc))

    def calls(self, task: LlmTask | None = None) -> list[Request]:
        return [r for r in self.requests if task is None or r.task == task]

    def _take_failure(self, task: LlmTask) -> BaseException | None:
        for i, (t, exc) in enumerate(self.failures):
            if t is None or t == task:
                del self.failures[i]
                return exc
        return None

    def _payload(self, req: Request) -> Any:
        script = self.scripts.get(req.task)
        if callable(script):
            return script(req)
        if script is not None:
            return script
        if req.task == LlmTask.TEST and req.json_schema is not None:
            return {"ok": True}                                  # o canário passa por padrão, como num provedor real
        if req.json_schema is not None:
            return minimal_instance(req.json_schema)
        return f"resposta simulada para {req.task}"

    def _usage(self, req: Request, text: str) -> Usage:
        audio = int(req.audio_seconds * 32)
        cached = min(self.cached_prefix_tokens, req.text_chars // 4) if req.cache_prefix else 0
        if req.cache_prefix:
            self.cached_prefix_tokens = len(req.messages[0].text) // 4 if req.messages else 0
        return Usage(max(1, req.text_chars // 4) + audio, max(1, len(text) // 4), cached, audio)

    async def _enter(self, req: Request) -> None:
        self.requests.append(req)
        exc = self._take_failure(req.task)
        if exc is not None:
            raise exc
        self.in_flight += 1
        self.peak_parallel = max(self.peak_parallel, self.in_flight)
        await asyncio.sleep(self.delay_s)

    # ---------------------------------------------------------------- protocolo
    async def complete(self, req: Request) -> Completion:
        await self._enter(req)
        try:
            payload = self._payload(req)
        finally:
            self.in_flight -= 1
        if isinstance(payload, str):
            text, data = payload, None
        else:
            text, data = json.dumps(payload, ensure_ascii=False), payload
        return Completion(text, data, self._usage(req, text), req.model, self.latency_ms, "stop", f"fake-{len(self.requests)}")

    def stream(self, req: Request) -> LlmStream:
        state: dict[str, Any] = {}

        async def chunks() -> AsyncIterator[str]:
            comp = await self.complete(req)
            state["comp"] = comp
            text = comp.text
            n = max(1, -(-len(text) // 3))                       # 3 pedaços (teto da divisão)
            for i in range(0, len(text), n):
                await asyncio.sleep(0)
                yield text[i:i + n]

        def finish(text: str) -> Completion:
            comp: Completion = state["comp"]
            return Completion(text, comp.data, comp.usage, comp.model, comp.latency_ms, comp.finish, comp.raw_id)

        return LlmStream(chunks(), finish)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        d = dim or self.embed_dim
        self.embed_calls.append((len(texts), model, dim, str(task)))
        out = np.zeros((len(texts), d), np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:4], "little")
            v = np.random.default_rng(seed).standard_normal(d).astype(np.float32)
            out[i] = v / float(np.linalg.norm(v))
        return Embeddings(model, d, out, Usage(sum(len(t) for t in texts) // 4, 0))

    async def count_tokens(self, req: Request) -> int | None:
        return req.text_chars // 4 if Capability.COUNT_TOKENS in self.capabilities else None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (ModelInfo(FAKE_FAST, "Fake rápido", self.input_limit, self.output_limit, self.capabilities, "gemini-2.5-flash"),
                ModelInfo(FAKE_STRONG, "Fake forte", self.input_limit, self.output_limit, self.capabilities, "gemini-2.5-pro"))

    async def test_connection(self, model: str) -> ConnectionTest:
        exc = self._take_failure(LlmTask.TEST)
        if exc is not None:
            return ConnectionTest(False, self.latency_ms, model, str(exc))
        return ConnectionTest(True, self.latency_ms, model)

    async def aclose(self) -> None:
        self.closed = True
