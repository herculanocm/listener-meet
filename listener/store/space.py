"""F5.6 (A-35, A-38, A-39): os pedaços de áudio de uma reunião excluída e o espaço do arquivo de volta, sempre numa conexão
própria com o arquivo (`mode=rw`: um arquivo ausente nunca é criado, e nada é anexado à conexão do app, cujo `BEGIN
IMMEDIATE` prenderia todos os arquivos anexados).

Liberar espaço: o arquivo com `auto_vacuum` incremental (os novos, `schema.AUDIO_NEW_FILE`) devolve as páginas livres em
passos de ~10 MB (`PRAGMA incremental_vacuum(n)`), conferindo entre passos a gravação e o pedido de parada; o arquivo antigo
(sem `auto_vacuum`) só recebe o `VACUUM` inteiro fora do mês corrente, sem gravação, sem worker vivo e com o dobro do arquivo
livre no disco, e sai dele incremental (a próxima exclusão nele já é em passos). O que não pôde ficar fica pendente em
`settings` (`audio.space_pending`, a lista dos arquivos, gravada antes de apagar os pedaços) e é tentado na próxima abertura
ociosa. `interrupt()` na conexão (de outra thread) para o `VACUUM` na hora; o SQLite desfaz a transação inteira: nada
corrompe, nem com o app fechado no meio (o pendente continua lá).

Revisão do L3: "liberado" só quando o arquivo encolheu (R-01: no Windows, o mapa de memória de outro processo impede o
truncamento sem erro; a nova tentativa faz uma escrita mínima para o checkpoint encolher); o VACUUM inteiro só num arquivo
que nenhuma conexão do app anexou (R-02, `attached.py`); o pedido de parada lido também durante o VACUUM (R-08)."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, closing, nullcontext, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ..log import get_logger
from .core import BUSY_TIMEOUT_S, _now

if TYPE_CHECKING:
    from .core import Core

log = get_logger("store.space")

PENDING_KEY = "audio.space_pending"      # chave interna (fora do SPECS): os arquivos com espaço a liberar, em JSON
STEP_BYTES = 10_000_000                  # um passo do incremental: curto o bastante para a gravação nunca esperar
FREE_FACTOR = 2                          # o VACUUM inteiro reescreve o arquivo no WAL: o dobro livre no disco
INCREMENTAL = 2                          # `PRAGMA auto_vacuum`: 0 nenhum, 1 inteiro, 2 incremental
PROGRESS_STEPS = 100                     # R-08: a parada lida a cada 100 instruções do SQLite (o VACUUM copia ~5 por pedaço)
HELD_TEXT = "arquivo aberto por outro processo (o worker ocioso, o ao vivo): encolhe na próxima tentativa"
ATTACHED_TEXT = "arquivo aberto no app: o VACUUM inteiro fica para a próxima abertura"

type Check = Callable[[], bool]
type OnConn = Callable[[sqlite3.Connection | None], None]
type Exclusive = Callable[[Callable[[], None]], AbstractContextManager[bool]]


class SpaceOutcome(StrEnum):
    FREED = "liberado"
    NOTHING = "nada a liberar"
    PENDING = "pendente"
    MISSING = "arquivo ausente"


@dataclass(frozen=True, slots=True)
class SpaceResult:
    file: str
    outcome: SpaceOutcome
    freed_bytes: int = 0
    reason: str = ""                     # por que ficou pendente (gravação, mês corrente, disco...)
    mode: str = ""                       # "incremental" ou "vacuum"


def open_audio(path: Path, readonly: bool = False) -> sqlite3.Connection:
    """Conexão própria com um arquivo de áudio que existe (`mode=rw`, ou `ro` no `--check`, falha em vez de criar o arquivo)."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode={'ro' if readonly else 'rw'}", uri=True, autocommit=True,
                           timeout=BUSY_TIMEOUT_S, check_same_thread=False)


def file_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in (path, Path(f"{path}-wal")) if f.exists())


