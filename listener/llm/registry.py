"""Registro e decoradores da camada de LLM (contrato "Decoradores e registro"): `RetryingProvider` (timeout por chamada,
3 tentativas com backoff exponencial e jitter respeitando `Retry-After`, reenvio único quando o JSON não valida, "request
too large" sem retentativa), carga do adaptador por nome com importação preguiçosa, proxy, certificado e `truststore`
antes de importar qualquer SDK, chave lida na hora e registrada na redação. `LISTENER_LLM=fake` troca tudo pelo
`FakeProvider`. Este módulo nunca importa `google`, `openai`, `anthropic`, `httpx` nem `truststore` no nível do módulo."""
from __future__ import annotations

import asyncio
import importlib
import os
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from . import redact
from .base import BadResponseError, LlmError, LlmStream, LLMProvider, ProviderUnavailableError, classify_exception
from .schema import SchemaError, parse_json_text, validate
from .types import Completion, ConnectionTest, Embeddings, EmbedTask, Message, ModelInfo, ProviderKind, Request, Role

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.registry")

FAKE_ENV = "LISTENER_LLM"
INSTALL_HINT = "instale o extra: pip install -e .[llm] (google-genai, openai, anthropic, truststore)"
ADAPTERS: dict[ProviderKind, tuple[str, str]] = {
    ProviderKind.GEMINI: ("listener.llm.gemini", "GeminiProvider"),
    ProviderKind.OPENAI: ("listener.llm.openai", "OpenAIProvider"),
    ProviderKind.ANTHROPIC: ("listener.llm.anthropic", "AnthropicProvider"),
}
RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 9.0)
RETRY_JITTER = 0.3
FIX_PROMPT = ("Your previous answer did not match the required JSON schema: {error}. "
              "Answer again with a single JSON object that follows the schema exactly, with no extra text.")


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """`llm.proxy_url` (vazio = ambiente), `llm.ca_bundle` (PEM; vazio = padrão) e `llm.system_certs` (truststore)."""
    proxy_url: str = ""
    ca_bundle: str = ""
    system_certs: bool = True
    timeout_s: float = 120.0

    @classmethod
    def from_store(cls, store: Store) -> NetworkConfig:
        from ..config.defaults import SPECS

        return cls(str(store.get(SPECS["llm.proxy_url"])).strip(), str(store.get(SPECS["llm.ca_bundle"])).strip(),
                   bool(store.get(SPECS["llm.system_certs"])))

    def verify(self) -> Any:
        """Valor para `verify=` do httpx: o PEM configurado ou o padrão (com truststore injetado, o padrão lê o sistema)."""
        return self.ca_bundle or True


_ssl_state: dict[str, bool] = {"injected": False, "warned": False}


def prepare_ssl(net: NetworkConfig) -> bool:
    """`truststore.inject_into_ssl()` uma vez por processo, antes de importar qualquer SDK: certificados do sistema (EDR
    com inspeção TLS, R-2). Sem truststore instalado, segue com o `certifi` dos SDKs e avisa uma vez."""
    if not net.system_certs or _ssl_state["injected"]:
        return _ssl_state["injected"]
    try:
        truststore = importlib.import_module("truststore")
    except ImportError:
        if not _ssl_state["warned"]:
            _ssl_state["warned"] = True
            log.info("truststore não instalado: certificados do sistema não entram (pip install truststore)")
        return False
    truststore.inject_into_ssl()
    _ssl_state["injected"] = True
    log.info("truststore ativo: certificados do sistema aceitos nas chamadas de LLM")
    return True


def fake_requested() -> bool:
    return os.environ.get(FAKE_ENV, "").lower() == "fake"


def load_provider(kind: ProviderKind | str, api_key: str | None, net: NetworkConfig | None = None, *, provider_id: str | None = None,
                  params: Mapping[str, Any] | None = None) -> LLMProvider:
    """Adaptador do provedor por nome (importação preguiçosa) ou o fake (`LISTENER_LLM=fake`). Sem chave: erro permanente."""
    kind = ProviderKind(str(kind))
    net = net or NetworkConfig()
    if fake_requested():
        from .fake import FakeProvider

        return FakeProvider(kind=kind, provider_id=provider_id or f"fake-{kind}")
    if not api_key:
        raise ProviderUnavailableError(f"chave do provedor {provider_id or kind} ausente: informe nas Configurações (LLM)")
    prepare_ssl(net)
    module, cls_name = ADAPTERS[kind]
    try:
        module_obj = importlib.import_module(module)
    except ImportError as e:
        raise ProviderUnavailableError(f"{INSTALL_HINT} ({e})") from e
    redact.GLOBAL.add(api_key)
    return getattr(module_obj, cls_name)(api_key, net=net, provider_id=provider_id or str(kind), params=dict(params or {}))  # type: ignore[no-any-return]


def provider_from_row(row: Mapping[str, Any], net: NetworkConfig | None = None) -> LLMProvider:
    """Linha de `providers` -> adaptador; `params` é JSON opcional (parâmetros extras do provedor)."""
    import json

    params: dict[str, Any] = {}
    try:
        params = dict(json.loads(row.get("params") or "{}"))
    except (ValueError, TypeError):
        log.warning("providers.params do %s não é JSON; ignorado", row.get("id"))
    return load_provider(str(row["kind"]), row.get("api_key"), net, provider_id=str(row["id"]), params=params)


