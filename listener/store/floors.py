"""F5.6 (A-34): o piso de id das tabelas cujo id outro dado guarda, em `settings` (CP9, sem migração, A-47), como o
`people.max_id` da F5.2 (A-07). `meetings.id` e `processing_jobs.id` são `INTEGER PRIMARY KEY` sem AUTOINCREMENT: apagada a
linha de maior id, o SQLite daria o mesmo id à próxima. Excluída a reunião mais recente, a próxima gravação herdaria o id que
segue em `voice_embeddings.meeting_id` (o vetor `automatico` de um perfil é um por reunião), no cache `_meeting_files` de
outro processo e nas citações do "Perguntar às reuniões"; o job, no runner que ainda o roda (o cancelamento, o progresso e
os artefatos cairiam no job novo) e em `llm_calls.job_id`. A exclusão grava o maior id que a tabela já teve; a inserção usa
um acima do maior entre a tabela e o piso, na mesma instrução."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .core import _now

if TYPE_CHECKING:
    from .core import Core

MEETINGS = ("meetings", "meetings.max_id")           # (tabela, chave do piso); chaves internas, fora do SPECS
JOBS = ("processing_jobs", "jobs.max_id")
# o maior id entre a tabela e o piso (a tabela é constante deste módulo; o parâmetro é a chave)
_TOP = "MAX(COALESCE((SELECT MAX(id) FROM {table}), 0), COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key = ?), 0))"


def next_id_sql(table: str) -> str:
    """O próximo id de `table` como expressão SQL com um parâmetro (a chave do piso), para o `VALUES` do INSERT."""
    return f"({_TOP.format(table=table)} + 1)"


def raise_floor(core: Core, table: str, key: str) -> int:
    """Antes de excluir: o piso passa a ser o maior id que `table` já teve; devolve esse id."""
    top = int(core.q(f"SELECT {_TOP.format(table=table)}", (key,))[0][0])
    core.execute("INSERT INTO settings(key, value, scope, updated_at) VALUES (?, ?, 'now', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (key, str(top), _now()))
    return top


__all__ = ["JOBS", "MEETINGS", "next_id_sql", "raise_floor"]
