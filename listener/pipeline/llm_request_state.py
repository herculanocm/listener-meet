"""F5.6 L1a (A-13 a A-17, P-07): o lado de leitura dos pedidos da LLM, sem interface: o estado de "Ata e ações" e de
"Resumo" (`request_states`, numa leitura por recarga, A-15), por que uma versão está desatualizada (calculado: o job, o
idioma, os nomes da F5.5 v2, o resumo mais antigo que a ata), quais tarefas um pedido leva (`resolve`: o resumo sai da ata,
que vai junto só quando falta ou está desatualizada, P-07), o custo (`estimate`) e as vozes sem nome (`voices`). Os pedidos
em si ficam em `llm_requests`, que reexporta tudo isto."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ..config.language import meeting_language, output_code
from ..llm.costs import estimate_tokens, price_for
from ..llm.jobconfig import LlmJobConfig
from ..model import JobKind, JobStatus, JobStep, SummaryKind
from ..store.jobs import ACTIVE
from . import summary_gate
from .llm_steps import REQUEST_ONLY, TASK_ORDER, estimate_steps, job_params, tasks_of
from .reprocess import REPLACES
from .summary_gate import Asked, Ledger

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow
    from ..store.summaries import SummaryRow

TOKENS_PER_MIN = 250        # [chute] sem transcrição, o custo pela duração (A-17; calibrar no banco do dono)
LINE_CHARS = 20             # [chute] o "[mm:ss] Nome (cargo):" de cada fala, além do texto
SUMMARY_STEPS = frozenset({JobStep.EXECUTIVE, JobStep.PRE_SUMMARY})


class Species(StrEnum):
    MINUTES = "minutes"         # "Ata e ações": uma chamada
    SUMMARY = "summary"         # "Resumo": executivo e pré-resumo, que saem da ata


class State(StrEnum):
    FAILED = "falhou"
    RUNNING = "gerando"
    REQUESTED = "pedido"
    STALE = "desatualizado"
    DONE = "gerado"
    NONE = "não gerado"


SPECIES_TASKS: dict[Species, tuple[JobStep, ...]] = {Species.MINUTES: (JobStep.MINUTES,),
                                                     Species.SUMMARY: (JobStep.EXECUTIVE, JobStep.PRE_SUMMARY)}
SPECIES_KINDS: dict[Species, tuple[SummaryKind, ...]] = {Species.MINUTES: (SummaryKind.MINUTES,),
                                                         Species.SUMMARY: (SummaryKind.EXECUTIVE, SummaryKind.PRE_SUMMARY)}
# por que uma versão está desatualizada (A-13): a tela do L1b diz o motivo
STALE_JOB, STALE_LANGUAGE, STALE_NAMES, STALE_MINUTES, STALE_PARTIAL = "transcrição", "idioma", "nomes", "ata nova", "incompleto"


@dataclass(frozen=True, slots=True)
class RequestState:
    state: State
    when: str | None = None             # `created_at` da versão atual (gerado em dd/mm; também no desatualizado)
    reasons: tuple[str, ...] = ()       # desatualizado: os motivos (STALE_*)
    error: str = ""                     # falhou: o erro do job (já redigido)


@dataclass(frozen=True, slots=True)
class Estimate:
    usd: float | None                   # None: sem provedor com modelo ou sem preço de algum modelo
    tokens: int                         # da transcrição (ou da duração)
    by_duration: bool                   # sem transcrição: pela duração ("~ estimado", A-17)
    tasks: tuple[str, ...]              # as tarefas contadas (a ata junto do resumo quando ela vai, P-07)


@dataclass(frozen=True, slots=True)
class Voices:
    pending: int                        # vozes sem nome agora (0 também sem transcrição)
    ready: bool                         # há transcrição: sem ela, "a identificação ainda não rodou" (A-16)
    identifying: bool                   # a identidade por contexto está na fila ou rodando


@dataclass(frozen=True, slots=True)
class Fresh:
    job_id: int | None                  # o job vigente
    language: str                       # o idioma da reunião, no código da saída
    names: bool                         # os nomes mudaram depois da ata (F5.5 v2)


def ready_job(store: Store, meeting_id: int) -> int | None:
    """O job vigente quando já terminou (a transcrição pronta); None sem processamento concluído."""
    job_id = store.segments.current_job_id(meeting_id)
    row = store.jobs.get(job_id) if job_id is not None else None
    return job_id if row is not None and str(row["status"]) == JobStatus.DONE else None


def resolve(store: Store, meeting_id: int, tasks: Iterable[JobStep | str]) -> list[JobStep]:
    """P-07: o resumo sai da ata; ela vai junto só quando falta ou está desatualizada e nenhum job da fila a gera antes."""
    want = {JobStep(str(t)) for t in tasks if str(t) in {str(s) for s in REQUEST_ONLY}}
    if want & SUMMARY_STEPS and JobStep.MINUTES not in want and _minutes_outdated(store, meeting_id):
        want.add(JobStep.MINUTES)
    return [t for t in TASK_ORDER if t in want]


def _minutes_outdated(store: Store, meeting_id: int) -> bool:
    if any(JobStep.MINUTES in tasks_of(j) for j in active_llm(store, meeting_id)):
        return False
    rows = store.summaries.current_all(meeting_id)
    ata = rows.get(str(SummaryKind.MINUTES))
    return ata is None or bool(stale(Species.MINUTES, [ata], rows, fresh(store, meeting_id)))


def active_llm(store: Store, meeting_id: int) -> list[JobRow]:
    return [j for j in store.jobs.for_meeting(meeting_id) if str(j.get("kind")) == JobKind.LLM and str(j["status"]) in ACTIVE
            and not j.get("cancel_requested")]


# ---------------------------------------------------------------- o que a tela mostra (L1b)
def request_states(store: Store, meeting_id: int) -> dict[Species, RequestState]:
    """A-13, A-14: o estado de "Ata e ações" e de "Resumo" numa leitura (A-15), na ordem: falhou (o último job com a tarefa
    em erro, salvo um pedido mais novo), gerando, pedido, desatualizado (calculado), gerado em, não gerado."""
    led = summary_gate.ledger(store, meeting_id)
    jobs = store.jobs.for_meeting(meeting_id)
    rows = store.summaries.current_all(meeting_id)
    now = fresh(store, meeting_id)
    by_id = {int(j["id"]): j for j in jobs}
    llm = [j for j in jobs if str(j.get("kind")) == JobKind.LLM]
    return {sp: _state(sp, led, llm, by_id, rows, now) for sp in Species}


def request_state(store: Store, meeting_id: int, species: Species | str) -> RequestState:
    return request_states(store, meeting_id)[Species(str(species))]


def _state(sp: Species, led: Ledger, llm: list[JobRow], by_id: Mapping[int, JobRow], rows: Mapping[str, SummaryRow],
           now: Fresh) -> RequestState:
    steps = {str(t) for t in SPECIES_TASKS[sp]}
    asked_at = max((a.created_at for a in led.asked if steps & set(a.tasks) and alive(a, led, by_id)), default=None)
    failed = _failed(llm, steps)
    if failed is not None and (asked_at is None or asked_at < str(failed.get("finished_at") or "")):
        return RequestState(State.FAILED, error=str(failed.get("error") or ""))
    if any(str(j["status"]) in ACTIVE and not j.get("cancel_requested") and steps & {str(t) for t in tasks_of(j)} for j in llm):
        return RequestState(State.RUNNING)
    if asked_at is not None:
        return RequestState(State.REQUESTED)
    present = [rows.get(str(k)) for k in SPECIES_KINDS[sp]]
    if not any(present):
        return RequestState(State.NONE)
    when = max(str(r.get("created_at") or "") for r in present if r)
    reasons = stale(sp, present, rows, now)
    return RequestState(State.STALE if reasons else State.DONE, when, reasons)


def alive(a: Asked, led: Ledger, by_id: Mapping[int, JobRow]) -> bool:
    """O pedido ainda vale: adiado e não liberado; ou por consumir e preso a nada (o próximo processamento), ao processamento
    que faz o trabalho do seu (o substituto da troca de idioma, R-01) enquanto ativo, ou a um concluído que ainda não
    encadeou a LLM (`llm_chained`: a corrida do A-12, um encadeamento que falhou ou sem chave, R-02, R-04). Preso a um
    desfeito (cancelado sem substituto), não."""
    if led.consumed(a.request_id):
        return a.request_id in led.deferrals and a.request_id not in led.released
    if a.after_job_id is None:
        return True
    job = by_id.get(follow(by_id, a.after_job_id))
    if job is None:
        return False
    if str(job["status"]) in ACTIVE:
        return not job.get("cancel_requested")
    return str(job["status"]) == JobStatus.DONE and int(job["id"]) not in led.chained


def follow(by_id: Mapping[int, JobRow], job_id: int) -> int:
    """O processamento que hoje faz o trabalho de `job_id`: ele mesmo ou, quando uma troca de idioma o cancelou e pôs outro
    no lugar (`replaces` nos params do novo), o último substituto (R-01)."""
    succ = {int(r): int(j["id"]) for j in by_id.values() if isinstance(r := job_params(j).get(REPLACES), int)}
    seen = {job_id}
    while job_id in succ and succ[job_id] not in seen:
        job_id = succ[job_id]
        seen.add(job_id)
    return job_id


def _failed(llm: list[JobRow], steps: set[str]) -> JobRow | None:
    """O último job com uma das tarefas, quando terminou em erro sem as fazer todas (o `done` das que saíram)."""
    last = next((j for j in llm if steps & {str(t) for t in tasks_of(j)}), None)     # `for_meeting`: o mais novo primeiro
    if last is None or str(last["status"]) != JobStatus.ERROR:
        return None
    try:
        done = set(json.loads(last.get("timings_json") or "{}").get("llm", {}).get("done") or ())
    except (ValueError, AttributeError):
        done = set()
    return None if steps <= done else last


def stale(sp: Species, present: Sequence[SummaryRow | None], rows: Mapping[str, SummaryRow], now: Fresh) -> tuple[str, ...]:
    """Por que as versões `present` de uma espécie estão desatualizadas (A-13); vazio = em dia."""
    real = [r for r in present if r]
    out: list[str] = [STALE_PARTIAL] if len(real) < len(present) else []
    if any(r.get("job_id") is not None and r.get("job_id") != now.job_id for r in real):
        out.append(STALE_JOB)
    if any(r.get("language") and output_code(r.get("language")) != now.language for r in real):
        out.append(STALE_LANGUAGE)
    if now.names:
        out.append(STALE_NAMES)
    ata = rows.get(str(SummaryKind.MINUTES))
    if sp == Species.SUMMARY and ata is not None and any(int(r["id"]) < int(ata["id"]) for r in real):
        out.append(STALE_MINUTES)
    return tuple(out)


def fresh(store: Store, meeting_id: int) -> Fresh:
    language = output_code(meeting_language(store, store.get_meeting(meeting_id)))
    return Fresh(store.segments.current_job_id(meeting_id), language, summary_gate.names_changed(store, meeting_id))


def estimate(store: Store, meeting_id: int, tasks: Iterable[JobStep | str]) -> Estimate:
    """A-17: o custo do pedido pela transcrição (`LENGTH(text)` em SQL, sem montar o contexto da LLM na thread da tela) ou,
    sem ela, pela duração (`TOKENS_PER_MIN`, "~ estimado")."""
    steps = resolve(store, meeting_id, tasks)
    ready = ready_job(store, meeting_id)
    if ready is not None:
        chars, lines = store.segments.text_size(meeting_id, ready)
        tokens, by_duration = estimate_tokens(chars + LINE_CHARS * lines), False
    else:
        minutes = int((store.get_meeting(meeting_id) or {}).get("duration_ms") or 0) / 60_000
        tokens, by_duration = int(minutes * TOKENS_PER_MIN), True
    cfg = LlmJobConfig.from_store(store)
    usd: float | None = None
    if cfg.ready and steps:
        prices = store.providers.list_prices(cfg.provider_kind or None)
        per = estimate_steps(tokens, steps, lambda strong: price_for(prices, cfg.provider_kind, cfg.model_for(strong)))
        usd = None if any(v is None for v in per.values()) else round(sum(v or 0.0 for v in per.values()), 4)
    return Estimate(usd, tokens, by_duration, tuple(str(s) for s in steps))


def voices(store: Store, meeting_id: int) -> Voices:
    """A-16: as vozes sem nome pela regra da revisão (`summary_gate.pending_voices`), com os três casos: sem transcrição (a
    identificação ainda não rodou), a identidade rodando e nenhuma."""
    ready = ready_job(store, meeting_id) is not None
    identifying = any(JobStep.IDENTIFY in tasks_of(j) for j in active_llm(store, meeting_id))
    return Voices(summary_gate.pending_voices(store, meeting_id) if ready else 0, ready, identifying)


__all__ = ["Estimate", "Fresh", "RequestState", "Species", "State", "Voices", "active_llm", "alive", "estimate", "follow", "fresh",
           "ready_job", "request_state", "request_states", "resolve", "stale", "voices"]
