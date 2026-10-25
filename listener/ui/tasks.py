"""Trabalho fora da thread da interface (M-26): `Task` roda uma função numa QThread e devolve o resultado (ou a exceção)
por sinal; a referência fica viva até `finished` (QThread sem parent é destruída pelo Qt se for coletada antes)."""
from __future__ import annotations

import functools
from collections.abc import Callable
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
