"""F5.27 (D-113): o repositório da tela gravada (`Store.screen`). Os pedaços ficam no arquivo mensal da tela da reunião
(`store/screen_file.py`), anexado sob demanda na conexão de cada thread, na mesma fila dos anexos de áudio (`MAX_ATTACHED`
por conexão, os menos usados desanexados primeiro).

Quem cria e migra o arquivo é o app (`ensure_file`, ao começar a gravar a tela, antes de lançar o filho), que grava
`meetings.screen_file`; o filho `--screen` (papel WORKER) anexa o arquivo em escrita sem criar (`mode=rw`) e só confere a
versão; o `--check` (READONLY) anexa com `mode=ro`. Sem mapa de memória: ele impediria o arquivo de encolher depois de
"Apagar a tela desta reunião" (F5.6 R-01). Apagar abre uma conexão própria com o arquivo, como o áudio (A-35), e põe o
arquivo na lista do espaço a liberar (`space.add_pending`): quem apaga chama depois o `free_pending`. O log leva ids,
contagens e tamanhos, nunca imagem nem caminho."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TypedDict, cast

from ..log import get_logger
from ..model import StoreRole
from . import space
from .core import MAX_ATTACHED, Core
from .screen_file import SCREEN_MIGRATIONS, SCREEN_NEW_FILE, SCREEN_SCHEMA_VERSION, screen_file_for, screen_files, screen_schema_for

log = get_logger("store.screen")

SCREEN_MAIN_VERSION = 12                 # a versão do main que tem `meetings.screen_file`
_META = "id, meeting_id, idx, t_start_ms, t_end_ms, frames, width, height, codec, length(mp4) AS bytes"


class ScreenChunkMeta(TypedDict):
    id: int
    meeting_id: int
    idx: int
    t_start_ms: int
    t_end_ms: int
    frames: int
    width: int
    height: int
    codec: str
    bytes: int


@dataclass(frozen=True, slots=True)
class ScreenDeleted:
    chunks: int = 0                      # pedaços apagados
    data_bytes: int = 0                  # soma dos MP4 apagados (o arquivo encolhe depois, no `free_pending`)


# ---------------------------------------------------------------- anexo
def attach(core: Core, file_name: str) -> str:
    """Garante o arquivo da tela anexado na conexão desta thread e devolve o schema. Só o app cria (e migra) um arquivo; o
    WORKER o abre em escrita e o READONLY em leitura, os dois só se ele existe. Um arquivo ainda não anexado só se anexa fora de
    `transaction()` e de `snapshot()`: lá dentro o SQLite 3.49 aceita o ATTACH, mas recusa o `synchronous`, o rollback desfaria a
    migração e a fila não desanexa ninguém (conferido); quem lê numa leitura consistente resolve o schema antes."""
    c = core.conn()
    schema = screen_schema_for(file_name)
    attached = core._local.attached
    if schema in attached:
        attached.move_to_end(schema)
        return schema
    if core._local.in_tx:
        raise RuntimeError(f"o arquivo da tela {file_name} só se anexa fora de transação: resolva o schema antes")
    path = core.base_dir / file_name
    new_file = not path.exists() or path.stat().st_size == 0
    if new_file and core.role != StoreRole.APP:
        raise FileNotFoundError(f"arquivo da tela ausente: {file_name}")
    core.release_audio(MAX_ATTACHED - 1)                # a mesma fila dos anexos de áudio
    target = str(path) if core.role == StoreRole.APP else f"{path.resolve().as_uri()}?mode={'ro' if core.readonly else 'rw'}"
    core.files.attaching(file_name)
    try:
        c.execute(f"ATTACH DATABASE ? AS {schema}", (target,))
    except sqlite3.Error:
        core.files.detached([file_name])
        raise
    attached[schema] = file_name
    try:
        _prepare_once(core, c, schema, file_name, new_file)
    except BaseException:
        attached.pop(schema, None)
        c.execute(f"DETACH DATABASE {schema}")
        core.files.detached([file_name])
        raise
    if not core.readonly:
        c.execute(f"PRAGMA {schema}.synchronous = NORMAL")
    return schema


def _prepare_once(core: Core, c: sqlite3.Connection, schema: str, file_name: str, new_file: bool) -> None:
    """Migra (no app) ou confere (nos outros papéis) o arquivo uma vez por processo."""
    with core._lock:
        if file_name in core._ready_files:
            return
    if core.role == StoreRole.APP:
        for sql in SCREEN_NEW_FILE if new_file else ():
            c.execute(sql.format(s=schema))
        c.execute(f"CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = _check_version(core, c, schema, file_name)
        for version, statements in SCREEN_MIGRATIONS:
            if version > current:
                core._apply(c, schema, version, statements, None)
        c.execute(f"PRAGMA {schema}.journal_mode = WAL")
    else:
        current = _check_version(core, c, schema, file_name)
        if core.role == StoreRole.WORKER and current < SCREEN_SCHEMA_VERSION:
            raise RuntimeError(f"{file_name} está na versão {current} e o filho precisa da {SCREEN_SCHEMA_VERSION}: abra o app")
    try:
        c.execute(f"SELECT 1 FROM {schema}.screen_chunks LIMIT 0")  # noqa: S608
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"arquivo da tela {file_name} sem a tabela screen_chunks ({e}); restaure o arquivo certo ou mova este "
                           "de lugar") from e
    with core._lock:
        core._ready_files.add(file_name)


def _check_version(core: Core, c: sqlite3.Connection, schema: str, file_name: str) -> int:
    current = core._version(c, schema) if core._has_table(c, schema, "schema_migrations") else 0
    if current > SCREEN_SCHEMA_VERSION:
        raise RuntimeError(f"{file_name} está na versão {current} e este app conhece até a {SCREEN_SCHEMA_VERSION}: atualize o app")
    return current


# ---------------------------------------------------------------- repositório
class ScreenRepo:
    def __init__(self, core: Core):
        self.core = core

    def file_for(self, meeting_id: int) -> str | None:
        """O `screen_file` da reunião (nulo: reunião sem tela, ou banco anterior à v12); não confere se o arquivo existe."""
        if self.core.main_version < SCREEN_MAIN_VERSION:
            return None
        rows = self.core.q("SELECT screen_file FROM meetings WHERE id = ?", (meeting_id,))
        return str(rows[0][0]) if rows and rows[0][0] is not None else None

    def _schema(self, meeting_id: int) -> str | None:
        """O schema da tela da reunião, anexando se preciso; None sem `screen_file` ou com o arquivo fora da pasta (anexar um
        ausente no app o criaria vazio)."""
        file = self.file_for(meeting_id)
        if file is None or not (self.core.base_dir / file).exists():
            return None
        return attach(self.core, file)

    def ensure_file(self, meeting_id: int) -> str:
        """Só o app: cria e migra o arquivo da tela do mês da reunião (o de `started_at`, como o áudio), se preciso, e grava
        `meetings.screen_file` na primeira vez; devolve o nome do arquivo. Chamado ao começar a gravar a tela, antes de lançar
        o filho, fora de transação. `LookupError` sem a reunião."""
        if self.core.role != StoreRole.APP:
            raise RuntimeError("só o app cria o arquivo da tela; o filho grava no que o app criou")
        rows = self.core.q("SELECT started_at, screen_file FROM meetings WHERE id = ?", (meeting_id,))
        if not rows:
            raise LookupError(f"reunião {meeting_id} não existe")
        file = str(rows[0]["screen_file"] or screen_file_for(str(rows[0]["started_at"])))
        attach(self.core, file)
        self.core.execute("UPDATE meetings SET screen_file = ? WHERE id = ? AND screen_file IS NULL", (file, meeting_id))
        return file

    def insert_chunk(self, meeting_id: int, *, idx: int, t_start_ms: int, t_end_ms: int, frames: int, width: int, height: int,
                     codec: str, data: bytes) -> int:
        """Um pedaço, num comando só (sem `transaction()`, cujo `BEGIN IMMEDIATE` prenderia o main e todos os anexos). O
        arquivo já existe (`ensure_file`); o índice repetido levanta `IntegrityError` (nada é sobrescrito). Devolve o id."""
        if t_end_ms < t_start_ms or frames < 0 or width <= 0 or height <= 0:
            raise ValueError(f"pedaço {idx} da tela inválido: {t_start_ms}..{t_end_ms} ms, {frames} quadros, {width}x{height}")
        s = self._schema(meeting_id)
        if s is None:
            raise sqlite3.OperationalError(f"reunião {meeting_id} não tem arquivo da tela")
        return self.core.insert(
            f"INSERT INTO {s}.screen_chunks(meeting_id, idx, t_start_ms, t_end_ms, frames, width, height, codec, mp4) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (meeting_id, int(idx), int(t_start_ms), int(t_end_ms), int(frames), int(width), int(height), codec, sqlite3.Binary(data)))

    def list_chunks(self, meeting_id: int) -> list[ScreenChunkMeta]:
        """Os pedaços da reunião em ordem, sem o MP4 (com o tamanho dele em `bytes`)."""
        s = self._schema(meeting_id)
        if s is None:
            return []
        rows = self.core.q(f"SELECT {_META} FROM {s}.screen_chunks WHERE meeting_id = ? ORDER BY idx", (meeting_id,))  # noqa: S608
        return [cast(ScreenChunkMeta, dict(r)) for r in rows]

    def get_chunk(self, meeting_id: int, idx: int) -> bytes | None:
        """O MP4 de um pedaço, ou None."""
        s = self._schema(meeting_id)
        if s is None:
            return None
        rows = self.core.q(f"SELECT mp4 FROM {s}.screen_chunks WHERE meeting_id = ? AND idx = ?", (meeting_id, int(idx)))  # noqa: S608
        return bytes(rows[0][0]) if rows else None

    def total_bytes(self, meeting_id: int) -> int:
        """A soma dos MP4 da reunião (o "(1,2 GB)" da U-184); 0 sem tela."""
        s = self._schema(meeting_id)
        if s is None:
            return 0
        rows = self.core.q(f"SELECT COALESCE(SUM(length(mp4)), 0) FROM {s}.screen_chunks WHERE meeting_id = ?", (meeting_id,))  # noqa: S608
        return int(rows[0][0])

    def delete_for_meeting(self, meeting_id: int) -> ScreenDeleted:
        """Apaga os pedaços da tela da reunião ("Apagar a tela desta reunião" e a exclusão da reunião; só por ação do usuário,
        CP3), numa conexão própria com o arquivo, numa transação: a contagem, os bytes e o DELETE. Antes, o arquivo entra na
        lista do espaço a liberar (fora de qualquer transação nesta conexão, que pode ter o arquivo anexado). O áudio e o
        `screen_file` ficam; o evento é de quem chama. Sem arquivo: nada a apagar."""
        file = self.file_for(meeting_id)
        path = self.core.base_dir / file if file is not None else None
        if path is None or not path.exists():
            return ScreenDeleted()
        space.add_pending(self.core, path.name)
        with closing(space.open_audio(path)) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                n, size = c.execute("SELECT COUNT(*), COALESCE(SUM(length(mp4)), 0) FROM screen_chunks WHERE meeting_id = ?",
                                    (meeting_id,)).fetchone()
                c.execute("DELETE FROM screen_chunks WHERE meeting_id = ?", (meeting_id,))
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        log.info("reunião %s: %d pedaço(s) da tela apagado(s), %d byte(s)", meeting_id, n, size)
        return ScreenDeleted(int(n), int(size))

    # ---------------------------------------------------------------- diagnóstico (só reporta, CP3)
    def files(self) -> list[str]:
        return screen_files(self.core.base_dir)

    def missing_files(self) -> list[str]:
        """Arquivos da tela citados por reuniões e ausentes da pasta."""
        if self.core.main_version < SCREEN_MAIN_VERSION:
            return []
        rows = self.core.q("SELECT DISTINCT screen_file FROM meetings WHERE screen_file IS NOT NULL ORDER BY screen_file")
        return [str(r[0]) for r in rows if not (self.core.base_dir / str(r[0])).exists()]

    def orphan_report(self) -> dict[str, list[int]]:
        """Por arquivo da tela, as reuniões com pedaços mas sem linha em `meetings`, cada arquivo numa conexão própria, sem anexar
        (como o do áudio)."""
        known = {int(r[0]) for r in self.core.q("SELECT id FROM main.meetings")}
        out: dict[str, list[int]] = {}
        for file in self.files():
            try:
                with closing(space.open_audio(self.core.base_dir / file, readonly=self.core.readonly)) as c:
                    found = {int(r[0]) for r in c.execute("SELECT DISTINCT meeting_id FROM screen_chunks")}
            except sqlite3.Error as e:
                log.warning("não consegui varrer %s: %s", file, e)
                continue
            if found - known:
                out[file] = sorted(found - known)
        return out


__all__ = ["SCREEN_MAIN_VERSION", "ScreenChunkMeta", "ScreenDeleted", "ScreenRepo", "attach"]
