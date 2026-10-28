"""Os textos das colunas da fila (F2 item 15, F3, F5, F5.3 L2), separados do diálogo para os testes e para a dica da
bandeja: reunião, enviado, tipo, estado, etapa, %, estimativa e detalhe de um job; a linha agregada dos jobs `index`
(F5A-05); a hora local, o tempo de trabalho pelo `timings_json` (A-08) e o rótulo do job `llm` com a contagem de tarefas e
o custo (U-020). F5.4 L3: saíram do `ui/queue.py` (que passava de 400 linhas com o layout da U-077), sem mudança; o
`ui/queue.py` os reexporta."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..llm.costs import fmt_usd
from ..model import JOB_KIND_LABELS, JOB_STEP_LABELS, JobKind, JobStatus
from ..pipeline.estimate import fmt_remaining, remaining_seconds

if TYPE_CHECKING:
    from ..store.jobs import JobRow

TIME_FORMAT = "%d/%m %H:%M:%S"
TIMING_BLOCKS = frozenset({"llm", "index"})         # os resumos do job `llm` (custo, tarefas) e do `index` (trechos, nota): sem tempo


def local_time(iso: str | None) -> str:
    """Data ISO do banco (com fuso, `store.core._now`) na hora local deste computador: "27/10 09:05:07"; vazio sem data."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime(TIME_FORMAT)
    except (ValueError, OverflowError, OSError):
        return ""


def fmt_work(seconds: float | None) -> str:
    """"menos de 1 s", "45 s", "3 min 07 s", "1 h 05 min"; vazio sem medida."""
    if seconds is None:
        return ""
    s = round(seconds)
    if s < 1:
        return "menos de 1 s"
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def work_seconds(timings_json: str | None) -> float | None:
    """Tempo de trabalho de um job pelo `timings_json` (A-08): a soma do `wall_s` das etapas, de todas as tentativas (o
    `started_at` é o do primeiro claim e o "Tentar de novo" não o zera: fim menos início contaria a espera). O pipeline grava
    `{etapa: {audio_s, speech_s, wall_s, model}}` (`StepTiming`), uma etapa depois da outra; o job `llm` grava o mesmo por
    tarefa mais o bloco `llm`, e o `index` o mesmo por etapa mais o bloco `index`, os dois sem tempo. Executivo, pré-resumo e
    título rodam juntos no job `llm` (`llm_steps.PARALLEL`): contam pelo maior, não pela soma. None quando nada foi medido."""
    from ..pipeline.llm_steps import PARALLEL

    try:
        data = json.loads(timings_json) if timings_json else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    walls = {str(k): w for k, v in data.items() if k not in TIMING_BLOCKS and (w := _wall(v)) is not None}
    if not walls:
        return None
    together = [w for k, w in walls.items() if k in PARALLEL]
    return sum(w for k, w in walls.items() if k not in PARALLEL) + max(together, default=0.0)


def _wall(block: object) -> float | None:
    """O `wall_s` de um bloco de etapa; None num bloco sem tempo ou com lixo (o JSON aceita NaN)."""
    if not isinstance(block, dict):
        return None
    wall = block.get("wall_s")
    return float(wall) if isinstance(wall, int | float) and math.isfinite(wall) else None


def job_times_tip(job: JobRow) -> str:
    """A dica de "Enviado" (F5.3 item 7): enviado, início, fim e tempo de trabalho; "—" no que ainda não houve."""
    rows = (("Enviado", local_time(job.get("created_at"))), ("Início", local_time(job.get("started_at"))),
            ("Fim", local_time(job.get("finished_at"))), ("Tempo de trabalho", fmt_work(work_seconds(job.get("timings_json")))))
    return "\n".join(f"{name}: {value or '—'}" for name, value in rows)


def pct_text(progress: float | None) -> str:
    return f"{float(progress or 0.0) * 100:.0f} %"