def delete_chunks(path: Path, meeting_id: int) -> int:
    """A-35, a primeira transação: os pedaços da reunião, com commit (um comando só). Arquivo ausente: nada a apagar."""
    if not path.exists():
        return 0
    with closing(open_audio(path)) as c:
        return int(c.execute("DELETE FROM audio_chunks WHERE meeting_id = ?", (meeting_id,)).rowcount)


# ---------------------------------------------------------------- pendentes
def pending(core: Core) -> list[str]:
    try:
        rows = core.q("SELECT value FROM settings WHERE key = ?", (PENDING_KEY,))
        data = json.loads(str(rows[0]["value"])) if rows else []
    except (ValueError, sqlite3.OperationalError):       # banco anterior às configurações (o `--check` de um banco cru)
        return []
    return sorted({str(f) for f in data if isinstance(f, str)}) if isinstance(data, list) else []


def _set_pending(core: Core, files: list[str]) -> None:
    core.execute("INSERT INTO settings(key, value, scope, updated_at) VALUES (?, ?, 'now', ?) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value, updated_at = excluded.updated_at", (PENDING_KEY, json.dumps(sorted(set(files))), _now()))


def add_pending(core: Core, file: str) -> None:
    with core.transaction():
        _set_pending(core, [*pending(core), file])


def drop_pending(core: Core, file: str) -> None:
    with core.transaction():
        _set_pending(core, [f for f in pending(core) if f != file])


# ---------------------------------------------------------------- liberar
def disk_free(folder: Path) -> int:
    return int(shutil.disk_usage(folder).free)


def _blocked(stop: Check, busy: Check) -> str:
    if stop():
        return "pedido de parada"
    return "gravação em curso" if busy() else ""


