"""Fila de processamento (F2 item 2) e artefatos por janela (item 10).

Claim atômico numa só transação `BEGIN IMMEDIATE`: devolve a `pendente` (`attempts + 1`) todo job `rodando` ou `pausado`
cujo heartbeat venceu há mais de `STALE_S`; esgota (`erro`) quem já tentou `MAX_ATTEMPTS` vezes; pega o primeiro
`pendente` com `UPDATE ... RETURNING` se não houver job fresco em outro worker. Dois workers por acidente não corrompem
nada: só dividem CPU (R-12). Datas ISO com offset (v2) comparadas por `julianday()`, que entende o fuso.
`config_json` recebe o snapshot das `pipeline.*` no primeiro claim e fica ("o modelo vale para o próximo job", CP8).
F3: duas classes de fila (`kinds`): o worker pesado pega `meeting`, `reprocess` e `embed_range`; o leve pega `llm`. O
`NOT EXISTS ... rodando fresco` vale por classe, logo um job `llm` não espera uma diarização de 2 h (F3A-03).
F5: o leve pega também o `index` (índice da busca), sempre depois de todo `llm` pendente, e os da varredura depois dos
outros (`ORDER BY (kind = 'index'), (motivo = 'sweep'), id`): a varredura da abertura enfileira dezenas e nem o `llm` nem o
`index` da reunião de hoje esperam por elas (F5A-02, R5-08). Um `index` pendente por reunião (`enqueue_index`, F5A-04), com
espera durável: "não antes de" em `params_json.not_before`, que o claim respeita e cada edição renova (R5-07); a fila e a
bandeja mostram os `index` agregados (`index_summary`, F5A-05).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
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
INDEX_REASONS = frozenset({"pipeline", "llm", "review", "edit", "sweep", "reindex"})   # `params_json.reason` do `index`
# campos de `params_json` lidos pelo SQL (só JSON válido; o resto vale nulo): o "não antes de" e o motivo do `index`
NOT_BEFORE_SQL = "julianday(CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.not_before') END)"
SWEEP_LAST_SQL = "COALESCE((CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.reason') END) = 'sweep', 0)"


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


def _after(now: str, seconds: float) -> str:
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _params(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _kinds(kinds: Iterable[JobKind | str] | None, negate: bool = False) -> tuple[str, tuple[str, ...]]:
    """Cláusula `AND kind IN (...)` (ou `NOT IN`, com `negate`) e seus parâmetros; vazia quando a classe não é filtrada
    (comportamento da F2)."""
    values = tuple(str(k) for k in kinds) if kinds is not None else ()
    if not values:
        return "", ()
    return f" AND kind {'NOT IN' if negate else 'IN'} ({','.join('?' * len(values))})", values


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

    def list_jobs(self, limit: int = 50, *, kinds: Iterable[JobKind | str] | None = None,
                  exclude_kinds: Iterable[JobKind | str] | None = None, statuses: Iterable[JobStatus | str] | None = None) -> list[JobRow]:
        """Ativos primeiro (na ordem da fila), depois os terminados mais recentes; `kinds`, `exclude_kinds` e `statuses` filtram
        (F5: a fila e a bandeja listam sem os `index`, que seriam dezenas depois da varredura)."""
        clause, params = _kinds(kinds)
        not_clause, not_params = _kinds(exclude_kinds, negate=True)
        wanted = tuple(str(x) for x in statuses) if statuses is not None else ()
        status_clause = f" AND status IN ({','.join('?' * len(wanted))})" if wanted else ""
        rows = self.core.q(f"SELECT * FROM processing_jobs WHERE 1 = 1{clause}{not_clause}{status_clause} "  # noqa: S608
                           "ORDER BY (status NOT IN (?, ?, ?)), CASE WHEN status IN (?, ?, ?) THEN id ELSE -id END LIMIT ?",
                           (*params, *not_params, *wanted, *ACTIVE, *ACTIVE, int(limit)))
        return [cast(JobRow, dict(r)) for r in rows]

    def enqueue_index(self, meeting_id: int, reason: str, *, force: bool = False, delay_s: float | None = None,
                      now: str | None = None) -> int | None:
        """Um `index` pendente por reunião (F5A-04), numa transação (dois processos não duplicam): devolve o id do job novo ou
        None quando já havia um pendente, que fica com a espera e o `force` deste pedido. O que está rodando não conta (pode
        ter lido o estado antigo). Espera durável (R5-07): `delay_s` > 0 põe "não antes de" agora + `delay_s` (cada edição
        renova); 0 tira a espera ("Concluir"); None não mexe na espera de quem já está na fila. `force` (o "reindexar" do
        usuário) pede os vetores mesmo com os vetores automáticos desligados."""
        if reason not in INDEX_REASONS:
            raise ValueError(f"motivo de indexação desconhecido: {reason!r}")
        now = now or _now()
        with self.core.transaction() as c:
            row = c.execute("SELECT id, params_json FROM processing_jobs WHERE meeting_id = ? AND kind = ? AND status = ? ORDER BY id "
                            "LIMIT 1", (meeting_id, JobKind.INDEX, JobStatus.PENDING)).fetchone()
            if row is None:
                params: dict[str, Any] = {"reason": reason}
                if force:
                    params["force"] = True
                if delay_s is not None and delay_s > 0:
                    params["not_before"] = _after(now, delay_s)
                new = c.execute("INSERT INTO processing_jobs(meeting_id, kind, status, params_json, created_at) VALUES (?, ?, ?, ?, ?) "
                                "RETURNING id", (meeting_id, JobKind.INDEX, JobStatus.PENDING, json.dumps(params, ensure_ascii=False),
                                                 now)).fetchone()
                return int(new[0])
            data = _params(row["params_json"])
            before = dict(data)
            if delay_s is not None:
                if delay_s > 0:
                    data["not_before"] = _after(now, delay_s)
                else:
                    data.pop("not_before", None)
            if force:
                data["force"] = True
            if data.get("reason") == "sweep" and reason != "sweep":
                data["reason"] = reason                           # um pedido da pessoa tira a reunião do fim da fila (R5-08)
            if data != before:
                c.execute("UPDATE processing_jobs SET params_json = ? WHERE id = ? AND status = ?",
                          (json.dumps(data, ensure_ascii=False), int(row["id"]), JobStatus.PENDING))
        return None

    def expedite_index(self, meeting_id: int) -> bool:
        """Tira a espera do `index` pendente da reunião (fechar a revisão); False quando não há pendente (nada a fazer: não
        cria job)."""
        return self.core.execute("UPDATE processing_jobs SET params_json = json_remove(params_json, '$.not_before') "
                                 "WHERE meeting_id = ? AND kind = ? AND status = ? AND json_valid(params_json) "
                                 "AND json_extract(params_json, '$.not_before') IS NOT NULL",
                                 (meeting_id, JobKind.INDEX, JobStatus.PENDING)) > 0

    def delayed(self, kinds: Iterable[JobKind | str] | None = None, now: str | None = None) -> int:
        """Pendentes da classe que ainda esperam o "não antes de": o worker leve não sai ocioso enquanto houver um (R5-07)."""
        clause, params = _kinds(kinds)
        return int(self.core.q(f"SELECT COUNT(*) FROM processing_jobs WHERE status = ?{clause} AND {NOT_BEFORE_SQL} > julianday(?)",  # noqa: S608
                               (JobStatus.PENDING, *params, now or _now()))[0][0])

    def failed_index_meetings(self) -> list[int]:
        """Reuniões cujo último `index` terminou em erro (a varredura da abertura tenta de novo, uma vez por abertura)."""
        rows = self.core.q("SELECT j.meeting_id FROM processing_jobs j WHERE j.kind = ? AND j.status = ? AND j.id = "
                           "(SELECT MAX(k.id) FROM processing_jobs k WHERE k.meeting_id = j.meeting_id AND k.kind = ?) "
                           "ORDER BY j.meeting_id", (JobKind.INDEX, JobStatus.ERROR, JobKind.INDEX))
        return [int(r[0]) for r in rows]

    def index_summary(self) -> dict[str, int]:
        """Jobs `index` agregados para a fila e a bandeja: pendentes, rodando (ou pausados), reuniões com algum ativo e
        reuniões cujo último `index` terminou em erro (os erros antigos já refeitos não contam)."""
        row = self.core.q(
            "SELECT COALESCE(SUM(status = ?), 0), COALESCE(SUM(status IN (?, ?)), 0), COUNT(DISTINCT CASE WHEN status IN (?, ?, ?) "
            "THEN meeting_id END) FROM processing_jobs WHERE kind = ?", (JobStatus.PENDING, *HELD, *ACTIVE, JobKind.INDEX))[0]
        errors = self.core.q("SELECT COUNT(*) FROM processing_jobs j WHERE j.kind = ? AND j.status = ? AND j.id = "
                             "(SELECT MAX(id) FROM processing_jobs k WHERE k.meeting_id = j.meeting_id AND k.kind = ?)",
                             (JobKind.INDEX, JobStatus.ERROR, JobKind.INDEX))[0][0]
        return {"pending": int(row[0]), "running": int(row[1]), "meetings": int(row[2]), "errors": int(errors)}

    def last_done(self, kind: JobKind | str, meeting_ids: Iterable[int]) -> dict[int, int]:
        """O id do último job concluído da espécie por reunião (o app recarrega a reunião aberta quando o `index` dela termina,
        sem varrer a fila inteira)."""
        ids = [int(i) for i in meeting_ids]
        if not ids:
            return {}
        rows = self.core.q(f"SELECT meeting_id, MAX(id) FROM processing_jobs WHERE kind = ? AND status = ? "  # noqa: S608
                           f"AND meeting_id IN ({','.join('?' * len(ids))}) GROUP BY meeting_id", (str(kind), JobStatus.DONE, *ids))
        return {int(r[0]): int(r[1]) for r in rows}

    def active(self, kinds: Iterable[JobKind | str] | None = None, *, due_only: bool = False, now: str | None = None) -> list[JobRow]:
        """Pendentes, rodando e pausados da classe; `due_only` deixa de fora os que esperam o "não antes de" (R5-07: o app
        não lança um worker só para esperar a edição assentar)."""
        clause, params = _kinds(kinds)
        due = f" AND NOT COALESCE({NOT_BEFORE_SQL} > julianday(?), 0)" if due_only else ""
        rows = self.core.q(f"SELECT * FROM processing_jobs WHERE status IN (?, ?, ?){clause}{due} ORDER BY id",  # noqa: S608
                           (*ACTIVE, *params, *((now or _now(),) if due_only else ())))
        return [cast(JobRow, dict(r)) for r in rows]

    def for_meeting(self, meeting_id: int) -> list[JobRow]:
        rows = self.core.q("SELECT * FROM processing_jobs WHERE meeting_id = ? ORDER BY id DESC", (meeting_id,))
        return [cast(JobRow, dict(r)) for r in rows]

    def has_pending(self, kinds: Iterable[JobKind | str] | None = None) -> bool:
        clause, params = _kinds(kinds)
        return bool(self.core.q(f"SELECT 1 FROM processing_jobs WHERE status = ?{clause} LIMIT 1", (JobStatus.PENDING, *params)))  # noqa: S608

    def held_fresh(self, now: str | None = None, stale_s: float = STALE_S, kinds: Iterable[JobKind | str] | None = None) -> list[JobRow]:
        """Jobs `rodando` ou `pausado` com heartbeat recente: há um worker vivo (da classe pedida, ou de qualquer uma)."""
        limit = _before(now or _now(), stale_s)
        clause, params = _kinds(kinds)
        rows = self.core.q(f"SELECT * FROM processing_jobs WHERE status IN (?, ?) AND heartbeat_at IS NOT NULL "  # noqa: S608
                           f"AND julianday(heartbeat_at) >= julianday(?){clause} ORDER BY id", (*HELD, limit, *params))
        return [cast(JobRow, dict(r)) for r in rows]

    def summary(self, exclude_kinds: Iterable[JobKind | str] | None = None) -> dict[str, Any]:
        """Para o tooltip da bandeja e o rodapé da fila: quantos ativos, qual roda e a porcentagem (sem os `exclude_kinds`)."""
        skip = {str(k) for k in exclude_kinds or ()}
        active = [j for j in self.active() if str(j.get("kind")) not in skip]
        running = next((j for j in active if j["status"] in HELD), None)
        clause, params = _kinds(exclude_kinds, negate=True)
        errors = self.core.q(f"SELECT COUNT(*) FROM processing_jobs WHERE status = ?{clause}", (JobStatus.ERROR, *params))[0][0]  # noqa: S608
        return {"active": len(active), "pending": sum(1 for j in active if j["status"] == JobStatus.PENDING),
                "running": running, "errors": int(errors)}

    # ---------------------------------------------------------------- claim e ciclo do worker
    def claim(self, pid: int, config_json: str | None = None, now: str | None = None, stale_s: float = STALE_S,
              max_attempts: int = MAX_ATTEMPTS, kinds: Iterable[JobKind | str] | None = None) -> JobRow | None:
        """Pega o primeiro `pendente` da classe (`kinds`; None = qualquer) se nenhum worker vivo segura um job dessa classe;
        os `index` vão depois de todos os outros pendentes, e os da varredura depois dos outros `index` (F5A-02, R5-08); job
        com "não antes de" no futuro espera (R5-07). A devolução dos jobs com heartbeat vencido e o esgotamento das
        tentativas valem para todas as classes."""
        now = now or _now()
        limit = _before(now, stale_s)
        clause, params = _kinds(kinds)
        with self.core.transaction() as c:
            c.execute("UPDATE processing_jobs SET status = ?, attempts = attempts + 1, worker_pid = NULL, "
                      "error = COALESCE(error, 'worker parou sem concluir') "
                      "WHERE status IN (?, ?) AND (heartbeat_at IS NULL OR julianday(heartbeat_at) < julianday(?))",
                      (JobStatus.PENDING, *HELD, limit))
            c.execute("UPDATE processing_jobs SET status = ?, finished_at = ?, error = COALESCE(error, '') || ' (tentativas esgotadas)' "
                      "WHERE status = ? AND attempts >= ?", (JobStatus.ERROR, now, JobStatus.PENDING, int(max_attempts)))
            row = c.execute(
                "UPDATE processing_jobs SET status = ?, worker_pid = ?, claimed_at = ?, heartbeat_at = ?, "  # noqa: S608
                "started_at = COALESCE(started_at, ?), config_json = COALESCE(config_json, ?) "
                f"WHERE id = (SELECT id FROM processing_jobs WHERE status = ? AND attempts < ?{clause} "
                f"AND NOT COALESCE({NOT_BEFORE_SQL} > julianday(?), 0) ORDER BY (kind = ?), {SWEEP_LAST_SQL}, id LIMIT 1) "
                "AND NOT EXISTS (SELECT 1 FROM processing_jobs WHERE status IN (?, ?) AND heartbeat_at IS NOT NULL "
                f"AND julianday(heartbeat_at) >= julianday(?){clause}) RETURNING *",
                (JobStatus.RUNNING, int(pid), now, now, now, config_json, JobStatus.PENDING, int(max_attempts), *params, now, JobKind.INDEX,
                 *HELD, limit, *params),
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
        """Pendente cancela na hora; rodando ou pausado pede e o worker atende entre janelas (item 11). Tudo numa transação,
        decidido pelo `rowcount`: um claim entre a leitura e a escrita não perde artefatos (R2-14)."""
        with self.core.transaction() as c:
            n = c.execute("UPDATE processing_jobs SET status = ?, cancel_requested = 1, finished_at = ? WHERE id = ? AND status = ?",
                          (JobStatus.CANCELLED, _now(), job_id, JobStatus.PENDING)).rowcount
            if n:
                c.execute("DELETE FROM job_artifacts WHERE job_id = ?", (job_id,))
                return JobStatus.CANCELLED
            c.execute("UPDATE processing_jobs SET cancel_requested = 1 WHERE id = ? AND status IN (?, ?)", (job_id, *HELD))
            row = c.execute("SELECT status FROM processing_jobs WHERE id = ?", (job_id,)).fetchone()
        return JobStatus(str(row[0])) if row is not None else None

    def cancel_requested(self, job_id: int) -> bool:
        rows = self.core.q("SELECT cancel_requested FROM processing_jobs WHERE id = ?", (job_id,))
        return bool(rows and rows[0][0])

    def retry(self, job_id: int, reset_config: bool = False) -> bool:
        """"Tentar de novo": erro ou cancelado volta a `pendente` com as tentativas zeradas; artefatos ficam (retomada).
        `reset_config` descarta o snapshot (`config_json`): o job `llm` passa a usar o provedor ativo (F3 item 15)."""
        return self.core.execute("UPDATE processing_jobs SET status = ?, attempts = 0, error = NULL, cancel_requested = 0, "
                                 "finished_at = NULL, worker_pid = NULL, config_json = CASE WHEN ? THEN NULL ELSE config_json END "
                                 "WHERE id = ? AND status IN (?, ?)",
                                 (JobStatus.PENDING, int(bool(reset_config)), job_id, JobStatus.ERROR, JobStatus.CANCELLED)) > 0

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

    def delete_artifacts(self, job_id: int, step: JobStep | str | None = None, keep: tuple[str, ...] = ()) -> int:
        if step is not None:
            return self.core.execute("DELETE FROM job_artifacts WHERE job_id = ? AND step = ?", (job_id, str(step)))
        if keep:
            marks = ",".join("?" * len(keep))
            return self.core.execute(f"DELETE FROM job_artifacts WHERE job_id = ? AND step NOT IN ({marks})", (job_id, *keep))  # noqa: S608
        return self.core.execute("DELETE FROM job_artifacts WHERE job_id = ?", (job_id,))
