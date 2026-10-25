"""`IndexJobRunner` (F5 item 5): o job `kind = index` no worker leve. Etapas `index_text` (trechos e FTS da reunião,
`rag.index.sync_text`) e `index_vectors` (os vetores que faltam do modelo ativo, `rag.index.fill_vectors`, com o progresso
por lote). Nunca espera a lease da gravação (um `index` parado seguraria todos os `llm` da fila leve, F5A-19). Sem embedder
pronto ou sem rede, conclui com o FTS e a nota "N trechos sem vetor: motivo" em `timings_json.index`, sem laço de
retentativa (a varredura da próxima abertura tenta de novo). Não grava `job_start`, `job_step` nem `job_done` em `events`:
o índice roda a cada edição e encheria a história da reunião; erro e cancelamento continuam lá."""
from __future__ import annotations

import json
import time
from typing import Any, override

from ..llm.base import LLMProvider
from ..log import get_logger
from ..model import JobStatus, JobStep
from ..store import Store
from ..store.jobs import JobRow
from .base import PermanentError
from .jobbase import BaseJobRunner, JobCancelledError

log = get_logger("pipeline.index")

STEP_WEIGHTS: dict[str, float] = {JobStep.INDEX_TEXT: 0.1, JobStep.INDEX_VECTORS: 0.9}


class IndexJobRunner(BaseJobRunner):
    step_weights = STEP_WEIGHTS

    def __init__(self, store: Store, job: JobRow, *, provider: LLMProvider | None = None, **kw: Any):
        self.info: dict[str, Any] = {}                       # antes do super(): `_load_timings` restaura a tentativa anterior
        super().__init__(store, job, **kw)
        self._provider_override = provider

    @property
    @override
    def respects_lease(self) -> bool:
        return False

    def run(self) -> JobStatus:
        if self.store.get_meeting(self.meeting_id) is None:
            return self._fail(PermanentError("a reunião deste job não existe mais"), permanent=True)
        self._start_heartbeat()
        try:
            return self._execute()
        except JobCancelledError:
            return self._cancelled()
        except Exception as e:  # falha fora do embedder (banco, bug): `erro` com nova tentativa
            return self._fail(e, permanent=False)
        finally:
            self._stop_heartbeat()

    @override
    def _execute(self) -> JobStatus:
        from ..rag.index import fill_vectors, sync_text

        self._enter(JobStep.INDEX_TEXT)
        t0 = time.perf_counter()
        synced = sync_text(self.store, self.meeting_id)
        self._timing(JobStep.INDEX_TEXT).wall_s += time.perf_counter() - t0
        self.info.update(chunks=synced.total, kept=synced.kept, added=synced.added, removed=synced.removed, moved=synced.moved)
        self._done_weight = STEP_WEIGHTS[JobStep.INDEX_TEXT]
        self.checkpoint()
        self._enter(JobStep.INDEX_VECTORS)
        t0 = time.perf_counter()
        filled = fill_vectors(self.store, self.meeting_id, provider=self._provider_override, check=self.checkpoint,
                              on_progress=lambda done, total: self._progress(JobStep.INDEX_VECTORS, done / max(1, total)))
        timing = self._timing(JobStep.INDEX_VECTORS, filled.model_key or "")
        timing.wall_s += time.perf_counter() - t0
        self.info.update(model=filled.model_key, wanted=filled.wanted, vectors=filled.written, missing=filled.missing,
                         calls=filled.calls, cost_usd=round(filled.cost_usd, 6), note=filled.note)
        if filled.note:
            log.info("job index %s (reunião %s): %s", self.job_id, self.meeting_id, filled.note)
        return self._finish_ok()

    def _enter(self, step: JobStep) -> None:
        """`_begin_step` sem o evento `job_step` (ver o docstring do módulo)."""
        self._step, self._share = step, 1.0
        self.store.jobs.set_progress(self.job_id, step, self._fraction(0.0), self._timings_dict())

    @override
    def _finish_ok(self) -> JobStatus:
        self._done_weight = 1.0
        self.store.jobs.finish(self.job_id, JobStatus.DONE, timings=self._timings_dict())
        log.info("job index %s concluído (reunião %s): %s", self.job_id, self.meeting_id, self.info)
        self.status = JobStatus.DONE
        return JobStatus.DONE

    @override
    def _timings_dict(self) -> dict[str, Any]:
        out = super()._timings_dict()
        out["index"] = dict(self.info)
        return out

    @override
    def _load_timings(self, text: str | None) -> None:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        info = data.pop("index", None) if isinstance(data, dict) else None
        super()._load_timings(json.dumps(data) if data else None)
        if isinstance(info, dict):
            self.info.update(info)
