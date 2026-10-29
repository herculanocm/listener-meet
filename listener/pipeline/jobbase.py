"""`BaseJobRunner` (F3 item 2, F3A-30): o que todo runner de job compartilha, extraído do `JobRunner` da F2: heartbeat em
thread própria, `checkpoint()` (heartbeat, cancelamento cooperativo e pausa), `_fail`, progresso ponderado por etapa e
`timings_json`. O `JobRunner` (pesado) e o `LlmJobRunner` (leve, `pipeline/llm_steps.py`) herdam daqui; cada um decide se
respeita a lease da gravação (`respects_lease`) e o que acontece ao concluir, cancelar e falhar."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from ..config.defaults import SPECS
from ..log import get_logger
from ..model import EventType, JobKind, JobStatus, JobStep
from ..store import Store
from ..store.jobs import JobRow

log = get_logger("pipeline.jobbase")

type Clock = Callable[[], datetime]
type Sleep = Callable[[float], None]


class JobCancelledError(Exception):
    """Cancelamento pedido pela interface e atendido entre janelas (ou entre tarefas de LLM)."""


@dataclass(slots=True)
class StepTiming:
    audio_s: float = 0.0
    speech_s: float = 0.0
    wall_s: float = 0.0
    model: str = ""
    prompt: str = ""        # F5.6 L2 (R-06): `transcribe/1.0+u<hash>` com instruções suas; fora do `model`, chave da velocidade

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"audio_s": round(self.audio_s, 3), "speech_s": round(self.speech_s, 3), "wall_s": round(self.wall_s, 3),
                               "model": self.model}
        if self.prompt:
            out["prompt"] = self.prompt
        return out


class BaseJobRunner:
    """Ciclo comum de um job. As subclasses implementam `_execute()` (o trabalho), `_finish_ok()` e, se precisarem,
    `_cancelled()`; `run()` cuida de reunião ausente, heartbeat, exceções e estado final."""

    step_weights: ClassVar[dict[str, float]] = {}

    def __init__(self, store: Store, job: JobRow, *, now: Clock | None = None, sleep: Sleep | None = None, heartbeat_s: float = 15.0,
                 heartbeat_thread: bool = True, wait_poll_s: float = 5.0, memory_rss: Callable[[], int | None] | None = None):
        self.store = store
        self.job = job
        self.job_id = int(job["id"])
        self.meeting_id = int(job["meeting_id"])
        self.kind = JobKind(str(job.get("kind") or JobKind.MEETING))
        self.now: Clock = now or (lambda: datetime.now().astimezone())
        self.sleep: Sleep = sleep or time.sleep
        self.heartbeat_s = heartbeat_s
        self.heartbeat_thread = heartbeat_thread
        self.wait_poll_s = wait_poll_s
        self.memory_rss = memory_rss
        self.timings: dict[str, StepTiming] = {}
        self._done_weight = 0.0
        self._share = 1.0
        self._step: JobStep | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.meeting: dict[str, Any] = {}
        self.status: JobStatus | None = None
        self._load_timings(job.get("timings_json"))

    # ---------------------------------------------------------------- pontos de extensão
    @property
    def respects_lease(self) -> bool:
        """True = espera a gravação terminar (`pipeline.paused_until`); o job `llm` ignora a lease (F3A-13)."""
        return True

    def _execute(self) -> JobStatus:
        raise NotImplementedError

    def _finish_ok(self) -> JobStatus:
        raise NotImplementedError

    def _scrub_orphan_people(self) -> None:
        """U-041 (A-08): o job leu pessoas e perfis no começo; uma pessoa apagada no meio volta como vínculo órfão, que sai
        aqui, antes de a ata e o índice lerem as falas. Falhar não desfaz o job concluído: só o log."""
        try:
            n = self.store.people.scrub_orphans()
        except Exception as e:
            log.warning("job %s: vínculos com pessoas apagadas não limpos: %s", self.job_id, e)
            return
        if n:
            log.info("job %s: %d vínculo(s) com pessoa apagada limpo(s)", self.job_id, n)

    def _event(self, type_: EventType, data: dict[str, Any]) -> None:
        """O evento do job na reunião. F5.6 (A-37, F5A-17): a reunião excluída no meio do job dispara a chave estrangeira; aí
        só o log (o job já sumiu com ela), sem derrubar o worker."""
        try:
            self.store.add_event(self.meeting_id, None, type_, data)
        except sqlite3.IntegrityError:
            if self.store.get_meeting(self.meeting_id) is not None:
                raise
            log.info("job %s: a reunião %s foi excluída; evento %s não gravado", self.job_id, self.meeting_id, type_)

    def _cancelled(self) -> JobStatus:
        self.store.jobs.finish(self.job_id, JobStatus.CANCELLED, timings=self._timings_dict())
        self._event(EventType.JOB_CANCEL, {"job_id": self.job_id, "step": str(self._step or "")})
        log.info("job %s cancelado pelo usuário na etapa %s", self.job_id, self._step)
        self.status = JobStatus.CANCELLED
        return JobStatus.CANCELLED

    def _fail(self, e: BaseException, permanent: bool) -> JobStatus:
        last = self._error_text(e)
        log.error("job %s falhou na etapa %s%s", self.job_id, self._step, " (erro permanente)" if permanent else "", exc_info=e)
        self.store.jobs.finish(self.job_id, JobStatus.ERROR, error=last, timings=self._timings_dict(), count_attempt=not permanent)
        self._event(EventType.JOB_ERROR, {"job_id": self.job_id, "step": str(self._step or ""), "error": last, "permanent": permanent})
        self.status = JobStatus.ERROR
        return JobStatus.ERROR

    def _error_text(self, e: BaseException) -> str:
        """Última linha do traceback (o inteiro vai para o log); o runner de LLM redige a chave aqui."""
        return traceback.format_exception_only(type(e), e)[-1].strip()

    # ---------------------------------------------------------------- heartbeat, pausa, cancelamento
    def _start_heartbeat(self) -> None:
        self.store.jobs.heartbeat(self.job_id)                      # antes de carregar qualquer modelo
        if self.heartbeat_thread:
            self._thread = threading.Thread(target=self._heartbeat_loop, name=f"heartbeat-{self.job_id}", daemon=True)
            self._thread.start()

    def _heartbeat_loop(self) -> None:
        try:
            while not self._stop.wait(self.heartbeat_s):
                try:
                    self.store.jobs.heartbeat(self.job_id)
                except Exception as e:  # o heartbeat nunca derruba o job; o banco pode estar ocupado
                    log.warning("heartbeat do job %s falhou: %s", self.job_id, e)
        finally:
            self.store.close_thread()

    def _stop_heartbeat(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def checkpoint(self) -> None:
        """Entre janelas ou tarefas (nunca no meio de uma): heartbeat, cancelamento e pausa (lease da gravação ou manual)."""
        self.store.jobs.heartbeat(self.job_id)
        if self.store.jobs.cancel_requested(self.job_id):
            raise JobCancelledError
        waited = False
        while self._paused():
            if not waited:
                self.store.jobs.set_status(self.job_id, JobStatus.PAUSED, JobStep.WAITING)
                log.info("job %s aguardando (gravação em curso ou pausa manual)", self.job_id)
                waited = True
            self.sleep(self.wait_poll_s)
            self.store.jobs.heartbeat(self.job_id)
            if self.store.jobs.cancel_requested(self.job_id):
                raise JobCancelledError
        if waited:
            self.store.jobs.set_status(self.job_id, JobStatus.RUNNING, self._step)

    def _paused(self) -> bool:
        if self.store.get(SPECS["pipeline.paused_manual"]):
            return True
        if not self.respects_lease:
            return False
        until = self.store.get_setting("pipeline.paused_until")
        if not until:
            return False
        try:
            return datetime.fromisoformat(until) > self.now()
        except ValueError:
            return False

    # ---------------------------------------------------------------- progresso e tempos
    def _begin_step(self, step: JobStep, share: float = 1.0) -> None:
        self._share = share
        if self._step != step:
            self._step = step
            self.store.jobs.set_progress(self.job_id, step, self._fraction(0.0), self._timings_dict())
            self._event(EventType.JOB_STEP, {"job_id": self.job_id, "step": str(step)})

    def _progress(self, step: JobStep, fraction: float) -> None:
        self.store.jobs.set_progress(self.job_id, step, self._fraction(fraction), self._timings_dict())

    def _end_step(self, step: JobStep, share: float = 1.0) -> None:
        self._done_weight += self.step_weights.get(step, 0.0) * share
        self._done_weight = min(1.0, self._done_weight)
        rss = self.memory_rss() if self.memory_rss is not None else None
        t = self.timings.get(step)
        log.info("job %s etapa %s: %s%s", self.job_id, step, t.as_dict() if t else "nada a fazer",
                 f"; RSS {rss / 1e6:.0f} MB" if rss else "")

    def _fraction(self, step_fraction: float) -> float:
        weight = self.step_weights.get(self._step or "", 0.0) * self._share
        return round(min(1.0, self._done_weight + weight * max(0.0, min(1.0, step_fraction))), 4)

    def _timing(self, step: JobStep | str, model: str = "", prompt: str = "") -> StepTiming:
        t = self.timings.get(str(step))
        if t is None:
            t = self.timings[str(step)] = StepTiming(model=model)
        if model and not t.model:
            t.model = model
        if prompt and not t.prompt:
            t.prompt = prompt
        return t

    def _timings_dict(self) -> dict[str, Any]:
        return {k: v.as_dict() for k, v in self.timings.items()}

    def _load_timings(self, text: str | None) -> None:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        for k, v in data.items():
            if isinstance(v, dict):
                self.timings[k] = StepTiming(float(v.get("audio_s", 0)), float(v.get("speech_s", 0)), float(v.get("wall_s", 0)),
                                             str(v.get("model", "")), str(v.get("prompt", "")))
