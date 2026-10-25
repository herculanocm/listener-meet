"""`LlmJobRunner` (F3 itens 2, 3, 5 a 8, 13, 15): o job `kind = llm` no worker leve. Ordem: identify -> minutes -> (executive,
pre_summary, title em paralelo sob `asyncio.Semaphore(llm.max_parallel)`) -> revise em blocos. Nenhuma tarefa levanta dentro do
`TaskGroup`: cada uma devolve `TaskOutcome`; as prontas ficam gravadas (artefato `ok` por tarefa) e `retry` refaz só as
faltantes; alguma falha -> job `erro` com a lista ("ata e executivo falharam: sem rede"). Tudo que o job escreve carrega o
`job_id` do pai (`params_json.parent_job_id`). Regras duras: nunca `await` dentro de `store.transaction()`; resultados gravados
tarefa a tarefa, só depois de o `Completion` chegar; o provedor nasce e fecha dentro do `asyncio.run()`; a chave é lida na
hora e nunca entra em `config_json`, eventos ou erros (redação). Modo hierárquico por tokens (60 % da janela) ou quando o
provedor devolve "request too large". Custo estimado antes de rodar: acima de `llm.cost_alert_usd`, as tarefas opcionais
(executivo e transcrição revisada) ficam de fora no job automático e a revisão oferece gerá-las (P5)."""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, override

from ..llm import prompts, redact
from ..log import get_logger
from ..llm.base import LlmError, LLMProvider, ProviderUnavailableError, RequestTooLargeError, classify_exception
from ..llm.costs import Price, cost_usd, estimate_cost, estimate_tokens, fmt_usd, price_for
from ..llm.evidence import check_identify, first_time_ms, valid_segment_ids
from ..llm.jobconfig import LlmJobConfig
from ..llm.models_table import model_info
from ..llm.prompts.common import Line, Marker, MeetingCtx, Person, render_lines
from ..llm.registry import build_provider
from ..llm.render import render_minutes
from ..llm.schema import schema_hash
from ..llm.types import TASK_CLASS, TASK_REASONING, Completion, LlmTask, Message, ModelClass, Request
from ..model import JOB_STEP_LABELS, ClusterStatus, EventType, JobKind, JobStatus, JobStep, LlmCallStatus, SummaryKind
from ..store import Store
from ..store.actions import ActionInsert
from ..store.jobs import JobRow
from ..store.llm_calls import LlmCallInsert
from ..store.segments import ClusterRow, SegmentRow, resolve_person
from ..store.summaries import SummaryInsert
from .jobbase import BaseJobRunner, Clock, JobCancelledError, Sleep
from .runner import USER_CLUSTER

log = get_logger("pipeline.llm")

STEP_TASK: dict[JobStep, LlmTask] = {JobStep.IDENTIFY: LlmTask.IDENTIFY, JobStep.MINUTES: LlmTask.MINUTES,
                                     JobStep.EXECUTIVE: LlmTask.EXECUTIVE, JobStep.PRE_SUMMARY: LlmTask.PRE_SUMMARY,
                                     JobStep.TITLE: LlmTask.TITLE, JobStep.REVISE: LlmTask.REVISE, JobStep.TEST: LlmTask.TEST}
TASK_ORDER: tuple[JobStep, ...] = (JobStep.TEST, JobStep.IDENTIFY, JobStep.MINUTES, JobStep.EXECUTIVE, JobStep.PRE_SUMMARY, JobStep.TITLE,
                                   JobStep.REVISE)
PARALLEL: tuple[JobStep, ...] = (JobStep.EXECUTIVE, JobStep.PRE_SUMMARY, JobStep.TITLE)
OPTIONAL: frozenset[JobStep] = frozenset({JobStep.EXECUTIVE, JobStep.REVISE})           # P5: automáticas só até o alerta de custo
TASKS_AFTER_MEETING: tuple[JobStep, ...] = (JobStep.IDENTIFY, JobStep.MINUTES, JobStep.EXECUTIVE, JobStep.PRE_SUMMARY, JobStep.TITLE,
                                            JobStep.REVISE)
TASKS_AFTER_REPROCESS: tuple[JobStep, ...] = (JobStep.IDENTIFY, JobStep.REVISE)         # P7: ata antiga fica, com aviso
PENDING = (str(ClusterStatus.AMBIGUOUS), str(ClusterStatus.UNNAMED))
WINDOW_SHARE = 0.6
BLOCK_SHARE = 0.4
IDENTIFY_NEIGHBOURS = 3
REVISE_BLOCK = 60
REVISE_CONTEXT = 5
SIM_MIN, SIM_MIN_SHORT, SHORT_CHARS = 0.6, 0.4, 20
MAX_OUTPUT: dict[LlmTask, int] = {LlmTask.IDENTIFY: 2048, LlmTask.MINUTES: 8192, LlmTask.EXECUTIVE: 1024, LlmTask.PRE_SUMMARY: 512,
                                  LlmTask.TITLE: 128, LlmTask.REVISE: 8192, LlmTask.TEST: 64}
# estimativa prévia por tarefa: (fator sobre os tokens da transcrição na entrada, tokens fixos de entrada, tokens de saída)
ESTIMATE: dict[JobStep, tuple[float, int, int]] = {JobStep.IDENTIFY: (1.0, 800, 1200), JobStep.MINUTES: (1.0, 600, 4000),
                                                   JobStep.EXECUTIVE: (0.0, 3000, 500), JobStep.PRE_SUMMARY: (0.0, 3000, 200),
                                                   JobStep.TITLE: (0.0, 400, 40), JobStep.REVISE: (1.3, 300, 0),
                                                   JobStep.TEST: (0.0, 50, 10)}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STOPWORDS = {"pt": {"de", "que", "não", "nao", "para", "com", "uma", "isso", "está", "esta", "mas", "ele", "ela", "você", "voce"},
             "en": {"the", "and", "that", "with", "this", "have", "for", "not", "you", "are", "was", "but"},
             "es": {"que", "los", "las", "pero", "una", "está", "esta", "con", "por", "para", "usted", "muy"}}


