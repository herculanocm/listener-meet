"""Trabalho fora da thread da interface (M-26): `Task` roda uma função numa QThread e devolve o resultado (ou a exceção)
por sinal; a referência fica viva até `finished` (QThread sem parent é destruída pelo Qt se for coletada antes).
F3 (item 10, F3A-19): `StreamTask` acrescenta um sinal por pedaço de texto e `cancel()` (um `threading.Event` lido entre
pedaços) para o chat em streaming. F5 R7-03: `wait_before_close` é a espera da saída do app antes de fechar o banco."""
from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QThread, Signal

from ..log import get_logger

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.tasks")
live_threads: set[QThread] = set()


def release(thread: QThread) -> None:
    live_threads.discard(thread)
    thread.deleteLater()


def wait_before_close(thread: QThread, wait_ms: int, what: str) -> bool:
    """Saída do app (R7-03): espera a thread (já cancelada) até `wait_ms`; se ainda roda (a rede presa além do cancelamento),
    avisa no log e espera o fim, como a varredura do índice: fechar o banco com a QThread viva, ou destruí-la rodando,
    derruba o processo na saída. True quando terminou dentro do prazo (ou já tinha sido destruída)."""
    with suppress(RuntimeError):                     # já destruída pelo `release`
        if thread.wait(wait_ms):
            return True
        log.warning("%s não terminou em %d ms: esperando o fim antes de fechar o banco", what, wait_ms)
        thread.wait()
        return False
    return True


class Task(QThread):
    done = Signal(object)

    def __init__(self, fn: Callable[[], Any], store: Store | None):
        super().__init__(None)
        self.fn = fn
        self.store = store

    @override
    def run(self) -> None:
        result: Any
        try:
            result = self.fn()
        except BaseException as e:  # a exceção é entregue a quem pediu a tarefa
            log.exception("tarefa em segundo plano falhou")
            result = e
        finally:
            if self.store is not None:
                self.store.close_thread()
        self.done.emit(result)


def run_task(fn: Callable[[], Any], store: Store | None, on_done: Callable[[Any], None]) -> Task:
    task = Task(fn, store)
    live_threads.add(task)
    task.done.connect(on_done)
    task.finished.connect(functools.partial(release, task))
    task.start()
    return task


type Emit = Callable[[str], None]
type Cancelled = Callable[[], bool]


class StreamTask(QThread):
    """`fn(emit, cancelled)` roda numa QThread: chama `emit(pedaço)` a cada trecho (sinal `chunk`) e consulta `cancelled()`
    entre pedaços; `done(resultado ou exceção)` ao fim. `cancel()` só pede: a função decide quando parar."""
    chunk = Signal(str)
    done = Signal(object)

    def __init__(self, fn: Callable[[Emit, Cancelled], Any], store: Store | None):
        super().__init__(None)
        self.fn = fn
        self.store = store
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @override
    def run(self) -> None:
        result: Any
        try:
            result = self.fn(self.chunk.emit, self._cancel.is_set)
        except BaseException as e:  # a exceção é entregue a quem pediu a tarefa
            log.exception("tarefa em streaming falhou")
            result = e
        finally:
            if self.store is not None:
                self.store.close_thread()
        self.done.emit(result)


def run_stream(fn: Callable[[Emit, Cancelled], Any], store: Store | None, on_chunk: Callable[[str], None],
               on_done: Callable[[Any], None]) -> StreamTask:
    task = StreamTask(fn, store)
    live_threads.add(task)
    task.chunk.connect(on_chunk)
    task.done.connect(on_done)
    task.finished.connect(functools.partial(release, task))
    task.start()
    return task
