"""`run_async`: o `asyncio.run` das chamadas de LLM que rodam em QThreads do app (resumo parcial, chat, glossário, testar
conexão). Igual, salvo no fim: não espera o executor padrão. O `asyncio.run` espera por até 300 s as threads dele, e
uma resolução de nome travada (`getaddrinfo` roda ali) seguraria a QThread depois de um cancelamento; o app, ao sair,
destruiria a QThread ainda rodando, o que aborta o processo no Windows (revisão do L4 a L6 da F4). Aqui as tarefas
pendentes são canceladas e o laço fecha o executor sem esperar; a thread travada termina sozinha quando o sistema
desistir do nome. `until_stopped` espera uma chamada conferindo um "parar" a cada `POLL_S` (a saída do app, F5 R8-04)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from typing import Any

POLL_S = 0.2                     # o "parar" é conferido a cada 0,2 s enquanto a chamada espera a rede


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()                                   # o executor padrão fecha com `wait=False`


async def until_stopped[T](aw: Awaitable[T], should_stop: Callable[[], bool] | None, poll_s: float = POLL_S) -> T | None:
    """O resultado de `aw`, conferindo `should_stop()` a cada `poll_s` (no padrão do resumo parcial); pedido o fim, a chamada
    presa na rede é cancelada e volta None (F5 R8-04). Uma exceção de `aw` sobe como veio."""
    call = asyncio.ensure_future(aw)
    try:
        while not call.done():
            await asyncio.wait({call}, timeout=poll_s)
            if should_stop is not None and should_stop() and not call.done():
                return None
        return call.result()
    finally:
        if not call.done():
            call.cancel()
            with suppress(BaseException):
                await call