@dataclass(slots=True)
class TaskOutcome:
    step: JobStep
    ok: bool
    error: str | None = None
    permanent: bool = False
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class Context:
    meeting: dict[str, Any]
    meeting_ctx: MeetingCtx
    segments: tuple[SegmentRow, ...]
    clusters: tuple[ClusterRow, ...]
    people: dict[int, dict[str, Any]]
    lines: tuple[Line, ...]
    by_id: dict[int, tuple[str, int]]           # segment_id -> (texto, t_start_ms)
    candidates: tuple[Person, ...]
    marker_notes: dict[int, str]


# ---------------------------------------------------------------- enfileirar
def enqueue_llm_job(store: Store, meeting_id: int, tasks: Sequence[JobStep | str], parent_job_id: int | None = None,
                    *, auto: bool = False) -> int | None:
    """Job `llm` com a lista de tarefas; só com provedor ativo com chave (comportamento 6). `parent_job_id` nulo = o vigente."""
    if store.providers.active_with_key("llm") is None:
        log.info("reunião %s: sem provedor de LLM ativo com chave; nenhum job llm criado", meeting_id)
        return None
    parent = parent_job_id if parent_job_id is not None else store.segments.current_job_id(meeting_id)
    if parent is None:
        log.info("reunião %s: sem job de pipeline concluído; nenhum job llm criado", meeting_id)
        return None
    names = [str(t) for t in tasks]
    params: dict[str, Any] = {"tasks": names, "parent_job_id": int(parent), "auto": bool(auto)}
    job_id = store.jobs.enqueue(meeting_id, JobKind.LLM, params)
    log.info("reunião %s: job llm %s enfileirado (%s)", meeting_id, job_id, ", ".join(names))
    return job_id


def enqueue_after_pipeline(store: Store, meeting_id: int, parent_job_id: int, kind: JobKind | str) -> int | None:
    """Encadeado por `JobRunner._finish_ok` (F3A-01): tarefas automáticas conforme `llm.auto_tasks` e o tipo do job pai."""
    from ..config.defaults import SPECS

    if not store.get(SPECS["llm.auto_tasks"]):
        return None
    tasks = TASKS_AFTER_REPROCESS if str(kind) == JobKind.REPROCESS else TASKS_AFTER_MEETING
    return enqueue_llm_job(store, meeting_id, tasks, parent_job_id, auto=True)


def enqueue_test_job(store: Store, meeting_id: int | None = None) -> int | None:
    """"Testar pelo worker" (F3 item 12, R-2): job `llm` de tarefa `test` na reunião mais recente (a fila exige uma reunião)."""
    if meeting_id is None:
        meetings = store.list_meetings()
        if not meetings:
            return None
        meeting_id = int(meetings[0]["id"])
    return store.jobs.enqueue(meeting_id, JobKind.LLM, {"tasks": [str(JobStep.TEST)], "parent_job_id": None, "auto": False})


def llm_config_snapshot(store: Store) -> str:
    return LlmJobConfig.from_store(store).to_json()


def tasks_of(job: JobRow) -> list[JobStep]:
    try:
        params = json.loads(job.get("params_json") or "{}")
    except ValueError:
        params = {}
    out: list[JobStep] = []
    for t in params.get("tasks") or []:
        try:
            out.append(JobStep(str(t)))
        except ValueError:
            log.warning("job %s: tarefa desconhecida %r ignorada", job.get("id"), t)
    return out


# ---------------------------------------------------------------- utilidades puras (testáveis sem banco)
def split_blocks(lines: Sequence[Line], max_tokens: int) -> list[tuple[Line, ...]]:
    """Blocos de até `max_tokens` (caracteres ÷ 3,5) cortados em troca de falante quando possível (F3A-38)."""
    blocks: list[tuple[Line, ...]] = []
    cur: list[Line] = []
    cur_tokens = 0
    for i, ln in enumerate(lines):
        t = estimate_tokens(ln.render()) + 1
        change = bool(cur) and cur[-1].speaker != ln.speaker
        if cur and cur_tokens + t > max_tokens and (change or cur_tokens + t > max_tokens * 1.15):
            blocks.append(tuple(cur))
            cur, cur_tokens = [], 0
        cur.append(ln)
        cur_tokens += t
        if i == len(lines) - 1:
            blocks.append(tuple(cur))
    return blocks or [()]


def neighbourhood(lines: Sequence[Line], labels: set[str], radius: int = IDENTIFY_NEIGHBOURS) -> tuple[Line, ...]:
    """Só as falas dos pendentes com `radius` vizinhos de cada lado (as pistas estão perto, F3A-33)."""
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if ln.label in labels or ln.speaker in labels:
            keep.update(range(max(0, i - radius), min(len(lines), i + radius + 1)))
    return tuple(lines[i] for i in sorted(keep))


def _language_of(text: str) -> str | None:
    words = set(re.findall(r"\w+", text.casefold()))
    scores = {lang: len(words & sw) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] else None


