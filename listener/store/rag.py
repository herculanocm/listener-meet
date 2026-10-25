"""Base consultável (F5, v8): `rag_chunks` (trechos da reunião; `rag_fts` acompanha pelos gatilhos), `rag_vectors` (um vetor
float32 normalizado e a assinatura binária por trecho e modelo) e `rag_models` (a média `mu` de cada modelo). Só SQL: montar
trechos, comparar e calcular vetores e assinaturas é de `listener/rag/`. O índice é derivado e refeito (CP3: a reunião e a
transcrição nunca saem daqui); apagar a reunião leva trechos, FTS e vetores pela cascata. L3: a busca por palavra (FTS5 com
os filtros, `snippet()` com marcadores que `rag.fts` troca por negrito depois do `html.escape`), os ids que passam nos
filtros (máscara da busca por sentido) e as linhas dos trechos achados com a reunião."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Any, NamedTuple, TypedDict, cast

import numpy as np

from ..model import PIPELINE_JOB_KINDS, JobStatus
from .core import Core, _now

BATCH = 500                     # ids por instrução (`IN (...)` abaixo do limite de variáveis do SQLite)
SNIPPET_OPEN, SNIPPET_CLOSE = "\x02", "\x03"   # marcadores do `snippet()` (char(2), char(3)): negrito só depois do escape
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RagChunkRow(TypedDict, total=False):
    id: int
    meeting_id: int
    kind: str
    t_start_ms: int | None
    t_end_ms: int | None
    seg_first: int | None
    seg_last: int | None
    title: str | None
    speakers: str | None
    person_ids: str | None
    text: str
    text_hash: str
    job_id: int | None
    created_at: str


class RagModelRow(TypedDict, total=False):
    model: str
    dim: int
    mu: np.ndarray | None
    mu_n: int
    backfill: int                # 1 = confirmado para completar as reuniões antigas ("reindexar agora", R5-09)
    updated_at: str


@dataclass(frozen=True, slots=True)
class ChunkInsert:
    meeting_id: int
    kind: str
    t_start_ms: int | None
    t_end_ms: int | None
    seg_first: int | None
    seg_last: int | None
    title: str
    speakers: str
    person_ids: tuple[int, ...]
    text: str
    text_hash: str
    job_id: int | None


@dataclass(frozen=True, slots=True)
class ChunkMeta:
    """O que muda num trecho mantido pelo diff sem mexer no texto (e, portanto, sem tocar o FTS nem os vetores)."""
    chunk_id: int
    t_start_ms: int | None
    t_end_ms: int | None
    seg_first: int | None
    seg_last: int | None
    person_ids: tuple[int, ...]
    job_id: int | None


@dataclass(frozen=True, slots=True)
class VectorInsert:
    chunk_id: int
    text_hash: str                 # o vetor só entra se o trecho ainda existe com este hash (F5A-18)
    vector: bytes                  # float32 normalizado
    sig: bytes                     # sinal de v - mu, empacotado


class VectorVersion(NamedTuple):
    """Chave do cache das assinaturas de um modelo (`rag.search.SignatureCache`). Com o AUTOINCREMENT da v8 (R5-03) um id
    nunca volta: entrar vetor muda o maior id, sair muda a contagem e refazer o `mu` muda o `mu_n` (R6-10)."""
    size: int                    # quantos vetores do modelo
    max_id: int
    mu_n: int                    # muda quando o `mu` é refeito (todas as assinaturas mudam)


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Filtros da busca (F5 item 4), aplicados nas duas listas antes do top-k: pessoas (qualquer uma delas no trecho, por
    `json_each` em `person_ids`), período pela data da reunião (`AAAA-MM-DD`, inclusivo nas duas pontas), reuniões e tipos
    de trecho (`ChunkKind`). Vazio = sem filtro."""
    person_ids: tuple[int, ...] = ()
    date_from: str | None = None
    date_to: str | None = None
    meeting_ids: tuple[int, ...] = ()
    kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for d in (self.date_from, self.date_to):
            if d is not None and not DATE_RE.match(d):
                raise ValueError(f"data do filtro fora do formato AAAA-MM-DD: {d!r}")

    @property
    def active(self) -> bool:
        return bool(self.person_ids or self.date_from or self.date_to or self.meeting_ids or self.kinds)

    def sql(self) -> tuple[str, tuple[Any, ...], bool]:
        """(" AND ..." sobre `c` = `rag_chunks` e `m` = `meetings`, parâmetros, precisa do JOIN com `meetings`)."""
        parts: list[str] = []
        params: list[Any] = []
        if self.meeting_ids:
            parts.append(f"c.meeting_id IN ({','.join('?' * len(self.meeting_ids))})")
            params += [int(i) for i in self.meeting_ids]
        if self.kinds:
            parts.append(f"c.kind IN ({','.join('?' * len(self.kinds))})")
            params += [str(k) for k in self.kinds]
        if self.person_ids:
            marks = ",".join("?" * len(self.person_ids))
            parts.append(f"EXISTS (SELECT 1 FROM json_each(c.person_ids) p WHERE p.value IN ({marks}))")  # noqa: S608
            params += [int(i) for i in self.person_ids]
        if self.date_from:
            parts.append("substr(m.started_at, 1, 10) >= ?")
            params.append(self.date_from)
        if self.date_to:
            parts.append("substr(m.started_at, 1, 10) <= ?")
            params.append(self.date_to)
        return "".join(f" AND {p}" for p in parts), tuple(params), bool(self.date_from or self.date_to)


