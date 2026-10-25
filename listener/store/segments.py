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
from ..model import PIPELINE_JOB_KINDS, ClusterStatus, JobStatus, MeetingStatus
from .core import Core, _now
from .jobs import ACTIVE
from .profiles import from_blob, to_blob

log = get_logger("store.segments")

BATCH = 1000
CLUSTER_FIELDS = frozenset({"person_id", "status", "merged_into", "similarity", "margin", "speech_ms", "decision_source"})
# `llm_*` só entram por `set_llm_suggestion` (CP11: recusa em `auto`, `confirmado` e mesclado); nunca por `update_cluster`
LLM_SUGGESTION_ALLOWED = (str(ClusterStatus.AMBIGUOUS), str(ClusterStatus.UNNAMED))


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
    llm_person_id: int | None          # F3: sugestão por contexto (só em ambíguo e sem_nome)
    llm_name: str | None
    llm_confidence: float | None
    llm_evidence_json: str | None
    decision_source: str | None


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
        """Todos os lotes (`executemany` de até 1000) numa só transação: ou a transcrição inteira entra, ou nada (R2-03)."""
        n = 0
        with self.core.transaction() as c:
            for batch in batched(rows, BATCH):
                c.executemany(
                    "INSERT INTO segments(meeting_id, job_id, channel, t_start_ms, t_end_ms, text, attribution, source, cluster, "
                    "cluster_id, person_id, role_at_time, confidence, words_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(s.meeting_id, s.job_id, s.channel, int(s.t_start_ms), int(s.t_end_ms), s.text, s.attribution, s.source, s.cluster,
                      s.cluster_id, s.person_id, s.role_at_time, s.confidence, s.words_json) for s in batch])
                n += len(batch)
        return n

    def delete_segments(self, meeting_id: int, job_id: int) -> int:
        return self.core.execute("DELETE FROM segments WHERE meeting_id = ? AND job_id = ?", (meeting_id, job_id))

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

    def set_text_llm(self, meeting_id: int, job_id: int, rows: Iterable[tuple[int, str]]) -> int:
        """Transcrição revisada (F3 item 8): só `text_llm` dos segmentos do job pai; `text` original fica intacto (CP3).
        Lotes de até 1000 numa só transação por bloco."""
        n = 0
        with self.core.transaction() as c:
            for batch in batched(rows, BATCH):
                c.executemany("UPDATE segments SET text_llm = ? WHERE id = ? AND meeting_id = ? AND job_id = ?",
                              [(text, int(sid), meeting_id, job_id) for sid, text in batch])
                n += len(batch)
        return n

    def segments_without_text_llm(self, meeting_id: int, job_id: int) -> int:
        return int(self.core.q("SELECT COUNT(*) FROM segments WHERE meeting_id = ? AND job_id = ? AND text_llm IS NULL AND text != ''",
                               (meeting_id, job_id))[0][0])

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

    def set_llm_suggestion(self, cluster_id: int, person_id: int | None, name: str | None, confidence: float | None,
                           evidence_json: str | None) -> None:
        """Sugestão da LLM para uma voz pendente (F3 item 5). Único ponto de escrita de `llm_*`: recusa (`ValueError`) quando
        o cluster está `auto` ou `confirmado` ou foi mesclado (CP11: a LLM nunca toca uma identificação de alta confiança);
        `status` e `person_id` nunca mudam aqui."""
        c = self.get_cluster(cluster_id)
        if c is None:
            raise ValueError(f"cluster {cluster_id} não existe")
        if str(c["status"]) not in LLM_SUGGESTION_ALLOWED or c.get("merged_into") is not None:
            raise ValueError(f"cluster {cluster_id} está {c['status']}{' (mesclado)' if c.get('merged_into') is not None else ''}: "
                             "a LLM só sugere para vozes pendentes (CP11)")
        self.core.execute("UPDATE clusters SET llm_person_id = ?, llm_name = ?, llm_confidence = ?, llm_evidence_json = ? WHERE id = ?",
                          (person_id, name, confidence, evidence_json, cluster_id))

    def clear_llm_suggestions(self, meeting_id: int, job_id: int) -> int:
        """Só das vozes ainda pendentes (uma confirmada guarda o que foi sugerido: mede o G3)."""
        return self.core.execute("UPDATE clusters SET llm_person_id = NULL, llm_name = NULL, llm_confidence = NULL, "
                                 "llm_evidence_json = NULL WHERE meeting_id = ? AND job_id = ? AND status IN (?, ?) "
                                 "AND merged_into IS NULL", (meeting_id, job_id, *LLM_SUGGESTION_ALLOWED))

    def latest_job_id(self, meeting_id: int) -> int | None:
        """Maior `job_id` já gravado em clusters ou segmentos da reunião (fallback de `current_job_id`)."""
        row = self.core.q("SELECT MAX(job_id) FROM clusters WHERE meeting_id = ?", (meeting_id,))
        if row and row[0][0] is not None:
            return int(row[0][0])
        row = self.core.q("SELECT MAX(job_id) FROM segments WHERE meeting_id = ?", (meeting_id,))
        return int(row[0][0]) if row and row[0][0] is not None else None

    def current_job_id(self, meeting_id: int) -> int | None:
        """Job vigente da reunião (R2-17): o `concluído` mais recente; sem concluído, o ativo (pendente, rodando, pausado);
        sem nenhum, o maior já gravado. Revisão, leitura e trecho de 5 s usam o mesmo id. Só jobs que escrevem clusters e
        segmentos contam (`meeting`, `reprocess`): um job `llm` concluído depois não muda o vigente (F3A-04)."""
        row = self.core.q("SELECT id FROM processing_jobs WHERE meeting_id = ? AND status = ? AND kind IN (?, ?) ORDER BY id DESC LIMIT 1",
                          (meeting_id, JobStatus.DONE, *PIPELINE_JOB_KINDS))
        if row:
            return int(row[0][0])
        row = self.core.q("SELECT id FROM processing_jobs WHERE meeting_id = ? AND status IN (?, ?, ?) AND kind IN (?, ?) "
                          "ORDER BY id LIMIT 1", (meeting_id, *ACTIVE, *PIPELINE_JOB_KINDS))
        if row:
            return int(row[0][0])
        return self.latest_job_id(meeting_id)

    def delete_clusters(self, meeting_id: int, job_id: int) -> int:
        """Só para um job cancelado sem nenhuma decisão humana (R2-10): clusters são dado derivado do job."""
        return self.core.execute("DELETE FROM clusters WHERE meeting_id = ? AND job_id = ?", (meeting_id, job_id))

    # ---------------------------------------------------------------- troca atômica (reprocessar, item 12)
    def swap_job(self, meeting_id: int, new_job_id: int, status: MeetingStatus = MeetingStatus.PROCESSED,
                 downgrade_reviewed: bool = False) -> dict[str, int]:
        """Numa só transação: solta os `voice_embeddings` dos segmentos antigos (`segment_id = NULL`), apaga clusters e
        segmentos dos jobs anteriores (só linhas de job: `job_id` nulo fica, R2-22) e atualiza o status da reunião, sem
        rebaixar `revisada` salvo quando o job novo deixou vozes pendentes (R2-11). Só é chamado no último passo de um
        reprocessamento disparado pelo usuário (CP3)."""
        with self.core.transaction():
            freed = self.core.execute("UPDATE voice_embeddings SET segment_id = NULL WHERE segment_id IN "
                                      "(SELECT id FROM segments WHERE meeting_id = ? AND job_id IS NOT NULL AND job_id != ?)",
                                      (meeting_id, new_job_id))
            # F3 item 8: a transcrição revisada (a tarefa mais cara) passa ao segmento novo com o mesmo canal, tempos e texto
            # (o Whisper é determinístico para o mesmo modelo e janela [exp]); as ações religam pelo tempo mais próximo
            moved = self.core.execute(
                "UPDATE segments SET text_llm = (SELECT old.text_llm FROM segments old WHERE old.meeting_id = segments.meeting_id "
                "AND old.job_id IS NOT NULL AND old.job_id != ? AND old.channel IS segments.channel "
                "AND old.t_start_ms = segments.t_start_ms AND old.t_end_ms = segments.t_end_ms AND old.text IS segments.text "
                "AND old.text_llm IS NOT NULL LIMIT 1) WHERE meeting_id = ? AND job_id = ? AND text_llm IS NULL",
                (new_job_id, meeting_id, new_job_id))
            relinked = 0
            for a in self.core.q("SELECT id, t_ms FROM action_items WHERE meeting_id = ? AND t_ms IS NOT NULL", (meeting_id,)):
                near = self.core.q("SELECT id FROM segments WHERE meeting_id = ? AND job_id = ? ORDER BY ABS(t_start_ms - ?) LIMIT 1",
                                   (meeting_id, new_job_id, int(a["t_ms"])))
                if near:
                    relinked += self.core.execute("UPDATE action_items SET segment_id = ? WHERE id = ?", (int(near[0]["id"]), int(a["id"])))
            segs = self.core.execute("DELETE FROM segments WHERE meeting_id = ? AND job_id IS NOT NULL AND job_id != ?",
                                     (meeting_id, new_job_id))
            clus = self.core.execute("DELETE FROM clusters WHERE meeting_id = ? AND job_id IS NOT NULL AND job_id != ?",
                                     (meeting_id, new_job_id))
            self.core.execute("UPDATE meetings SET status = ? WHERE id = ? AND (status != ? OR ?)",
                              (status, meeting_id, MeetingStatus.REVIEWED, int(downgrade_reviewed)))
        return {"embeddings_freed": freed, "segments_deleted": segs, "clusters_deleted": clus, "text_llm_moved": moved,
                "actions_relinked": relinked}


DECIDED_WITH_PERSON = (str(ClusterStatus.AUTO), str(ClusterStatus.CONFIRMED))


def resolve_person(cluster: ClusterRow, by_id: dict[int, ClusterRow]) -> int | None:
    """Pessoa de um cluster seguindo `merged_into` (mesclas da revisão, D-F2-9), com guarda contra ciclos. Só vale quando o
    cluster final da cadeia está `auto` ou `confirmado`: em `sem_nome` e `ambíguo` o `person_id` é só sugestão (R2-07)."""
    seen: set[int] = set()
    cur: ClusterRow | None = cluster
    while cur is not None and cur["id"] not in seen:
        seen.add(cur["id"])
        nxt = cur.get("merged_into")
        if nxt is not None and int(nxt) in by_id and int(nxt) not in seen:
            cur = by_id[int(nxt)]
            continue
        pid = cur.get("person_id")
        return int(pid) if pid is not None and str(cur["status"]) in DECIDED_WITH_PERSON else None
    return None
