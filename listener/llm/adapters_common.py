"""O que os três adaptadores compartilham sem tocar em SDK: separar a instrução de sistema das mensagens, argumentos do
cliente HTTP (proxy, certificado, timeout), tradução de erros por forma (código HTTP, nome da classe, cabeçalho
`Retry-After`, texto de "request too large") e o canário do "testar conexão" (um JSON mínimo com esquema, F3 R-1).
F5.14: o cliente `httpx2` (Anthropic e OpenAI) recebe o PEM como contexto TLS pronto (`verify=<caminho>` é obsoleto no
httpx2); o erro que chega no meio do streaming (HTTP 200, o tipo no corpo) é traduzido pelo tipo. F5.18 (PF-28): o contexto do
PEM é um por configuração de rede e por processo (`shared_ssl_context`), partilhado pelos clientes dos três adaptadores."""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import redact
from .base import AuthError, LlmError, RateLimitError, RequestTooLargeError, TransientError
from .schema import validate
from .types import Capability, ConnectionTest, LlmTask, Message, Request, Role

if TYPE_CHECKING:
    import ssl

    from .base import LLMProvider
    from .registry import NetworkConfig

TOO_LARGE_HINTS = ("too large", "too long", "context_length", "context length", "maximum context", "exceeds the", "token limit",
                   "tokens exceed", "input is too", "request entity too large", "maximum number of tokens", "prompt is too long")
TEST_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": False, "required": ["ok"],
                               "properties": {"ok": {"type": "boolean"}}}
TEST_PROMPT = 'Responda exatamente com este JSON e nada mais: {"ok": true}'
TEST_TIMEOUT_S = 30.0
TEST_MAX_TOKENS = 512            # D-055: modelo que sempre pensa (o Gemini 3.1 Pro) gastava os 64 de antes no pensamento
# timeout por chamada (F3.1 R3-03): o cliente nasce com o teto (`NetworkConfig.timeout_s`, 600 s); geração e streaming levam o
# `Request.timeout_s`; contar tokens e listar modelos são curtos; embeddings de um lote, no meio-termo
AUX_TIMEOUT_S = 30.0
EMBED_TIMEOUT_S = 120.0
# F5.14: o erro do meio do streaming chega como evento `error` com o HTTP 200 da resposta; o tipo do corpo diz o código
STREAM_ERROR_STATUS = {"invalid_request_error": 400, "authentication_error": 401, "permission_error": 403, "not_found_error": 404,
                       "request_too_large": 413, "rate_limit_error": 429, "api_error": 500, "timeout_error": 504,
                       "overloaded_error": 529}


def split_system(req: Request) -> tuple[str | None, tuple[Message, ...]]:
    """Instrução de sistema (todas as `SYSTEM` juntas) e as demais mensagens na ordem."""
    system = "\n\n".join(m.text for m in req.messages if m.role == Role.SYSTEM).strip() or None
    rest = tuple(m for m in req.messages if m.role != Role.SYSTEM)
    return system, rest


_SSL_CONTEXTS: dict[tuple[str, bool], ssl.SSLContext] = {}
_SSL_LOCK = threading.Lock()


def shared_ssl_context(net: NetworkConfig) -> ssl.SSLContext:
    """O contexto TLS dos clientes HTTP próprios, um por configuração de rede e por processo (F5.18, PF-28). M-10: montar um
    contexto custa ~50 ms com o truststore (a loja de certificados do Windows lida a cada vez), pago por cliente novo, isto é,
    por tarefa de LLM; o contexto é feito para ser partilhado por conexões e threads. A chave é o PEM e o toggle do truststore:
    o proxy (com a sua senha) e o timeout não moldam o contexto e ficam fora dela (R-04). Com PEM, o contexto padrão com ele (`capath`
    numa pasta, `cafile` num arquivo), como o httpx2 montava do caminho; sem PEM, o padrão do processo (com o truststore
    injetado, o do sistema)."""
    import ssl

    key = (net.ca_bundle, net.system_certs)
    with _SSL_LOCK:
        ctx = _SSL_CONTEXTS.get(key)
        if ctx is None:
            path = net.ca_bundle
            if not path:
                ctx = ssl.create_default_context()
            else:
                ctx = ssl.create_default_context(capath=path) if Path(path).is_dir() else ssl.create_default_context(cafile=path)
            _SSL_CONTEXTS[key] = ctx
    return ctx


def http_client_kwargs(net: NetworkConfig, timeout_s: float) -> dict[str, Any]:
    """Argumentos de um `httpx(2).AsyncClient`: proxy explícito ou o do ambiente, timeout e, com PEM configurado, o contexto TLS
    partilhado (`shared_ssl_context`); sem PEM, o padrão do cliente. O google-genai (httpx) e o httpx2 aceitam o contexto em
    `verify=` e o httpx2 recusa o caminho (F5.14)."""
    kw: dict[str, Any] = {"timeout": float(timeout_s), "trust_env": True}
    if net.proxy_url:
        kw["proxy"] = net.proxy_url
    if net.ca_bundle:
        kw["verify"] = shared_ssl_context(net)
    return kw


def httpx2_client_kwargs(net: NetworkConfig, timeout_s: float) -> dict[str, Any]:
    """Os do `http_client_kwargs` para um `httpx2.AsyncClient` (Anthropic e OpenAI). Desde a F5.18 (PF-28) são os mesmos: o PEM
    vai como o contexto TLS partilhado nos dois clientes; o nome fica pelos adaptadores."""
    return http_client_kwargs(net, timeout_s)


def needs_own_http_client(net: NetworkConfig) -> bool:
    return bool(net.proxy_url or net.ca_bundle)


def audio_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


