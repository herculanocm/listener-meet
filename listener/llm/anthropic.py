"""Adaptador Anthropic (anthropic 2.x; contrato `specs/llm-contrato.md`). F5.14: a montagem do pedido por família mora em
`anthropic_request.py` (esquema por `output_config.format` ou ferramenta forçada, pensamento adaptativo com o esforço ou o
orçamento, o teto com a folga do pensamento); `complete()` lê pelo streaming (`messages.stream(...).get_final_message()`,
o beta com `fallbacks: "default"` nas famílias que o aceitam), para a conexão não ficar muda atrás do proxy enquanto o
modelo pensa [exp]. A recusa (`stop_reason: refusal`, HTTP 200) vira `RefusalError` permanente com a categoria, sem
reenvio; a janela estourada (`model_context_window_exceeded`) vira "grande demais" (o modo hierárquico); com o fallback, o
modelo é o que respondeu e o uso é a soma das partes (`usage.iterations`), com uma linha no log. `cache_control` efêmero
no prefixo estável; `messages.count_tokens`; `models.list` traz `max_input_tokens`; sem embeddings nem áudio nativo
[fato]; sem `temperature`, `top_p` nem `top_k` (a 2.x não os aceita [fato, medido com o SDK 2.3.0]). Cliente por event loop
com `max_retries = 0` e timeout explícito; `stream()` (o chat) pelos eventos brutos (`content_block_delta`,
`message_delta`), com o esquema e o pensamento do pedido e sem fallback; a recusa nele também vira `RefusalError`, ao fim
dos eventos (revisão do diff, R-01). O SDK é importado só dentro dos métodos."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..log import get_logger
from . import models_table
from .adapters_common import AUX_TIMEOUT_S, canary, httpx2_client_kwargs, needs_own_http_client, now_ms, translate_error
from .anthropic_request import FALLBACK_BETA, FALLBACK_MODE, ClaudeParams, build
from .base import LlmStream, ProviderUnavailableError, RefusalError, RequestTooLargeError
from .registry import NetworkConfig
from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Request, Usage

log = get_logger("llm.anthropic")

# `model_context_window_exceeded` só chega aqui no streaming do chat: a resposta parou no fim da janela, como no teto
FINISH = {"end_turn": "stop", "tool_use": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "blocked",
          "model_context_window_exceeded": "length"}
PARTS = ("message", "fallback_message")      # as partes de `usage.iterations` que somam (a recusada e a que respondeu)
NO_CATEGORY = "sem categoria"


def _category(details: Any) -> str:
    """A categoria da recusa (`stop_details.category`: cyber, bio...) ou "sem categoria" (nula é um estado válido)."""
    return str(getattr(details, "category", None) or "") or NO_CATEGORY


def _fallback_category(msg: Any) -> str:
    """A categoria da última troca de modelo (o bloco `fallback` do conteúdo), ou vazio."""
    for block in reversed(getattr(msg, "content", None) or ()):
        if str(getattr(block, "type", "") or "") == "fallback":
            return str(getattr(getattr(block, "trigger", None), "category", None) or "")
    return ""


class AnthropicProvider:
    kind = ProviderKind.ANTHROPIC

    def __init__(self, api_key: str, *, net: NetworkConfig | None = None, provider_id: str = "anthropic",
                 params: Mapping[str, Any] | None = None, client: Any = None):
        self.id = provider_id
        self.api_key = api_key
        self.net = net or NetworkConfig()
        self.params = dict(params or {})
        self.settings = ClaudeParams.parse(self.params, provider_id)
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

                http_client = httpx2.AsyncClient(**httpx2_client_kwargs(self.net, self.net.timeout_s))
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

    # ---------------------------------------------------------------- tradução pedido -> SDK -> Completion
    def _kwargs(self, req: Request) -> dict[str, Any]:
        return build(req, self.settings, self.id)

    @staticmethod
    def _usage(usage: Any) -> Usage:
        if usage is None:
            return Usage()
        plain = int(getattr(usage, "input_tokens", 0) or 0)
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        # `input_tokens` exclui o cache [fato]; a escrita no cache é cobrada à parte (R3-10)
        return Usage(plain + read + write, int(getattr(usage, "output_tokens", 0) or 0), read, 0, write)

    @staticmethod
    def _parse(msg: Any) -> tuple[str, dict[str, Any] | None]:
        """O texto (os blocos `text` juntos: depois de um fallback no meio da resposta, o modelo de reserva continua o texto
        da parte recusada [fato: skill]; pensamento e o bloco `fallback` ficam de fora) e a entrada da ferramenta forçada."""
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

    def _log_refusal(self, req: Request, model: str, details: Any) -> str:
        """A linha da recusa (D-059: a categoria, nunca o conteúdo); devolve a categoria para a mensagem."""
        category = _category(details)
        log.warning("%s: o Claude recusou %s em %s (%s)", self.id, req.task, model, category)
        return category

    def _served(self, req: Request, msg: Any, model: str) -> tuple[Usage, str]:
        """O uso e o modelo da resposta. Com o fallback do servidor (uma parte `fallback_message` em `usage.iterations`), o uso
        é a soma das partes (o `usage` de cima traz só a que respondeu [fato: skill]) e o modelo, o de reserva; o log diz o
        modelo e os tokens de cada parte, sem conteúdo."""
        usage = getattr(msg, "usage", None)
        parts = [(p, self._usage(p)) for p in getattr(usage, "iterations", None) or () if str(getattr(p, "type", "") or "") in PARTS]
        served = [p for p, _u in parts if str(getattr(p, "type", "") or "") == "fallback_message"]
        if not served:
            return self._usage(usage), model
        reserve = str(getattr(served[-1], "model", None) or model)
        category = _fallback_category(msg)
        tokens = "; ".join(f"{getattr(p, 'model', None) or '?'} entrada {u.input_tokens}, saída {u.output_tokens}, cache {u.cached_tokens}"
                           for p, u in parts)
        log.info("%s: %s: resposta do modelo de reserva %s (recusa em %s%s); partes: %s", self.id, req.task, reserve, req.model,
                 f", {category}" if category else "", tokens)
        return sum((u for _p, u in parts), Usage()), reserve

    def _completion(self, req: Request, msg: Any, latency_ms: int) -> Completion:
        """A mensagem final -> `Completion`; a recusa e a janela estourada viram erro antes do texto."""
        stop = str(getattr(msg, "stop_reason", "") or "")
        model = str(getattr(msg, "model", None) or req.model)
        if stop == "refusal":
            category = self._log_refusal(req, model, getattr(msg, "stop_details", None))
            raise RefusalError(f"o Claude recusou esta tarefa ({category})", category=category)
        if stop == "model_context_window_exceeded":
            raise RequestTooLargeError(f"{self.id}: o pedido e a resposta passaram da janela de contexto de {model} ({stop})")
        text, data = self._parse(msg)
        usage, served = self._served(req, msg, model)
        return Completion(text, data, usage, served, latency_ms, FINISH.get(stop, "unknown"), getattr(msg, "id", None))

    async def _final_message(self, kw: dict[str, Any], fallback: bool) -> Any:
        """A mensagem inteira pelo streaming (`get_final_message` junta os eventos); o beta só com o fallback."""
        client = self._client()
        if fallback:
            manager = client.beta.messages.stream(**kw, betas=[FALLBACK_BETA], fallbacks=FALLBACK_MODE)
        else:
            manager = client.messages.stream(**kw)
        async with manager as events:
            return await events.get_final_message()

    # ---------------------------------------------------------------- protocolo
    async def complete(self, req: Request) -> Completion:
        kw = self._kwargs(req)
        t0 = now_ms()
        try:
            msg = await self._final_message(kw, self.settings.fallback_for(req.model))
        except Exception as e:
            raise translate_error(e, self.id) from e
        return self._completion(req, msg, now_ms() - t0)

    def stream(self, req: Request) -> LlmStream:
        kw = self._kwargs(req)
        state: dict[str, Any] = {"t0": now_ms(), "in": 0, "out": 0, "cached": 0, "write": 0, "finish": "stop", "model": req.model,
                                 "id": None}

        async def chunks() -> AsyncIterator[str]:
            try:
                events = await self._client().messages.create(**kw, stream=True)
                async for ev in events:
                    kind = str(getattr(ev, "type", "") or "")
                    if kind == "message_start":
                        msg = getattr(ev, "message", None)
                        u = self._usage(getattr(msg, "usage", None))
                        state["in"], state["cached"], state["write"] = u.input_tokens, u.cached_tokens, u.cache_write_tokens
                        state["model"] = getattr(msg, "model", None) or state["model"]
                        state["id"] = getattr(msg, "id", None)
                    elif kind == "content_block_delta":
                        delta = getattr(ev, "delta", None)
                        if str(getattr(delta, "type", "")) == "text_delta":     # o pensamento (thinking_delta) fica de fora
                            yield str(getattr(delta, "text", "") or "")
                    elif kind == "message_delta":
                        self._stream_end(req, ev, state)
            except Exception as e:
                raise translate_error(e, self.id) from e
            if "refused" in state:                    # R-01: ao fim dos eventos, como no `complete()`: o chat vai ao erro
                raise RefusalError(f"o Claude recusou esta tarefa ({state['refused']})", category=state["refused"])

        def finish(text: str) -> Completion:
            return Completion(text, None, Usage(state["in"], state["out"], state["cached"], 0, state["write"]), str(state["model"]),
                              now_ms() - state["t0"], state["finish"], state["id"])

        return LlmStream(chunks(), finish)

    def _stream_end(self, req: Request, ev: Any, state: dict[str, Any]) -> None:
        """O `message_delta` do streaming: a saída acumulada e o motivo do fim; a recusa vai ao log e fica marcada (`refused`,
        com a categoria) para virar `RefusalError` ao fim dos eventos (R-01: nunca uma resposta vazia gravada como certa)."""
        state["out"] = int(getattr(getattr(ev, "usage", None), "output_tokens", 0) or 0)
        delta = getattr(ev, "delta", None)
        stop = getattr(delta, "stop_reason", None)
        if stop:
            state["finish"] = FINISH.get(str(stop), "unknown")
            if str(stop) == "refusal":
                state["refused"] = self._log_refusal(req, str(state["model"]), getattr(delta, "stop_details", None))

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        del texts, model, dim, task
        raise ProviderUnavailableError("Anthropic não oferece embeddings: escolha Gemini ou OpenAI para embeddings")

    async def count_tokens(self, req: Request) -> int | None:
        kw = self._kwargs(req)
        for key in ("max_tokens", "service_tier"):
            kw.pop(key, None)
        kw["timeout"] = AUX_TIMEOUT_S
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
            page = await self._client().models.list(limit=100, timeout=AUX_TIMEOUT_S)
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
