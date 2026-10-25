"""Adaptador OpenAI (openai 3.x, Responses API; contrato `specs/llm-contrato.md`). Saída estruturada por `text.format =
json_schema` estrito (o esquema vai sem as chaves que o modo estrito recusa; o projeto valida com o esquema inteiro);
`reasoning.effort` só nas famílias que raciocinam (e sem `temperature` nelas); `input_audio` em wav ou mp3; `prompt_cache_key`
quando o pedido tem prefixo estável; `input_tokens.count`; `embeddings.create` com `dimensions`; streaming por eventos
`response.output_text.delta`. Cliente por event loop com `max_retries = 0` e timeout explícito (`httpx2` próprio só com
proxy ou certificado configurados). O SDK é importado só dentro dos métodos."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import numpy as np

from ..log import get_logger
from . import models_table
from .adapters_common import audio_b64, canary, has_audio, http_client_kwargs, needs_own_http_client, now_ms, split_system, translate_error
from .base import LlmStream, ProviderUnavailableError
from .registry import NetworkConfig
from .schema import provider_schema
from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Reasoning, Request, Role, Usage

log = get_logger("llm.openai")

AUDIO_FORMATS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3"}
EFFORT = {Reasoning.LOW: "low", Reasoning.HIGH: "high"}
MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "text-embedding")
EXCLUDED = ("realtime", "transcribe", "tts", "image", "search", "dall-e", "moderation")


class OpenAIProvider:
    kind = ProviderKind.OPENAI

    def __init__(self, api_key: str, *, net: NetworkConfig | None = None, provider_id: str = "openai",
                 params: Mapping[str, Any] | None = None, client: Any = None):
        self.id = provider_id
        self.api_key = api_key
        self.net = net or NetworkConfig()
        self.params = dict(params or {})
        self.capabilities: frozenset[Capability] = models_table.PROVIDER_CAPABILITIES[ProviderKind.OPENAI]
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
                import openai
            except ImportError as e:
                raise ProviderUnavailableError(f"openai não instalado: pip install -e .[llm] ({e})") from e
            http_client = None
            if needs_own_http_client(self.net):
                import httpx2

                http_client = httpx2.AsyncClient(**http_client_kwargs(self.net, self.net.timeout_s))
            client = openai.AsyncOpenAI(api_key=self.api_key, max_retries=0, timeout=self.net.timeout_s, http_client=http_client)
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
                    log.debug("fechar cliente openai: %s", e)

    # ---------------------------------------------------------------- tradução pedido -> SDK
    def _input(self, req: Request) -> tuple[str | None, list[dict[str, Any]]]:
        system, rest = split_system(req)
        items: list[dict[str, Any]] = []
        for m in rest:
            assistant = m.role == Role.ASSISTANT
            content: list[dict[str, Any]] = []
            if m.text:
                content.append({"type": "output_text" if assistant else "input_text", "text": m.text})
            for a in m.audio:
                fmt = AUDIO_FORMATS.get(a.mime)
                if fmt is None:
                    raise ProviderUnavailableError(f"{self.id} só aceita áudio wav ou mp3 (veio {a.mime})")
                content.append({"type": "input_audio", "input_audio": {"data": audio_b64(a.data), "format": fmt}})
            items.append({"role": "assistant" if assistant else "user", "content": content or [{"type": "input_text", "text": ""}]})
        return system, items

    def _kwargs(self, req: Request) -> dict[str, Any]:
        system, items = self._input(req)
        kw: dict[str, Any] = {"model": req.model, "input": items, "max_output_tokens": req.max_output_tokens, "store": False}
        if system:
            kw["instructions"] = system
        reasoning_model = models_table.supports_reasoning(self.kind, req.model)
        if reasoning_model:
            if req.reasoning in EFFORT:
                kw["reasoning"] = {"effort": EFFORT[req.reasoning]}
        else:
            kw["temperature"] = req.temperature
        if req.json_schema is not None:
            kw["text"] = {"format": {"type": "json_schema", "name": str(req.task), "schema": provider_schema(req.json_schema),
                                     "strict": True}}
        if req.cache_prefix and req.messages:
            kw["prompt_cache_key"] = "listener-" + hashlib.sha256(req.messages[0].text.encode("utf-8")).hexdigest()[:16]
        for key in ("top_p", "service_tier"):
            if key in self.params:
                kw[key] = self.params[key]
        return kw

    @staticmethod
    def _usage(usage: Any) -> Usage:
        if usage is None:
            return Usage()
        details = getattr(usage, "input_tokens_details", None)
        return Usage(int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0),
                     int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0, 0)

    @staticmethod
    def _finish(resp: Any) -> str:
        status = str(getattr(resp, "status", "") or "")
        if status in ("completed", ""):
            return "stop"
        details = getattr(resp, "incomplete_details", None)
        reason = str(getattr(details, "reason", "") or "")
        return "length" if reason == "max_output_tokens" else ("blocked" if reason == "content_filter" else "unknown")

    @staticmethod
    def _text(resp: Any) -> str:
        text = getattr(resp, "output_text", None)
        if text:
            return str(text)
        return "".join(str(getattr(c, "text", "")) for item in getattr(resp, "output", None) or ()
                       for c in getattr(item, "content", None) or () if getattr(c, "type", "") == "output_text")

    # ---------------------------------------------------------------- protocolo
    async def complete(self, req: Request) -> Completion:
        kw = self._kwargs(req)
        t0 = now_ms()
        try:
            resp = await self._client().responses.create(**kw)
        except Exception as e:
            raise translate_error(e, self.id) from e
        return Completion(self._text(resp), None, self._usage(getattr(resp, "usage", None)), str(getattr(resp, "model", None) or req.model),
                          now_ms() - t0, self._finish(resp), getattr(resp, "id", None))

    def stream(self, req: Request) -> LlmStream:
        kw = self._kwargs(req)
        state: dict[str, Any] = {"t0": now_ms(), "final": None}

        async def chunks() -> AsyncIterator[str]:
            try:
                events = await self._client().responses.create(**kw, stream=True)
                async for ev in events:
                    kind = str(getattr(ev, "type", "") or "")
                    if kind == "response.output_text.delta":
                        yield str(getattr(ev, "delta", "") or "")
                    elif kind in ("response.completed", "response.incomplete", "response.failed"):
                        state["final"] = getattr(ev, "response", None)
            except Exception as e:
                raise translate_error(e, self.id) from e

        def finish(text: str) -> Completion:
            final = state["final"]
            return Completion(text, None, self._usage(getattr(final, "usage", None)), str(getattr(final, "model", None) or req.model),
                              now_ms() - state["t0"], self._finish(final) if final is not None else "unknown", getattr(final, "id", None))

        return LlmStream(chunks(), finish)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        del task                                                  # a OpenAI não distingue documento de consulta
        kw: dict[str, Any] = {"model": model, "input": list(texts)}
        if dim:
            kw["dimensions"] = int(dim)
        try:
            resp = await self._client().embeddings.create(**kw)
        except Exception as e:
            raise translate_error(e, self.id) from e
        vectors = np.asarray([list(d.embedding) for d in resp.data], dtype=np.float32)
        usage = getattr(resp, "usage", None)
        return Embeddings(str(getattr(resp, "model", None) or model), int(vectors.shape[1]) if vectors.size else int(dim or 0), vectors,
                          Usage(int(getattr(usage, "prompt_tokens", 0) or 0), 0))

    async def count_tokens(self, req: Request) -> int | None:
        if has_audio(req):
            return None
        system, items = self._input(req)
        kw: dict[str, Any] = {"model": req.model, "input": items}
        if system:
            kw["instructions"] = system
        try:
            resp = await self._client().responses.input_tokens.count(**kw)
        except Exception as e:
            log.debug("count_tokens: %s", e)
            return None
        n = getattr(resp, "input_tokens", None)
        return int(n) if n is not None else None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        out: list[ModelInfo] = []
        try:
            page = await self._client().models.list()
            async for m in page:
                mid = str(getattr(m, "id", "") or "")
                if mid.startswith(MODEL_PREFIXES) and not any(x in mid for x in EXCLUDED):
                    out.append(models_table.model_info(self.kind, mid))
        except Exception as e:
            raise translate_error(e, self.id) from e
        return tuple(sorted(out, key=lambda i: i.id))

    async def test_connection(self, model: str) -> ConnectionTest:
        return await canary(self, model)
