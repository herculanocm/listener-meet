"""Resultado do pipeline por reunião: `clusters` (vozes do canal R dentro de uma reunião, item 6) e `segments` (falas com
texto, tempo e pessoa, item 8). Os dois carregam `job_id`: reprocessar escreve os do job novo e troca no fim, numa só
transação (`swap_job`, item 12, CP3). Escritas em lotes de até 1000 linhas (`itertools.batched`, CP17)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import batched
from typing import Any, TypedDict, cast

import numpy as np

from ..log import get_logger
from ..model import ClusterStatus, MeetingStatus
from .core import Core, _now
from .profiles import from_blob, to_blob

log = get_logger("store.segments")

BATCH = 1000
CLUSTER_FIELDS = frozenset({"person_id", "status", "merged_into", "similarity", "margin", "speech_ms"})


class SegmentRow(TypedDict, total=False):
    id: int
    meeting_id: int
    channel: str | None
    t_start_ms: int
    t_end_ms: int
    text: str | None
    text_llm: str | None
    cluster: str | None
    person_id: int | None
    role_at_time: str | None
    confidence: float | None
    attribution: str | None
    source: str | None
    words_json: str | None
    cluster_id: int | None
    job_id: int | None


class ClusterRow(TypedDict, total=False):
    id: int
    meeting_id: int
    job_id: int
    channel: str
    cluster: str
    speech_ms: int
    centroid: np.ndarray
    model: str | None
    person_id: int | None
    similarity: float | None
    margin: float | None
    status: str
    merged_into: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class SegmentInsert:
    meeting_id: int
    job_id: int
    channel: str
    t_start_ms: int
    t_end_ms: int
    text: str
    attribution: str
    source: str
    cluster: str | None = None
    cluster_id: int | None = None
    person_id: int | None = None
    role_at_time: str | None = None
    confidence: float | None = None
    words_json: str | None = None


@dataclass(frozen=True, slots=True)
class ClusterInsert:
    meeting_id: int
    job_id: int
    channel: str
    cluster: str
    speech_ms: int
    centroid: np.ndarray | None
    model: str | None
    status: ClusterStatus
    person_id: int | None = None
    similarity: float | None = None
    margin: float | None = None


class SegmentsRepo:
    def __init__(self, core: Core):
        self.core = core

    # ---------------------------------------------------------------- segmentos
    def insert_segments(self, rows: Iterable[SegmentInsert]) -> int:
        n = 0
        for batch in batched(rows, BATCH):
            with self.core.transaction() as c:
                c.executemany(
                    "INSERT INTO segments(meeting_id, job_id, channel, t_start_ms, t_end_ms, text, attribution, source, cluster, "
                    "cluster_id, person_id, role_at_time, confidence, words_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(s.meeting_id, s.job_id, s.channel, int(s.t_start_ms), int(s.t_end_ms), s.text, s.attribution, s.source, s.cluster,
                      s.cluster_id, s.person_id, s.role_at_time, s.confidence, s.words_json) for s in batch])
            n += len(batch)
        return n

    def list_segments(self, meeting_id: int, job_id: int | None = None) -> list[SegmentRow]:
        if job_id is None:
            rows = self.core.q("SELECT * FROM segments WHERE meeting_id = ? ORDER BY t_start_ms, id", (meeting_id,))
        else:
            rows = self.core.q("SELECT * FROM segments WHERE meeting_id = ? AND job_id = ? ORDER BY t_start_ms, id", (meeting_id, job_id))
        return [cast(SegmentRow, dict(r)) for r in rows]

    def count_segments(self, meeting_id: int, job_id: int | None = None) -> int:
        if job_id is None:
            return int(self.core.q("SELECT COUNT(*) FROM segments WHERE meeting_id = ?", (meeting_id,))[0][0])
        return int(self.core.q("SELECT COUNT(*) FROM segments WHERE meeting_id = ? AND job_id = ?", (meeting_id, job_id))[0][0])

    def set_segments_person(self, meeting_id: int, cluster_id: int, person_id: int | None, role_at_time: str | None) -> int:
        """Confirmação na revisão: todas as falas do cluster ganham a pessoa (e o cargo do dia, D-016)."""
        return self.core.execute("UPDATE segments SET person_id = ?, role_at_time = ? WHERE meeting_id = ? AND cluster_id = ?",
                                 (person_id, role_at_time, meeting_id, cluster_id))

    # ---------------------------------------------------------------- clusters
    @staticmethod
    def _cluster(r: Any) -> ClusterRow:
        d = dict(r)
        d["centroid"] = from_blob(d.get("centroid"))
        return cast(ClusterRow, d)

    def insert_clusters(self, rows: Iterable[ClusterInsert]) -> dict[str, int]:
        """Insere e devolve rótulo local -> id (o alinhamento precisa dos ids)."""
        ids: dict[str, int] = {}
        with self.core.transaction():
            for c in rows:
                ids[c.cluster] = self.core.insert(
                    "INSERT INTO clusters(meeting_id, job_id, channel, cluster, speech_ms, centroid, model, person_id, similarity, margin, "
                    "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (c.meeting_id, c.job_id, c.channel, c.cluster, int(c.speech_ms), None if c.centroid is None else to_blob(c.centroid),
                     c.model, c.person_id, c.similarity, c.margin, str(c.status), _now()))
        return ids

    def list_clusters(self, meeting_id: int, job_id: int | None = None) -> list[ClusterRow]:
        if job_id is None:
            rows = self.core.q("SELECT * FROM clusters WHERE meeting_id = ? ORDER BY speech_ms DESC, id", (meeting_id,))
        else:
            rows = self.core.q("SELECT * FROM clusters WHERE meeting_id = ? AND job_id = ? ORDER BY speech_ms DESC, id",
                               (meeting_id, job_id))
        return [self._cluster(r) for r in rows]

    def get_cluster(self, cluster_id: int) -> ClusterRow | None:
        rows = self.core.q("SELECT * FROM clusters WHERE id = ?", (cluster_id,))
        return self._cluster(rows[0]) if rows else None

    def update_cluster(self, cluster_id: int, **fields: Any) -> None:
        bad = set(fields) - CLUSTER_FIELDS
        if bad:
            raise ValueError(f"campos de cluster desconhecidos: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = [str(v) if isinstance(v, ClusterStatus) else v for v in fields.values()]
        self.core.execute(f"UPDATE clusters SET {cols} WHERE id = ?", (*values, cluster_id))  # noqa: S608

    def latest_job_id(self, meeting_id: int) -> int | None:
        """Job cujos clusters e segmentos são os vigentes da reunião (o maior já gravado)."""
        row = self.core.q("SELECT MAX(job_id) FROM clusters WHERE meeting_id = ?", (meeting_id,))
        if row and row[0][0] is not None:
            return int(row[0][0])
        row = self.core.q("SELECT MAX(job_id) FROM segments WHERE meeting_id = ?", (meeting_id,))
        return int(row[0][0]) if row and row[0][0] is not None else None

    # ---------------------------------------------------------------- troca atômica (reprocessar, item 12)
    def swap_job(self, meeting_id: int, new_job_id: int, status: MeetingStatus = MeetingStatus.PROCESSED) -> dict[str, int]:
        """Numa só transação: solta os `voice_embeddings` dos segmentos antigos (`segment_id = NULL`), apaga clusters e
        segmentos dos jobs anteriores e atualiza o status da reunião. Só é chamado no último passo de um reprocessamento
        disparado pelo usuário (CP3)."""
        with self.core.transaction():
            freed = self.core.execute("UPDATE voice_embeddings SET segment_id = NULL WHERE segment_id IN "
                                      "(SELECT id FROM segments WHERE meeting_id = ? AND job_id IS NOT ?)", (meeting_id, new_job_id))
            segs = self.core.execute("DELETE FROM segments WHERE meeting_id = ? AND job_id IS NOT ?", (meeting_id, new_job_id))
            clus = self.core.execute("DELETE FROM clusters WHERE meeting_id = ? AND job_id IS NOT ?", (meeting_id, new_job_id))
            self.core.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))
        return {"embeddings_freed": freed, "segments_deleted": segs, "clusters_deleted": clus}


def resolve_person(cluster: ClusterRow, by_id: dict[int, ClusterRow]) -> int | None:
    """Pessoa de um cluster seguindo `merged_into` (mesclas da revisão, D-F2-9), com guarda contra ciclos."""
    seen: set[int] = set()
    cur: ClusterRow | None = cluster
    while cur is not None and cur["id"] not in seen:
        seen.add(cur["id"])
        nxt = cur.get("merged_into")
        if nxt is not None and int(nxt) in by_id and int(nxt) not in seen:
            cur = by_id[int(nxt)]
            continue
        pid = cur.get("person_id")
        return int(pid) if pid is not None else None
    return None
