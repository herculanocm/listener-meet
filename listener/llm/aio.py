"""`run_async`: o `asyncio.run` das chamadas de LLM que rodam em QThreads do app (resumo parcial, chat, glossário, testar
conexão). Igual, salvo no fim: não espera o executor padrão. O `asyncio.run` espera por até 300 s as threads dele, e
uma resolução de nome travada (`getaddrinfo` roda ali) seguraria a QThread depois de um cancelamento; o app, ao sair,
destruiria a QThread ainda rodando, o que aborta o processo no Windows (revisão do L4 a L6 da F4). Aqui as tarefas
pendentes são canceladas e o laço fecha o executor sem esperar; a thread travada termina sozinha quando o sistema
desistir do nome."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


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
