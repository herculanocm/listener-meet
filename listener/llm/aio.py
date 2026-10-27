"""`run_async`: o `asyncio.run` das chamadas de LLM que rodam em QThreads do app (resumo parcial, chat, glossário, testar
conexão). Igual, salvo no fim: não espera o executor padrão. O `asyncio.run` espera por até 300 s as threads dele, e
uma resolução de nome travada (`getaddrinfo` roda ali) seguraria a QThread depois de um cancelamento; o app, ao sair,
destruiria a QThread ainda rodando, o que aborta o processo no Windows (revisão do L4 a L6 da F4). Aqui as tarefas
pendentes são canceladas e o laço fecha o executor sem esperar; a thread travada termina sozinha quando o sistema
desistir do nome. `until_stopped` espera uma chamada conferindo um "parar" a cada `POLL_S` (a saída do app, F5 R8-04).
`watched` (D-058): o vigia do laço que o worker liga com `configure_stall`."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from typing import Any

POLL_S = 0.2                     # o "parar" é conferido a cada 0,2 s enquanto a chamada espera a rede
STALL_S = 120.0                  # D-058: laço do asyncio parado por isto = chamada presa em código nativo
TICK_S = 1.0


class _Stall:
    """O vigia ligado (só no worker): o que fazer quando o laço para e depois de quanto tempo."""
    on_stall: Callable[[str], None] | None = None
    limit_s: float = STALL_S


def configure_stall(on_stall: Callable[[str], None] | None, limit_s: float = STALL_S) -> None:
    """O worker liga o vigia de `watched` (None desliga; o app nunca liga)."""
    _Stall.on_stall, _Stall.limit_s = on_stall, limit_s


async def watched[T](aw: Awaitable[T]) -> T:
    """`aw` com o vigia do laço, quando ligado. Uma chamada nativa presa no laço (27/10: a verificação de certificado do
    Windows dentro do aperto de mão TLS travou o worker leve por 52 min) não deixa nenhum timeout do Python disparar, e o
    heartbeat, em outra thread, segue batendo: a fila parava para sempre. Aqui uma tarefa conta voltas a cada `TICK_S` e
    uma thread confere; `limit_s / TICK_S` conferências seguidas sem volta nova chamam `on_stall` com o motivo (o worker
    grava e sai, e o app o relança). Voltas, não relógio: o notebook suspenso congela as duas threads e não conta (R-07)."""
    on_stall, limit = _Stall.on_stall, _Stall.limit_s
    if on_stall is None:
        return await aw
    beats = 0
    done = threading.Event()

    async def tick() -> None:
        nonlocal beats
        while True:
            beats += 1
            await asyncio.sleep(TICK_S)

    def watch() -> None:
        seen, still = beats, 0
        while not done.wait(TICK_S):
            if beats != seen:
                seen, still = beats, 0
                continue
            still += 1
            if still * TICK_S >= limit:
                on_stall(f"o laço do asyncio não responde há {still * TICK_S:.0f} s (chamada presa em código nativo)")
                return

    ticker = asyncio.ensure_future(tick())
    threading.Thread(target=watch, name="loop-watchdog", daemon=True).start()
    try:
        return await aw
    finally:
        done.set()
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker


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
