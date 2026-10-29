"""Protocolo único de provedor (CP2, `specs/llm-contrato.md`): `LLMProvider`, os erros que todo adaptador traduz dos SDKs e
`LlmStream` (iterador de pedaços de texto cujo `result` é o `Completion` ao terminar). Nenhum SDK é importado aqui."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Protocol, runtime_checkable

from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Request, Usage


class LlmError(RuntimeError):
    """Falha de uma chamada. `retryable`: 429, 5xx, rede, timeout; `permanent`: chave ausente ou inválida, SDK ausente
    (não conta tentativa do job); `too_large`: o pedido excede a janela (o runner cai para o modo hierárquico)."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False, retry_after_s: float | None = None,
                 permanent: bool = False, too_large: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.permanent = permanent
        self.too_large = too_large


class RateLimitError(LlmError):
    def __init__(self, message: str, *, retry_after_s: float | None = None, status: int | None = 429):
        super().__init__(message, status=status, retryable=True, retry_after_s=retry_after_s)


class TransientError(LlmError):
    """5xx, rede, timeout: vale nova tentativa."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message, status=status, retryable=True)


class RequestTooLargeError(LlmError):
    def __init__(self, message: str, *, status: int | None = 400):
        super().__init__(message, status=status, too_large=True)


class AuthError(LlmError):
    def __init__(self, message: str, *, status: int | None = 401):
        super().__init__(message, status=status, permanent=True)


class ProviderUnavailableError(LlmError):
    """SDK não instalado, chave ausente ou provedor sem a capacidade pedida: erro permanente com mensagem própria."""

    def __init__(self, message: str):
        super().__init__(message, permanent=True)


OFF_SCHEMA = "resposta fora do esquema"         # o começo do erro do `RetryingProvider` (F5.6 L2, A-29: a tela o reconhece)


class BadResponseError(LlmError):
    """Resposta sem JSON válido, bloqueada ou vazia; o `RetryingProvider` reenvia uma vez com a mensagem de erro."""

    def __init__(self, message: str, *, text: str = ""):
        super().__init__(message)
        self.text = text


class LlmStream:
    """`async for chunk in stream:`; ao fim, `stream.result` é o `Completion` com o texto inteiro e o uso."""

    def __init__(self, chunks: AsyncIterator[str], finish: Callable[[str], Completion]):
        self._chunks = chunks
        self._finish = finish
        self._parts: list[str] = []
        self.result: Completion | None = None

    def __aiter__(self) -> LlmStream:
        return self

    async def __anext__(self) -> str:
        try:
            chunk = await self._chunks.__anext__()
        except StopAsyncIteration:
            if self.result is None:
                self.result = self._finish("".join(self._parts))
            raise
        self._parts.append(chunk)
        return chunk

    @property
    def text(self) -> str:
        return "".join(self._parts)


@runtime_checkable
class LLMProvider(Protocol):
    id: str
    kind: ProviderKind
    capabilities: frozenset[Capability]

    async def complete(self, req: Request) -> Completion: ...

    def stream(self, req: Request) -> LlmStream: ...

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings: ...

    async def count_tokens(self, req: Request) -> int | None: ...

    async def list_models(self) -> tuple[ModelInfo, ...]: ...

    async def test_connection(self, model: str) -> ConnectionTest: ...

    async def aclose(self) -> None: ...


def classify_exception(e: BaseException) -> LlmError:
    """Tradução genérica (sem SDK): timeout e rede viram transitórios; `LlmError` passa; o resto é falha comum."""
    if isinstance(e, LlmError):
        return e
    if isinstance(e, TimeoutError):
        return TransientError(f"tempo esgotado ({e or 'timeout'})")
    if isinstance(e, ConnectionError | OSError):
        return TransientError(f"rede: {e}")
    return LlmError(f"{type(e).__name__}: {e}")


def empty_usage() -> Usage:
    return Usage()
