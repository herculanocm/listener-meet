"""Núcleo do Store (F1.2 M-01, M-02, M-18, M-19; F2): caminhos, uma conexão SQLite por thread, arquivos de áudio mensais
anexados sob demanda, migrações atômicas por arquivo, `transaction()`, papéis (app, worker, só leitura) e utilidades.

Papéis (`StoreRole`): APP abre o main em escrita, migra e faz VACUUM de legado; WORKER (F2) abre o main em escrita sem
migrar (recusa se a versão for diferente da do código) e anexa o áudio com `mode=ro` e `mmap_size` de 256 MB; READONLY
abre tudo com `mode=ro` (`--check`).

Arquivos: `listener.db` (main) e um `listener-audio-AAAA-MM.db` por mês de `meetings.started_at` (D-039); o
`listener-audio.db` da F1 continua sendo lido pelas reuniões antigas e nunca é apagado nem renomeado (CP3).
Cada arquivo de áudio tem `schema_migrations` e `audio_chunks` próprios, `page_size` de 32 KB e WAL.
"""
from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..log import get_logger
from ..model import StoreRole
from .schema import (AUDIO_DATE_COLUMNS, AUDIO_MIGRATIONS, AUDIO_SCHEMA_VERSION, MAIN_DATE_COLUMNS, MAIN_MIGRATIONS,
                     MAIN_SCHEMA_VERSION, MAIN_V4_COLUMNS)

log = get_logger("store")

MAIN_FILE = "listener.db"
LEGACY_AUDIO_FILE = "listener-audio.db"
AUDIO_FILE_PREFIX = "listener-audio-"
AUDIO_PAGE_SIZE = 32768          # blobs de ~1,5 MB: 2x no INSERT e no blobopen (M-19)
WAL_AUTOCHECKPOINT = 4000        # páginas; vale para a conexão inteira (o main é pequeno)
MAX_ATTACHED = 8                 # anexos simultâneos por conexão; os menos usados são desanexados
VACUUM_MAX_BYTES = 100_000_000   # só arquivos legados pequenos recebem VACUUM para mudar o page_size
BUSY_TIMEOUT_S = 5.0
AUDIO_MMAP_BYTES = 256 * 1024 * 1024   # worker da F2: horas de FLAC lidas por mmap, sem cópia para o cache de páginas

type PyMigration = Callable[["Core", sqlite3.Connection, str], None]


def _now() -> str:
    """Hora local com offset (ISO 8601), por exemplo 2026-10-24T18:03:12.345-03:00 (F1.1 A5)."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def with_offset(value: str | None) -> str | None:
    """Reescreve uma data ISO sem fuso como hora local com offset; datas já com offset ou vazias voltam iguais."""
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="milliseconds")


def json_default(o: object) -> Any:
    """Serializa numpy e datas em `events.data`; o resto levanta `TypeError` em vez de virar texto (M-14)."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, datetime | date):
        return o.isoformat()
    raise TypeError(f"não sei serializar {type(o).__name__} em JSON")


def audio_file_for(started_at: str) -> str:
    """Nome do arquivo de áudio do mês de uma data ISO: 2026-10-24T... -> listener-audio-2026-10.db."""
    month = datetime.fromisoformat(started_at).strftime("%Y-%m")
    return f"{AUDIO_FILE_PREFIX}{month}.db"


def schema_for(file_name: str) -> str:
    """Nome do schema anexado: legado -> `audio`; listener-audio-2026-10.db -> `audio_2026_10`."""
    if file_name == LEGACY_AUDIO_FILE:
        return "audio"
    stem = file_name.removeprefix(AUDIO_FILE_PREFIX).removesuffix(".db").replace("-", "_")
    if not stem.replace("_", "").isdigit():
        raise ValueError(f"nome de arquivo de áudio inesperado: {file_name}")
    return f"audio_{stem}"


def _rewrite_dates(conn: sqlite3.Connection, schema: str, columns: dict[str, tuple[str, ...]]) -> None:
    for table, cols in columns.items():
        for col in cols:
            rows = conn.execute(f"SELECT rowid, {col} FROM {schema}.{table} WHERE {col} IS NOT NULL").fetchall()  # noqa: S608
            for rowid, value in rows:
                new = with_offset(value)
                if new != value:
                    conn.execute(f"UPDATE {schema}.{table} SET {col} = ? WHERE rowid = ?", (new, rowid))  # noqa: S608


def _main_v2_dates(_core: Core, conn: sqlite3.Connection, schema: str) -> None:
    _rewrite_dates(conn, schema, MAIN_DATE_COLUMNS)


