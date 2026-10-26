"""`IndexJobRunner` (F5 item 5): o job `kind = index` no worker leve. Etapas `index_text` (trechos e FTS da reunião,
`rag.index.sync_text`) e `index_vectors` (os vetores que faltam do modelo ativo, `rag.index.fill_vectors`, com o progresso
por lote). Nunca espera a lease da gravação (um `index` parado seguraria todos os `llm` da fila leve, F5A-19). Sem embedder
pronto ou sem rede, conclui com o FTS e a nota "N trechos sem vetor: motivo" em `timings_json.index`, sem laço de
retentativa (a varredura da próxima abertura tenta de novo). Com os vetores automáticos desligados (`rag.auto_index`), só
o FTS, com a nota "vetores automáticos desligados", salvo o pedido forçado (`params_json.force`, "reindexar"). Dentro do
laço dos lotes só heartbeat e cancelamento, sem bloquear; a pausa manual da fila deixa os lotes no ar terminarem e espera
fora do laço (R5-04). Não grava `job_start`, `job_step` nem `job_done` em `events`:
o índice roda a cada edição e encheria a história da reunião; erro e cancelamento continuam lá.
F5.1 (A-06): com o embedder local, a lease da gravação (`pipeline.paused_until`, que só existe com `pause_on_record` ou com
o ao vivo) adia os vetores antes de cada lote: o job conclui com trechos e FTS, a nota "vetores adiados: gravando" e
`timings_json.index.deferred`, e a varredura ao parar ou cancelar a gravação completa (A-07). Os pagos não esperam."""
from __future__ import annotations

import json
import time
from datetime import datetime
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
        from ..rag.index import AUTO_OFF, FillResult, auto_index_enabled, embedder_status, fill_vectors, sync_text

        self._enter(JobStep.INDEX_TEXT)
        t0 = time.perf_counter()
        synced = sync_text(self.store, self.meeting_id)
        self._timing(JobStep.INDEX_TEXT).wall_s += time.perf_counter() - t0
        self.info.update(chunks=synced.total, kept=synced.kept, added=synced.added, removed=synced.removed, moved=synced.moved)
        self._done_weight = STEP_WEIGHTS[JobStep.INDEX_TEXT]
        self.checkpoint()
        self._enter(JobStep.INDEX_VECTORS)
        t0 = time.perf_counter()
        if self.forced or auto_index_enabled(self.store):
            filled = fill_vectors(self.store, self.meeting_id, provider=self._provider_override, check=self._beat, paused=self._paused,
                                  wait=self.checkpoint,             # a espera da pausa, fora do laço (volta a `rodando` sozinho)
                                  on_progress=lambda done, total: self._progress(JobStep.INDEX_VECTORS, done / max(1, total)),
                                  defer=self._recording)            # só o local consulta (A-06)
        else:                                                  # vetores automáticos desligados: o FTS já foi; os vetores, não
            key = embedder_status(self.store).model_key
            n = self.store.rag.count_missing(self.meeting_id, key) if key else self.store.rag.count_chunks(self.meeting_id)
            filled = FillResult(key, n, 0, n, AUTO_OFF)
        timing = self._timing(JobStep.INDEX_VECTORS, filled.model_key or "")
        timing.wall_s += time.perf_counter() - t0
        self.info.update(model=filled.model_key, wanted=filled.wanted, vectors=filled.written, missing=filled.missing,
                         calls=filled.calls, cost_usd=round(filled.cost_usd, 6), note=filled.note, deferred=filled.deferred)
        if filled.note:
            log.info("job index %s (reunião %s): %s", self.job_id, self.meeting_id, filled.note)
        return self._finish_ok()

    @property
    def forced(self) -> bool:
        """O pedido do usuário ("reindexar", L5): os vetores saem mesmo com os vetores automáticos desligados."""
        try:
            params = json.loads(self.job.get("params_json") or "{}")
        except ValueError:
            return False
        return isinstance(params, dict) and bool(params.get("force"))

    def _beat(self) -> None:
        """O que roda dentro do laço dos lotes: heartbeat e cancelamento, sem bloquear (R5-04)."""
        self.store.jobs.heartbeat(self.job_id)
        if self.store.jobs.cancel_requested(self.job_id):
            raise JobCancelledError

    def _recording(self) -> bool:
        """A lease da gravação está de pé (A-06): o `index` não a espera (F5A-19), mas o local adia os vetores por ela."""
        until = self.store.get_setting("pipeline.paused_until")
        if not until:
            return False
        try:
            return datetime.fromisoformat(until) > self.now()
        except ValueError:
            return False

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
