"""Os fragmentos SQL e os auxiliares puros da fila (`store/jobs.py`, que passava de 400 linhas na F5.18): os campos de
`params_json` lidos pelo SQL, as datas relativas ao `_now()` e a cláusula da classe do job."""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ..model import JobKind

# campos de `params_json` lidos pelo SQL (só JSON válido; o resto vale nulo): o "não antes de" e o motivo do `index`
NOT_BEFORE_SQL = "julianday(CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.not_before') END)"
SWEEP_LAST_SQL = "COALESCE((CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.reason') END) = 'sweep', 0)"


def iso_before(now: str, seconds: float) -> str:
    return (datetime.fromisoformat(now) - timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def iso_after(now: str, seconds: float) -> str:
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def parse_params(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def kinds_clause(kinds: Iterable[JobKind | str] | None, negate: bool = False) -> tuple[str, tuple[str, ...]]:
    """Cláusula `AND kind IN (...)` (ou `NOT IN`, com `negate`) e seus parâmetros; vazia quando a classe não é filtrada
    (comportamento da F2)."""
    values = tuple(str(k) for k in kinds) if kinds is not None else ()
    if not values:
        return "", ()
    return f" AND kind {'NOT IN' if negate else 'IN'} ({','.join('?' * len(values))})", values


__all__ = ["NOT_BEFORE_SQL", "SWEEP_LAST_SQL", "iso_after", "iso_before", "kinds_clause", "parse_params"]
