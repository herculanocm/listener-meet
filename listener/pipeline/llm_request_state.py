"""F5.6 L1a (A-13 a A-17, P-07): o lado de leitura dos pedidos da LLM, sem interface: o estado de "Ata e ações" e de
"Resumo" (`request_states`, numa leitura por recarga, A-15), por que uma versão está desatualizada (calculado: o job, o
idioma, os nomes da F5.5 v2, o resumo mais antigo que a ata), quais tarefas um pedido leva (`resolve`: o resumo sai da ata,
que vai junto só quando falta ou está desatualizada, P-07), o custo (`estimate`) e as vozes sem nome (`voices`). Os pedidos
em si ficam em `llm_requests`, que reexporta tudo isto. L1b (o "Para o L1b" do L1a): o custo inclui o núcleo quando o
encadeamento do processamento vigente ficou pendente (`pending_core`: processado sem chave, ou o encadeamento que falhou),
porque o próximo pedido o põe na fila na frente da ata. F5.6 L2 (A-29): "falhou" com a resposta fora do esquema numa tarefa
que usava instruções do usuário (as do job) ainda nas Configurações diz quais (`custom`), para a tela oferecer "Restaurar o
prompt padrão". Revisão do L2: só a tarefa que falhou fora do esquema (`timings_json.llm.off_schema`, R-01) e só quando o
texto salvo agora é o mesmo que o job usou (R-02)."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from ..config import prompts as prompt_settings
from ..config.defaults import SPECS
from ..config.language import meeting_language, output_code
from ..llm.base import OFF_SCHEMA
from ..llm.costs import estimate_tokens, price_for
from ..llm.jobconfig import LlmJobConfig
from ..model import EventType, JobKind, JobStatus, JobStep, SummaryKind
from ..store.jobs import ACTIVE
from . import summary_gate
from .llm_steps import REQUEST_ONLY, TASK_ORDER, TASKS_AFTER_MEETING, auto_tasks_for, estimate_steps, job_params, tasks_of
from .reprocess import REPLACES, target_language
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
    job_id: int | None = None           # falhou: o job
    custom: tuple[str, ...] = ()        # falhou fora do esquema com instruções suas: as tarefas (A-29, "Restaurar o prompt padrão")


@dataclass(frozen=True, slots=True)
class Estimate:
    usd: float | None                   # None: sem provedor com modelo ou sem preço de algum modelo
    tokens: int                         # da transcrição (ou da duração)
    by_duration: bool                   # sem transcrição: pela duração ("~ estimado", A-17)
    tasks: tuple[str, ...]              # as tarefas contadas (a ata junto do resumo quando ela vai, P-07)
    core: tuple[str, ...] = ()          # o núcleo pendente que vai na frente (`pending_core`), contado no custo


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


def unchained(store: Store, meeting_id: int, job_id: int) -> bool:
    """O processamento concluído por esta versão do runner (`llm_chain` no `job_done`) sem o marcador `llm_chained`: o
    encadeamento da LLM ainda não rodou (a corrida entre o `finish` e o enfileiramento) ou falhou (A-12, R-02, R-05)."""
    if job_id in summary_gate.ledger(store, meeting_id).chained:
        return False
    return any(isinstance(e.get("data"), dict) and e["data"].get("job_id") == job_id and e["data"].get("llm_chain")
               for e in store.meetings.list_events_of(meeting_id, (EventType.JOB_DONE,)))


def pending_core(store: Store, meeting_id: int) -> tuple[JobStep, ...]:
    """L1b: o núcleo que o próximo pedido encadeia antes da ata, quando o processamento vigente não encadeou (sem chave no
    fim dele, R-04; o encadeamento que falhou, R-02): o que `enqueue_after_pipeline` poria na fila. Vazio quando já encadeou,
    sem transcrição pronta ou com `llm.auto_tasks` desligado (salvo a troca de idioma, que refaz o núcleo, A-06)."""
    ready = ready_job(store, meeting_id)
    if ready is None or not unchained(store, meeting_id, ready):
        return ()
    job = store.jobs.get(ready) or {}
    if target_language(job) is not None:
        core = set(TASKS_AFTER_MEETING)
    elif store.get(SPECS["llm.auto_tasks"]):
        core = set(auto_tasks_for(store, meeting_id, str(job.get("kind") or JobKind.MEETING)))
    else:
        core = set()
    return tuple(t for t in TASK_ORDER if t in core)


def ready_job(store: Store, meeting_id: int) -> int | None:
    """O job vigente quando já terminou (a transcrição pronta); None sem processamento concluído."""
    job_id = store.segments.current_job_id(meeting_id)
    row = store.jobs.get(job_id) if job_id is not None else None
    return job_id if row is not None and str(row["status"]) == JobStatus.DONE else None


def resolve(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], *, new_transcript: bool = False) -> list[JobStep]:
    """P-07: o resumo sai da ata; ela vai junto só quando falta ou está desatualizada e nenhum job da fila a gera antes.
    `new_transcript` (revisão do L1b, R-03): o pedido fica preso a um processamento que ainda vai rodar, e a ata de agora
    estará desatualizada quando ele for aplicado."""
    want = {JobStep(str(t)) for t in tasks if str(t) in {str(s) for s in REQUEST_ONLY}}
    if want & SUMMARY_STEPS and JobStep.MINUTES not in want and (new_transcript or _minutes_outdated(store, meeting_id)):
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
def request_states(store: Store, meeting_id: int, *, led: Ledger | None = None, names: bool | None = None) -> dict[Species, RequestState]:
    """A-13, A-14: o estado de "Ata e ações" e de "Resumo" numa leitura (A-15), na ordem: falhou (o último job com a tarefa
    em erro sobre a transcrição vigente, salvo um pedido mais novo), gerando, pedido, desatualizado (calculado), gerado em, não
    gerado. `led` e `names` (o livro dos pedidos e `names_changed`), quando quem chama já os leu na mesma recarga (R-06)."""
    led = led if led is not None else summary_gate.ledger(store, meeting_id)
    jobs = store.jobs.for_meeting(meeting_id)
    rows = store.summaries.current_all(meeting_id)
    now = fresh(store, meeting_id, names)
    by_id = {int(j["id"]): j for j in jobs}
    llm = [j for j in jobs if str(j.get("kind")) == JobKind.LLM]
    return {sp: blamed(store, sp, _state(sp, led, llm, by_id, rows, now), by_id) for sp in Species}


def request_state(store: Store, meeting_id: int, species: Species | str) -> RequestState:
    return request_states(store, meeting_id)[Species(str(species))]


def _state(sp: Species, led: Ledger, llm: list[JobRow], by_id: Mapping[int, JobRow], rows: Mapping[str, SummaryRow],
           now: Fresh) -> RequestState:
    steps = {str(t) for t in SPECIES_TASKS[sp]}
    asked_at = max((a.created_at for a in led.asked if steps & set(a.tasks) and alive(a, led, by_id)), default=None)
    failed = _failed(llm, steps, now.job_id)
    if failed is not None and (asked_at is None or asked_at < str(failed.get("finished_at") or "")):
        return RequestState(State.FAILED, error=str(failed.get("error") or ""), job_id=int(failed["id"]))
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


def _failed(llm: list[JobRow], steps: set[str], current: int | None) -> JobRow | None:
    """O último job com uma das tarefas, quando terminou em erro sem as fazer todas (o `done` das que saíram) sobre a
    transcrição vigente (`current`). R-01 da revisão do L1b: a falha sobre uma transcrição que o reprocessamento ou a troca de
    idioma trocou não é mais "falhou" (o "Gerar de novo" pediria sem perguntar sobre a transcrição nova): a espécie fica no
    estado da versão que existe (desatualizada pela transcrição) ou "não gerado"."""
    last = next((j for j in llm if steps & {str(t) for t in tasks_of(j)}), None)     # `for_meeting`: o mais novo primeiro
    if last is None or str(last["status"]) != JobStatus.ERROR or job_params(last).get("parent_job_id") != current:
        return None
    return None if steps <= done_steps(last) else last


def done_steps(job: JobRow) -> set[str]:
    """As tarefas que o job fez (`timings_json.llm.done`)."""
    return job_steps(job, "done")


def job_steps(job: JobRow, key: str) -> set[str]:
    """Uma lista de tarefas do `timings_json.llm` do job: `done` ou `off_schema` (as que falharam fora do esquema, R-01)."""
    try:
        return set(json.loads(job.get("timings_json") or "{}").get("llm", {}).get(key) or ())
    except (ValueError, AttributeError, TypeError):
        return set()


def blamed(store: Store, sp: Species, rs: RequestState, by_id: Mapping[int, JobRow]) -> RequestState:
    """A-29: "falhou" com a resposta fora do esquema numa tarefa da espécie que rodou com instruções do usuário (as do
    `config_json` do job): o estado leva essas tarefas em `custom`. Revisão do L2: só as que falharam elas mesmas fora do
    esquema (R-01: a ata do app fora do esquema não culpa o executivo personalizado, que nem chegou a chamar a LLM) e só
    quando o texto salvo agora é o que o job usou (R-02: um texto novo, ainda não usado, não leva a culpa)."""
    job = by_id.get(rs.job_id) if rs.job_id is not None else None
    if rs.state != State.FAILED or job is None or OFF_SCHEMA not in rs.error:
        return rs
    used = LlmJobConfig.from_json(job.get("config_json")).prompts
    off = job_steps(job, "off_schema") & {str(t) for t in SPECIES_TASKS[sp]} & set(used)
    if not off:
        return rs
    now = prompt_settings.custom_prompts(store)
    same = [str(t) for t in TASK_ORDER if str(t) in off and now.get(str(t), "").strip() == used[str(t)].strip()]
    return replace(rs, custom=tuple(same))


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


def fresh(store: Store, meeting_id: int, names: bool | None = None) -> Fresh:
    language = output_code(meeting_language(store, store.get_meeting(meeting_id)))
    changed = summary_gate.names_changed(store, meeting_id) if names is None else names
    return Fresh(store.segments.current_job_id(meeting_id), language, changed)


def estimate(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], *, new_transcript: bool = False) -> Estimate:
    """A-17: o custo do pedido pela transcrição (`LENGTH(text)` em SQL, sem montar o contexto da LLM na thread da tela) ou,
    sem ela, pela duração (`TOKENS_PER_MIN`, "~ estimado"). L1b: com o encadeamento pendente, o núcleo entra na conta; preso
    a um processamento por rodar (`new_transcript`, R-03), a ata conta como desatualizada e o núcleo de agora não entra (o
    processamento novo o encadeia sozinho)."""
    steps = resolve(store, meeting_id, tasks, new_transcript=new_transcript)
    ready = ready_job(store, meeting_id)
    core: tuple[JobStep, ...] = ()
    if ready is not None:
        chars, lines = store.segments.text_size(meeting_id, ready)
        tokens, by_duration = estimate_tokens(chars + LINE_CHARS * lines), False
        core = pending_core(store, meeting_id) if steps and not new_transcript else ()
    else:
        minutes = int((store.get_meeting(meeting_id) or {}).get("duration_ms") or 0) / 60_000
        tokens, by_duration = int(minutes * TOKENS_PER_MIN), True
    cfg = LlmJobConfig.from_store(store)
    usd: float | None = None
    if cfg.ready and steps:
        prices = store.providers.list_prices(cfg.provider_kind or None)
        per = estimate_steps(tokens, [*core, *steps], lambda strong: price_for(prices, cfg.provider_kind, cfg.model_for(strong)))
        usd = None if any(v is None for v in per.values()) else round(sum(v or 0.0 for v in per.values()), 4)
    return Estimate(usd, tokens, by_duration, tuple(str(s) for s in steps), tuple(str(c) for c in core))


def voices(store: Store, meeting_id: int) -> Voices:
    """A-16: as vozes sem nome pela regra da revisão (`summary_gate.pending_voices`), com os três casos: sem transcrição (a
    identificação ainda não rodou), a identidade rodando e nenhuma."""
    ready = ready_job(store, meeting_id) is not None
    identifying = any(JobStep.IDENTIFY in tasks_of(j) for j in active_llm(store, meeting_id))
    return Voices(summary_gate.pending_voices(store, meeting_id) if ready else 0, ready, identifying)


__all__ = ["Estimate", "Fresh", "RequestState", "Species", "State", "Voices", "active_llm", "alive", "blamed", "done_steps", "estimate",
           "follow", "fresh", "job_steps", "pending_core", "ready_job", "request_state", "request_states", "resolve", "stale",
           "unchained", "voices"]
