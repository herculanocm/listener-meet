"""Reprocessar uma reunião (F2 item 12; F5.3 A-05, A-06): o único caminho do "Reprocessar" da fila e da troca de idioma da
reunião aberta, sempre por ação do usuário (CP3). Com a reunião gravando, recusa. Sem idioma, reprocessa como antes, mas
não duplica um processamento ativo da reunião. Com idioma (U-046), o job leva `{"reason": "language", "language": <código
do Whisper>}` no `params_json`: um processamento pendente da reunião só troca de idioma (as janelas que uma tentativa
anterior já transcreveu saem; as vozes ficam, o pyannote não tem idioma); um rodando é cancelado (atende entre janelas) e
outro entra na fila. O runner transcreve no idioma pedido e o grava em `meetings.language` junto da troca dos segmentos
(cancelado ou com erro, fica o antigo); a LLM refaz tudo como pedido manual (`target_language` do job pai, A-06)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..config.language import meeting_language, output_code, whisper_code
from ..log import get_logger
from ..model import PIPELINE_JOB_KINDS, JobKind, JobStatus, JobStep, MeetingStatus
from ..store.jobs import ACTIVE, HELD

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow

log = get_logger("pipeline.reprocess")

REASON_LANGUAGE = "language"
RECORDING = frozenset({str(MeetingStatus.RECORDING), str(MeetingStatus.PAUSED)})
TRANSCRIPT_STEPS = (JobStep.TRANSCRIBE_R, JobStep.TRANSCRIBE_L)


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    job_id: int | None                   # o job que fará o trabalho (novo, ou o pendente que trocou de idioma); None = recusado
    message: str                         # o que a tela mostra
    cancelled: int | None = None         # o processamento em curso cancelado para dar lugar a este


def _params(job: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        data = json.loads(str((job or {}).get("params_json") or "{}"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def target_language(job: Mapping[str, Any] | None) -> str | None:
    """O idioma pedido por uma troca de idioma (`params_json.reason = language`), em código do Whisper; None nos outros."""
    data = _params(job)
    if data.get("reason") != REASON_LANGUAGE or not data.get("language"):
        return None
    return whisper_code(str(data["language"]))


def active_jobs(store: Store, meeting_id: int) -> list[JobRow]:
    """Processamentos (`meeting`, `reprocess`) pendentes, rodando ou pausados da reunião, do mais antigo ao mais novo."""
    rows = [j for j in store.jobs.for_meeting(meeting_id) if str(j.get("kind")) in PIPELINE_JOB_KINDS and str(j["status"]) in ACTIVE]
    return sorted(rows, key=lambda j: int(j["id"]))


def pending_language(store: Store, meeting_id: int) -> tuple[int, str] | None:
    """A troca de idioma em curso: (job, idioma pedido em código do Whisper) do processamento ativo com `reason = language`;
    a reunião aberta mostra "pt-BR → en (reprocessando)"."""
    for job in reversed(active_jobs(store, meeting_id)):
        target = target_language(job)
        if target is not None:
            return int(job["id"]), target
    return None


def request_reprocess(store: Store, meeting_id: int, language: str | None = None) -> ReprocessResult:
    """Reprocessa a reunião, em `language` quando dado (qualquer código; vai como o do Whisper). A leitura da fila e a
    decisão ficam numa transação: o claim de um worker não entra no meio."""
    target = whisper_code(language) if language else None
    with store.transaction():
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            return ReprocessResult(None, "Esta reunião não existe mais.")
        if str(meeting.get("status")) in RECORDING:
            log.info("reunião %s: reprocessar recusado: a reunião está gravando", meeting_id)
            return ReprocessResult(None, "A reunião ainda está sendo gravada: reprocesse depois de parar.")
        active = active_jobs(store, meeting_id)
        if target is None:
            return _plain(store, meeting_id, active)
        pending = next((j for j in active if str(j["status"]) == JobStatus.PENDING), None)
        if pending is not None:
            return _retarget(store, meeting, pending, target)
        running = next((j for j in active if str(j["status"]) in HELD), None)
        if running is not None:
            store.jobs.request_cancel(int(running["id"]))
        return _enqueue(store, meeting_id, target, int(running["id"]) if running is not None else None)


def _plain(store: Store, meeting_id: int, active: list[JobRow]) -> ReprocessResult:
    """O "Reprocessar" da fila: um processamento ativo da reunião já fará o trabalho; nada de outro igual atrás dele."""
    if not active:
        return _enqueue(store, meeting_id, None, None)
    job = active[0]
    state = "na fila" if str(job["status"]) == JobStatus.PENDING else "em andamento"
    log.info("reunião %s: reprocessar recusado: o processamento %s já está %s", meeting_id, job["id"], state)
    return ReprocessResult(None, f"Esta reunião já tem um processamento {state} (job {job['id']}): espere terminar.")


def _retarget(store: Store, meeting: Mapping[str, Any], job: JobRow, target: str) -> ReprocessResult:
    """O processamento ainda na fila só troca de idioma; o que uma tentativa anterior transcreveu em outro idioma sai (as
    vozes ficam: a retomada não refaz a separação)."""
    job_id = int(job["id"])
    message = f"O processamento na fila (job {job_id}) vai transcrever em {output_code(target)}."
    asked = target_language(job)
    if asked == target:                                  # já era este idioma: o que ele transcreveu vale
        return ReprocessResult(job_id, message)
    store.jobs.set_params(job_id, {**_params(job), "reason": REASON_LANGUAGE, "language": target})
    before = asked or whisper_code(meeting_language(store, meeting))
    dropped = sum(store.jobs.delete_artifacts(job_id, step) for step in TRANSCRIPT_STEPS) if before != target else 0
    log.info("reunião %s: o processamento na fila (job %s) passa a transcrever em %s (%d janela(s) já transcritas saem)",
             meeting["id"], job_id, target, dropped)
    return ReprocessResult(job_id, message)


def _enqueue(store: Store, meeting_id: int, target: str | None, cancelled: int | None) -> ReprocessResult:
    params = {"reason": REASON_LANGUAGE, "language": target} if target else None
    job_id = store.jobs.enqueue(meeting_id, JobKind.REPROCESS, params)
    if target is None:
        log.info("reunião %s: reprocessamento na fila (job %s)", meeting_id, job_id)
        return ReprocessResult(job_id, f"Reprocessamento na fila (job {job_id}).")
    log.info("reunião %s: reprocessar em %s na fila (job %s)%s", meeting_id, target, job_id,
             f"; o processamento {cancelled} foi cancelado" if cancelled is not None else "")
    text = f"Reprocessando em {output_code(target)} (job {job_id})"
    if cancelled is not None:
        text += f"; o processamento em curso (job {cancelled}) foi cancelado"
    return ReprocessResult(job_id, f"{text}.", cancelled)


__all__ = ["REASON_LANGUAGE", "RECORDING", "ReprocessResult", "active_jobs", "pending_language", "request_reprocess",
           "target_language"]
