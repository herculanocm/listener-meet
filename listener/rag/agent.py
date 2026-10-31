"""F5.10 (Escopo 4 e 5, Comportamento 1 a 9, D5, D6): "Perguntar à base". A pergunta e a conversa vão a rodadas de `rag_sql`
(o dicionário no prefixo em cache, o agora, os filtros e o que as rodadas anteriores trouxeram); cada rodada devolve
`consultar` (consultas SQL na sandbox e buscas por sentido pelo `retrieve` do chat global, com o apelido `R<reuniao_id>`) ou
`responder`. Não há número fixo de consultas nem de rodadas (D-073): para quando a LLM responde, por exaustão (três rodadas
seguidas sem novidade), pelo contexto cheio ou numa guarda (custo, somado em memória com os vetores das buscas e inativo sem
preço; tempo; rodadas). A camada semântica sai do cache ou é montada numa thread em paralelo com a 1ª rodada, que não usa a
sandbox. A resposta é o `rag_answer` em streaming por `chat_task.ask`, com os resultados e as reuniões citáveis; com filtros,
começa por "Considerando os filtros: …"; as citações `[R<reuniao_id> mm:ss]` ligam à reunião e à linha mais próxima até 5 s.

Pergunta, resposta e passos (`trace_json`) em `chat_messages`; cada rodada e a resposta em `llm_calls` (`rag_sql`,
`rag_answer`). Sem provedor, sem rede, cancelada ou banco ocupado, como o chat global: a pergunta fica e nenhuma resposta
vazia é gravada. O log leva a conversa, rodadas, consultas, linhas, ms, custo, a categoria dos erros e por que parou; nunca a
pergunta, o SQL, os `params`, os dados nem nomes (D8). Síncrono: a tela chama numa `StreamTask`."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config.language import default_language
from ..llm import chat_task, redact
from ..llm.aio import run_async
from ..llm.base import LlmError, classify_exception
from ..llm.costs import cost_usd, estimate_tokens, price_for
from ..llm.models_table import model_info
from ..llm.prompts import rag_sql
from ..llm.prompts.rag_answer import RagAnswerCtx
from ..llm.prompts.rag_sql import RagSqlCtx
from ..llm.schema import SchemaError, schema_hash
from ..llm.types import LlmTask, Reasoning, Request
from ..log import get_logger
from ..model import ChatRole, LlmCallStatus
from ..store.llm_calls import LlmCallInsert
from ..store.meetings import effective_title
from ..store.rag import SearchFilters
from . import agent_results as ar
from . import citations, dictionary, semantic, trace
from .agent_results import AgentAnswer, Guards, describe, now_text
from .chat_global import _history, retrieve, thread_title
from .citations import SentLine, Target

if TYPE_CHECKING:
    from ..llm.jobconfig import LlmJobConfig
    from ..llm.types import Completion
    from ..store import Store
    from .sandbox import Sandbox

log = get_logger("rag.agent")

WINDOW_SHARE = 0.6               # o contexto das rodadas até 60 % da janela do modelo; acima, as antigas viram uma linha
SQL_OUTPUT_TOKENS = 4096
ANSWER_OUTPUT_TOKENS = 2048
type Progress = Callable[[dict[str, Any]], None]
type Emit = Callable[[str], None]


class _Run:
    """Uma pergunta: as rodadas, o que já veio, os alvos das citações, o custo, a sandbox e a resposta."""

    def __init__(self, store: Store, cfg: LlmJobConfig, guards: Guards, question: str, filters: SearchFilters | None, label: str,
                 history: list[tuple[str, str]], cancelled: Callable[[], bool] | None, progress: Progress | None,
                 clock: Callable[[], float]):
        self.store, self.cfg, self.guards, self.question, self.filters = store, cfg, guards, question, filters
        self.label, self.history, self.progress, self.clock = label, history, progress, clock
        self.stopped = cancelled or (lambda: False)
        self.model = cfg.strong_model if guards.strong and cfg.strong_model else cfg.fast_model
        self.window = model_info(cfg.provider_kind, self.model).input_limit
        self.price = price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, self.model)
        self.language, self.now, self.dictionary = default_language(store), datetime.now().astimezone(), dictionary.render()
        self.rounds: list[ar.Round] = []
        self.seen = ar.Seen()
        self.targets: dict[int, set[SentLine]] = {}
        self.spent, self.mark, self.unpriced, self.no_news, self.partial = 0.0, 0.0, False, 0, ""
        self.stop: str | None = None
        self.error: str | None = None
        self.t0, self.started = clock(), time.perf_counter()
        self.box: Sandbox | None = None
        self.halt = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camada")
        self.layer = self.pool.submit(self._build)                 # em paralelo com a 1ª rodada, que não usa a sandbox

    # ---------------------------------------------------------------- a camada e a sandbox
    def _build(self) -> semantic.Layer:
        try:
            return semantic.CACHE.get(self.store, self._halted)[0]
        finally:
            self.store.close_thread()

    def _halted(self) -> bool:
        return self.halt.is_set() or self.stopped()

    def _sandbox(self) -> Sandbox | None:
        if self.box is None:
            try:
                self.box = semantic.open_sandbox(self.store, self.filters, layer=self.layer.result(), now=self.now)
            except Exception as e:  # cancelada no meio da montagem, banco ocupado: a consulta não roda e a LLM sabe
                log.warning("perguntar à base: a sandbox não abriu (%s)", type(e).__name__)
                return None
        return self.box

    def close(self) -> None:
        self.halt.set()
        self.pool.shutdown(wait=True)
        if self.box is not None:
            self.box.close()

    # ---------------------------------------------------------------- o laço
    def loop(self) -> None:
        while self.stop is None:
            self.stop = self._guard()
            if self.stop is not None:
                break
            decision = self._ask()
            if decision is None:
                break
            if decision.answer:
                self.stop = "resposta"
                break
            self._execute(decision)

    def _guard(self) -> str | None:
        if self.stopped():
            return "cancelada"
        if len(self.rounds) >= self.guards.max_rounds:
            return "rodadas"
        if self._time_up():
            return "tempo"
        if not self.unpriced and self.spent >= self.guards.max_cost_usd:
            return "custo"
        return None

    def _time_up(self) -> bool:
        return self.clock() - self.t0 >= self.guards.max_seconds

    def _request(self) -> Request | None:
        """O pedido da rodada; acima de 60 % da janela, as rodadas antigas viram uma linha (e podem rodar de novo)."""
        while True:
            ctx = RagSqlCtx(self.dictionary, self.history, now_text(self.now), self.label, self.question, ar.rounds_text(self.rounds),
                            len(self.rounds) + 1, self.language)
            messages = self.cfg.build(LlmTask.RAG_SQL, ctx)
            if estimate_tokens(sum(len(m.text) for m in messages)) <= WINDOW_SHARE * self.window:
                strong = self.model != self.cfg.fast_model
                return Request(LlmTask.RAG_SQL, self.model, messages, rag_sql.SCHEMA, max_output_tokens=SQL_OUTPUT_TOKENS,
                               reasoning=Reasoning.LOW if strong else Reasoning.NONE, timeout_s=120.0, cache_prefix=True)
            if not self._compact():
                return None

    def _compact(self) -> bool:
        old = next((r for r in self.rounds if not r.compact), None)
        if old is None:
            return False
        old.compact = True
        self.seen.forget({i.ref for i in old.items})
        return True

    def _ask(self) -> ar.Decision | None:
        self.mark = self.spent
        req = self._request()
        if req is None:
            self.stop = "janela"
            return None
        t1 = time.perf_counter()
        comp, err = self._call(req, None)
        if comp is None:
            self._failed(LlmTask.RAG_SQL, err, t1)
            return None
        self._record(LlmTask.RAG_SQL, req, comp, t1)
        try:
            return ar.decision(comp.data or {})
        except (SchemaError, ValueError, KeyError) as e:
            self.stop, self.error = "erro", f"resposta fora do esquema ({type(e).__name__})"
            return None

    def _call(self, req: Request, emit: Emit | None) -> tuple[Completion | None, LlmError | None]:
        """Uma chamada com o cancelamento valendo também na espera da rede (`chat_task.ask`); o que chegou fica em `partial`."""
        try:
            comp, self.partial, err = run_async(chat_task.ask(self.store, self.cfg.provider_id, req, emit, self.stopped))
        except Exception as e:  # SDK ausente, chave apagada no meio
            return None, classify_exception(e)
        return comp, err

    def _failed(self, task: LlmTask, err: LlmError | None, t1: float) -> None:
        """A chamada que não voltou: cancelada (a rede presa), grande demais (compacta e pede a resposta) ou com erro."""
        message = chat_task.CANCELLED if err is None else redact.GLOBAL.redact(str(err))
        self._insert(task, None, None, t1, message)
        if err is None:
            self.stop = "cancelada"
        elif err.too_large and task == LlmTask.RAG_SQL:
            while self._compact():
                pass
            self.stop = "janela"
        else:
            self.stop, self.error = "erro", message

    # ---------------------------------------------------------------- uma rodada
    def _execute(self, d: ar.Decision) -> None:
        t1, n, budget = time.perf_counter(), len(self.rounds) + 1, ar.ROUND_CHARS
        items: list[ar.Item] = []
        for i, q in enumerate(d.queries, 1):
            items.append(self._query(f"{n}.{i}", q, budget))
            budget -= len(items[-1].text)
        for j, s in enumerate(d.searches, len(d.queries) + 1):
            items.append(self._search(f"{n}.{j}", s, budget))
            budget -= len(items[-1].text)
        self.rounds.append(ar.Round(n, d.note, items, (time.perf_counter() - t1) * 1000, round(self.spent - self.mark, 6)))
        self.no_news = 0 if any(i.new for i in items) else self.no_news + 1
        if self.no_news >= ar.NO_NEWS_ROUNDS:
            self.stop = "exaustao"
        log.debug("perguntar à base: rodada %d com %d consulta(s) e %d busca(s), %d com novidade", n, len(d.queries),
                  len(d.searches), sum(1 for i in items if i.new))
        self.report(False)

    def _query(self, ref: str, q: ar.Query, budget: int) -> ar.Item:
        earlier = self.seen.queries.get(q.key)
        if earlier is not None:
            return ar.skipped(ref, q, "", earlier)
        cap = min(ar.QUERY_CHARS, budget)
        if self._time_up() or self.stopped():
            return ar.skipped(ref, q, ar.TIME_UP)
        if cap < ar.MIN_QUERY_CHARS:
            return ar.skipped(ref, q, ar.NOT_RUN)
        box = self._sandbox()
        if box is None:
            return ar.skipped(ref, q, ar.NO_BASE)
        res = box.run(q.sql, q.params, max_rows=self.guards.max_rows, timeout_s=self.guards.timeout_s, should_stop=self.stopped)
        self.seen.queries[q.key] = ref
        for mid, lines in ar.targets(res).items():
            self.targets.setdefault(mid, set()).update(lines)
        log.debug("perguntar à base: consulta %s com %d linha(s) em %.1f ms%s", ref, res.total, res.ms,
                  f" (erro: {res.error})" if res.error else "")
        return ar.query_item(ref, q, res, cap, self.seen.tables)

    def _search(self, ref: str, s: ar.Search, budget: int) -> ar.Item:
        cap = min(ar.QUERY_CHARS, budget)
        if self._time_up() or self.stopped():
            return ar.search_skipped(ref, s, ar.TIME_UP)
        if cap < ar.MIN_QUERY_CHARS:
            return ar.search_skipped(ref, s, ar.NOT_RUN)
        base = self.filters or SearchFilters()
        filters = SearchFilters(base.person_ids, base.date_from, base.date_to, s.meeting_ids or base.meeting_ids)
        got = retrieve(self.store, s.text, filters, lambda mid: f"R{mid}", should_stop=self.stopped)
        if got.vector_cost_usd is not None:
            self.spent += got.vector_cost_usd
        for target in got.targets.values():
            self.targets.setdefault(target.meeting_id, set()).update(target.lines)
        log.debug("perguntar à base: busca %s com %d trecho(s) em %.1f ms", ref, len(got.hits), got.ms)
        return ar.search_item(ref, s, got.blocks, cap, self.seen.lines, got.ms, got.hybrid)

    def report(self, answering: bool) -> None:
        """O progresso para a tela: a rodada e quantas consultas e buscas rodaram até aqui (D6)."""
        if self.progress is None:
            return
        items = [i.trace for r in self.rounds for i in r.items]
        self.progress({"respondendo": answering, "rodada": len(self.rounds),
                       "consultas": sum(1 for t in items if t["tipo"] == "consulta" and t["status"] in ("ok", "erro")),
                       "buscas": sum(1 for t in items if t["tipo"] == "busca" and t["status"] == "ok")})

    # ---------------------------------------------------------------- chamadas gravadas
    def _record(self, task: LlmTask, req: Request, comp: Completion, t1: float, strategy: str = "direct") -> int:
        cost = cost_usd(comp.usage, self.price)
        if cost is None:
            self.unpriced = True
        else:
            self.spent += cost
        payload = [{"role": str(m.role), "text": m.text} for m in req.messages] if self.cfg.store_payloads else None
        return self._insert(task, comp, cost, t1, None, strategy, payload, {"text": comp.text} if self.cfg.store_payloads else None)

    def _insert(self, task: LlmTask, comp: Completion | None, cost: float | None, t1: float, error: str | None,
                strategy: str = "direct", request: Any = None, response: Any = None) -> int:
        u = comp.usage if comp is not None else None
        row = LlmCallInsert(None, None, str(task), self.cfg.provider_id, comp.model if comp else self.model, self.cfg.version(task),
                            schema_hash(rag_sql.SCHEMA) if task == LlmTask.RAG_SQL else None, u.input_tokens if u else 0,
                            u.output_tokens if u else 0, u.cached_tokens if u else 0, 0, cost,
                            comp.latency_ms if comp else int((time.perf_counter() - t1) * 1000),
                            LlmCallStatus.OK if error is None else LlmCallStatus.ERROR, error, strategy, request, response)
        return self.store.llm_calls.insert(row, store_payloads=self.cfg.store_payloads and error is None)

    # ---------------------------------------------------------------- a resposta
    def citable(self) -> dict[str, Target]:
        """As reuniões citáveis (`R<reuniao_id>`), com as linhas que vieram com momento (da consulta e da busca)."""
        out: dict[str, Target] = {}
        for mid, lines in sorted(self.targets.items()):
            m = self.store.get_meeting(mid)
            if m is not None:
                ordered = tuple(sorted(lines, key=lambda ln: (ln.t_ms, ln.segment_id or 0)))
                out[f"R{mid}"] = Target(mid, effective_title(m) or "", str(m.get("started_at") or "")[:10], ordered)
        return out

    def _answer_request(self, targets: dict[str, Target]) -> Request:
        legend = "\n".join(f"{alias} = {t.title or '(sem título)'} — {t.date}" for alias, t in targets.items())
        note = trace.STOPS.get(self.stop or "", "")
        stop = note if self.stop == "resposta" else f"pararam por {note}: diga isso na resposta e o que faltou"
        ctx = RagAnswerCtx(self.history, now_text(self.now), self.label, self.question, legend, ar.rounds_text(self.rounds), stop,
                           self.language)
        return Request(LlmTask.RAG_ANSWER, self.cfg.fast_model, self.cfg.build(LlmTask.RAG_ANSWER, ctx), None,
                       max_output_tokens=ANSWER_OUTPUT_TOKENS, reasoning=Reasoning.NONE, timeout_s=120.0)

    def steps(self) -> dict[str, Any]:
        return trace.build(self.rounds, self.stop or "erro", (time.perf_counter() - self.started) * 1000,
                           None if self.unpriced else self.spent, self.unpriced, self.filters, self.label, self.model)

    def finish(self, thread_id: int, question_id: int, emit: Emit | None) -> AgentAnswer:
        """A resposta: o `rag_answer` em streaming com o que as rodadas trouxeram; gravada com as citações e os passos."""
        if self.stop in ("cancelada", "erro"):
            return self._summary(AgentAnswer("", "", (), None, thread_id, None, question_id, None, self.error,
                                             self.stop == "cancelada", self.steps()))
        targets = self.citable()
        req = self._answer_request(targets)
        prefix = f"Considerando os filtros: {self.label}.\n\n" if self.label else ""
        self.report(True)
        if prefix and emit is not None:
            emit(prefix)
        t1 = time.perf_counter()
        comp, err = self._call(req, emit)
        if comp is None and (err is not None or not self.partial):     # erro, ou cancelada antes do primeiro pedaço
            self._failed(LlmTask.RAG_ANSWER, err, t1)
            return self._summary(AgentAnswer("", "", (), None, thread_id, None, question_id, None, self.error, err is None,
                                             self.steps()))
        text = prefix + (self.partial or (comp.text if comp is not None else ""))
        if comp is not None:
            call_id = self._record(LlmTask.RAG_ANSWER, req, comp, t1, "stream" if emit else "direct")
        else:                                                          # cancelada no meio: a parte que chegou fica
            call_id = self._insert(LlmTask.RAG_ANSWER, None, None, t1, None, "stream")
        rendered, cites = citations.rewrite(text, targets)
        steps = self.steps()
        message_id = self.store.chat.add(None, ChatRole.ASSISTANT, text, self.cfg.provider_id, comp.model if comp else self.model, cites,
                                         steps["custo_usd"], thread_id=thread_id, trace=steps)
        return self._summary(AgentAnswer(text, rendered, tuple(cites), steps["custo_usd"], thread_id, message_id, question_id, call_id,
                                         None, comp is None, steps))

    def _summary(self, result: AgentAnswer) -> AgentAnswer:
        """D8: a conversa, as contagens, o tempo, o custo e por que parou; nunca a pergunta, o SQL, os dados nem nomes."""
        s = result.trace
        cost = "sem preço" if s["sem_preco"] else f"US$ {s['custo_usd'] or 0:.4f}"
        log.info("perguntar à base: conversa %s, %d rodada(s), %d consulta(s) (%d com erro), %d busca(s), %d linha(s), %.0f ms, %s; "
                 "parou: %s", result.thread_id, len(s["rodadas"]), s["consultas"], s["erros"], s["buscas"], s["linhas"], s["ms"], cost,
                 s["parou"])
        return result


def _check(store: Store, thread_id: int | None, question: str) -> str:
    question = (question or "").strip()
    if not question:
        raise ValueError("escreva uma pergunta")
    if store.providers.active_with_key("llm") is None:
        raise ValueError("nenhum provedor de LLM ativo com chave: informe a chave em Configurações, Chaves de acesso")
    if thread_id is not None and store.chat.get_thread(thread_id) is None:
        raise ValueError("esta conversa não existe mais: comece uma nova")
    return question


def run_agent(store: Store, thread_id: int | None, question: str, filters: SearchFilters | None = None, label: str = "",
              emit: Emit | None = None, cancelled: Callable[[], bool] | None = None, progress: Progress | None = None, *,
              guards: Guards | None = None, clock: Callable[[], float] = time.monotonic) -> AgentAnswer:
    """Pergunta -> rodadas -> resposta (em streaming quando `emit` é dado; `progress` recebe a rodada e as contagens).
    `thread_id` None cria a conversa; sem provedor com chave, pergunta vazia ou conversa apagada, `ValueError`. A pergunta é
    gravada antes da 1ª rodada (é ela que chama a LLM)."""
    from ..llm.jobconfig import LlmJobConfig

    question = _check(store, thread_id, question)
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))           # CP10: a chave some do erro gravado e do log
    previous = store.chat.list_for_thread(thread_id) if thread_id is not None else []
    with store.transaction():
        if thread_id is None:
            thread_id = store.chat.create_thread(thread_title(question))
        question_id = store.chat.add(None, ChatRole.USER, question, thread_id=thread_id)
    run = _Run(store, cfg, guards or Guards.from_store(store), question, filters, label or describe(filters),
               _history(store, previous), cancelled, progress, clock)
    try:
        run.loop()
        return run.finish(thread_id, question_id, emit)
    finally:
        run.close()


__all__ = ["AgentAnswer", "Guards", "describe", "now_text", "run_agent"]
