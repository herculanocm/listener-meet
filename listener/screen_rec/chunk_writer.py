"""F5.27 (lote 2): as saídas do filho `--screen` (`proc.py`): as linhas do protocolo no stdout (`Emitter`) e a thread
`screen-write` (`ChunkWriter`), que grava os pedaços em ordem, cada um numa instrução própria, com nova tentativa em
`OperationalError` ("database is locked": a conexão do app pode segurar a trava de escrita até 5 s). Nenhum pedaço é descartado
por isso: só depois do EOF, passado `DRAIN_S` de novas tentativas, o que sobrou é contado em `unwritten` e vai ao log. O `idx`
começa no maior já gravado da reunião mais 1 e, num índice repetido (um filho anterior ainda gravando), relê o maior e segue."""
from __future__ import annotations

import functools
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, BinaryIO

from ..log import get_logger
from ..store import Store
from .client import CHUNK, encode_line
from .segment import Segment

log = get_logger("screen_rec.proc")

DRAIN_S = 15.0                   # depois do EOF, as novas tentativas de gravar os pendentes (mais uma espera do SQLite < 30 s)
RETRY_BASE_S, RETRY_MAX_S = 0.5, 5.0


class Emitter:
    """Linhas JSON no stdout, uma por vez entre threads; o app fechou o pipe -> para de emitir e segue gravando no banco."""

    def __init__(self, out: BinaryIO):
        self._out = out
        self._lock = threading.Lock()
        self.closed = False

    def emit(self, msg: dict[str, Any]) -> None:
        try:
            data = encode_line(msg)
        except (TypeError, ValueError) as e:
            log.warning("linha %s não serializável: %s", msg.get("type"), e)
            return
        with self._lock:
            if self.closed:
                return
            try:
                self._out.write(data)
                self._out.flush()
            except (OSError, ValueError) as e:
                self.closed = True
                log.info("o app fechou a saída do filho da tela (%s); os pedaços seguem só no banco", type(e).__name__)



class ChunkWriter:
    """A thread `screen-write`: os pedaços em ordem, com nova tentativa; só ela usa o `Store` depois da abertura."""

    def __init__(self, store: Store, meeting_id: int, out: Emitter, *, retry_base_s: float = RETRY_BASE_S):
        self.store, self.meeting_id, self.out = store, meeting_id, out
        self.retry_base_s = retry_base_s
        self.written = 0
        self.unwritten = 0
        self.failed = False               # a thread parou por um erro que não é a trava: a sessão sai com erro
        self._pending: queue.SimpleQueue[Segment | None] = queue.SimpleQueue()
        self._deadline: float | None = None
        self._thread = threading.Thread(target=self._run, name="screen-write", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def put(self, segment: Segment) -> None:
        self._pending.put(segment)

    def finish(self, drain_s: float) -> None:
        """Sem mais pedaços: grava os pendentes até `drain_s` e termina (espera a thread um pouco além do prazo)."""
        self._deadline = time.monotonic() + drain_s
        self._pending.put(None)
        self._thread.join(drain_s + 2 * RETRY_MAX_S + 1.0)   # a última espera e um `busy_timeout`
        if self._thread.is_alive():
            log.error("reunião %s: a gravação dos pedaços da tela não terminou no prazo", self.meeting_id)
            self.unwritten += 1 + self._pending.qsize()

    def _run(self) -> None:
        try:
            idx = self._retrying(self._next_idx, -1)
            while (segment := self._pending.get()) is not None:
                idx = self._write(segment, idx)
        except _GaveUpError:                                    # passou o prazo do fim: o que sobrou fica contado
            self.unwritten += 1 + self._drain()
        except Exception:                                  # não é a trava do SQLite: a sessão para e o app relança
            log.exception("reunião %s: a gravação dos pedaços da tela parou", self.meeting_id)
            self.failed = True
            self.unwritten += self._drain()
        finally:
            with suppress(Exception):
                self.store.close_thread()

    def _drain(self) -> int:
        n = 0
        while self._pending.get() is not None:             # o `finish` sempre põe o None no fim
            n += 1
        return n

    def _next_idx(self) -> int:
        return max((int(c["idx"]) for c in self.store.screen.list_chunks(self.meeting_id)), default=-1) + 1

    def _write(self, s: Segment, idx: int) -> int:
        """Grava `s` com o índice `idx` (ou o seguinte livre); devolve o próximo índice."""
        while True:
            insert = functools.partial(self.store.screen.insert_chunk, self.meeting_id, idx=idx, t_start_ms=s.t_start_ms,
                                       t_end_ms=s.t_end_ms, frames=s.frames, width=s.width, height=s.height, codec=s.codec, data=s.data)
            try:
                self._retrying(insert, idx)
            except sqlite3.IntegrityError:                 # o índice já existe: um filho anterior ainda gravava
                idx = max(idx + 1, self._retrying(self._next_idx, idx))
                continue
            self.written += 1
            self.out.emit({"type": CHUNK, "idx": idx, "t_start_ms": s.t_start_ms, "t_end_ms": s.t_end_ms, "frames": s.frames,
                           "bytes": len(s.data)})
            return idx + 1

    def _retrying[T](self, fn: Callable[[], T], idx: int) -> T:
        """`fn` com nova tentativa em erro do SQLite que não é de integridade ("database is locked" e afins), sem prazo até o
        fim da tela; depois do `finish`, até o prazo dele (`_GaveUpError`)."""
        failures = 0
        while True:
            try:
                return fn()
            except sqlite3.IntegrityError:
                raise
            except sqlite3.Error as e:
                failures += 1
                if self._deadline is not None and time.monotonic() >= self._deadline:
                    log.error("reunião %s: pedaço %d da tela não gravado depois de %d tentativas: %s", self.meeting_id, idx,  # noqa: TRY400
                              failures, e)                 # o erro esperado (a trava), sem a pilha
                    raise _GaveUpError from e
                (log.warning if failures == 1 else log.debug)("reunião %s: pedaço %d da tela, nova tentativa: %s", self.meeting_id,
                                                               idx, e)
                time.sleep(min(RETRY_MAX_S, self.retry_base_s * 2 ** min(failures - 1, 10)))


class _GaveUpError(Exception):
    """O prazo do fim passou com o SQLite ainda recusando."""


__all__ = ["DRAIN_S", "RETRY_BASE_S", "RETRY_MAX_S", "ChunkWriter", "Emitter"]
