"""Os filtros da busca por sentido em numpy (F5.18, PF-19): pessoa, período e reunião viram uma máscara sobre os vetores do
cache das assinaturas (`rag.search.SignatureCache`), antes dos candidatos por Hamming, sem varrer `rag_chunks` a cada busca
(o `json_each` do filtro de pessoa custava 22 ms a 60 mil trechos).

A paridade com o SQL de `RagRepo.chunk_ids_where` (a referência, o oráculo do teste de paridade) vale termo a termo: reunião
por `meeting_id IN (...)`; pessoa por "alguma das pessoas do trecho está entre as pedidas" (o `EXISTS` sobre o `json_each`
de `person_ids`, que só casa inteiro com inteiro); período pela comparação de texto do `substr(started_at, 1, 10)` com
`AAAA-MM-DD`, inclusiva nas duas pontas (as datas ficam em `S10`, e a comparação de bytes do numpy é a do SQLite para o
ASCII delas); a reunião sem data não passa em nenhum período, como no SQL."""
from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

from ..store.rag import SearchFilters


class VectorIndex(NamedTuple):
    """O que a máscara precisa de cada vetor do cache, na ordem dos vetores, mais as datas das reuniões."""
    meeting_ids: np.ndarray      # int64, por vetor
    offsets: np.ndarray          # int32 (n + 1): as pessoas do vetor i são `person_ids[offsets[i]:offsets[i + 1]]`
    person_ids: np.ndarray       # int32
    meeting_index: np.ndarray    # int64: os ids das reuniões com data
    meeting_dates: np.ndarray    # S10: a data (`AAAA-MM-DD`) de cada uma


def empty_index() -> VectorIndex:
    return VectorIndex(np.zeros(0, np.int64), np.zeros(1, np.int32), np.zeros(0, np.int32), np.zeros(0, np.int64), np.zeros(0, "S10"))


def vector_mask(filters: SearchFilters, idx: VectorIndex) -> np.ndarray | None:
    """A máscara (bool, uma posição por vetor) dos filtros ativos, E entre eles; None sem filtro."""
    if not filters.active:
        return None
    mask = np.ones(len(idx.meeting_ids), dtype=bool)
    if filters.meeting_ids:
        mask &= np.isin(idx.meeting_ids, np.asarray(filters.meeting_ids, np.int64))
    if filters.date_from or filters.date_to:
        allowed = meetings_between(idx.meeting_index, idx.meeting_dates, filters.date_from, filters.date_to)
        mask &= np.isin(idx.meeting_ids, allowed)
    if filters.person_ids:
        mask &= people_mask(idx.offsets, idx.person_ids, filters.person_ids)
    return mask


def meetings_between(ids: np.ndarray, dates: np.ndarray, date_from: str | None, date_to: str | None) -> np.ndarray:
    """Os ids das reuniões com `date_from <= data <= date_to` (cada ponta opcional), pela comparação de texto."""
    keep = np.ones(len(ids), dtype=bool)
    if date_from:
        keep &= dates >= date_from.encode("utf-8")
    if date_to:
        keep &= dates <= date_to.encode("utf-8")
    return ids[keep]


def people_mask(offsets: np.ndarray, person_ids: np.ndarray, wanted: Sequence[int]) -> np.ndarray:
    """Verdadeiro no vetor que tem alguma das pessoas pedidas (o `EXISTS ... IN (...)` do SQL)."""
    n = len(offsets) - 1
    mask = np.zeros(n, dtype=bool)
    if n <= 0 or not len(person_ids):
        return mask
    hit = np.isin(person_ids, np.asarray(list(wanted), np.int64))
    owner = np.repeat(np.arange(n, dtype=np.int64), np.diff(offsets))      # o vetor dono de cada entrada do CSR
    mask[owner[hit]] = True
    return mask


__all__ = ["VectorIndex", "empty_index", "meetings_between", "people_mask", "vector_mask"]