def _incremental(c: sqlite3.Connection, stop: Check, busy: Check, step_bytes: int) -> str:
    step = max(1, step_bytes // int(c.execute("PRAGMA page_size").fetchone()[0]))
    while int(c.execute("PRAGMA freelist_count").fetchone()[0]) > 0:
        why = _blocked(stop, busy)
        if why:
            return why
        c.execute(f"PRAGMA incremental_vacuum({int(step)})").fetchall()      # o passo só roda inteiro sendo lido até o fim
    return ""


def _alone(_interrupt: Callable[[], None]) -> AbstractContextManager[bool]:
    return nullcontext(True)


@dataclass(frozen=True, slots=True)
class _Guards:
    current: bool
    stop: Check
    busy: Check
    worker_alive: Check
    disk_free: Callable[[Path], int]
    exclusive: Exclusive
    halt: Callable[[], None]                             # R-02: anexar o arquivo no meio do VACUUM chama este


def _full(c: sqlite3.Connection, path: Path, size: int, g: _Guards) -> str:
    if g.current:
        return "arquivo do mês corrente, sem liberação incremental"
    why = _blocked(g.stop, g.busy) or ("processamento em curso" if g.worker_alive() else "")
    if why:
        return why
    if g.disk_free(path.parent) < FREE_FACTOR * size:
        return "disco livre abaixo do dobro do arquivo"
    with g.exclusive(g.halt) as alone:                 # R-02: nenhuma conexão do app com ele anexado; anexar interrompe
        if not alone:
            return ATTACHED_TEXT
        c.execute("PRAGMA auto_vacuum = INCREMENTAL")   # vale com o VACUUM: a próxima exclusão neste arquivo já é em passos
        c.execute("VACUUM")
    return ""


def _content(c: sqlite3.Connection) -> int:
    return int(c.execute("PRAGMA page_count").fetchone()[0]) * int(c.execute("PRAGMA page_size").fetchone()[0])


def _mode(c: sqlite3.Connection, path: Path) -> str:
    """"incremental" ou "vacuum" com páginas livres; sem elas, "truncar" quando o arquivo passa do conteúdo (R-01: o
    encolhimento da vez anterior não aconteceu) e vazio quando não há nada a fazer."""
    if int(c.execute("PRAGMA freelist_count").fetchone()[0]):
        return "incremental" if int(c.execute("PRAGMA auto_vacuum").fetchone()[0]) == INCREMENTAL else "vacuum"
    return "truncar" if path.stat().st_size > _content(c) else ""


def _touch(c: sqlite3.Connection) -> None:
    """R-01: uma escrita mínima (o `user_version` com o mesmo valor) põe um quadro no WAL; o checkpoint seguinte encolhe o
    arquivo até o conteúdo, mesmo com a lista de páginas livres já vazia."""
    c.execute(f"PRAGMA user_version = {int(c.execute('PRAGMA user_version').fetchone()[0])}")


def _tail(c: sqlite3.Connection, path: Path) -> int:
    """R-01: o checkpoint encolhe o arquivo (WAL); devolve quanto ele ainda passa do conteúdo. No Windows, o mapa de memória
    de outra conexão (o worker ocioso, o ao vivo) impede o truncamento sem erro nenhum."""
    with suppress(sqlite3.Error):
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    try:
        return max(0, path.stat().st_size - _content(c))
    except (sqlite3.Error, OSError):
        return 0


def free_file(path: Path, *, current: bool, stop: Check, busy: Check, worker_alive: Check, on_conn: OnConn | None = None,
              step_bytes: int = STEP_BYTES, disk_free: Callable[[Path], int] = disk_free, exclusive: Exclusive = _alone) -> SpaceResult:
    """Devolve ao disco as páginas livres de um arquivo de áudio (A-38). `on_conn` recebe a conexão aberta (e None ao fechar):
    quem a guarda pode interrompê-la de outra thread. R-08: o pedido de parada também é lido a cada `PROGRESS_STEPS`
    instruções (o `interrupt()` antes de o VACUUM começar não teria efeito). R-01: liberado só quando o arquivo encolheu
    de fato; senão, pendente. `exclusive(interrupt)` diz se o VACUUM inteiro pode rodar (R-02). Nunca exceção de banco."""
    if not path.exists():
        return SpaceResult(path.name, SpaceOutcome.MISSING)
    before = file_bytes(path)
    c, halted = open_audio(path), threading.Event()
    # R-08: o `interrupt()` que chega entre instruções se perde (o SQLite o zera na instrução seguinte); a marca, não
    c.set_progress_handler(lambda: int(halted.is_set() or stop()), PROGRESS_STEPS)

    def halt() -> None:
        halted.set()
        c.interrupt()

    mode, why = "", ""
    if on_conn is not None:
        on_conn(c)
    try:
        mode = _mode(c, path)
        if mode == "truncar":
            _touch(c)
        elif mode == "incremental":
            why = _incremental(c, stop, busy, step_bytes)
        elif mode == "vacuum":
            why = _full(c, path, before, _Guards(current, stop, busy, worker_alive, disk_free, exclusive, halt))
    except sqlite3.OperationalError as e:               # `interrupt()` da saída ou da gravação; banco ocupado além do prazo
        why = "interrompido" if "interrupt" in str(e).lower() else f"banco ocupado ({e})"
    finally:
        if on_conn is not None:
            on_conn(None)
        c.set_progress_handler(None, 0)
        tail = _tail(c, path)
        c.close()
    if not mode and not why:
        return SpaceResult(path.name, SpaceOutcome.NOTHING)
    why = why or (HELD_TEXT if tail else "")
    return SpaceResult(path.name, SpaceOutcome.PENDING if why else SpaceOutcome.FREED, max(0, before - file_bytes(path)), why, mode)


__all__ = ["ATTACHED_TEXT", "FREE_FACTOR", "HELD_TEXT", "PENDING_KEY", "STEP_BYTES", "SpaceOutcome", "SpaceResult", "add_pending",
           "delete_chunks", "disk_free", "drop_pending", "file_bytes", "free_file", "open_audio", "pending"]
