"""`IndexJobRunner` (F5 item 5): o job `kind = index` no worker leve. Etapas `index_text` (trechos e FTS da reunião,
`rag.index.sync_text`) e `index_vectors` (os vetores que faltam do modelo ativo, `rag.index.fill_vectors`). Nunca espera a
lease da gravação (um `index` parado seguraria todos os `llm` da fila leve, F5A-19)."""
from __future__ import annotations

from typing import Any, override

from ..log import get_logger
from ..model import JobStatus, JobStep
from ..store import Store
from ..store.jobs import JobRow
from .jobbase import BaseJobRunner, JobCancelledError

log = get_logger("pipeline.index")

STEP_WEIGHTS: dict[str, float] = {JobStep.INDEX_TEXT: 0.1, JobStep.INDEX_VECTORS: 0.9}


class IndexJobRunner(BaseJobRunner):
    step_weights = STEP_WEIGHTS

    def __init__(self, store: Store, job: JobRow, **kw: Any):
        super().__init__(store, job, **kw)
        self.result: dict[str, Any] = {}

    @property
    @override
    def respects_lease(self) -> bool:
        return False

    def run(self) -> JobStatus:
        if self.store.get_meeting(self.meeting_id) is None:
            from .base import PermanentError

            return self._fail(PermanentError("a reunião deste job não existe mais"), permanent=True)
        self._start_heartbeat()
        try:
            return self._execute()
        except JobCancelledError:
            return self._cancelled()
        except Exception as e:  # qualquer falha vira `erro` com nova tentativa
            return self._fail(e, permanent=False)
        finally:
            self._stop_heartbeat()

    @override
    def _execute(self) -> JobStatus:
        return self._finish_ok()

    @override
    def _finish_ok(self) -> JobStatus:
        self.store.jobs.finish(self.job_id, JobStatus.DONE, timings=self._timings_dict())
        log.info("job index %s concluído (reunião %s): %s", self.job_id, self.meeting_id, self.result)
        self.status = JobStatus.DONE
        return JobStatus.DONE