def _audio_v2_dates(_core: Core, conn: sqlite3.Connection, schema: str) -> None:
    _rewrite_dates(conn, schema, AUDIO_DATE_COLUMNS)


def _main_v3_audio_file(core: Core, conn: sqlite3.Connection, schema: str) -> None:
    """`meetings.audio_file` (idempotente: só cria a coluna se faltar). Reuniões anteriores à v3 apontam para o arquivo
    legado, se ele existir; sem ele, ficam sem áudio (CP3: nada se apaga; se o arquivo voltar, ele é achado pelo nome)."""
    cols = {r[1] for r in conn.execute(f"PRAGMA {schema}.table_info(meetings)")}
    if "audio_file" not in cols:
        conn.execute(f"ALTER TABLE {schema}.meetings ADD COLUMN audio_file TEXT")
    if (core.base_dir / LEGACY_AUDIO_FILE).exists():
        conn.execute(f"UPDATE {schema}.meetings SET audio_file = ? WHERE audio_file IS NULL", (LEGACY_AUDIO_FILE,))  # noqa: S608
    else:
        n = conn.execute(f"SELECT COUNT(*) FROM {schema}.meetings WHERE audio_file IS NULL").fetchone()[0]  # noqa: S608
        if n:
            log.warning("%d reunião(ões) anteriores sem o arquivo de áudio legado %s na pasta", n, LEGACY_AUDIO_FILE)


def _main_v4_pipeline(_core: Core, conn: sqlite3.Connection, schema: str) -> None:
    """Colunas da F2 em tabelas que já existem, só se faltarem (`ALTER TABLE ADD COLUMN` não é idempotente); as tabelas
    novas e os índices vêm de `MAIN_V4` na mesma transação."""
    for table, column, type_ in MAIN_V4_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN {column} {type_}")


MAIN_PY_MIGRATIONS: dict[int, PyMigration] = {2: _main_v2_dates, 3: _main_v3_audio_file, 4: _main_v4_pipeline}
AUDIO_PY_MIGRATIONS: dict[int, PyMigration] = {2: _audio_v2_dates}


class _Local(threading.local):
    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.attached: OrderedDict[str, str] = OrderedDict()   # schema -> arquivo, do menos ao mais recente
        self.in_tx = False