def job_row_texts(job: JobRow, meeting: dict[str, Any] | None, rtf_json: str, llm_latency_s: float | None = None) -> tuple[str, ...]:
    """Texto das colunas de um job, na ordem de `COLUMNS` (separado do widget para o teste e para o tooltip da bandeja).
    `llm_latency_s`: latência média das chamadas (`llm_calls`) para estimar o que falta num job `llm` (F3 item 16)."""
    title = (meeting or {}).get("title") or f"reunião {job['meeting_id']}"
    started = str((meeting or {}).get("started_at") or "")[:16].replace("T", " ")
    status = str(job["status"])
    step = str(job.get("step") or "")
    duration_ms = (meeting or {}).get("duration_ms") or 0
    eta = ""
    if status in (JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.PENDING):
        eta = fmt_remaining(remaining_seconds(step or None, float(job.get("progress") or 0.0), duration_ms / 1000 if duration_ms else None,
                                              rtf_json, job.get("timings_json"), kind=str(job.get("kind") or "")))
    detail = str(job.get("error") or "")
    label = JOB_STEP_LABELS.get(step, step)
    if str(job.get("kind")) == JobKind.LLM:                              # F3: "LLM: ata (2 de 4)" e custo acumulado (U-020)
        label, cost, left = llm_job_texts(job, label)
        detail = " · ".join(x for x in (cost, detail) if x)
        if status in (JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.PENDING) and llm_latency_s and left:
            eta = fmt_remaining(llm_latency_s * left)
    return (f"{title} ({started})" if started else title, local_time(job.get("created_at")),
            JOB_KIND_LABELS.get(str(job.get("kind")), str(job.get("kind"))), status, label, pct_text(job.get("progress")), eta, detail)


def index_row_texts(summary: dict[str, int], running: JobRow | None) -> tuple[str, ...] | None:
    """Linha agregada dos jobs `index` (F5A-05): "Índice: 3 reuniões (1 com erro)"; None quando não há nada a mostrar.
    "Enviado" é o do job que roda; sem ele, vazio (F5.3)."""
    meetings, errors = int(summary.get("meetings") or 0), int(summary.get("errors") or 0)
    if not meetings and not errors:
        return None
    title = f"Índice: {meetings} {'reunião' if meetings == 1 else 'reuniões'}" + (f" ({errors} com erro)" if errors else "")
    sent = ""
    if running is not None:
        state, step, sent = str(running["status"]), str(running.get("step") or ""), local_time(running.get("created_at"))
        label, pct = JOB_STEP_LABELS.get(step, step), pct_text(running.get("progress"))
    elif summary.get("pending"):
        state, label, pct = str(JobStatus.PENDING), "", pct_text(0.0)
    else:
        state, label, pct = str(JobStatus.ERROR), "", ""
    detail = "a busca por palavra já vale; os vetores vêm em seguida" if meetings else ""
    return (title, sent, JOB_KIND_LABELS[JobKind.INDEX], state, label, pct, "", detail)


def index_progress(summary: dict[str, int], running: JobRow | None) -> float | None:
    """A fração da barra da linha agregada: a do job que roda; 0 com pendentes; None (sem barra) quando só há erros."""
    if running is not None:
        return float(running.get("progress") or 0.0)
    return 0.0 if summary.get("pending") else None


def llm_job_texts(job: JobRow, label: str) -> tuple[str, str, int]:
    """Rótulo com a contagem de tarefas, custo acumulado e tarefas que faltam do job `llm` (`params_json.tasks`, `timings_json.llm`)."""
    try:
        tasks = json.loads(job.get("params_json") or "{}").get("tasks") or []
    except ValueError:
        tasks = []
    try:
        llm = json.loads(job.get("timings_json") or "{}").get("llm") or {}
    except ValueError:
        llm = {}
    done = len(llm.get("done") or [])
    if tasks and label:
        label = f"{label} ({min(done + 1, len(tasks))} de {len(tasks)})" if str(job["status"]) in (JobStatus.RUNNING, JobStatus.PAUSED) \
            else f"{label} ({done} de {len(tasks)})"
    cost = llm.get("cost_usd")
    return label, (f"custo {fmt_usd(float(cost))}" if cost else ""), max(0, len(tasks) - done)


__all__ = ["TIME_FORMAT", "TIMING_BLOCKS", "fmt_work", "index_progress", "index_row_texts", "job_row_texts", "job_times_tip",
           "llm_job_texts", "local_time", "pct_text", "work_seconds"]