def accept_revision(old: str, new: str) -> bool:
    """Proteção da transcrição revisada (F3A-39): semelhança mínima (0,6; 0,4 em falas curtas), mesmo número de dígitos,
    mesmo idioma aparente; nunca vazio."""
    new = (new or "").strip()
    if not new or new == old:
        return False
    ratio = difflib.SequenceMatcher(None, old, new).ratio()
    if ratio < (SIM_MIN_SHORT if len(old) < SHORT_CHARS else SIM_MIN):
        return False
    if sum(c.isdigit() for c in old) != sum(c.isdigit() for c in new):
        return False
    lo, ln = _language_of(old), _language_of(new)
    return not (lo and ln and lo != ln)


def match_person(name: str | None, people: dict[int, dict[str, Any]]) -> int | None:
    """Nome -> id de `people` por casefold (nome inteiro ou primeiro nome único)."""
    if not name:
        return None
    key = name.strip().casefold()
    if not key:
        return None
    exact = [pid for pid, p in people.items() if str(p.get("name") or "").casefold() == key]
    if len(exact) == 1:
        return exact[0]
    first = [pid for pid, p in people.items() if str(p.get("name") or "").casefold().split(" ")[0] == key.split(" ")[0]]
    return first[0] if len(first) == 1 else None


def resolve_language(output_language: str, meeting_language: str | None) -> str:
    if output_language != "auto":
        return output_language
    lang = (meeting_language or "pt").lower()
    return "pt-BR" if lang.startswith("pt") else lang


def build_context(store: Store, meeting_id: int, parent_job_id: int, output_language: str) -> Context:
    meeting: dict[str, Any] = dict(store.get_meeting(meeting_id) or {})
    segments = tuple(store.segments.list_segments(meeting_id, parent_job_id))
    clusters = tuple(store.segments.list_clusters(meeting_id, parent_job_id))
    by_cluster = {int(c["id"]): c for c in clusters}
    people = {int(p["id"]): dict(p) for p in store.people.list_people()}
    me = store.get_me()
    lines: list[Line] = []
    by_id: dict[int, tuple[str, int]] = {}
    for s in segments:
        text = str(s.get("text") or "")
        cid = s.get("cluster_id")
        crow = by_cluster.get(int(cid)) if cid is not None else None
        pid = s.get("person_id")
        if pid is None and crow is not None:
            pid = resolve_person(crow, by_cluster)
        label = str(crow["cluster"]) if crow is not None else None
        if pid is not None and int(pid) in people:
            p = people[int(pid)]
            speaker = str(p["name"]) + (f" ({p['role']})" if p.get("role") else "")
        elif crow is not None and str(crow["status"]) in PENDING:
            speaker = str(crow["cluster"])
        elif s.get("channel") == "L" and me is not None:
            speaker = str(me["name"])
        else:
            speaker = f"Voz {label}" if label else "Voz"
        lines.append(Line(int(s["id"]), int(s["t_start_ms"]), speaker, text, label))
        by_id[int(s["id"])] = (text, int(s["t_start_ms"]))
    expected: list[Person] = []
    try:
        for pid in json.loads(str(meeting.get("expected_people_json") or "[]")):
            if int(pid) in people:
                p = people[int(pid)]
                expected.append(Person(int(pid), str(p["name"]), str(p.get("role") or ""), str(p.get("company") or "")))
    except (ValueError, TypeError):
        pass
    markers = tuple(Marker(int(m["id"]), int(m["t_ms"]), str(m.get("note") or "")) for m in store.list_markers(meeting_id))
    title = meeting.get("title") or meeting.get("title_llm")
    participants = meeting.get("expected_participants")
    ctx = MeetingCtx(str(title) if title else None, str(meeting.get("started_at") or "")[:10], str(participants) if participants else None,
                     tuple(expected), markers, resolve_language(output_language, meeting.get("language")),
                     str(meeting.get("glossary") or ""))
    candidates = tuple(Person(pid, str(p["name"]), str(p.get("role") or ""), str(p.get("company") or "")) for pid, p in people.items())
    return Context(meeting, ctx, segments, clusters, people, tuple(lines), by_id, candidates, {m.id: m.note for m in markers})


