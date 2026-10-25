"""Fila de processamento (F2 item 2) e artefatos por janela (item 10).

Claim atômico numa só transação `BEGIN IMMEDIATE`: devolve a `pendente` (`attempts + 1`) todo job `rodando` ou `pausado`
cujo heartbeat venceu há mais de `STALE_S`; esgota (`erro`) quem já tentou `MAX_ATTEMPTS` vezes; pega o primeiro
`pendente` com `UPDATE ... RETURNING` se não houver job fresco em outro worker. Dois workers por acidente não corrompem
nada: só dividem CPU (R-12). Datas ISO com offset (v2) comparadas por `julianday()`, que entende o fuso.
`config_json` recebe o snapshot das `pipeline.*` no primeiro claim e fica ("o modelo vale para o próximo job", CP8).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, TypedDict, cast

from ..log import get_logger
from ..model import JobKind, JobStatus, JobStep
from .core import Core, _now, json_default

log = get_logger("store.jobs")

STALE_S = 60.0           # heartbeat mais velho que isto = worker morto
MAX_ATTEMPTS = 3
ACTIVE = (str(JobStatus.PENDING), str(JobStatus.RUNNING), str(JobStatus.PAUSED))
HELD = (str(JobStatus.RUNNING), str(JobStatus.PAUSED))      # um worker vivo está com o job


class JobRow(TypedDict, total=False):
    id: int
    meeting_id: int
    kind: str
    status: str
    step: str | None
    progress: float
    error: str | None
    attempts: int
    params_json: str | None
    config_json: str | None
    cancel_requested: int
    heartbeat_at: str | None
    worker_pid: int | None
    claimed_at: str | None
    started_at: str | None
    finished_at: str | None
    timings_json: str | None
    created_at: str


def _before(now: str, seconds: float) -> str:
    return (datetime.fromisoformat(now) - timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


class JobsRepo:
    def __init__(self, core: Core):
        self.core = core

    # ---------------------------------------------------------------- criar e ler
    def enqueue(self, meeting_id: int, kind: JobKind = JobKind.MEETING, params: dict[str, Any] | None = None) -> int:
        payload = json.dumps(params, ensure_ascii=False, default=json_default) if params else None
        return self.core.insert("INSERT INTO processing_jobs(meeting_id, kind, status, params_json, created_at) VALUES (?, ?, ?, ?, ?)",
                                (meeting_id, str(kind), JobStatus.PENDING, payload, _now()))

    def get(self, job_id: int) -> JobRow | None:
        rows = self.core.q("SELECT * FROM processing_jobs WHERE id = ?", (job_id,))
        return cast(JobRow, dict(rows[0])) if rows else None

    def list_jobs(self, limit: int = 50) -> list[JobRow]:
        """Ativos primeiro (na ordem da fila), depois os terminados mais recentes."""
        rows = self.core.q("SELECT * FROM processing_jobs ORDER BY (status NOT IN (?, ?, ?)), "
                           "CASE WHEN status IN (?, ?, ?) THEN id ELSE -id END LIMIT ?", (*ACTIVE, *ACTIVE, int(limit)))
        return [cast(JobRow, dict(r)) for r in rows]

    def active(self) -> list[JobRow]:
        rows = self.core.q("SELECT * FROM processing_jobs WHERE status IN (?, ?, ?) ORDER BY id", ACTIVE)
        return [cast(JobRow, dict(r)) for r in rows]

    def for_meeting(self, meeting_id: int) -> list[JobRow]:
        rows = self.core.q("SELECT * FROM processing_jobs WHERE meeting_id = ? ORDER BY id DESC", (meeting_id,))
        return [cast(JobRow, dict(r)) for r in rows]

    def has_pending(self) -> bool:
        return bool(self.core.q("SELECT 1 FROM processing_jobs WHERE status = ? LIMIT 1", (JobStatus.PENDING,)))

    def held_fresh(self, now: str | None = None, stale_s: float = STALE_S) -> list[JobRow]:
        """Jobs `rodando` ou `pausado` com heartbeat recente: há um worker vivo."""
        limit = _before(now or _now(), stale_s)
        rows = self.core.q("SELECT * FROM processing_jobs WHERE status IN (?, ?) AND heartbeat_at IS NOT NULL "
                           "AND julianday(heartbeat_at) >= julianday(?) ORDER BY id", (*HELD, limit))
        return [cast(JobRow, dict(r)) for r in rows]

    def summary(self) -> dict[str, Any]:
        """Para o tooltip da bandeja: quantos ativos, qual roda e a porcentagem."""
        active = self.active()
        running = next((j for j in active if j["status"] in HELD), None)
        return {"active": len(active), "pending": sum(1 for j in active if j["status"] == JobStatus.PENDING),
                "running": running, "errors": len(self.core.q("SELECT 1 FROM processing_jobs WHERE status = ?", (JobStatus.ERROR,)))}

    # ---------------------------------------------------------------- claim e ciclo do worker
    def claim(self, pid: int, config_json: str | None = None, now: str | None = None, stale_s: float = STALE_S,
              max_attempts: int = MAX_ATTEMPTS) -> JobRow | None:
        now = now or _now()
        limit = _before(now, stale_s)
        with self.core.transaction() as c:
            c.execute("UPDATE processing_jobs SET status = ?, attempts = attempts + 1, worker_pid = NULL, "
                      "error = COALESCE(error, 'worker parou sem concluir') "
                      "WHERE status IN (?, ?) AND (heartbeat_at IS NULL OR julianday(heartbeat_at) < julianday(?))",
                      (JobStatus.PENDING, *HELD, limit))
            c.execute("UPDATE processing_jobs SET status = ?, finished_at = ?, error = COALESCE(error, '') || ' (tentativas esgotadas)' "
                      "WHERE status = ? AND attempts >= ?", (JobStatus.ERROR, now, JobStatus.PENDING, int(max_attempts)))
            row = c.execute(
                "UPDATE processing_jobs SET status = ?, worker_pid = ?, claimed_at = ?, heartbeat_at = ?, "
                "started_at = COALESCE(started_at, ?), config_json = COALESCE(config_json, ?) "
                "WHERE id = (SELECT id FROM processing_jobs WHERE status = ? AND attempts < ? ORDER BY id LIMIT 1) "
                "AND NOT EXISTS (SELECT 1 FROM processing_jobs WHERE status IN (?, ?) AND heartbeat_at IS NOT NULL "
                "AND julianday(heartbeat_at) >= julianday(?)) RETURNING *",
                (JobStatus.RUNNING, int(pid), now, now, now, config_json, JobStatus.PENDING, int(max_attempts), *HELD, limit),
            ).fetchone()
        return cast(JobRow, dict(row)) if row is not None else None

    def heartbeat(self, job_id: int, now: str | None = None) -> None:
        self.core.execute("UPDATE processing_jobs SET heartbeat_at = ? WHERE id = ?", (now or _now(), job_id))

    def set_progress(self, job_id: int, step: JobStep | str | None, progress: float, timings: dict[str, Any] | None = None) -> None:
        if timings is None:
            self.core.execute("UPDATE processing_jobs SET step = ?, progress = ? WHERE id = ?",
                              (None if step is None else str(step), float(progress), job_id))
        else:
            self.core.execute("UPDATE processing_jobs SET step = ?, progress = ?, timings_json = ? WHERE id = ?",
                              (None if step is None else str(step), float(progress),
                               json.dumps(timings, ensure_ascii=False, default=json_default), job_id))

    def set_status(self, job_id: int, status: JobStatus, step: JobStep | str | None = None) -> None:
        """`rodando` <-> `pausado` enquanto o worker espera a lease; mantém o heartbeat vivo por fora."""
        self.core.execute("UPDATE processing_jobs SET status = ?, step = COALESCE(?, step) WHERE id = ?",
                          (status, None if step is None else str(step), job_id))

    def finish(self, job_id: int, status: JobStatus, error: str | None = None, timings: dict[str, Any] | None = None,
               count_attempt: bool = False) -> None:
        """Estado terminal. `count_attempt` só para exceções (erros permanentes não contam, item 11)."""
        timings_json = json.dumps(timings, ensure_ascii=False, default=json_default) if timings is not None else None
        self.core.execute("UPDATE processing_jobs SET status = ?, error = ?, finished_at = ?, worker_pid = NULL, "
                          "attempts = attempts + ?, timings_json = COALESCE(?, timings_json), "
                          "progress = CASE WHEN ? = ? THEN 1.0 ELSE progress END WHERE id = ?",
                          (status, error, _now(), 1 if count_attempt else 0, timings_json, status, JobStatus.DONE, job_id))

    def request_cancel(self, job_id: int) -> JobStatus | None:
        """Pendente cancela na hora; rodando ou pausado pede e o worker atende entre janelas (item 11)."""
        job = self.get(job_id)
        if job is None:
            return None
        if job["status"] == JobStatus.PENDING:
            self.core.execute("UPDATE processing_jobs SET status = ?, cancel_requested = 1, finished_at = ? WHERE id = ? AND status = ?",
                              (JobStatus.CANCELLED, _now(), job_id, JobStatus.PENDING))
            self.delete_artifacts(job_id)
            return JobStatus.CANCELLED
        if job["status"] in HELD:
            self.core.execute("UPDATE processing_jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            return JobStatus(job["status"])
        return JobStatus(job["status"])

    def cancel_requested(self, job_id: int) -> bool:
        rows = self.core.q("SELECT cancel_requested FROM processing_jobs WHERE id = ?", (job_id,))
        return bool(rows and rows[0][0])

    def retry(self, job_id: int) -> bool:
        """"Tentar de novo": erro ou cancelado volta a `pendente` com as tentativas zeradas; artefatos ficam (retomada)."""
        return self.core.execute("UPDATE processing_jobs SET status = ?, attempts = 0, error = NULL, cancel_requested = 0, "
                                 "finished_at = NULL, worker_pid = NULL WHERE id = ? AND status IN (?, ?)",
                                 (JobStatus.PENDING, job_id, JobStatus.ERROR, JobStatus.CANCELLED)) > 0

    # ---------------------------------------------------------------- artefatos (dado derivado; CP9: no SQLite)
    def put_artifact(self, job_id: int, step: JobStep | str, idx: int, data: bytes) -> None:
        self.core.execute("INSERT OR REPLACE INTO job_artifacts(job_id, step, idx, data, created_at) VALUES (?, ?, ?, ?, ?)",
                          (job_id, str(step), int(idx), data, _now()))

    def get_artifact(self, job_id: int, step: JobStep | str, idx: int) -> bytes | None:
        rows = self.core.q("SELECT data FROM job_artifacts WHERE job_id = ? AND step = ? AND idx = ?", (job_id, str(step), int(idx)))
        return bytes(rows[0][0]) if rows else None

    def artifact_indexes(self, job_id: int, step: JobStep | str) -> set[int]:
        return {int(r[0]) for r in self.core.q("SELECT idx FROM job_artifacts WHERE job_id = ? AND step = ?", (job_id, str(step)))}

    def artifacts(self, job_id: int, step: JobStep | str) -> list[tuple[int, bytes]]:
        rows = self.core.q("SELECT idx, data FROM job_artifacts WHERE job_id = ? AND step = ? ORDER BY idx", (job_id, str(step)))
        return [(int(r[0]), bytes(r[1])) for r in rows]

    def delete_artifacts(self, job_id: int, step: JobStep | str | None = None) -> int:
        if step is None:
            return self.core.execute("DELETE FROM job_artifacts WHERE job_id = ?", (job_id,))
        return self.core.execute("DELETE FROM job_artifacts WHERE job_id = ? AND step = ?", (job_id, str(step)))