def build_provider(store: Store, provider_id: str | None = None, *, role: str = "llm",
                   retrying: bool = True) -> tuple[LLMProvider, dict[str, Any]]:
    """Provedor pronto a partir do banco: o `provider_id` pedido (snapshot do job) ou o ativo para o papel (`llm` ou
    `embed`); a chave é lida agora, nunca copiada para `config_json`. Devolve também a linha, para modelos e preços."""
    row = store.providers.get(provider_id) if provider_id else store.providers.active_provider(role)
    if row is None:
        raise ProviderUnavailableError("nenhum provedor de LLM ativo: escolha um nas Configurações (LLM)")
    net = NetworkConfig.from_store(store)
    provider = provider_from_row(row, net)
    return (RetryingProvider(provider) if retrying else provider), dict(row)


# ---------------------------------------------------------------- retentativa
type Sleep = Callable[[float], Awaitable[None]]


class RetryingProvider:
    """Decorador sobre qualquer `LLMProvider`: `asyncio.timeout(req.timeout_s)` por tentativa; 429, 5xx, rede e timeout
    repetem até 3 vezes com 1, 3 e 9 s (jitter de 30 %) ou o `Retry-After`; "request too large" e erros permanentes não
    repetem; com esquema, valida a resposta e reenvia uma vez com a mensagem de erro quando o JSON não vale."""

    def __init__(self, inner: LLMProvider, *, attempts: int = 3, delays: Sequence[float] = RETRY_DELAYS, jitter: float = RETRY_JITTER,
                 sleep: Sleep | None = None, rand: Callable[[], float] = random.random, resend_on_invalid: bool = True):
        self.inner = inner
        self.id = inner.id
        self.kind = inner.kind
        self.capabilities = inner.capabilities
        self.attempts = max(1, attempts)
        self.delays = tuple(delays)
        self.jitter = jitter
        self.sleep: Sleep = sleep or asyncio.sleep
        self.rand = rand
        self.resend_on_invalid = resend_on_invalid
        self.retries = 0                       # instrumentação: quantas tentativas extras houve neste processo
        self.resends = 0

    async def complete(self, req: Request) -> Completion:
        comp = await self._with_retries(req)
        if req.json_schema is None:
            return comp
        try:
            return self._validated(req, comp)
        except (SchemaError, ValueError) as e:
            if not self.resend_on_invalid:
                raise BadResponseError(f"resposta fora do esquema: {e}", text=comp.text) from e
            self.resends += 1
            log.info("%s: resposta de %s fora do esquema (%s); reenviando uma vez", self.id, req.task, e)
            fix = req.with_messages((*req.messages, Message(Role.ASSISTANT, comp.text[:20_000]),
                                     Message(Role.USER, FIX_PROMPT.format(error=str(e)[:500]))))
            comp2 = await self._with_retries(fix)
            try:
                return self._validated(req, comp2)
            except (SchemaError, ValueError) as e2:
                raise BadResponseError(f"resposta fora do esquema após reenvio: {e2}", text=comp2.text) from e2

    async def _with_retries(self, req: Request) -> Completion:
        for attempt in range(self.attempts):
            try:
                async with asyncio.timeout(req.timeout_s):
                    return await self.inner.complete(req)
            except Exception as e:  # o adaptador traduz os erros do SDK; o resto cai em `classify_exception`
                err = classify_exception(e)
                if not err.retryable or attempt == self.attempts - 1:
                    raise err from (e if e is not err else None)
                self.retries += 1
                delay = self._delay(attempt, err)
                log.warning("%s: %s na tentativa %d de %d; nova em %.1f s", self.id, redact.GLOBAL.redact(str(err)), attempt + 1,
                            self.attempts, delay)
                await self.sleep(delay)
        raise LlmError("sem tentativas")      # inalcançável: o laço levanta na última

    def _delay(self, attempt: int, err: LlmError) -> float:
        base = self.delays[min(attempt, len(self.delays) - 1)]
        delay = base * (1.0 + self.jitter * (2 * self.rand() - 1))
        if err.retry_after_s:
            delay = max(delay, float(err.retry_after_s))
        return round(delay, 3)

    @staticmethod
    def _validated(req: Request, comp: Completion) -> Completion:
        if req.json_schema is None:
            return comp
        raw = comp.data if comp.data is not None else parse_json_text(comp.text)
        return replace(comp, data=validate(req.json_schema, raw))

    def stream(self, req: Request) -> LlmStream:
        return self.inner.stream(req)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        return await self.inner.embed(texts, model=model, dim=dim, task=task)

    async def count_tokens(self, req: Request) -> int | None:
        try:
            return await self.inner.count_tokens(req)
        except Exception as e:  # contar tokens é opcional: o projeto estima
            log.debug("count_tokens falhou: %s", e)
            return None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.inner.list_models()

    async def test_connection(self, model: str) -> ConnectionTest:
        return await self.inner.test_connection(model)

    async def aclose(self) -> None:
        await self.inner.aclose()
