"""O que os três adaptadores compartilham sem tocar em SDK: separar a instrução de sistema das mensagens, argumentos do
cliente HTTP (proxy, certificado, timeout), tradução de erros por forma (código HTTP, nome da classe, cabeçalho
`Retry-After`, texto de "request too large") e o canário do "testar conexão" (um JSON mínimo com esquema, F3 R-1)."""
from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from . import redact
from .base import AuthError, LlmError, RateLimitError, RequestTooLargeError, TransientError
from .schema import validate
from .types import Capability, ConnectionTest, LlmTask, Message, Request, Role

if TYPE_CHECKING:
    from .base import LLMProvider
    from .registry import NetworkConfig

TOO_LARGE_HINTS = ("too large", "too long", "context_length", "context length", "maximum context", "exceeds the", "token limit",
                   "tokens exceed", "input is too", "request entity too large", "maximum number of tokens", "prompt is too long")
TEST_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": False, "required": ["ok"],
                               "properties": {"ok": {"type": "boolean"}}}
TEST_PROMPT = 'Reply with exactly this JSON and nothing else: {"ok": true}'
TEST_TIMEOUT_S = 30.0
# timeout por chamada (F3.1 R3-03): o cliente nasce com o teto (`NetworkConfig.timeout_s`, 600 s); geração e streaming levam o
# `Request.timeout_s`; contar tokens e listar modelos são curtos; embeddings de um lote, no meio-termo
AUX_TIMEOUT_S = 30.0
EMBED_TIMEOUT_S = 120.0


def split_system(req: Request) -> tuple[str | None, tuple[Message, ...]]:
    """Instrução de sistema (todas as `SYSTEM` juntas) e as demais mensagens na ordem."""
    system = "\n\n".join(m.text for m in req.messages if m.role == Role.SYSTEM).strip() or None
    rest = tuple(m for m in req.messages if m.role != Role.SYSTEM)
    return system, rest


def http_client_kwargs(net: NetworkConfig, timeout_s: float) -> dict[str, Any]:
    """Argumentos de um `httpx(2).AsyncClient`: proxy explícito ou o do ambiente, PEM configurado ou padrão, timeout."""
    kw: dict[str, Any] = {"timeout": float(timeout_s), "trust_env": True}
    if net.proxy_url:
        kw["proxy"] = net.proxy_url
    if net.ca_bundle:
        kw["verify"] = net.ca_bundle
    return kw


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


def translate_error(e: BaseException, provider: str) -> LlmError:
    """Erro de qualquer SDK -> `LlmError` pela forma (sem importar o SDK): `status_code` ou `code` inteiro, nome da classe,
    `response.headers['retry-after']` e o texto. A mensagem sai redigida (CP10)."""
    if isinstance(e, LlmError):
        return e
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        code = getattr(e, "code", None)
        status = code if isinstance(code, int) else None
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
    req = Request(LlmTask.TEST, model, (Message(Role.USER, TEST_PROMPT),), TEST_SCHEMA, temperature=0.0, max_output_tokens=64,
                  timeout_s=TEST_TIMEOUT_S)
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