def retry_after_s(headers: Any) -> float | None:
    try:
        value = headers.get("retry-after") if headers is not None else None
    except Exception:
        return None
    if not value:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        return None


def looks_too_large(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in TOO_LARGE_HINTS)


def _status_of(e: BaseException) -> int | None:
    """`status_code` ou `code` inteiro; sem código de erro (o evento `error` no meio do streaming vem com o 200 da resposta),
    o do tipo no corpo (`{"error": {"type": "overloaded_error"}}`, F5.14)."""
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        code = getattr(e, "code", None)
        status = code if isinstance(code, int) else None
    if status is None or status < 400:
        body = getattr(e, "body", None)
        error = body.get("error") if isinstance(body, Mapping) else None
        kind = error.get("type") if isinstance(error, Mapping) else None
        status = STREAM_ERROR_STATUS.get(str(kind), status)
    return status


def translate_error(e: BaseException, provider: str) -> LlmError:
    """Erro de qualquer SDK -> `LlmError` pela forma (sem importar o SDK): `status_code` ou `code` inteiro (ou o tipo do
    corpo, `_status_of`), nome da classe, `response.headers['retry-after']` e o texto. A mensagem sai redigida (CP10)."""
    if isinstance(e, LlmError):
        return e
    name = type(e).__name__
    status = _status_of(e)
    text = redact.GLOBAL.redact(f"{provider}: {status or name}: {str(e)[:400]}")
    headers = getattr(getattr(e, "response", None), "headers", None)
    if isinstance(e, asyncio.TimeoutError | TimeoutError) or name in ("APITimeoutError", "ReadTimeout", "ConnectTimeout",
                                                                      "DeadlineExceededError"):
        return TransientError(text, status=status)
    if isinstance(e, ConnectionError | OSError) or name in ("APIConnectionError", "ConnectError", "RemoteProtocolError"):
        return TransientError(text, status=status)
    if status == 429 or name == "RateLimitError":
        return RateLimitError(text, retry_after_s=retry_after_s(headers), status=status or 429)
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return AuthError(text, status=status or 401)
    if status == 413 or name == "RequestTooLargeError" or (status in (None, 400, 422) and looks_too_large(str(e))):
        return RequestTooLargeError(text, status=status or 400)
    if (status is not None and status >= 500) or name in ("InternalServerError", "OverloadedError", "ServiceUnavailableError",
                                                          "ServerError", "RetryableError"):
        return TransientError(text, status=status)
    return LlmError(text, status=status)


async def canary(provider: LLMProvider, model: str) -> ConnectionTest:
    """"Testar conexão": uma chamada com esquema mínimo; confirma chave, rede, modelo e a forma da saída estruturada."""
    req = Request(LlmTask.TEST, model, (Message(Role.USER, TEST_PROMPT),), TEST_SCHEMA, temperature=0.0,
                  max_output_tokens=TEST_MAX_TOKENS, timeout_s=TEST_TIMEOUT_S)
    t0 = now_ms()
    try:
        async with asyncio.timeout(TEST_TIMEOUT_S):
            comp = await provider.complete(req)
        data = comp.data
        if data is None:
            from .schema import parse_json_text

            data = parse_json_text(comp.text)
        validate(TEST_SCHEMA, data)
        if data.get("ok") is not True:
            return ConnectionTest(False, now_ms() - t0, comp.model or model, "o modelo respondeu, mas não o JSON esperado")
    except Exception as e:
        err = translate_error(e, provider.id)
        return ConnectionTest(False, now_ms() - t0, model, redact.GLOBAL.redact(str(err)))
    return ConnectionTest(True, now_ms() - t0, comp.model or model)


FRIENDLY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("getaddrinfo", "name or service not known", "nodename nor servname", "temporary failure in name resolution", "11001"),
     "DNS: o nome do provedor não resolveu. Sem rede, ou o proxy da empresa precisa ser informado em Configurações > LLM > Proxy."),
    (("407", "proxy authentication"), "Proxy pediu autenticação (407): informe usuário e senha na URL do proxy em Configurações > LLM."),
    (("certificate_verify_failed", "certificate verify failed", "ssl", "tls", "self signed"),
     ("TLS: o certificado não foi aceito (inspeção TLS da empresa?). Ligue \"certificados do sistema\" ou aponte o PEM em "
      "Configurações > LLM.")),
    (("401", "invalid api key", "api key not valid", "authentication", "unauthorized", "permission_denied", "403"),
     "Chave recusada pelo provedor: confira a chave nesta aba."),
    (("429", "quota", "rate limit", "resource_exhausted"), "Limite de uso do provedor (429): espere um pouco ou confira o plano da conta."),
    (("timed out", "timeout", "tempo esgotado"), "Tempo esgotado: rede lenta ou bloqueada (EDR?); tente de novo ou pelo worker."),
    (("not found", "404", "no such model", "does not exist"), "Modelo não encontrado: escolha outro em \"listar modelos\"."),
)


def friendly_error(text: str | None) -> str:
    """Erro técnico -> frase em PT-BR com a dica da configuração (F3A-14); o texto original segue em seguida, redigido."""
    raw = redact.GLOBAL.redact(text or "")
    low = raw.lower()
    for needles, hint in FRIENDLY_HINTS:
        if any(n in low for n in needles):
            return f"{hint} ({raw[:200]})" if raw else hint
    return raw or "falha desconhecida"


def has_audio(req: Request) -> bool:
    return any(m.audio for m in req.messages)


def require(capabilities: frozenset[Capability], cap: Capability, provider: str, what: str) -> None:
    if cap not in capabilities:
        from .base import ProviderUnavailableError

        raise ProviderUnavailableError(f"{provider} não oferece {what}")


def params_get(params: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    return (params or {}).get(key, default)
