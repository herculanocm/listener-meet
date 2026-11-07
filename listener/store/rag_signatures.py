"""As assinaturas da busca por sentido como o cache do app as carrega (F5.18, PF-19; `store/rag.py`, que passava de 400
linhas): as linhas por modelo com a reunião e as pessoas de cada vetor (CSR) e o contador `rag.vector_version`, que todo
caminho que cria ou remove vetores, ou muda o que a busca guarda deles, incrementa."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import NamedTuple

import numpy as np

from .core import Core, _now

VECTOR_VERSION_KEY = "rag.vector_version"      # a chave de `settings` com o contador (a `SettingSpec` interna)


class SignatureRows(NamedTuple):
    """As assinaturas de um modelo com o que a busca por sentido precisa de cada vetor, na ordem dos ids (F5.18, PF-19)."""
    vids: np.ndarray             # ids de `rag_vectors` (int64)
    chunk_ids: np.ndarray        # o trecho de cada um (int64)
    sigs: np.ndarray             # uint8 (n, bytes)
    meeting_ids: np.ndarray      # a reunião de cada um (int64)
    offsets: np.ndarray          # int32 (n + 1): as pessoas do vetor i são `person_ids[offsets[i]:offsets[i + 1]]`
    person_ids: np.ndarray       # int32


def empty_signature_rows() -> SignatureRows:
    return SignatureRows(np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 0), np.uint8), np.zeros(0, np.int64),
                         np.zeros(1, np.int32), np.zeros(0, np.int32))


def bump_vector_version(core: Core) -> int:
    """F5.18 (PF-19): incrementa `rag.vector_version` na conexão desta thread (dentro da transação em curso, quando há uma) e
    devolve o valor novo; a chave ausente (um banco anterior) vale 0 e a primeira escrita a cria."""
    row = core.conn().execute(
        "INSERT INTO settings(key, value, scope, updated_at) VALUES (?, '1', 'now', ?) ON CONFLICT(key) DO UPDATE SET "
        "value = CAST(CAST(value AS INTEGER) + 1 AS TEXT), updated_at = excluded.updated_at RETURNING CAST(value AS INTEGER)",
        (VECTOR_VERSION_KEY, _now())).fetchone()
    return int(row[0])


def _parse_person_ids(raw: str | None) -> tuple[int, ...]:
    """Os inteiros de um `person_ids` ("[1, 7]", de `person_ids_json`); o que não é lista de inteiros fica de fora, como o
    `p.value IN (...)` do SQL, que só casa inteiro com inteiro."""
    try:
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return ()
    return tuple(int(v) for v in data if isinstance(v, int)) if isinstance(data, list) else ()


def people_csr(values: Iterable[str | None]) -> tuple[np.ndarray, np.ndarray]:
    """O `person_ids` de cada trecho como CSR: `offsets` (n + 1, int32) e `person_ids` (int32). As cadeias repetidas (as mesmas
    pessoas em todos os trechos de uma reunião) são lidas uma vez."""
    parsed: dict[str | None, tuple[int, ...]] = {}
    counts: list[int] = []
    flat: list[int] = []
    for raw in values:
        ids = parsed.get(raw)
        if ids is None:
            ids = parsed[raw] = _parse_person_ids(raw)
        counts.append(len(ids))
        flat.extend(ids)
    offsets = np.zeros(len(counts) + 1, np.int32)
    if counts:
        offsets[1:] = np.cumsum(counts)
    return offsets, np.asarray(flat, np.int32)


__all__ = ["VECTOR_VERSION_KEY", "SignatureRows", "bump_vector_version", "empty_signature_rows", "people_csr"]
