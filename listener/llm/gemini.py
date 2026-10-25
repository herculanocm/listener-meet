"""Adaptador Gemini (google-genai 2.x; contrato `specs/llm-contrato.md`). Saída estruturada por `response_mime_type =
application/json` + `response_json_schema`; `thinking_config` por classe de tarefa (Flash com orçamento zero no rápido);
áudio em linha (`inline_data`); `count_tokens`, `list_models` (limites e ações reais), `embed_content` com `task_type` e
`output_dimensionality`; streaming por `generate_content_stream`. Cliente por event loop com `retry_options.attempts = 1`
(sem retentativa do SDK: o projeto faz a sua) e timeout em milissegundos. O SDK é importado só dentro dos métodos."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import numpy as np

from ..log import get_logger
from . import models_table
from .adapters_common import canary, http_client_kwargs, needs_own_http_client, now_ms, split_system, translate_error
from .base import LlmStream, ProviderUnavailableError
from .registry import NetworkConfig
from .schema import provider_schema
from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Reasoning, Request, Role, Usage

log = get_logger("llm.gemini")

FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "blocked", "BLOCKLIST": "blocked", "PROHIBITED_CONTENT": "blocked",
          "SPII": "blocked", "RECITATION": "blocked"}
EMBED_TASK = {EmbedTask.DOCUMENT: "RETRIEVAL_DOCUMENT", EmbedTask.QUERY: "RETRIEVAL_QUERY"}
THINKING_BUDGET = {Reasoning.NONE: 0, Reasoning.LOW: 1024, Reasoning.HIGH: 8192}


class GeminiProvider:
    kind = ProviderKind.GEMINI

    def __init__(self, api_key: str, *, net: NetworkConfig | None = None, provider_id: str = "gemini",
                 params: Mapping[str, Any] | None = None, client: Any = None):
        self.id = provider_id
        self.api_key = api_key
        self.net = net or NetworkConfig()
        self.params = dict(params or {})
        self.capabilities: frozenset[Capability] = models_table.PROVIDER_CAPABILITIES[ProviderKind.GEMINI]
        self._injected = client
        self._clients: dict[int, Any] = {}

    # ---------------------------------------------------------------- cliente
    def _client(self) -> Any:
        if self._injected is not None:
            return self._injected
        key = id(asyncio.get_running_loop())
        client = self._clients.get(key)
        if client is None:
            try:
                from google import genai
            except ImportError as e:
                raise ProviderUnavailableError(f"google-genai não instalado: pip install -e .[llm] ({e})") from e
            http_options: Any = {"timeout": int(self.net.timeout_s * 1000), "retry_options": {"attempts": 1}}
            if needs_own_http_client(self.net):
                args = http_client_kwargs(self.net, self.net.timeout_s)
                args.pop("timeout", None)
                http_options["client_args"] = dict(args)
                http_options["async_client_args"] = dict(args)
            client = self._clients[key] = genai.Client(api_key=self.api_key, http_options=http_options)
        return client

    async def aclose(self) -> None:
        client = self._clients.pop(id(asyncio.get_running_loop()), None)
        if client is not None:
            try:
                await client.aio.aclose()
            except Exception as e:  # fechar nunca derruba o job
                log.debug("fechar cliente gemini: %s", e)

    # ---------------------------------------------------------------- tradução pedido -> SDK
    @staticmethod
    def _contents(req: Request) -> tuple[str | None, list[dict[str, Any]]]:
        system, rest = split_system(req)
        contents: list[dict[str, Any]] = []
        for m in rest:
            parts: list[dict[str, Any]] = []
            if m.text:
                parts.append({"text": m.text})
            parts += [{"inline_data": {"data": a.data, "mime_type": a.mime}} for a in m.audio]
            contents.append({"role": "model" if m.role == Role.ASSISTANT else "user", "parts": parts or [{"text": ""}]})
        return system, contents

    def _config(self, req: Request, system: str | None) -> dict[str, Any]:
        config: dict[str, Any] = {"temperature": req.temperature, "max_output_tokens": req.max_output_tokens}
        if system:
            config["system_instruction"] = system
        if req.json_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = provider_schema(req.json_schema, frozenset())
        if models_table.supports_reasoning(self.kind, req.model):
            budget = THINKING_BUDGET[req.reasoning]
            if not (budget == 0 and "pro" in req.model):          # o Pro não desliga o pensamento [fato]: deixa o padrão
                config["thinking_config"] = {"thinking_budget": budget}
        for key in ("top_p", "top_k", "seed"):
            if key in self.params:
                config[key] = self.params[key]
        return config

    @staticmethod
    def _usage(meta: Any) -> Usage:
        if meta is None:
            return Usage()
        audio = 0
        for d in getattr(meta, "prompt_tokens_details", None) or ():
            if str(getattr(d, "modality", "")).upper().endswith("AUDIO"):
                audio += int(getattr(d, "token_count", 0) or 0)
        return Usage(int(getattr(meta, "prompt_token_count", 0) or 0),
                     int(getattr(meta, "candidates_token_count", 0) or 0) + int(getattr(meta, "thoughts_token_count", 0) or 0),
                     int(getattr(meta, "cached_content_token_count", 0) or 0), audio)

    @staticmethod
    def _finish(resp: Any) -> str:
        cands = getattr(resp, "candidates", None) or []
        reason = str(getattr(cands[0], "finish_reason", "") or "") if cands else ""
        return FINISH.get(reason.rsplit(".", 1)[-1].upper(), "unknown" if reason else "stop")

    # ---------------------------------------------------------------- protocolo
    async def complete(self, req: Request) -> Completion:
        system, contents = self._contents(req)
        t0 = now_ms()
        try:
            resp = await self._client().aio.models.generate_content(model=req.model, contents=contents, config=self._config(req, system))
        except Exception as e:
            raise translate_error(e, self.id) from e
        text = str(getattr(resp, "text", None) or "")
        parsed = getattr(resp, "parsed", None)
        data = parsed if isinstance(parsed, dict) else None
        return Completion(text, data, self._usage(getattr(resp, "usage_metadata", None)),
                          str(getattr(resp, "model_version", None) or req.model), now_ms() - t0, self._finish(resp),
                          getattr(resp, "response_id", None))

    def stream(self, req: Request) -> LlmStream:
        system, contents = self._contents(req)
        state: dict[str, Any] = {"t0": now_ms(), "usage": Usage(), "finish": "stop", "model": req.model, "id": None}

        async def chunks() -> AsyncIterator[str]:
            try:
                it = await self._client().aio.models.generate_content_stream(model=req.model, contents=contents,
                                                                             config=self._config(req, system))
                async for part in it:
                    meta = getattr(part, "usage_metadata", None)
                    if meta is not None and getattr(meta, "prompt_token_count", None):
                        state["usage"] = self._usage(meta)
                    if getattr(part, "candidates", None):
                        state["finish"] = self._finish(part)
                    state["model"] = getattr(part, "model_version", None) or state["model"]
                    state["id"] = getattr(part, "response_id", None) or state["id"]
                    text = getattr(part, "text", None)
                    if text:
                        yield str(text)
            except Exception as e:
                raise translate_error(e, self.id) from e

        def finish(text: str) -> Completion:
            return Completion(text, None, state["usage"], str(state["model"]), now_ms() - state["t0"], state["finish"], state["id"])

        return LlmStream(chunks(), finish)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        config: dict[str, Any] = {"task_type": EMBED_TASK[task]}
        if dim:
            config["output_dimensionality"] = int(dim)
        try:
            resp = await self._client().aio.models.embed_content(model=model, contents=list(texts), config=config)
        except Exception as e:
            raise translate_error(e, self.id) from e
        vectors = np.asarray([list(e.values) for e in (resp.embeddings or [])], dtype=np.float32)
        if vectors.size:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            vectors = vectors / np.where(norms > 0, norms, 1.0)
        return Embeddings(model, int(vectors.shape[1]) if vectors.ndim == 2 and vectors.size else int(dim or 0), vectors,
                          Usage(sum(len(t) for t in texts) // 4, 0))

    async def count_tokens(self, req: Request) -> int | None:
        _system, contents = self._contents(req)
        try:
            resp = await self._client().aio.models.count_tokens(model=req.model, contents=contents)
        except Exception as e:
            log.debug("count_tokens: %s", e)
            return None
        total = getattr(resp, "total_tokens", None)
        return int(total) if total is not None else None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        out: list[ModelInfo] = []
        try:
            pager = await self._client().aio.models.list()
            async for m in pager:
                actions = {str(a) for a in (getattr(m, "supported_actions", None) or ())}
                name = str(getattr(m, "name", "") or "")
                if not name:
                    continue
                caps: frozenset[Capability] | None = None
                if "embedContent" in actions:
                    caps = frozenset({Capability.EMBEDDINGS})
                elif "generateContent" in actions:
                    family = models_table.family_for(self.kind, name)
                    caps = frozenset((family.capabilities if family else models_table.TEXT_JSON) | ({Capability.COUNT_TOKENS}
                                                                                                    if "countTokens" in actions else set()))
                elif actions:
                    continue
                out.append(models_table.model_info(self.kind, name, display_name=getattr(m, "display_name", None),
                                                   input_limit=getattr(m, "input_token_limit", None),
                                                   output_limit=getattr(m, "output_token_limit", None), capabilities=caps))
        except Exception as e:
            raise translate_error(e, self.id) from e
        return tuple(out)

    async def test_connection(self, model: str) -> ConnectionTest:
        return await canary(self, model)