# ---------------------------------------------------------------- runner
class LlmJobRunner(BaseJobRunner):
    def __init__(self, store: Store, job: JobRow, *, provider: LLMProvider | None = None, config: LlmJobConfig | None = None,
                 now: Clock | None = None, sleep: Sleep | None = None, heartbeat_s: float = 15.0, heartbeat_thread: bool = True,
                 wait_poll_s: float = 5.0, memory_rss: Callable[[], int | None] | None = None):
        super().__init__(store, job, now=now, sleep=sleep, heartbeat_s=heartbeat_s, heartbeat_thread=heartbeat_thread,
                         wait_poll_s=wait_poll_s, memory_rss=memory_rss)
        self._provider_override = provider
        self.config: LlmJobConfig = config or LlmJobConfig()
        self.tasks: list[JobStep] = []
        self.parent_job_id: int = 0
        self.auto = False
        self.outcomes: dict[JobStep, TaskOutcome] = {}
        self.cost_usd = 0.0
        self.estimated_usd: float | None = None
        self.calls = 0
        self._skipped: dict[str, str] = {}
        self._done_steps: set[JobStep] = set()
        self._prices: list[Any] = []
        self._sem: asyncio.Semaphore | None = None
        self._provider: LLMProvider | None = None
        self.ctx: Context | None = None
        self._last_call_id: int | None = None

    @property
    @override
    def respects_lease(self) -> bool:
        return False                                          # rede e JSON não disputam CPU com a gravação (F3A-13)

    # ---------------------------------------------------------------- ciclo
    def run(self) -> JobStatus:
        if self.store.get_meeting(self.meeting_id) is None:
            return self._fail(ProviderUnavailableError("a reunião deste job não existe mais"), permanent=True)
        self._start_heartbeat()
        try:
            return self._execute()
        except JobCancelledError:
            return self._cancelled()
        except LlmError as e:
            return self._fail(e, permanent=e.permanent)
        except Exception as e:  # qualquer falha fora das tarefas vira `erro` com nova tentativa
            return self._fail(e, permanent=False)
        finally:
            self._stop_heartbeat()

    @override
    def _execute(self) -> JobStatus:
        params = self._params()
        self.tasks = [t for t in TASK_ORDER if t in tasks_of(self.job)]
        self.auto = bool(params.get("auto"))
        parent = params.get("parent_job_id")
        if parent is None:
            parent = self.store.segments.current_job_id(self.meeting_id)
        if parent is None and any(t != JobStep.TEST for t in self.tasks):
            raise ProviderUnavailableError("a reunião ainda não foi processada: sem transcrição para a LLM")
        self.parent_job_id = int(parent or 0)
        if not self.config.ready:
            self.config = LlmJobConfig.from_json(self.job.get("config_json"))
        if not self.config.ready:
            self.config = LlmJobConfig.from_store(self.store)
        if not self.config.ready:
            raise ProviderUnavailableError("nenhum provedor de LLM ativo com modelo: escolha um nas Configurações (LLM)")
        redact.GLOBAL.add(self.store.providers.key(self.config.provider_id))
        if not self.tasks:
            raise ProviderUnavailableError("job llm sem tarefas")
        self.store.add_event(self.meeting_id, None, EventType.JOB_START,
                             {"job_id": self.job_id, "kind": str(self.kind), "tasks": [str(t) for t in self.tasks],
                              "parent_job_id": self.parent_job_id, "provider": self.config.provider_id, "fast": self.config.fast_model,
                              "strong": self.config.strong_model, "attempts": self.job.get("attempts", 0)})
        self._done_steps = {t for t in self.tasks if 0 in self.store.jobs.artifact_indexes(self.job_id, t)}
        self._prices = self.store.providers.list_prices(self.config.provider_kind or None)
        if any(t != JobStep.TEST for t in self.tasks):
            self.ctx = build_context(self.store, self.meeting_id, self.parent_job_id, self.config.output_language)
        asyncio.run(self._run_tasks())
        failed = [o for o in self.outcomes.values() if not o.ok]
        if failed:
            return self._fail_partial(failed)
        return self._finish_ok()

    def _params(self) -> dict[str, Any]:
        try:
            data = json.loads(self.job.get("params_json") or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    @override
    def _finish_ok(self) -> JobStatus:
        self.store.jobs.delete_artifacts(self.job_id)
        self.store.jobs.finish(self.job_id, JobStatus.DONE, timings=self._timings_dict())
        self.store.add_event(self.meeting_id, None, EventType.JOB_DONE,
                             {"job_id": self.job_id, "kind": str(self.kind), "tasks": [str(t) for t in self.tasks], "calls": self.calls,
                              "cost_usd": round(self.cost_usd, 6), "skipped": dict(self._skipped)})
        log.info("job llm %s concluído: %d chamada(s), %s%s", self.job_id, self.calls, fmt_usd(self.cost_usd),
                 f"; puladas: {', '.join(self._skipped)}" if self._skipped else "")
        self.status = JobStatus.DONE
        return JobStatus.DONE

    def _fail_partial(self, failed: list[TaskOutcome]) -> JobStatus:
        names = [JOB_STEP_LABELS.get(o.step, str(o.step)).removeprefix("LLM: ") for o in failed]
        first = failed[0].error or "falha"
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
        message = redact.GLOBAL.redact(f"{joined} falhou: {first}" if len(names) == 1 else f"{joined} falharam: {first}")
        permanent = all(o.permanent for o in failed)
        log.error("job llm %s: %s", self.job_id, message)
        self.store.jobs.finish(self.job_id, JobStatus.ERROR, error=message, timings=self._timings_dict(), count_attempt=not permanent)
        self.store.add_event(self.meeting_id, None, EventType.JOB_ERROR,
                             {"job_id": self.job_id, "kind": str(self.kind), "failed": [str(o.step) for o in failed], "error": message,
                              "permanent": permanent, "cost_usd": round(self.cost_usd, 6)})
        self.status = JobStatus.ERROR
        return JobStatus.ERROR

    @override
    def _error_text(self, e: BaseException) -> str:
        return redact.GLOBAL.redact(super()._error_text(e))

    @override
    def _timings_dict(self) -> dict[str, Any]:
        out = super()._timings_dict()
        out["llm"] = {"cost_usd": round(self.cost_usd, 6), "estimated_usd": self.estimated_usd, "calls": self.calls,
                      "skipped": dict(self._skipped), "done": [str(s) for s, o in self.outcomes.items() if o.ok and not o.skipped]}
        return out

    @override
    def _load_timings(self, text: str | None) -> None:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        llm = data.pop("llm", None) if isinstance(data, dict) else None
        super()._load_timings(json.dumps(data) if data else None)
        if isinstance(llm, dict):
            self.cost_usd = float(llm.get("cost_usd") or 0.0)
            self.calls = int(llm.get("calls") or 0)

    # ---------------------------------------------------------------- orquestração
    async def _run_tasks(self) -> None:
        cfg = self.config
        self._sem = asyncio.Semaphore(max(1, cfg.max_parallel))
        own = self._provider_override is None
        provider = self._provider_override or build_provider(self.store, cfg.provider_id)[0]
        self._provider = provider
        try:
            await self._plan_costs()
            for step in (JobStep.TEST, JobStep.IDENTIFY, JobStep.MINUTES):
                if step in self.tasks:
                    await self._run_step(step)
            group = [s for s in PARALLEL if s in self.tasks]
            if group:
                self.checkpoint()
                async with asyncio.TaskGroup() as tg:
                    for s in group:
                        tg.create_task(self._run_step(s, check=False))
            if JobStep.REVISE in self.tasks:
                await self._run_step(JobStep.REVISE)
        finally:
            if own:
                await provider.aclose()

    async def _run_step(self, step: JobStep, check: bool = True) -> None:
        if check:
            self.checkpoint()
        if str(step) in self._skipped:
            self.outcomes[step] = TaskOutcome(step, True, skipped=self._skipped[str(step)])
            self._advance(step)
            return
        if step in self._done_steps:
            self.outcomes[step] = TaskOutcome(step, True)
            self._advance(step)
            return
        if self._step != step:
            self._step = step
            self.store.add_event(self.meeting_id, None, EventType.JOB_STEP, {"job_id": self.job_id, "step": str(step)})
        self.store.jobs.set_progress(self.job_id, step, self._done_fraction(), self._timings_dict())
        t0 = time.perf_counter()
        try:
            await TASK_FN[step](self)
            self.store.jobs.put_artifact(self.job_id, step, 0, b"ok")
            self.outcomes[step] = TaskOutcome(step, True)
        except JobCancelledError:
            raise
        except LlmError as e:
            log.warning("job llm %s: tarefa %s falhou: %s", self.job_id, step, redact.GLOBAL.redact(str(e)))
            self.outcomes[step] = TaskOutcome(step, False, redact.GLOBAL.redact(str(e)), e.permanent)
        except Exception as e:  # a tarefa nunca derruba as irmãs do TaskGroup (R-7)
            log.exception("job llm %s: tarefa %s falhou", self.job_id, step)
            self.outcomes[step] = TaskOutcome(step, False, redact.GLOBAL.redact(f"{type(e).__name__}: {e}"))
        self._timing(step, self.config.fast_model).wall_s += time.perf_counter() - t0
        self._advance(step)

    def _done_fraction(self) -> float:
        return round(len(self.outcomes) / max(1, len(self.tasks)), 4)

    def _advance(self, step: JobStep) -> None:
        self.store.jobs.set_progress(self.job_id, step, self._done_fraction(), self._timings_dict())

    # ---------------------------------------------------------------- custo
    def _price(self, model: str) -> Price | None:
        return price_for(self._prices, self.config.provider_kind, model)

    def _window(self, strong: bool) -> int:
        """Janela do modelo: o que `list_models()` informou (cache em `providers.models_json`, preenchido pela sub-aba), senão a
        tabela do projeto (`models_table`)."""
        model = self.config.model_for(strong)
        for m in self.store.providers.models(self.config.provider_id):
            if str(m.get("id")) == model and m.get("input_limit"):
                return int(m["input_limit"])
        return model_info(self.config.provider_kind, model).input_limit

    async def _transcript_tokens(self) -> int:
        if self.ctx is None:
            return 0
        text = render_lines(self.ctx.lines)
        provider = self._provider
        if provider is not None and text:
            req = Request(LlmTask.MINUTES, self.config.fast_model, (Message(prompts.common.Role.USER, text),))
            try:
                n = await provider.count_tokens(req)
            except Exception:  # contar tokens é opcional
                n = None
            if n:
                return int(n)
        return estimate_tokens(text)

    async def _plan_costs(self) -> None:
        """Estimativa prévia por tarefa (R-5); no job automático, acima do alerta as opcionais ficam de fora (P5)."""
        tokens = await self._transcript_tokens()
        self._transcript_token_count = tokens
        total = 0.0
        per_task: dict[JobStep, float | None] = {}
        for step in self.tasks:
            factor, fixed, out = ESTIMATE[step]
            strong = TASK_CLASS[STEP_TASK[step]] == ModelClass.STRONG
            out_tokens = int(tokens * 0.9) if step == JobStep.REVISE else out
            est = estimate_cost(int(tokens * factor) + fixed, out_tokens, self._price(self.config.model_for(strong)))
            per_task[step] = est
            total += est or 0.0
        self.estimated_usd = round(total, 4)
        if self.auto and total > self.config.cost_alert_usd:
            for step in self.tasks:
                if step in OPTIONAL and step not in self._done_steps:
                    self._skipped[str(step)] = (f"estimado {fmt_usd(per_task.get(step))} (reunião {fmt_usd(total)}), acima do alerta de "
                                                f"{fmt_usd(self.config.cost_alert_usd)}")
            if self._skipped:
                log.info("job llm %s: tarefas opcionais puladas pelo alerta de custo: %s", self.job_id, self._skipped)

    # ---------------------------------------------------------------- chamada
    async def _call(self, task: LlmTask, messages: tuple[Message, ...], schema: dict | None, *, strategy: str = "direct",
                    cache_prefix: bool = False) -> tuple[Completion, int]:
        """Uma chamada sob o semáforo; `llm_calls` recebe a linha (ok ou erro redigido) depois de o `Completion` chegar."""
        if self._provider is None or self._sem is None:
            raise LlmError("runner sem provedor")
        strong = TASK_CLASS[task] == ModelClass.STRONG
        model = self.config.model_for(strong)
        req = Request(task, model, messages, schema, temperature=0.2, max_output_tokens=MAX_OUTPUT.get(task, 4096),
                      reasoning=TASK_REASONING[task], timeout_s=240.0 if strong else 120.0, cache_prefix=cache_prefix)
        payload_req = ([{"role": str(m.role), "text": m.text, "audio": [a.label for a in m.audio]} for m in messages]
                       if self.config.store_payloads else None)
        version, digest = prompts.prompt_version(task), (schema_hash(schema) if schema else None)
        t0 = time.perf_counter()
        async with self._sem:
            try:
                comp = await self._provider.complete(req)
            except Exception as e:
                err = classify_exception(e)
                latency = int((time.perf_counter() - t0) * 1000)
                self.calls += 1
                self.store.llm_calls.insert(LlmCallInsert(self.meeting_id, self.parent_job_id, str(task), self.config.provider_id, model,
                                                          version, digest, 0, 0, 0, 0, None, latency, LlmCallStatus.ERROR,
                                                          redact.GLOBAL.redact(str(err)), strategy, payload_req, None),
                                            store_payloads=self.config.store_payloads)
                raise err from e
        cost = cost_usd(comp.usage, self._price(comp.model or model))
        self.cost_usd += cost or 0.0
        self.calls += 1
        payload_resp = ({"text": comp.text, "data": comp.data, "finish": comp.finish, "raw_id": comp.raw_id}
                        if self.config.store_payloads else None)
        u = comp.usage
        call_id = self.store.llm_calls.insert(LlmCallInsert(self.meeting_id, self.parent_job_id, str(task), self.config.provider_id,
                                                            comp.model or model, version, digest, u.input_tokens, u.output_tokens,
                                                            u.cached_tokens, u.audio_tokens, cost, comp.latency_ms, LlmCallStatus.OK, None,
                                                            strategy, payload_req, payload_resp), store_payloads=self.config.store_payloads)
        self._last_call_id = call_id
        return comp, call_id

    def _ctx(self) -> Context:
        if self.ctx is None:
            raise LlmError("contexto da reunião não carregado")
        return self.ctx

    # ---------------------------------------------------------------- tarefas
    async def _task_test(self) -> None:
        if self._provider is None:
            raise LlmError("runner sem provedor")
        t0 = time.perf_counter()
        result = await self._provider.test_connection(self.config.fast_model)
        self.calls += 1
        self.store.llm_calls.insert(LlmCallInsert(self.meeting_id, self.parent_job_id or None, str(LlmTask.TEST), self.config.provider_id,
                                                  result.model, prompts.prompt_version(LlmTask.TEST), None, 0, 0, 0, 0, None,
                                                  result.latency_ms or int((time.perf_counter() - t0) * 1000),
                                                  LlmCallStatus.OK if result.ok else LlmCallStatus.ERROR, result.error, "canary"),
                                    store_payloads=False)
        if not result.ok:
            raise LlmError(result.error or "teste de conexão falhou")

    def _pending_clusters(self) -> list[ClusterRow]:
        """Relidos do banco agora: o que o usuário confirmou durante o Whisper não entra (CP11, P1)."""
        return [c for c in self.store.segments.list_clusters(self.meeting_id, self.parent_job_id)
                if str(c["status"]) in PENDING and c.get("merged_into") is None
                and not (c["channel"] == "L" and c["cluster"] == USER_CLUSTER)]

    async def _task_identify(self) -> None:
        ctx = self._ctx()
        pending = self._pending_clusters()
        if not pending:
            log.info("job llm %s: nenhuma voz pendente para a identidade por contexto", self.job_id)
            return
        labels = [str(c["cluster"]) for c in pending]
        window = self._window(strong=True)
        lines: tuple[Line, ...] = ctx.lines
        if estimate_tokens(render_lines(lines)) > WINDOW_SHARE * window:
            lines = neighbourhood(lines, set(labels))
        blocks = [lines]
        if estimate_tokens(render_lines(lines)) > WINDOW_SHARE * window:
            blocks = split_blocks(lines, int(BLOCK_SHARE * window))
        best: dict[str, dict[str, Any]] = {}
        strategy = "direct" if len(blocks) == 1 else f"map_reduce:{len(blocks)}"
        for block in blocks:
            present = [lb for lb in labels if any(ln.label == lb or ln.speaker == lb for ln in block)]
            if not present:
                continue
            msgs = prompts.identify.build(prompts.identify.IdentifyCtx(ctx.meeting_ctx, block, present, ctx.candidates))
            try:
                comp, _ = await self._call(LlmTask.IDENTIFY, msgs, prompts.identify.SCHEMA, strategy=strategy)
            except RequestTooLargeError:
                if len(blocks) > 1:
                    raise
                half = max(1, int(estimate_tokens(render_lines(block)) / 2))
                for sub in split_blocks(block, half):
                    sub_present = [lb for lb in present if any(ln.label == lb or ln.speaker == lb for ln in sub)]
                    if sub_present:
                        comp, _ = await self._call(LlmTask.IDENTIFY, prompts.identify.build(prompts.identify.IdentifyCtx(
                            ctx.meeting_ctx, sub, sub_present, ctx.candidates)), prompts.identify.SCHEMA, strategy="map_reduce:2")
                        self._merge_identify(best, comp)
                continue
            self._merge_identify(best, comp)
        self._apply_identify(best)

    @staticmethod
    def _merge_identify(best: dict[str, dict[str, Any]], comp: Completion) -> None:
        for sp in (comp.data or {}).get("speakers") or []:
            label = str(sp.get("cluster") or "")
            if label and (label not in best or float(sp.get("confidence") or 0) > float(best[label].get("confidence") or 0)):
                best[label] = dict(sp)

    def _apply_identify(self, best: dict[str, dict[str, Any]]) -> None:
        ctx = self._ctx()
        by_label = {str(c["cluster"]): c for c in self._pending_clusters()}      # relidos de novo: a revisão pode ter andado
        suggested = 0
        for label, sp in best.items():
            c = by_label.get(label)
            if c is None:
                continue
            evidence, conf = check_identify(sp.get("evidence") or [], float(sp.get("confidence") or 0.0), ctx.by_id)
            pid = sp.get("person_id")
            pid = int(pid) if isinstance(pid, int) and int(pid) in ctx.people else None
            name = str(sp.get("new_name") or "").strip() or None
            if pid is None and name:
                pid = match_person(name, ctx.people)                 # nome de pessoa cadastrada vira `person_id` (F3A-33)
            if pid is not None:
                name = str(ctx.people[pid]["name"])
            if pid is None and not name:
                continue
            try:
                evidence_json = json.dumps([asdict(e) for e in evidence], ensure_ascii=False)
                self.store.segments.set_llm_suggestion(int(c["id"]), pid, name, conf, evidence_json)
                suggested += 1
            except ValueError as e:                                   # confirmada entre a leitura e a escrita: CP11 vence
                log.info("job llm %s: %s", self.job_id, e)
        self.store.add_event(self.meeting_id, None, EventType.JOB_STEP,
                             {"job_id": self.job_id, "step": "identify_done", "pending": len(by_label), "suggested": suggested})

    async def _task_minutes(self) -> None:
        ctx = self._ctx()
        people = tuple(ctx.candidates)
        window = self._window(strong=True)
        budget = int(WINDOW_SHARE * window) - MAX_OUTPUT[LlmTask.MINUTES]
        direct = estimate_tokens(render_lines(ctx.lines)) <= budget
        strategy = "direct"
        if direct:
            try:
                msgs = prompts.minutes.build(prompts.minutes.MinutesCtx(ctx.meeting_ctx, ctx.lines, people))
                comp, call_id = await self._call(LlmTask.MINUTES, msgs, prompts.minutes.SCHEMA)
            except RequestTooLargeError:
                direct = False
        if not direct:
            comp, call_id, strategy = await self._minutes_hierarchical(window)
        self._store_minutes(dict(comp.data or {}), call_id, strategy)

    async def _minutes_hierarchical(self, window: int) -> tuple[Completion, int, str]:
        """Mapa por bloco (notas no mesmo esquema, artefato por bloco para retomada) e redução para a ata (F3A-38)."""
        ctx = self._ctx()
        people = tuple(ctx.candidates)
        blocks = split_blocks(ctx.lines, max(500, int(BLOCK_SHARE * window) - MAX_OUTPUT[LlmTask.MINUTES]))
        strategy = f"map_reduce:{len(blocks)}"
        notes: list[str] = []
        for i, block in enumerate(blocks):
            saved = self.store.jobs.get_artifact(self.job_id, JobStep.MINUTES, i + 1)
            if saved is not None:
                notes.append(saved.decode("utf-8"))
                continue
            self.checkpoint()
            msgs = prompts.minutes.build(prompts.minutes.MinutesCtx(ctx.meeting_ctx, block, people, (i, len(blocks))))
            comp, _ = await self._call(LlmTask.MINUTES, msgs, prompts.minutes.SCHEMA, strategy=strategy)
            text = json.dumps(comp.data, ensure_ascii=False)
            self.store.jobs.put_artifact(self.job_id, JobStep.MINUTES, i + 1, text.encode("utf-8"))
            notes.append(text)
        msgs = prompts.minutes.build_reduce(prompts.minutes.ReduceCtx(ctx.meeting_ctx, notes, people))
        comp, call_id = await self._call(LlmTask.MINUTES, msgs, prompts.minutes.SCHEMA, strategy=strategy)
        return comp, call_id, strategy

    def _store_minutes(self, data: dict[str, Any], call_id: int, strategy: str) -> None:
        ctx = self._ctx()
        for key in ("decisions", "actions", "highlights", "open_points", "next_steps"):
            for item in data.get(key) or []:
                item["segment_ids"] = valid_segment_ids(item.get("segment_ids") or [], ctx.by_id)
        people_names = {pid: str(p["name"]) for pid, p in ctx.people.items()}
        md = render_minutes(data, ctx.by_id, ctx.meeting_ctx.output_language, people_names, ctx.marker_notes)
        row = SummaryInsert(self.meeting_id, SummaryKind.MINUTES, json.dumps(data, ensure_ascii=False), md, self.config.provider_id,
                            self.config.model_for(True), prompts.prompt_version(LlmTask.MINUTES), self.parent_job_id, call_id,
                            ctx.meeting_ctx.output_language)
        summary_id = self.store.summaries.insert(row)
        rows: list[ActionInsert] = []
        for a in data.get("actions") or []:
            ids = a.get("segment_ids") or []
            pid = a.get("owner_person_id")
            owner = int(pid) if isinstance(pid, int) and int(pid) in ctx.people else match_person(a.get("owner_text"), ctx.people)
            due = str(a.get("due_date")) if a.get("due_date") and DATE_RE.match(str(a.get("due_date"))) else None
            text = str(a.get("text") or "").strip()
            if not text:
                continue
            if a.get("due_text") and not due:
                text = f"{text} (prazo: {a['due_text']})"
            rows.append(ActionInsert(self.meeting_id, summary_id, text, a.get("owner_text") or None, owner, due,
                                     first_time_ms(ids, ctx.by_id), ids[0] if ids else None, self.parent_job_id))
        if rows:
            self.store.actions.insert_many(rows)
        self.store.add_event(self.meeting_id, None, EventType.JOB_STEP,
                             {"job_id": self.job_id, "step": "minutes_done", "strategy": strategy,
                              "decisions": len(data.get("decisions") or []), "actions": len(rows)})

    def _minutes_json(self) -> str:
        row = self.store.summaries.current(self.meeting_id, SummaryKind.MINUTES)
        if row is None or not row.get("data_json"):
            raise LlmError("ata ausente: gere a ata primeiro")
        return str(row["data_json"])

    async def _task_summary(self, kind: SummaryKind, task: LlmTask, module: Any) -> None:
        ctx = self._ctx()
        minutes_json = self._minutes_json()
        comp, call_id = await self._call(task, module.build(prompts.executive.SummaryCtx(ctx.meeting_ctx, minutes_json)), module.SCHEMA)
        text = str((comp.data or {}).get("text") or "").strip()
        if not text:
            raise LlmError(f"{task}: resposta vazia")
        self.store.summaries.insert(SummaryInsert(self.meeting_id, kind, json.dumps(comp.data, ensure_ascii=False), text,
                                                  self.config.provider_id, comp.model, prompts.prompt_version(task), self.parent_job_id,
                                                  call_id, ctx.meeting_ctx.output_language))

    async def _task_executive(self) -> None:
        await self._task_summary(SummaryKind.EXECUTIVE, LlmTask.EXECUTIVE, prompts.executive)

    async def _task_pre_summary(self) -> None:
        await self._task_summary(SummaryKind.PRE_SUMMARY, LlmTask.PRE_SUMMARY, prompts.pre_summary)

    async def _task_title(self) -> None:
        """Título do pré-resumo quando já existe; senão do contexto da ata; senão das primeiras falas (o grupo roda em paralelo)."""
        ctx = self._ctx()
        pre = self.store.summaries.current(self.meeting_id, SummaryKind.PRE_SUMMARY)
        summary = str(pre.get("text_llm") or "") if pre else ""
        if not summary:
            minutes = self.store.summaries.current(self.meeting_id, SummaryKind.MINUTES)
            try:
                summary = str(json.loads(minutes.get("data_json") or "{}").get("context") or "") if minutes else ""
            except ValueError:
                summary = ""
        if not summary:
            summary = render_lines(ctx.lines[:40])
        msgs = prompts.title.build(prompts.title.TitleCtx(ctx.meeting_ctx, summary))
        comp, _ = await self._call(LlmTask.TITLE, msgs, prompts.title.SCHEMA)
        title = str((comp.data or {}).get("title") or "").strip().strip('"').rstrip(".")
        if not title:
            raise LlmError("título vazio")
        self.store.meetings.set_title_llm(self.meeting_id, title[:80])

    async def _task_revise(self) -> None:
        ctx = self._ctx()
        todo = [ln for ln in ctx.lines if ln.text.strip()]
        done_ids = {int(s["id"]) for s in ctx.segments if s.get("text_llm")}
        targets = [ln for ln in todo if ln.segment_id not in done_ids]              # P7: só o que ainda não tem revisão
        if not targets:
            return
        index = {ln.segment_id: i for i, ln in enumerate(todo)}
        blocks = [targets[i:i + REVISE_BLOCK] for i in range(0, len(targets), REVISE_BLOCK)]
        results: dict[int, list[tuple[int, str]]] = {}

        async def one(bi: int, block: list[Line]) -> None:
            first, last = index[block[0].segment_id], index[block[-1].segment_id]
            before = todo[max(0, first - REVISE_CONTEXT):first]
            after = todo[last + 1:last + 1 + REVISE_CONTEXT]
            try:
                msgs = prompts.revise.build(prompts.revise.ReviseCtx(ctx.meeting_ctx, before, block, after))
                comp, _ = await self._call(LlmTask.REVISE, msgs, prompts.revise.SCHEMA)
            except LlmError as e:
                results[bi] = []
                errors.append(e)
                return
            allowed = {ln.segment_id: ln.text for ln in block}
            accepted: list[tuple[int, str]] = []
            for r in (comp.data or {}).get("revisions") or []:
                sid = r.get("segment_id")
                if isinstance(sid, int) and sid in allowed and accept_revision(allowed[sid], str(r.get("text") or "")):
                    accepted.append((sid, str(r["text"]).strip()))
            if accepted:
                self.store.segments.set_text_llm(self.meeting_id, self.parent_job_id, accepted)     # uma transação por bloco
            results[bi] = accepted

        errors: list[LlmError] = []
        async with asyncio.TaskGroup() as tg:
            for bi, block in enumerate(blocks):
                tg.create_task(one(bi, block))
        if errors:
            raise errors[0]
        self.store.add_event(self.meeting_id, None, EventType.JOB_STEP,
                             {"job_id": self.job_id, "step": "revise_done", "blocks": len(blocks),
                              "revised": sum(len(v) for v in results.values())})


TASK_FN: dict[JobStep, Callable[[LlmJobRunner], Awaitable[None]]] = {
    JobStep.TEST: LlmJobRunner._task_test, JobStep.IDENTIFY: LlmJobRunner._task_identify, JobStep.MINUTES: LlmJobRunner._task_minutes,
    JobStep.EXECUTIVE: LlmJobRunner._task_executive, JobStep.PRE_SUMMARY: LlmJobRunner._task_pre_summary,
    JobStep.TITLE: LlmJobRunner._task_title, JobStep.REVISE: LlmJobRunner._task_revise,
}
