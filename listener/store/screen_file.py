"""F5.27 (D-113): os arquivos mensais da tela, `listener-screen-AAAA-MM.db`, no padrão dos de áudio (D-003, D-039): o mês de
`meetings.started_at`, páginas de 32 KB, `auto_vacuum` incremental desde a criação (apagar a tela devolve o espaço em passos,
`store/space.py`), WAL e `schema_migrations` próprio. A tabela `screen_chunks` guarda um MP4 autocontido por pedaço; não há FK
entre arquivos: os pedaços de reunião que não existe mais só são reportados (CP3).

Aqui ficam só os nomes, o esquema e a religação da v12 do main (sem importar o `core`, que importa este módulo); o anexo e o
repositório estão em `store/screen.py`."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from ..log import get_logger
from .schema import AUDIO_PAGE_SIZE

log = get_logger("store.screen")

SCREEN_FILE_PREFIX = "listener-screen-"
SCREEN_GLOB = f"{SCREEN_FILE_PREFIX}????-??.db"

# arquivo novo, antes da primeira tabela: os blobs de ~2 MB pedem as páginas grandes do áudio (M-19) e o `auto_vacuum`
# incremental (A-38), sem migração depois
SCREEN_NEW_FILE: tuple[str, ...] = (f"PRAGMA {{s}}.page_size = {AUDIO_PAGE_SIZE}", "PRAGMA {s}.auto_vacuum = INCREMENTAL")

# v1: um pedaço por linha (o quadro fixo do pedaço em `width` x `height`, o `codec` que abriu e o MP4 que começa num quadro-chave);
# o UNIQUE já é o índice das leituras por reunião
SCREEN_V1: tuple[str, ...] = (
    ("CREATE TABLE IF NOT EXISTS {s}.screen_chunks ("
     "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, idx INTEGER NOT NULL, t_start_ms INTEGER NOT NULL,"
     "  t_end_ms INTEGER NOT NULL, frames INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, codec TEXT NOT NULL,"
     "  mp4 BLOB NOT NULL, UNIQUE (meeting_id, idx))"),
)
SCREEN_MIGRATIONS: list[tuple[int, tuple[str, ...]]] = [(1, SCREEN_V1)]
SCREEN_SCHEMA_VERSION = SCREEN_MIGRATIONS[-1][0]


def screen_file_for(started_at: str) -> str:
    """Nome do arquivo da tela do mês de uma data ISO: 2026-11-20T... -> listener-screen-2026-11.db."""
    month = datetime.fromisoformat(started_at).strftime("%Y-%m")
    return f"{SCREEN_FILE_PREFIX}{month}.db"


def is_screen_file(file_name: str) -> bool:
    return file_name.startswith(SCREEN_FILE_PREFIX)


def screen_schema_for(file_name: str) -> str:
    """Nome do schema anexado: listener-screen-2026-11.db -> `screen_2026_11` (validado: vai para o SQL como nome)."""
    stem = file_name.removeprefix(SCREEN_FILE_PREFIX).removesuffix(".db").replace("-", "_")
    if not is_screen_file(file_name) or not stem.replace("_", "").isdigit():
        raise ValueError(f"nome de arquivo da tela inesperado: {file_name}")
    return f"screen_{stem}"


def screen_files(base_dir: Path) -> list[str]:
    """Os arquivos da tela presentes na pasta, em ordem."""
    return sorted(p.name for p in base_dir.glob(SCREEN_GLOB))


def relink_meetings(conn: sqlite3.Connection, schema: str, base_dir: Path) -> int:
    """A v12 do main (ida): as reuniões sem `screen_file` cujos pedaços estão num arquivo da pasta voltam a apontar para ele
    (a volta da v12 apaga a coluna e deixa os arquivos, CP3; numa pasta sem arquivos da tela, nada a fazer). Cada arquivo é
    lido numa conexão própria; o que não abre fica de fora com o aviso (os pedaços dele seguem órfãos, só reportados)."""
    from .space import open_audio

    n = 0
    for file in screen_files(base_dir):
        try:
            with closing(open_audio(base_dir / file)) as c:
                ids = [int(r[0]) for r in c.execute("SELECT DISTINCT meeting_id FROM screen_chunks")]
        except sqlite3.Error as e:
            log.warning("v12: não consegui ler %s para religar as reuniões: %s", file, e)
            continue
        for mid in ids:
            n += conn.execute(f"UPDATE {schema}.meetings SET screen_file = ? WHERE id = ? AND screen_file IS NULL",  # noqa: S608
                              (file, mid)).rowcount
    if n:
        log.info("v12: %d reunião(ões) religada(s) ao arquivo da tela", n)
    return n


__all__ = ["SCREEN_FILE_PREFIX", "SCREEN_MIGRATIONS", "SCREEN_NEW_FILE", "SCREEN_SCHEMA_VERSION", "is_screen_file", "relink_meetings",
           "screen_file_for", "screen_files", "screen_schema_for"]