class Core:
    """Conexões, anexos e migrações. Os repositórios (`settings`, `meetings`, `audio`) só conhecem isto."""

    def __init__(self, base_dir: str | Path, role: StoreRole = StoreRole.APP):
        self.base_dir = Path(base_dir)
        self.role = role
        self.main_path = self.base_dir / MAIN_FILE
        self._local = _Local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._ready_files: set[str] = set()          # arquivos de áudio já migrados ou verificados neste processo
        self._meeting_files: dict[int, str] = {}
        self._closed = False
        self.main_version = 0
        if role == StoreRole.APP:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        elif not self.main_path.exists():
            raise FileNotFoundError(f"não existe banco em {self.base_dir}")
        conn = self.conn()
        if role == StoreRole.APP:
            self._migrate_main(conn)
            self.main_version = MAIN_SCHEMA_VERSION
        else:
            self.main_version = self._version(conn, "main") if self._has_table(conn, "main", "schema_migrations") else 0
            if self.main_version > MAIN_SCHEMA_VERSION:
                raise RuntimeError(f"o banco está na versão {self.main_version} e este app conhece até a "
                                   f"{MAIN_SCHEMA_VERSION}: atualize o app")
            if role == StoreRole.WORKER and self.main_version < MAIN_SCHEMA_VERSION:
                raise RuntimeError(f"o banco está na versão {self.main_version} e o worker precisa da {MAIN_SCHEMA_VERSION}: "
                                   "abra o app uma vez para migrar")
        current = self.current_audio_file()
        if role == StoreRole.APP or (self.base_dir / current).exists():
            self.attach(current)
        if (self.base_dir / LEGACY_AUDIO_FILE).exists():
            self.attach(LEGACY_AUDIO_FILE)

    @property
    def readonly(self) -> bool:
        return self.role == StoreRole.READONLY

    # ---------------------------------------------------------------- conexões
    def conn(self) -> sqlite3.Connection:
        """Conexão desta thread, criada sob demanda com os mesmos pragmas e o arquivo de áudio do mês anexado."""
        c = self._local.conn
        if c is not None:
            return c
        if self._closed:
            raise RuntimeError("Store fechado")
        c = self._connect()
        self._local.conn = c
        self._local.attached = OrderedDict()
        with self._lock:
            self._conns.append(c)
        return c

    def _connect(self) -> sqlite3.Connection:
        if self.readonly:
            c = sqlite3.connect(f"{self.main_path.as_uri()}?mode=ro", uri=True, autocommit=True, timeout=BUSY_TIMEOUT_S,
                                check_same_thread=False)
        else:
            c = sqlite3.connect(str(self.main_path), autocommit=True, timeout=BUSY_TIMEOUT_S, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        if not self.readonly:
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
            c.execute(f"PRAGMA wal_autocheckpoint = {WAL_AUTOCHECKPOINT}")
        return c

    def close_thread(self) -> None:
        """Fecha a conexão da thread atual (a thread de trabalho chama ao terminar)."""
        c = self._local.conn
        if c is None:
            return
        self._local.conn = None
        self._local.attached = OrderedDict()
        with self._lock, suppress(ValueError):
            self._conns.remove(c)
        with suppress(sqlite3.Error):
            c.close()

    def close(self) -> None:
        """Fecha todas as conexões; checkpoint PASSIVE antes (não espera leitores de outro processo)."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            conns, self._conns = list(self._conns), []
        for i, c in enumerate(conns):
            if i == 0 and not self.readonly:
                with suppress(sqlite3.Error):
                    c.execute("PRAGMA wal_checkpoint(PASSIVE)")
            with suppress(sqlite3.Error):
                c.close()
        self._local.conn = None
        self._local.attached = OrderedDict()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE` ... `COMMIT` (ou `ROLLBACK`) na conexão desta thread; reentrante dentro da mesma thread."""
        c = self.conn()
        if self._local.in_tx:
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        self._local.in_tx = True
        try:
            yield c
        except BaseException:
            with suppress(sqlite3.Error):
                c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
        finally:
            self._local.in_tx = False

    # ---------------------------------------------------------------- linhas
    def q(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def insert(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        cur = self.conn().execute(sql, params)
        return int(cur.lastrowid or 0)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        return int(self.conn().execute(sql, params).rowcount)

    # ---------------------------------------------------------------- migrações
    @staticmethod
    def _has_table(c: sqlite3.Connection, schema: str, table: str) -> bool:
        return bool(c.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",  # noqa: S608
                              (table,)).fetchone())

    @staticmethod
    def _version(c: sqlite3.Connection, schema: str) -> int:
        row = c.execute(f"SELECT MAX(version) FROM {schema}.schema_migrations").fetchone()  # noqa: S608
        return int(row[0] or 0)

    def _apply(self, c: sqlite3.Connection, schema: str, version: int, statements: tuple[str, ...] | None,
               py: PyMigration | None) -> None:
        """Uma migração inteira (SQL, Python, linha de versão e `user_version`) num único `BEGIN IMMEDIATE`."""
        with self.transaction():
            for sql in statements or ():
                c.execute(sql.format(s=schema))
            if py is not None:
                py(self, c, schema)
            c.execute(f"INSERT OR IGNORE INTO {schema}.schema_migrations(version, applied_at) VALUES (?, ?)",  # noqa: S608
                      (version, _now()))
            c.execute(f"PRAGMA {schema}.user_version = {int(version)}")
        log.info("%s migrado para a versão %d", schema, version)

    def _migrate_main(self, c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE IF NOT EXISTS main.schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = self._version(c, "main")
        if current > MAIN_SCHEMA_VERSION:
            raise RuntimeError(f"o banco está na versão {current} e este app conhece até a {MAIN_SCHEMA_VERSION}: atualize o app")
        for version, statements in MAIN_MIGRATIONS:
            if version > current:
                self._apply(c, "main", version, statements, MAIN_PY_MIGRATIONS.get(version))

    def _prepare_audio(self, c: sqlite3.Connection, schema: str, path: Path, new_file: bool) -> None:
        """Migra (ou só verifica, fora do papel APP) um arquivo de áudio recém-anexado; erro claro se estiver errado."""
        if self.role != StoreRole.APP:
            self._check_audio(c, schema, path)
            return
        if new_file:
            c.execute(f"PRAGMA {schema}.page_size = {AUDIO_PAGE_SIZE}")     # antes da primeira tabela
        c.execute(f"CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = self._version(c, schema)
        if current > AUDIO_SCHEMA_VERSION:
            raise RuntimeError(f"{path.name} está na versão {current} e este app conhece até a {AUDIO_SCHEMA_VERSION}: atualize o app")
        for version, statements in AUDIO_MIGRATIONS:
            if version > current:
                self._apply(c, schema, version, statements, AUDIO_PY_MIGRATIONS.get(version))
        c.execute(f"PRAGMA {schema}.journal_mode = WAL")
        self._check_audio(c, schema, path)
        self._maybe_vacuum(c, schema, path)

    @staticmethod
    def _check_audio(c: sqlite3.Connection, schema: str, path: Path) -> None:
        try:
            c.execute(f"SELECT 1 FROM {schema}.audio_chunks LIMIT 0")  # noqa: S608
        except sqlite3.OperationalError as e:
            raise RuntimeError(f"arquivo de áudio {path.name} sem a tabela audio_chunks ({e}); restaure o arquivo "
                               "certo ou mova este de lugar antes de gravar") from e

    def _maybe_vacuum(self, c: sqlite3.Connection, schema: str, path: Path) -> None:
        """Arquivo legado com páginas de 4 KB: `VACUUM` para 32 KB enquanto é pequeno; grande fica como está."""
        page = int(c.execute(f"PRAGMA {schema}.page_size").fetchone()[0])
        if page == AUDIO_PAGE_SIZE:
            return
        if path.stat().st_size > VACUUM_MAX_BYTES:
            log.info("%s mantém page_size %d (arquivo grande demais para VACUUM)", path.name, page)
            return
        try:
            c.execute(f"PRAGMA {schema}.journal_mode = DELETE")      # em WAL o page_size não muda
            c.execute(f"PRAGMA {schema}.page_size = {AUDIO_PAGE_SIZE}")
            c.execute(f"VACUUM {schema}")
        finally:
            c.execute(f"PRAGMA {schema}.journal_mode = WAL")
        log.info("%s: page_size %d -> %d (VACUUM)", path.name, page, AUDIO_PAGE_SIZE)

    # ---------------------------------------------------------------- arquivos de áudio
    def current_audio_file(self) -> str:
        return audio_file_for(_now())

    def audio_files(self) -> list[str]:
        """Arquivos de áudio presentes na pasta: o legado primeiro, depois os mensais em ordem."""
        out = [LEGACY_AUDIO_FILE] if (self.base_dir / LEGACY_AUDIO_FILE).exists() else []
        out += sorted(p.name for p in self.base_dir.glob(f"{AUDIO_FILE_PREFIX}????-??.db"))
        return out

    def attach(self, file_name: str) -> str:
        """Garante o arquivo anexado na conexão desta thread (migrado uma vez por processo); devolve o schema."""
        c = self.conn()
        schema = schema_for(file_name)
        attached = self._local.attached
        if schema in attached:
            attached.move_to_end(schema)
            return schema
        path = self.base_dir / file_name
        new_file = not path.exists() or path.stat().st_size == 0
        audio_ro = self.role != StoreRole.APP          # worker e --check nunca escrevem no áudio
        if audio_ro and new_file:
            raise FileNotFoundError(f"arquivo de áudio ausente: {path}")
        while len(attached) >= MAX_ATTACHED and not self._local.in_tx:
            old, _ = attached.popitem(last=False)
            c.execute(f"DETACH DATABASE {old}")
        if audio_ro:
            c.execute(f"ATTACH DATABASE ? AS {schema}", (f"{path.as_uri()}?mode=ro",))
        else:
            c.execute(f"ATTACH DATABASE ? AS {schema}", (str(path),))
        attached[schema] = file_name
        with self._lock:
            ready = file_name in self._ready_files
        if not ready:
            self._prepare_audio(c, schema, path, new_file)
            with self._lock:
                self._ready_files.add(file_name)
        if self.role == StoreRole.APP:
            c.execute(f"PRAGMA {schema}.synchronous = NORMAL")
        elif self.role == StoreRole.WORKER:
            c.execute(f"PRAGMA {schema}.mmap_size = {AUDIO_MMAP_BYTES}")
        return schema

    def audio_schema_for_meeting(self, meeting_id: int) -> str | None:
        """Schema do arquivo de áudio da reunião, anexando se preciso; None quando a reunião não tem arquivo."""
        file = self._meeting_files.get(meeting_id)
        if file is None:
            if self.main_version >= 3:
                row = self.conn().execute("SELECT audio_file FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
                if row is None:
                    return None
                file = row[0]
            if file is None and (self.base_dir / LEGACY_AUDIO_FILE).exists():
                file = LEGACY_AUDIO_FILE
            if file is None:
                return None
            self._meeting_files[meeting_id] = file
        return self.attach(file)

    def forget_meeting(self, meeting_id: int) -> None:
        self._meeting_files.pop(meeting_id, None)

    def db_sizes(self) -> dict[str, Any]:
        def size(p: Path) -> int:
            return sum(f.stat().st_size for s in ("", "-wal", "-shm") if (f := Path(str(p) + s)).exists())
        files = {name: size(self.base_dir / name) for name in self.audio_files()}
        return {"main": size(self.main_path), "audio": sum(files.values()), "audio_files": files}