class FtsHit(NamedTuple):
    chunk_id: int
    rank: float                  # bm25 com pesos (menor = melhor)
    snippet: str | None          # texto com SNIPPET_OPEN/SNIPPET_CLOSE em volta dos termos; None sem `snippet_tokens`


def person_ids_json(ids: Iterable[int]) -> str:
    return json.dumps(sorted({int(i) for i in ids}))


def blob_to_vec(blob: bytes | memoryview | None) -> np.ndarray | None:
    return None if blob is None else np.frombuffer(bytes(blob), np.float32).copy()


class RagRepo:
    def __init__(self, core: Core):
        self.core = core

    # ---------------------------------------------------------------- trechos
    def list_chunks(self, meeting_id: int) -> list[RagChunkRow]:
        rows = self.core.q("SELECT * FROM rag_chunks WHERE meeting_id = ? ORDER BY id", (meeting_id,))
        return [cast(RagChunkRow, dict(r)) for r in rows]

    def get_chunk(self, chunk_id: int) -> RagChunkRow | None:
        rows = self.core.q("SELECT * FROM rag_chunks WHERE id = ?", (chunk_id,))
        return cast(RagChunkRow, dict(rows[0])) if rows else None

    def insert_chunks(self, rows: Sequence[ChunkInsert]) -> list[int]:
        """Na transação de quem chama (o `sync_text` faz ler, comparar e gravar numa só); o gatilho alimenta o FTS."""
        now = _now()
        return [self.core.insert(
            "INSERT INTO rag_chunks(meeting_id, kind, t_start_ms, t_end_ms, seg_first, seg_last, title, speakers, person_ids, text, "
            "text_hash, job_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.meeting_id, c.kind, c.t_start_ms, c.t_end_ms, c.seg_first, c.seg_last, c.title, c.speakers, person_ids_json(c.person_ids),
             c.text, c.text_hash, c.job_id, now)) for c in rows]

    def update_meta(self, rows: Sequence[ChunkMeta]) -> int:
        """Tempos, segmentos, pessoas e job novos dos trechos mantidos; o gatilho do FTS só dispara em título, falantes e texto."""
        self.core.conn().executemany(
            "UPDATE rag_chunks SET t_start_ms = ?, t_end_ms = ?, seg_first = ?, seg_last = ?, person_ids = ?, job_id = ? WHERE id = ?",
            [(m.t_start_ms, m.t_end_ms, m.seg_first, m.seg_last, person_ids_json(m.person_ids), m.job_id, m.chunk_id) for m in rows])
        return len(rows)

    def delete_chunks(self, ids: Iterable[int]) -> int:
        n = 0
        for part in batched((int(i) for i in ids), BATCH):
            marks = ",".join("?" * len(part))
            n += self.core.execute(f"DELETE FROM rag_chunks WHERE id IN ({marks})", part)  # noqa: S608
        return n

    def count_chunks(self, meeting_id: int | None = None) -> int:
        if meeting_id is None:
            return int(self.core.q("SELECT COUNT(*) FROM rag_chunks")[0][0])
        return int(self.core.q("SELECT COUNT(*) FROM rag_chunks WHERE meeting_id = ?", (meeting_id,))[0][0])

    def chunks_by_kind(self, meeting_id: int) -> dict[str, int]:
        rows = self.core.q("SELECT kind, COUNT(*) FROM rag_chunks WHERE meeting_id = ? GROUP BY kind ORDER BY kind", (meeting_id,))
        return {str(r[0]): int(r[1]) for r in rows}

    # ---------------------------------------------------------------- vetores
    def missing_vectors(self, meeting_id: int, model: str) -> list[RagChunkRow]:
        """Trechos da reunião sem vetor do modelo (o `index` só pede estes)."""
        rows = self.core.q("SELECT c.* FROM rag_chunks c WHERE c.meeting_id = ? AND NOT EXISTS "
                           "(SELECT 1 FROM rag_vectors v WHERE v.chunk_id = c.id AND v.model = ?) ORDER BY c.id", (meeting_id, model))
        return [cast(RagChunkRow, dict(r)) for r in rows]

    def count_missing(self, meeting_id: int, model: str) -> int:
        return int(self.core.q("SELECT COUNT(*) FROM rag_chunks c WHERE c.meeting_id = ? AND NOT EXISTS "
                               "(SELECT 1 FROM rag_vectors v WHERE v.chunk_id = c.id AND v.model = ?)", (meeting_id, model))[0][0])

    def insert_vectors(self, model: str, dim: int, rows: Sequence[VectorInsert]) -> int:
        """Grava os vetores de um lote numa transação; o que mudou (ou saiu) no meio do caminho fica de fora."""
        n = 0
        now = _now()
        with self.core.transaction() as c:
            for r in rows:
                n += c.execute("INSERT OR IGNORE INTO rag_vectors(chunk_id, model, dim, vector, sig, created_at) "
                               "SELECT ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM rag_chunks WHERE id = ? AND text_hash = ?)",
                               (r.chunk_id, model, int(dim), r.vector, r.sig, now, r.chunk_id, r.text_hash)).rowcount
        return n

    def count_vectors(self, model: str | None = None, meeting_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM rag_vectors v"
        params: list[Any] = []
        where: list[str] = []
        if meeting_id is not None:
            sql += " JOIN rag_chunks c ON c.id = v.chunk_id"
            where.append("c.meeting_id = ?")
            params.append(meeting_id)
        if model is not None:
            where.append("v.model = ?")
            params.append(model)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return int(self.core.q(sql, tuple(params))[0][0])

    def vectors_by_model(self, model: str) -> tuple[np.ndarray, np.ndarray]:
        """(ids de `rag_vectors`, matriz float32) de todos os vetores do modelo, em ordem de id: recalcular o `mu`."""
        rows = self.core.q("SELECT id, vector FROM rag_vectors WHERE model = ? ORDER BY id", (model,))
        ids = np.fromiter((int(r[0]) for r in rows), np.int64, len(rows))
        if not rows:
            return ids, np.zeros((0, 0), np.float32)
        return ids, np.frombuffer(b"".join(bytes(r[1]) for r in rows), np.float32).reshape(len(rows), -1).copy()

    def vectors_by_ids(self, ids: Sequence[int]) -> dict[int, np.ndarray]:
        """Vetores float32 por id de `rag_vectors` (a reordenação exata da busca lê só os candidatos)."""
        out: dict[int, np.ndarray] = {}
        for part in batched((int(i) for i in ids), BATCH):
            marks = ",".join("?" * len(part))
            for r in self.core.q(f"SELECT id, vector FROM rag_vectors WHERE id IN ({marks})", part):  # noqa: S608
                out[int(r[0])] = np.frombuffer(bytes(r[1]), np.float32)
        return out

    def candidate_vectors(self, ids: Sequence[int]) -> dict[int, tuple[int, np.ndarray]]:
        """(trecho, vetor float32) por id de `rag_vectors`: a reordenação da busca lê só os candidatos e confere que o id ainda
        é do mesmo trecho que o cache das assinaturas conhece (o id pode ter sido reaproveitado no meio da busca)."""
        out: dict[int, tuple[int, np.ndarray]] = {}
        for part in batched((int(i) for i in ids), BATCH):
            marks = ",".join("?" * len(part))
            for r in self.core.q(f"SELECT id, chunk_id, vector FROM rag_vectors WHERE id IN ({marks})", part):  # noqa: S608
                out[int(r[0])] = (int(r[1]), np.frombuffer(bytes(r[2]), np.float32))
        return out

    def signatures(self, model: str, after_id: int = 0, limit: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(ids de `rag_vectors`, ids dos trechos, assinaturas uint8 (n, bytes)) do modelo depois de `after_id`, em ordem de
        id: o cache da busca carrega em lotes e, depois, só o que entrou."""
        sql = "SELECT id, chunk_id, sig FROM rag_vectors WHERE model = ? AND id > ? ORDER BY id"
        params: tuple[Any, ...] = (model, int(after_id))
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, int(limit))
        rows = self.core.q(sql, params)
        ids = np.fromiter((int(r[0]) for r in rows), np.int64, len(rows))
        chunk_ids = np.fromiter((int(r[1]) for r in rows), np.int64, len(rows))
        if not rows:
            return ids, chunk_ids, np.zeros((0, 0), np.uint8)
        sigs = np.frombuffer(b"".join(bytes(r[2]) for r in rows), np.uint8).reshape(len(rows), -1).copy()
        return ids, chunk_ids, sigs

    def vector_version(self, model: str) -> VectorVersion:
        """Chave do cache das assinaturas do modelo: (quantos, maior id, `mu_n`). Muda quando entra ou sai vetor (o leve grava
        em outro processo) e quando o `mu` é refeito (todas as assinaturas mudam); o AUTOINCREMENT da v8 garante que um id
        não volta com outro vetor (R5-03). Só o índice (model, id) e uma busca por chave: barato a cada busca."""
        row = self.core.q("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE((SELECT mu_n FROM rag_models WHERE model = ?), 0) "
                          "FROM rag_vectors WHERE model = ?", (model, model))[0]
        return VectorVersion(int(row[0]), int(row[1]), int(row[2]))

    def set_signatures(self, rows: Sequence[tuple[int, bytes]]) -> int:
        """Regrava assinaturas (id de `rag_vectors`, sig) na transação de quem chama (o `mu` refeito vale para todas)."""
        self.core.conn().executemany("UPDATE rag_vectors SET sig = ? WHERE id = ?", [(sig, int(vid)) for vid, sig in rows])
        return len(rows)

    # ---------------------------------------------------------------- modelos (média por modelo)
    def get_model(self, model: str) -> RagModelRow | None:
        rows = self.core.q("SELECT * FROM rag_models WHERE model = ?", (model,))
        if not rows:
            return None
        d = dict(rows[0])
        d["mu"] = blob_to_vec(d.get("mu"))
        return cast(RagModelRow, d)

    def set_model(self, model: str, dim: int, mu: np.ndarray, mu_n: int) -> None:
        self.core.execute("INSERT INTO rag_models(model, dim, mu, mu_n, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(model) DO UPDATE "
                          "SET dim = excluded.dim, mu = excluded.mu, mu_n = excluded.mu_n, updated_at = excluded.updated_at",
                          (model, int(dim), np.asarray(mu, np.float32).tobytes(), int(mu_n), _now()))

    def mark_backfill(self, model: str, dim: int) -> None:
        """"Reindexar agora" (L5): o modelo passa a ser completado pela varredura nas reuniões antigas (R5-09). Cria a linha
        sem `mu` quando o modelo ainda não tem vetor; nunca mexe no `mu`."""
        self.core.execute("INSERT INTO rag_models(model, dim, mu_n, backfill, updated_at) VALUES (?, ?, 0, 1, ?) ON CONFLICT(model) "
                          "DO UPDATE SET backfill = 1, updated_at = excluded.updated_at", (model, int(dim), _now()))

    def model_confirmed(self, model: str) -> bool:
        """A varredura pode completar este modelo nas reuniões antigas? Sim quando ele é o primeiro do índice (nenhum vetor de
        outro modelo: a primeira abertura depois da F5, padrão da Q1) ou foi confirmado pelo "reindexar agora" (R5-09). As
        duas buscas por intervalo usam o índice (model, id)."""
        row = self.core.q("SELECT EXISTS (SELECT 1 FROM rag_vectors WHERE model < ?) OR EXISTS (SELECT 1 FROM rag_vectors "
                          "WHERE model > ?), COALESCE((SELECT backfill FROM rag_models WHERE model = ?), 0)", (model, model, model))[0]
        return not bool(row[0]) or bool(row[1])

    def list_models(self) -> list[RagModelRow]:
        out: list[RagModelRow] = []
        for r in self.core.q("SELECT * FROM rag_models ORDER BY model"):
            d = dict(r)
            d["mu"] = blob_to_vec(d.get("mu"))
            out.append(cast(RagModelRow, d))
        return out

    # ---------------------------------------------------------------- cobertura e varredura
    def meetings_without_chunks(self) -> list[int]:
        """Reuniões com pipeline concluído (`meeting` ou `reprocess`) e nenhum trecho: a varredura da abertura (F5A-12). O
        `NOT EXISTS` dos trechos vem antes (a maioria das reuniões tem trecho) e o job usa o índice por reunião (R5-02)."""
        rows = self.core.q("SELECT m.id FROM meetings m WHERE NOT EXISTS (SELECT 1 FROM rag_chunks c WHERE c.meeting_id = m.id) "
                           "AND EXISTS (SELECT 1 FROM processing_jobs j WHERE j.meeting_id = m.id AND j.kind IN (?, ?) AND j.status = ?) "
                           "ORDER BY m.id", (*PIPELINE_JOB_KINDS, JobStatus.DONE))
        return [int(r[0]) for r in rows]

    def processed_meetings(self) -> list[int]:
        """Reuniões com pipeline concluído (as que o "reindexar agora" põe na fila)."""
        rows = self.core.q("SELECT m.id FROM meetings m WHERE EXISTS (SELECT 1 FROM processing_jobs j WHERE j.meeting_id = m.id "
                           "AND j.kind IN (?, ?) AND j.status = ?) ORDER BY m.id", (*PIPELINE_JOB_KINDS, JobStatus.DONE))
        return [int(r[0]) for r in rows]

    def meetings_missing_vectors(self, model: str) -> list[int]:
        """Reuniões com algum trecho sem vetor do modelo (a varredura só pede com o embedder pronto)."""
        rows = self.core.q("SELECT DISTINCT c.meeting_id FROM rag_chunks c WHERE NOT EXISTS "
                           "(SELECT 1 FROM rag_vectors v WHERE v.chunk_id = c.id AND v.model = ?) ORDER BY c.meeting_id", (model,))
        return [int(r[0]) for r in rows]

    def coverage(self, model: str | None = None) -> dict[str, int]:
        """Totais do índice: trechos, reuniões com trechos e, com o modelo, vetores e trechos sem vetor dele."""
        row = self.core.q("SELECT COUNT(*), COUNT(DISTINCT meeting_id) FROM rag_chunks")[0]
        out = {"chunks": int(row[0]), "meetings": int(row[1])}
        if model is not None:
            out["vectors"] = self.count_vectors(model)
            out["missing"] = int(self.core.q("SELECT COUNT(*) FROM rag_chunks c WHERE NOT EXISTS "
                                             "(SELECT 1 FROM rag_vectors v WHERE v.chunk_id = c.id AND v.model = ?)", (model,))[0][0])
        return out

    # ---------------------------------------------------------------- busca (L3)
    def fts_hits(self, match: str, limit: int, filters: SearchFilters | None = None, *, snippet_tokens: int = 16) -> list[FtsHit]:
        """Trechos que casam a expressão FTS5 (montada e segura em `rag.fts`; o usuário nunca escreve a sintaxe), do melhor
        para o pior pelo `rank` (bm25 com pesos 3, 2, 1 em título, falantes e texto), com os filtros antes do `LIMIT`.
        `snippet()` só da coluna do texto, com até `snippet_tokens` palavras (0 = sem trecho destacado: o chat não usa)."""
        where, params, meetings = (filters or SearchFilters()).sql()
        snippet = (f"snippet(rag_fts, 2, char(2), char(3), '…', {max(1, min(64, int(snippet_tokens)))})" if snippet_tokens > 0
                   else "NULL")
        join = ""
        if where:
            join = " JOIN rag_chunks c ON c.id = rag_fts.rowid" + (" JOIN meetings m ON m.id = c.meeting_id" if meetings else "")
        rows = self.core.q(f"SELECT rag_fts.rowid, rag_fts.rank, {snippet} FROM rag_fts{join} WHERE rag_fts MATCH ?{where} "  # noqa: S608
                           "ORDER BY rag_fts.rank LIMIT ?", (match, *params, int(limit)))
        return [FtsHit(int(r[0]), float(r[1]), r[2]) for r in rows]

    def chunk_ids_where(self, filters: SearchFilters) -> np.ndarray:
        """Ids dos trechos que passam nos filtros (a máscara da busca por sentido, antes dos candidatos)."""
        where, params, meetings = filters.sql()
        join = " JOIN meetings m ON m.id = c.meeting_id" if meetings else ""
        rows = self.core.q(f"SELECT c.id FROM rag_chunks c{join} WHERE 1 = 1{where}", params)  # noqa: S608
        return np.fromiter((int(r[0]) for r in rows), np.int64, len(rows))

    def hit_rows(self, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """Os trechos achados com a reunião (título atual do usuário ou da LLM, `started_at`), por id; os que saíram no meio
        do caminho ficam de fora."""
        out: dict[int, dict[str, Any]] = {}
        for part in batched((int(i) for i in ids), BATCH):
            marks = ",".join("?" * len(part))
            sql = ("SELECT c.id, c.meeting_id, c.kind, c.t_start_ms, c.t_end_ms, c.seg_first, c.seg_last, c.title, c.speakers, "  # noqa: S608
                   "c.person_ids, c.text, m.title AS meeting_title, m.title_llm AS meeting_title_llm, m.started_at AS started_at "
                   f"FROM rag_chunks c JOIN meetings m ON m.id = c.meeting_id WHERE c.id IN ({marks})")
            for r in self.core.q(sql, part):
                out[int(r["id"])] = dict(r)
        return out
