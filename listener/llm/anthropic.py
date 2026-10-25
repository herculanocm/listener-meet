"""Adaptador Anthropic (anthropic 2.x; contrato `specs/llm-contrato.md`). Saída estruturada por uso de ferramenta com
`input_schema` e `tool_choice` forçado (resposta em `content[].input`); `cache_control` efêmero no prefixo estável;
pensamento estendido só sem ferramenta forçada (o SDK não combina os dois); `messages.count_tokens`; `models.list` traz
`max_input_tokens`; sem embeddings nem áudio nativo [fato]. Na major 2.x `messages.create` não aceita `temperature`,
`top_p` nem `top_k` [fato, medido com o SDK 2.3.0]: o adaptador não os envia. Cliente por event loop com `max_retries = 0`
e timeout explícito; streaming pelos eventos brutos (`content_block_delta`, `message_delta`). O SDK é importado só dentro
dos métodos."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..log import get_logger
from . import models_table
from .adapters_common import canary, has_audio, http_client_kwargs, needs_own_http_client, now_ms, split_system, translate_error
from .base import LlmStream, ProviderUnavailableError
from .registry import NetworkConfig
from .schema import provider_schema
from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Reasoning, Request, Role, Usage

log = get_logger("llm.anthropic")

TOOL_NAME = "emit_result"
THINKING_BUDGET = {Reasoning.LOW: 2048, Reasoning.HIGH: 8192}
FINISH = {"end_turn": "stop", "tool_use": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "blocked"}


class AnthropicProvider:
    kind = ProviderKind.ANTHROPIC

    def __init__(self, api_key: str, *, net: NetworkConfig | None = None, provider_id: str = "anthropic",
                 params: Mapping[str, Any] | None = None, client: Any = None):
        self.id = provider_id
        self.api_key = api_key
        self.net = net or NetworkConfig()
        self.params = dict(params or {})
        self.capabilities: frozenset[Capability] = models_table.PROVIDER_CAPABILITIES[ProviderKind.ANTHROPIC]
        self._injected = client
        self._clients: dict[int, tuple[Any, Any]] = {}

    # ---------------------------------------------------------------- cliente
    def _client(self) -> Any:
        if self._injected is not None:
            return self._injected
        key = id(asyncio.get_running_loop())
        pair = self._clients.get(key)
        if pair is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderUnavailableError(f"anthropic não instalado: pip install -e .[llm] ({e})") from e
            http_client = None
            if needs_own_http_client(self.net):
                import httpx2

                http_client = httpx2.AsyncClient(**http_client_kwargs(self.net, self.net.timeout_s))
            client = anthropic.AsyncAnthropic(api_key=self.api_key, max_retries=0, timeout=self.net.timeout_s, http_client=http_client)
            pair = self._clients[key] = (client, http_client)
        return pair[0]

    async def aclose(self) -> None:
        pair = self._clients.pop(id(asyncio.get_running_loop()), None)
        if pair is None:
            return
        for obj in pair:
            if obj is not None:
                try:
                    await obj.aclose() if hasattr(obj, "aclose") else await obj.close()
                except Exception as e:  # fechar nunca derruba o job
                    log.debug("fechar cliente anthropic: %s", e)

    # ---------------------------------------------------------------- tradução pedido -> SDK
    def _messages(self, req: Request) -> tuple[str | None, list[dict[str, Any]]]:
        if has_audio(req):
            raise ProviderUnavailableError(f"{self.id} não aceita áudio nativo")
        system, rest = split_system(req)
        out: list[dict[str, Any]] = []
        first_user = True
        for m in rest:
            block: dict[str, Any] = {"type": "text", "text": m.text or " "}
            if req.cache_prefix and first_user and m.role == Role.USER:
                block["cache_control"] = {"type": "ephemeral"}
            if m.role == Role.USER:
                first_user = False
            out.append({"role": "assistant" if m.role == Role.ASSISTANT else "user", "content": [block]})
        return system, out

    def _kwargs(self, req: Request) -> dict[str, Any]:
        system, messages = self._messages(req)
        kw: dict[str, Any] = {"model": req.model, "max_tokens": req.max_output_tokens, "messages": messages}
        if system:
            kw["system"] = system
        thinking = (req.json_schema is None and req.reasoning in THINKING_BUDGET and models_table.supports_reasoning(self.kind, req.model))
        if thinking:
            kw["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET[req.reasoning]}
            kw["max_tokens"] = max(req.max_output_tokens, THINKING_BUDGET[req.reasoning] + 1024)
        if req.json_schema is not None:
            kw["tools"] = [{"name": TOOL_NAME, "description": "Return the structured result for this task.",
                            "input_schema": provider_schema(req.json_schema, frozenset())}]
            kw["tool_choice"] = {"type": "tool", "name": TOOL_NAME, "disable_parallel_tool_use": True}
        if "service_tier" in self.params:
            kw["service_tier"] = self.params["service_tier"]
        return kw

    @staticmethod
    def _usage(usage: Any) -> Usage:
        if usage is None:
            return Usage()
        plain = int(getattr(usage, "input_tokens", 0) or 0)
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return Usage(plain + read + write, int(getattr(usage, "output_tokens", 0) or 0), read, 0)   # `input_tokens` exclui o cache [fato]

    @staticmethod
    def _parse(msg: Any) -> tuple[str, dict[str, Any] | None]:
        texts: list[str] = []
        data: dict[str, Any] | None = None
        for block in getattr(msg, "content", None) or ():
            kind = str(getattr(block, "type", "") or "")
            if kind == "text":
                texts.append(str(getattr(block, "text", "") or ""))
            elif kind == "tool_use" and data is None and isinstance(getattr(block, "input", None), dict):
                data = dict(block.input)
        text = "".join(texts) or (json.dumps(data, ensure_ascii=False) if data is not None else "")
        return text, data

    # ---------------------------------------------------------------- protocolo
    async def complete(self, req: Request) -> Completion:
        kw = self._kwargs(req)
        t0 = now_ms()
        try:
            msg = await self._client().messages.create(**kw)
        except Exception as e:
            raise translate_error(e, self.id) from e
        text, data = self._parse(msg)
        return Completion(text, data, self._usage(getattr(msg, "usage", None)), str(getattr(msg, "model", None) or req.model),
                          now_ms() - t0, FINISH.get(str(getattr(msg, "stop_reason", "") or ""), "unknown"), getattr(msg, "id", None))

    def stream(self, req: Request) -> LlmStream:
        kw = self._kwargs(req)
        state: dict[str, Any] = {"t0": now_ms(), "in": 0, "out": 0, "cached": 0, "finish": "stop", "model": req.model, "id": None}

        async def chunks() -> AsyncIterator[str]:
            try:
                events = await self._client().messages.create(**kw, stream=True)
                async for ev in events:
                    kind = str(getattr(ev, "type", "") or "")
                    if kind == "message_start":
                        msg = getattr(ev, "message", None)
                        u = self._usage(getattr(msg, "usage", None))
                        state["in"], state["cached"] = u.input_tokens, u.cached_tokens
                        state["model"] = getattr(msg, "model", None) or state["model"]
                        state["id"] = getattr(msg, "id", None)
                    elif kind == "content_block_delta":
                        delta = getattr(ev, "delta", None)
                        if str(getattr(delta, "type", "")) == "text_delta":
                            yield str(getattr(delta, "text", "") or "")
                    elif kind == "message_delta":
                        mu = getattr(ev, "usage", None)
                        state["out"] = int(getattr(mu, "output_tokens", 0) or 0)
                        stop = getattr(getattr(ev, "delta", None), "stop_reason", None)
                        if stop:
                            state["finish"] = FINISH.get(str(stop), "unknown")
            except Exception as e:
                raise translate_error(e, self.id) from e

        def finish(text: str) -> Completion:
            return Completion(text, None, Usage(state["in"], state["out"], state["cached"], 0), str(state["model"]), now_ms() - state["t0"],
                              state["finish"], state["id"])

        return LlmStream(chunks(), finish)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        del texts, model, dim, task
        raise ProviderUnavailableError("Anthropic não oferece embeddings: escolha Gemini ou OpenAI para embeddings")

    async def count_tokens(self, req: Request) -> int | None:
        kw = self._kwargs(req)
        for key in ("max_tokens", "service_tier"):
            kw.pop(key, None)
        try:
            resp = await self._client().messages.count_tokens(**kw)
        except Exception as e:
            log.debug("count_tokens: %s", e)
            return None
        n = getattr(resp, "input_tokens", None)
        return int(n) if n is not None else None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        out: list[ModelInfo] = []
        try:
            page = await self._client().models.list(limit=100)
            async for m in page:
                mid = str(getattr(m, "id", "") or "")
                if not mid:
                    continue
                out.append(models_table.model_info(self.kind, mid, display_name=getattr(m, "display_name", None),
                                                   input_limit=getattr(m, "max_input_tokens", None),
                                                   output_limit=getattr(m, "max_tokens", None)))
        except Exception as e:
            raise translate_error(e, self.id) from e
        return tuple(out)

    async def test_connection(self, model: str) -> ConnectionTest:
        return await canary(self, model)
