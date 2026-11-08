"""F5.10 (v9): a volta das migrações do arquivo principal, pela linha de comando e com o app fechado
(`python -m listener --downgrade <pasta do banco> 8`). Cada volta roda inteira num `BEGIN IMMEDIATE`: desfaz o que a
migração fez, apaga a linha dela em `schema_migrations` e põe o `user_version` na anterior; uma falha no meio desfaz tudo.
A v9, a v10 (F5.20) e a v11 (F5.21) têm volta (`MAIN_DOWNS`): a v8 para trás reconstrói tabelas e não volta. Abre o arquivo direto
(o `Store` do app migraria de novo); os arquivos de áudio não mudam. Apaga as colunas e as tabelas da versão e o que
estava nelas (os passos das respostas; as sugestões, as avaliações e os objetivos; as telas das sugestões): só por pedido do
usuário (CP3). O
log leva as versões, nunca o conteúdo."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..log import get_logger
from .core import BUSY_TIMEOUT_S, MAIN_FILE
from .schema import MAIN_SCHEMA_VERSION

log = get_logger("store.downgrade")

type Down = Callable[[sqlite3.Connection], None]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA main.table_info({table})")}


def _down_v9(conn: sqlite3.Connection) -> None:
    """A v9 de volta: sem `chat_messages.trace_json` (o `DROP COLUMN` só se ela existe; a coluna não tem índice)."""
    if "trace_json" in _columns(conn, "chat_messages"):
        conn.execute("ALTER TABLE main.chat_messages DROP COLUMN trace_json")


def _down_v10(conn: sqlite3.Connection) -> None:
    """A v10 de volta (F5.20): sem as sugestões ao vivo (as opções antes, pela FK) e sem `meetings.goal` (o `DROP COLUMN` só se
    ela existe; a coluna não tem índice). Apaga as sugestões, as avaliações e os objetivos: só a pedido (CP3)."""
    conn.execute("DROP TABLE IF EXISTS main.suggestion_options")
    conn.execute("DROP TABLE IF EXISTS main.suggestions")
    if "goal" in _columns(conn, "meetings"):
        conn.execute("ALTER TABLE main.meetings DROP COLUMN goal")


def _down_v11(conn: sqlite3.Connection) -> None:
    """A v11 de volta (F5.21): sem as telas das sugestões e sem as duas colunas (o `DROP COLUMN` só se elas existem; nenhuma
    tem índice). Apaga as miniaturas, as cópias e os resumos das telas: só a pedido (CP3)."""
    conn.execute("DROP TABLE IF EXISTS main.suggestion_screens")
    for table, column in (("suggestions", "screen_summary"), ("suggestion_options", "screen")):
        if column in _columns(conn, table):
            conn.execute(f"ALTER TABLE main.{table} DROP COLUMN {column}")


MAIN_DOWNS: dict[int, Down] = {9: _down_v9, 10: _down_v10, 11: _down_v11}


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM main.schema_migrations").fetchone()
    return int(row[0] or 0)


def _step(conn: sqlite3.Connection, version: int) -> None:
    """Uma volta inteira numa transação: o desfazer, a linha da versão e o `user_version`."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        MAIN_DOWNS[version](conn)
        conn.execute("DELETE FROM main.schema_migrations WHERE version = ?", (version,))
        conn.execute(f"PRAGMA main.user_version = {int(version) - 1}")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def downgrade(base_dir: str | Path, target: int) -> list[int]:
    """Volta o arquivo principal até a versão `target`; devolve as versões desfeitas, da mais nova para a mais velha (vazia
    quando já está nela ou abaixo). `ValueError` quando alguma versão no caminho não tem volta ou o banco é de um app mais
    novo; `FileNotFoundError` sem banco na pasta."""
    path = Path(base_dir) / MAIN_FILE
    if not path.exists():
        raise FileNotFoundError(f"não existe banco em {base_dir}")
    conn = sqlite3.connect(str(path), autocommit=True, timeout=BUSY_TIMEOUT_S)
    try:
        current = current_version(conn)
        if current > MAIN_SCHEMA_VERSION:
            raise ValueError(f"o banco está na versão {current} e este app conhece até a {MAIN_SCHEMA_VERSION}")
        steps = list(range(current, int(target), -1))
        missing = [v for v in steps if v not in MAIN_DOWNS]
        if missing:
            raise ValueError(f"a versão {missing[0]} não tem volta: o menor destino possível é {min(MAIN_DOWNS) - 1}")
        for version in steps:
            _step(conn, version)
            log.info("banco voltou da versão %d para a %d", version, version - 1)
        return steps
    finally:
        conn.close()


__all__ = ["MAIN_DOWNS", "current_version", "downgrade"]
