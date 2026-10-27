"""U-042 (F5.2 L3): a ata depois da identificação das vozes. No fim do processamento, com voz pendente e as tarefas
automáticas ligadas, o job `llm` leva só a identidade por contexto e a transcrição revisada (pergunta 1 da F5.2); ata,
executivo, pré-resumo e título ficam adiados no evento `llm_deferred` (job pai e tarefas). `release` enfileira as adiadas
uma vez por job pai (`llm_released`), quando a revisão fica sem voz pendente, no "Concluir" ou em "Gerar ata mesmo assim".
Reprocessar troca o job pai: adiamento de job velho não libera (A-13). Os nomes que mudam depois da ata pronta acendem o
aviso "Atualizar a ata com os nomes" pela assinatura dos falantes gravada no `minutes_done` (A-15; um hash, sem nomes).
Só eventos da reunião: sem esquema novo."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..log import get_logger
from ..model import PIPELINE_JOB_KINDS, EventType, JobStep, SummaryKind
from . import review

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("pipeline.summary_gate")

EARLY = frozenset({JobStep.IDENTIFY, JobStep.REVISE})   # saem logo: ajudam a revisar (a identidade sugere quem é quem)
REFRESH = (JobStep.MINUTES, JobStep.EXECUTIVE, JobStep.PRE_SUMMARY)   # "Atualizar a ata com os nomes" (D-057, decisão 5)


@dataclass(frozen=True, slots=True)
class Deferral:
    parent_job_id: int
    tasks: tuple[str, ...]
    pending: int                  # vozes pendentes agora


def pending_voices(store: Store, meeting_id: int) -> int:
    """Vozes a confirmar no job vigente, pela regra da própria revisão (A-13: um lugar só decide o que é pendente)."""
    return len(review.pending(review.review_items(store, meeting_id)))


def split(tasks: Iterable[JobStep], pending: int, manual: Iterable[JobStep] = ()) -> tuple[list[JobStep], list[JobStep]]:
    """(agora, adiadas): sem voz pendente tudo sai agora; os pedidos manuais ("gerar de novo") nunca esperam."""
    tasks, keep = list(tasks), set(manual)
    if not pending:
        return tasks, []
    return [t for t in tasks if t in EARLY or t in keep], [t for t in tasks if t not in EARLY and t not in keep]


def record(store: Store, meeting_id: int, parent_job_id: int, tasks: Iterable[JobStep], pending: int) -> None:
    names = [str(t) for t in tasks]
    store.add_event(meeting_id, None, EventType.LLM_DEFERRED, {"parent_job_id": parent_job_id, "tasks": names, "pending": pending})
    log.info("reunião %s: ata adiada até a identificação de %d voz(es) (job pai %s; tarefas %s)", meeting_id, pending,
             parent_job_id, ", ".join(names))


def deferred(store: Store, meeting_id: int) -> Deferral | None:
    """O adiamento vigente: do job pai atual e ainda não liberado; None quando não há."""
    parent = store.segments.current_job_id(meeting_id)
    if parent is None:
        return None
    tasks: tuple[str, ...] | None = None
    for e in store.list_events(meeting_id):
        raw = e.get("data")
        data = raw if isinstance(raw, dict) else {}
        if int(data.get("parent_job_id") or 0) != parent:
            continue
        if e["type"] == EventType.LLM_DEFERRED:
            tasks = tuple(str(t) for t in data.get("tasks") or ())
        elif e["type"] == EventType.LLM_RELEASED:
            tasks = None
    return Deferral(parent, tasks, pending_voices(store, meeting_id)) if tasks else None


def release(store: Store, meeting_id: int, reason: str, *, force: bool = False) -> int | None:
    """Enfileira as tarefas adiadas do job vigente, uma vez (A-13): sem voz pendente, ou com `force` (o "Concluir", o
    "Gerar ata mesmo assim", o "Gerar de novo" de uma adiada). Nada com reprocessamento na fila (ele terá o próprio
    adiamento) nem sem provedor com chave (segue adiada). Devolve o job `llm` criado."""
    from .llm_steps import enqueue_llm_job

    with store.transaction():
        d = deferred(store, meeting_id)
        if d is None:
            return None
        if d.pending and not force:
            log.debug("reunião %s: a ata segue adiada (%d voz(es) pendente(s))", meeting_id, d.pending)
            return None
        heavy = [j for j in store.jobs.active(kinds=PIPELINE_JOB_KINDS) if int(j["meeting_id"]) == meeting_id]   # R-09
        if heavy:
            log.info("reunião %s: a ata adiada não sai agora (%s): o processamento %s está na fila e terá o próprio adiamento",
                     meeting_id, reason, heavy[0]["id"])
            return None
        job_id = enqueue_llm_job(store, meeting_id, list(d.tasks), d.parent_job_id, auto=True)
        if job_id is None:
            log.info("reunião %s: a ata adiada não saiu (%s): sem provedor de LLM com chave; segue adiada", meeting_id, reason)
            return None
        store.add_event(meeting_id, None, EventType.LLM_RELEASED,
                        {"parent_job_id": d.parent_job_id, "tasks": list(d.tasks), "job_id": job_id, "reason": reason})
    log.info("reunião %s: ata liberada (%s; %d voz(es) ainda sem nome): job llm %s (%s)", meeting_id, reason, d.pending, job_id,
             ", ".join(d.tasks))
    return job_id


# ---------------------------------------------------------------- nomes depois da ata (A-15)
def speakers_digest(store: Store, meeting_id: int) -> str:
    """Hash de quem fala na leitura de agora (rótulo da voz, pessoa, nome e cargo do dia): muda quando um nome muda. Um
    hash e não os nomes: o evento fica no banco e o esquecimento (U-041) não precisaria reescrevê-lo."""
    seen = sorted({(r.channel, r.name, r.role, r.person_id or 0) for r in review.transcript_rows(store, meeting_id)})
    return hashlib.sha256(json.dumps(seen, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def names_changed(store: Store, meeting_id: int) -> bool:
    """A ata atual foi gerada com outros nomes: só quando ela é do job vigente (D-043) e tem a assinatura gravada; ata
    antiga, sem assinatura, não acende o aviso."""
    row = store.summaries.current(meeting_id, SummaryKind.MINUTES)
    if row is None or row.get("job_id") != store.segments.current_job_id(meeting_id):
        return False
    digest = None
    for e in store.list_events(meeting_id):
        raw = e.get("data")
        data = raw if isinstance(raw, dict) else {}
        if data.get("step") == "minutes_done" and data.get("summary_id") == row["id"]:
            digest = data.get("speakers")
    return bool(digest) and digest != speakers_digest(store, meeting_id)
