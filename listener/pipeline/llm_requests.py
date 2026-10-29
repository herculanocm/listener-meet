"""F5.6 L1a (U-089 a U-091; A-05 a A-17): ata, resumo e ações só a pedido, sem interface. O núcleo (identidade, título,
revisada) segue automático (`llm_steps.enqueue_after_pipeline`); ata, executivo e pré-resumo só saem de um pedido, o evento
`llm_requested` (A-11), consumido uma vez (A-12):

- `request`: com a transcrição pronta, aplica na hora; com processamento ativo, fica preso a ele (`after_job_id`, que
  `_enqueue_processing` e `request_reprocess` devolvem, A-18); sem nenhum, ao próximo (o `pipeline.auto_start` desligado).
  O processamento que a troca de idioma cancela e substitui passa o pedido ao substituto (`replaces`, R-01); o desfeito
  (cancelado sem substituto) não o consome nunca.
- `after_pipeline`: o fim do processamento enfileira o núcleo e consome os pedidos dele numa transação `BEGIN IMMEDIATE`,
  uma vez (o marcador `llm_chained`). Sem o marcador depois do `job_done` do runner (a corrida entre os dois, ou um
  encadeamento que falhou), o próximo `request` encadeia antes: o núcleo entra na fila na frente da ata (R-02, R-05). Sem
  chave, nada é gasto nem marcado: os pedidos seguem vivos (R-04).
- Aplicar: "quando todos tiverem nome" com voz pendente vira o adiamento pedido (`summary_gate.record`), que `release` solta
  ao resolver a última voz ou no "Concluir" (P-01; numa reunião já revisada também, R-03); o resto, um job `llm` com
  `auto=False` (A-09) e o modo do pedido, atrás de um job de LLM ativo, salvo quando ele ainda vai fazer as mesmas tarefas
  (A-10, R-08), e o `llm_released`. O pedido "agora" tira as tarefas dele dos adiamentos pendentes.
- O resumo sai da ata: pedir o resumo leva a ata junto só quando ela falta ou está desatualizada (P-07, `resolve`).
- `request_state` (A-13, A-14), `estimate` (A-17) e `voices` (A-16): o que a tela do L1b mostra (`llm_request_state`).
O log leva ids, tarefas e contagens; nunca título, nome ou fala."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model import EventType, JobKind, JobStatus, JobStep, MeetingStatus
from ..store.jobs import ACTIVE
from . import summary_gate
from .llm_request_state import (Estimate, RequestState, Species, State, Voices, active_llm, alive, estimate, follow, pending_core,
                                ready_job, request_state, request_states, resolve, unchained, voices)
from .llm_steps import NO_SPEECH, REQUEST_ONLY, TASK_ORDER, enqueue_after_pipeline, enqueue_llm_job, job_params, tasks_of
from .reprocess import active_jobs
from .summary_gate import Asked, Ledger

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow

log = get_logger("pipeline.llm_requests")

NO_KEY = "Nenhum provedor de LLM ativo com chave: informe a chave em Configurações, Chaves de acesso."
NOTHING = "Nada a pedir: só ata, executivo e pré-resumo saem a pedido."
GONE = "Esta reunião não existe mais."
CONCLUDED = "concluída"                 # o `profile_confirm` do "Concluir" da revisão (`pipeline/review.finish`)


class Mode(StrEnum):
    NAMES = "names"             # "Gerar quando todos tiverem nome" (o padrão)
    NOW = "now"                 # "Gerar agora mesmo assim"


class Outcome(StrEnum):
    QUEUED = "na fila"          # um job `llm` gera (novo, ou o da fila que ainda vai fazer as mesmas tarefas)
    WAITING = "adiado"          # espera todas as vozes terem nome (ou o "Concluir")
    BOUND = "preso"             # sai quando o processamento ativo da reunião terminar
    NEXT = "próximo"            # sai quando a reunião for processada (nada na fila)
    NO_KEY = "sem chave"        # sem provedor com chave na hora de gerar: segue pedido
    REFUSED = "recusado"        # nada registrado; o motivo em `refused`


@dataclass(frozen=True, slots=True)
class RequestResult:
    request_id: int | None              # o evento `llm_requested`; None = recusado (o motivo em `refused`)
    job_id: int | None = None           # o job `llm` que gera (novo, ou o da fila que ainda faz as mesmas tarefas, A-10)
    waiting: bool = False               # adiado: sai quando todas as vozes tiverem nome (ou no "Concluir")
    after_job_id: int | None = None     # preso a este processamento ativo
    refused: str = ""
    outcome: Outcome = Outcome.REFUSED  # o que a tela diz (R-01, R-04)


# ---------------------------------------------------------------- pedir
def request(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], mode: Mode | str, origin: str,
            after_job_id: int | None = None) -> RequestResult:
    """Registra o pedido (A-11) e o aplica já quando a transcrição está pronta; senão o prende ao processamento."""
    asked = {str(t) for t in tasks}
    wanted = tuple(str(t) for t in TASK_ORDER if t in REQUEST_ONLY and str(t) in asked)
    how = Mode(str(mode))
    if not wanted:
        return RequestResult(None, refused=NOTHING)
    with store.transaction():
        refused = _refusal(store, meeting_id)
        now, bound = _bind(store, meeting_id, after_job_id)
        if not refused and now and not store.segments.count_segments(meeting_id, bound, with_text=True):
            refused = NO_SPEECH                                          # o job sairia em erro, sem chamada (R3-02)
        if refused:
            return RequestResult(None, refused=refused)
        rid = store.add_event(meeting_id, None, EventType.LLM_REQUESTED, {"tasks": list(wanted), "mode": str(how), "origin": origin,
                                                                          "after_job_id": bound})
        log.info("reunião %s: pedido %s da LLM (%s; modo %s; %s): %s", meeting_id, rid, ", ".join(wanted), how, origin,
                 f"já, sobre o job {bound}" if now else f"preso ao job {bound}" if bound else "o próximo processamento")
        if not now or bound is None:
            return RequestResult(rid, after_job_id=bound, outcome=Outcome.BOUND if bound else Outcome.NEXT)
        if unchained(store, meeting_id, bound):                         # A-12 (R-02, R-05): o núcleo entra antes da ata
            after_pipeline(store, meeting_id, bound, str((store.jobs.get(bound) or {}).get("kind") or JobKind.MEETING))
            return _settled(store, meeting_id, rid)
        job_id, waiting = _apply(store, meeting_id, bound, [Asked(rid, wanted, str(how), origin, bound)], origin)
    return RequestResult(rid, job_id, waiting, outcome=Outcome.WAITING if waiting else Outcome.QUEUED if job_id else Outcome.NO_KEY)


def _refusal(store: Store, meeting_id: int) -> str:
    """Por que nada é registrado: a reunião sumiu ou não há provedor com chave (comportamento 1; lido na transação, R-04)."""
    if store.get_meeting(meeting_id) is None:
        return GONE
    return NO_KEY if store.providers.active_with_key("llm") is None else ""


def _bind(store: Store, meeting_id: int, after_job_id: int | None) -> tuple[bool, int | None]:
    """(aplica já, job): preso ao processamento pedido enquanto ele roda, ou ao substituto dele quando a troca de idioma o
    cancelou (R-01); senão ao processamento ativo mais novo; senão, com o pedido cancelado ou com erro e sem substituto, ao
    próximo (None); senão já, sobre a transcrição pronta (o job pedido que já terminou é a corrida do A-12)."""
    dead = False
    if after_job_id is not None:
        jobs = {int(j["id"]): j for j in store.jobs.for_meeting(meeting_id)}
        target = jobs.get(follow(jobs, after_job_id))
        if target is not None and str(target["status"]) in ACTIVE and not target.get("cancel_requested"):
            return False, int(target["id"])
        dead = target is not None and str(target["status"]) != JobStatus.DONE
    active = active_jobs(store, meeting_id)
    if active:
        return False, int(active[-1]["id"])
    if dead:
        return False, None
    ready = ready_job(store, meeting_id)
    return ready is not None, ready


def _settled(store: Store, meeting_id: int, request_id: int) -> RequestResult:
    """O que o encadeamento fez do pedido: liberado num job, adiado, ou parado sem chave."""
    led = summary_gate.ledger(store, meeting_id)
    if request_id in led.released:
        job_id = led.released[request_id]
        return RequestResult(request_id, job_id, outcome=Outcome.QUEUED if job_id else Outcome.REFUSED, refused="" if job_id else NO_SPEECH)
    if request_id in led.deferrals:
        return RequestResult(request_id, waiting=True, outcome=Outcome.WAITING)
    return RequestResult(request_id, outcome=Outcome.NO_KEY)


# ---------------------------------------------------------------- consumir
def after_pipeline(store: Store, meeting_id: int, parent_job_id: int, kind: JobKind | str) -> int | None:
    """Encadeado pelo `JobRunner._finish_ok` (F3A-01), ou pelo `request` quando ele não rodou: o núcleo e os pedidos numa
    transação só (A-12), uma vez por processamento (`llm_chained`). Os jobs de pedido do job pai antigo voltam como pedido,
    no modo de antes. Sem provedor com chave, nada é feito nem marcado: os pedidos seguem vivos e o próximo pedido com chave
    refaz o encadeamento (R-04). Devolve o job do núcleo."""
    with store.transaction():
        if parent_job_id in summary_gate.ledger(store, meeting_id).chained:
            return None
        if store.providers.active_with_key("llm") is None:
            log.info("reunião %s, job %s: sem provedor de LLM com chave; o núcleo e os pedidos esperam a chave", meeting_id,
                     parent_job_id)
            return None
        core = _carry_requests(store, meeting_id, parent_job_id)
        job_id = enqueue_after_pipeline(store, meeting_id, parent_job_id, kind, core)
        _consume(store, meeting_id, parent_job_id)
        store.add_event(meeting_id, None, EventType.LLM_CHAINED, {"parent_job_id": parent_job_id, "job_id": job_id})
    return job_id


def _carry_requests(store: Store, meeting_id: int, parent: int) -> set[JobStep]:
    """Os jobs de pedido ainda na fila para outro job pai (o reprocessamento trocou a transcrição): cancelados e de volta como
    pedido do mesmo modo (o "quando todos tiverem nome" volta a esperar as vozes da transcrição nova); devolve o núcleo deles."""
    core: set[JobStep] = set()
    for job in store.jobs.for_meeting(meeting_id):
        params, tasks = job_params(job), set(tasks_of(job))
        if (str(job.get("kind")) != JobKind.LLM or str(job["status"]) != JobStatus.PENDING or params.get("auto")
                or params.get("parent_job_id") in (None, parent) or not tasks & REQUEST_ONLY):
            continue
        if store.jobs.request_cancel(int(job["id"])) != JobStatus.CANCELLED:
            continue
        store.add_event(meeting_id, None, EventType.LLM_REQUESTED, {
            "tasks": [str(t) for t in TASK_ORDER if t in tasks & REQUEST_ONLY], "mode": str(params.get("mode") or Mode.NOW),
            "origin": "pedido refeito sobre a transcrição nova", "after_job_id": parent})
        core |= tasks - REQUEST_ONLY - {JobStep.TEST}
    return core


def _consume(store: Store, meeting_id: int, parent: int) -> None:
    led = summary_gate.ledger(store, meeting_id)
    jobs = {int(j["id"]): j for j in store.jobs.for_meeting(meeting_id)}
    asks = [a for a in led.asked if not led.consumed(a.request_id) and _here(a, parent, jobs, led)]
    when = {a.request_id: a.created_at for a in led.asked}
    asks += [Asked(rid, tuple(str(t) for t in d.get("tasks") or ()), str(Mode.NAMES), "adiado antes do reprocessamento", None,
                   when.get(rid, "")) for rid, d in led.open() if int(d.get("parent_job_id") or 0) != parent]
    if not asks:
        return
    if not store.segments.count_segments(meeting_id, parent, with_text=True):
        for a in asks:
            _released(store, meeting_id, parent, a.request_id, a.tasks, None, NO_SPEECH)
        log.info("reunião %s, job %s: %d pedido(s) sem efeito: %s", meeting_id, parent, len(asks), NO_SPEECH)
        return
    _apply(store, meeting_id, parent, asks, "fim do processamento", _concluded_id(store, meeting_id))


def _here(a: Asked, parent: int, jobs: Mapping[int, JobRow], led: Ledger) -> bool:
    """O pedido é deste processamento: preso a nada (o próximo), a ele ou ao que ele substituiu (R-01), ou a um concluído
    que não encadeou a LLM (R-02, R-04)."""
    if a.after_job_id is None:
        return True
    target = follow(jobs, a.after_job_id)
    job = jobs.get(target)
    return target == parent or (job is not None and str(job["status"]) == JobStatus.DONE and target not in led.chained)


def _concluded_id(store: Store, meeting_id: int) -> int:
    """O evento do último "Concluir" da revisão, se a reunião segue revisada (0 senão). A ordem é pelo id do evento: o
    horário empata no mesmo milissegundo."""
    if str((store.get_meeting(meeting_id) or {}).get("status")) != MeetingStatus.REVIEWED:
        return 0
    return max((int(e["id"]) for e in store.meetings.list_events_of(meeting_id, (EventType.PROFILE_CONFIRM,))
                if isinstance(e.get("data"), dict) and e["data"].get("review") == CONCLUDED), default=0)


def _waits(meeting_id: int, a: Asked, pending: int, concluded: int) -> bool:
    """"Quando todos tiverem nome" espera enquanto houver voz pendente; o pedido feito antes do "Concluir" de uma reunião que
    segue revisada, não (o R-01 da F5.2: nada mais o liberaria). O feito depois espera como os outros (R-03)."""
    if a.mode != Mode.NAMES or not pending:
        return False
    if a.request_id < concluded:
        log.info("reunião %s: pedido %s anterior ao \"Concluir\"; não espera as %d voz(es) sem nome (R-01)", meeting_id,
                 a.request_id, pending)
        return False
    return True


def _apply(store: Store, meeting_id: int, parent: int, asks: Sequence[Asked], reason: str,
           concluded: int = 0) -> tuple[int | None, bool]:
    """(job, algum adiado): "quando todos tiverem nome" com voz pendente espera; o resto sai num job só. Sem job (sem chave
    na hora), nada é liberado: o pedido segue vivo (R-04)."""
    pending = summary_gate.pending_voices(store, meeting_id)
    waiting = [a for a in asks if _waits(meeting_id, a, pending, concluded)]
    for a in waiting:
        summary_gate.record(store, meeting_id, parent, a.tasks, pending, a.request_id)
    held = {a.request_id for a in waiting}
    now = [a for a in asks if a.request_id not in held]
    if not now:
        return None, bool(waiting)
    tasks = resolve(store, meeting_id, [t for a in now for t in a.tasks])
    mode = Mode.NOW if any(a.mode == Mode.NOW for a in now) else Mode.NAMES
    job_id = _enqueue_or_join(store, meeting_id, tasks, parent, mode)
    if job_id is None:
        log.info("reunião %s: %d pedido(s) sem job agora (sem provedor de LLM com chave); seguem pedidos", meeting_id, len(now))
        return None, bool(waiting)
    for a in now:
        _released(store, meeting_id, parent, a.request_id, [str(t) for t in tasks], job_id, reason)
    _trim(store, meeting_id, parent, {t for a in now for t in a.tasks}, job_id)
    return job_id, bool(waiting)


def _released(store: Store, meeting_id: int, parent: int, request_id: int, tasks: Iterable[str], job_id: int | None,
              reason: str) -> None:
    store.add_event(meeting_id, None, EventType.LLM_RELEASED, {"parent_job_id": parent, "tasks": list(tasks), "job_id": job_id,
                                                               "reason": reason, "request_id": request_id})


def _trim(store: Store, meeting_id: int, parent: int, tasks: set[str], job_id: int) -> None:
    """O pedido "agora" tira as tarefas dele dos adiamentos pendentes do job pai: o adiamento fica com o resto (que a
    liberação recalcula, P-07) ou sai, vazio, atendido por este job."""
    for rid, d in summary_gate.ledger(store, meeting_id).open():
        kept = [str(t) for t in d.get("tasks") or ()]
        if int(d.get("parent_job_id") or 0) != parent or not tasks & set(kept):
            continue
        rest = [t for t in kept if t not in tasks]
        if rest:
            summary_gate.record(store, meeting_id, parent, rest, int(d.get("pending") or 0), rid)
        else:
            _released(store, meeting_id, parent, rid, kept, job_id, "atendido por outro pedido")


def release(store: Store, meeting_id: int, reason: str, *, force: bool = False) -> int | None:
    """U-042, F5.6 (A-03, P-01): os pedidos adiados do job vigente saem uma vez, sem voz pendente (a revisão resolveu a
    última) ou com `force` (o "Concluir"). Só o que foi pedido. Nada com processamento na fila (o fim dele os refaz sobre a
    transcrição nova) nem sem provedor com chave (seguem adiados). Devolve o job `llm`."""
    with store.transaction():
        d = summary_gate.deferred(store, meeting_id)
        if d is None:
            return None
        if d.pending and not force:
            log.debug("reunião %s: o pedido segue adiado (%d voz(es) pendente(s))", meeting_id, d.pending)
            return None
        heavy = active_jobs(store, meeting_id)
        if heavy:
            log.info("reunião %s: o pedido adiado não sai agora (%s): o processamento %s o refaz ao terminar", meeting_id, reason,
                     heavy[0]["id"])
            return None
        tasks = resolve(store, meeting_id, d.tasks)
        job_id = _enqueue_or_join(store, meeting_id, tasks, d.parent_job_id, Mode.NAMES)
        if job_id is None:
            log.info("reunião %s: o pedido adiado não saiu (%s): sem provedor de LLM com chave; segue adiado", meeting_id, reason)
            return None
        for rid in d.request_ids:
            _released(store, meeting_id, d.parent_job_id, rid, [str(t) for t in tasks], job_id, reason)
    log.info("reunião %s: pedido(s) %s liberado(s) (%s; %d voz(es) ainda sem nome): job llm %s (%s)", meeting_id,
             list(d.request_ids), reason, d.pending, job_id, ", ".join(str(t) for t in tasks))
    return job_id


def _enqueue_or_join(store: Store, meeting_id: int, tasks: Sequence[JobStep], parent: int, mode: Mode) -> int | None:
    """A-10: um job `llm` da reunião, do mesmo job pai, que ainda vai fazer estas tarefas (pendente, ou rodando sem ter
    gravado nenhuma delas, R-08) atende o pedido; senão um job novo, atrás dele na fila, com `auto=False` (A-09) e o modo."""
    want = {str(t) for t in tasks}
    if not want:
        return None
    for job in active_llm(store, meeting_id):
        if int(job_params(job).get("parent_job_id") or 0) != parent or not want <= {str(t) for t in tasks_of(job)}:
            continue
        if str(job["status"]) != JobStatus.PENDING and any(0 in store.jobs.artifact_indexes(int(job["id"]), t) for t in want):
            continue                                     # R-08: já passou de alguma delas; o pedido entra atrás dele
        log.info("reunião %s: o pedido (%s) fica com o job llm %s, que ainda o faz (A-10)", meeting_id, ", ".join(sorted(want)),
                 job["id"])
        return int(job["id"])
    return enqueue_llm_job(store, meeting_id, [t for t in TASK_ORDER if str(t) in want], parent, requested=True, mode=str(mode))


# ---------------------------------------------------------------- avisos (A-06: o controlador só pergunta)
def summary_waits(store: Store, meeting_id: int) -> bool:
    """U-042: "a ata sai depois que as vozes forem identificadas" só com um pedido "quando todos tiverem nome": já adiado, ou
    ainda por consumir (o aviso do fim do processamento pode chegar antes do enfileiramento, A-12) que vai esperar."""
    led = summary_gate.ledger(store, meeting_id)
    if summary_gate.deferred(store, meeting_id, led) is not None:
        return True
    by_id = {int(j["id"]): j for j in store.jobs.for_meeting(meeting_id)}
    names = [a for a in led.asked if a.mode == Mode.NAMES and not led.consumed(a.request_id) and alive(a, led, by_id)]
    if not names:
        return False
    pending, concluded = summary_gate.pending_voices(store, meeting_id), _concluded_id(store, meeting_id)
    return any(_waits(meeting_id, a, pending, concluded) for a in names)


def identity_only(store: Store, meeting_id: int, job: Mapping[str, Any]) -> bool:
    """O job `llm` só do núcleo enquanto um pedido espera as vozes (A-14: o balão "LLM concluída" enganaria)."""
    try:
        done = set(json.loads(job.get("timings_json") or "{}").get("llm", {}).get("done") or [])
    except (ValueError, AttributeError):
        return False
    return bool(done) and done <= {str(s) for s in summary_gate.EARLY} and summary_gate.deferred(store, meeting_id) is not None


__all__ = ["Estimate", "Mode", "Outcome", "RequestResult", "RequestState", "Species", "State", "Voices", "after_pipeline", "estimate",
           "identity_only", "pending_core", "release", "request", "request_state", "request_states", "resolve", "summary_waits",
           "voices"]
