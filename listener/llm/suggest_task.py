"""F5.20 (C8): a tarefa de uma sugestão ao vivo e a do memorando, no padrão do resumo parcial (`llm/partial_task.py`) e do
chat (`llm/chat_task.py`): síncronas (rodam numa `StreamTask` ou numa `Task`), canceláveis a cada 0,2 s, com o cliente
próprio de cada chamada (`run_async`), a chave lida na hora e redigida no erro (CP10) e `llm_calls` com a tarefa, o custo e o
`prompt_version` (`+u<hash>` com as instruções do usuário). Nenhum Qt aqui: cada opção sai por `on_option`, que o host leva à
interface por sinal.

- A janela (`window`): na primeira sugestão e a cada 20 min de reunião, os últimos 15 min de legendas (reancorada); nas
  outras, desde o fim da anterior, com ao menos 3 min e no máximo `suggest.window_min`. As legendas vão compactadas
  (`llm/suggest_compact.py`) e cortadas pelo começo no teto de caracteres.
- O pedido (`suggest_ctx`): quem pede (a ficha "Você"), o cabeçalho com os marcadores e os falantes da janela, o dossiê, o
  memorando, o objetivo, o parcial atual, o fio da sugestão anterior, as avaliações desta reunião e "Como sugerir agora".
- A resposta: JSON com esquema em streaming (`stream()` do provedor; o `RetryingProvider` só repassa, A-03: sem retentativa,
  o clique é a retentativa), lido pelo `OptionsReader`; cada opção validada pelo esquema dela, com os fatos limitados às
  legendas do pedido (os `t_ms` saem das legendas já lidas, sem consulta a mais); no fim, a validação do texto inteiro dá o
  fio. Prazo total de 30 s no laço; cancelada ou no prazo, o custo estimado pelos caracteres (A-26).
- O teto: o mesmo do resumo parcial (`partial_task.budget`, a metade do alerta da reunião); quem para é o host (A-13).
- O memorando (`run_memo`): com 10 avaliações novas desde o último, uma chamada sem reunião (o custo do mês) refaz
  `suggest.memo` só com estilo e abordagem.

O log leva ids, contagens, tempos e custo; nunca o texto da sugestão, do fio, do objetivo, da linha nem do memorando."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..config.defaults import SPECS
from ..config.language import default_language
from ..log import get_logger
from ..model import Evidence, LlmCallStatus, SuggestMode, SuggestStatus
from ..store.llm_calls import LlmCallInsert
from ..store.suggestions import OptionInsert, SuggestionInsert
from . import partial_task, prompts, redact
from .aio import run_async
from .base import OFF_SCHEMA, LlmError, LLMProvider, classify_exception
from .costs import CHARS_PER_TOKEN, cost_usd, fmt_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.common import Marker, Person
from .prompts.suggest import KINDS_ORIENT, KINDS_SPEAK, OPTION_SCHEMA, SUMMARY_LIMIT, RatedOption, SuggestCtx
from .prompts.suggest_memo import MemoCtx
from .registry import RetryingProvider, build_provider
from .schema import SchemaError, parse_json_text, schema_hash, validate
from .suggest_compact import PARTICIPANT, Compacted, compact, trim
from .suggest_parse import OptionsReader
from .types import TASK_REASONING, Completion, LlmTask, Request, Usage

if TYPE_CHECKING:
    from ..store import Store
    from ..store.people import PersonRow
    from ..store.suggestions import OptionRow

log = get_logger("llm.suggest")

MAX_OUTPUT_TOKENS = 700
TIMEOUT_S = 30.0
POLL_S = 0.2
MIN_WINDOW_MS = 3 * 60_000
ANCHOR_WINDOW_MS = 15 * 60_000
REANCHOR_EVERY_MS = 20 * 60_000
MAX_INPUT_CHARS = 8_000
MAX_ANCHOR_CHARS = 12_000
RATINGS_IN_PROMPT = 10
MEMO_MIN_RATINGS = 10
MEMO_MAX_RATED = 200
MEMO_OUTPUT_TOKENS = 400
DOSSIER_MAX_CHARS = 1_500
DOSSIER_MAX_ACTIONS = 12
DOSSIER_MAX_MEETINGS = 3
ORIENT_OPTIONS = 3
ME_FALLBACK = "Usuário"
NO_PROVIDER = "sem provedor de IA com chave: as sugestões ficam de fora (Configurações, Chaves de acesso)"
BUDGET_TEXT = "sugestões paradas nesta reunião: o custo da IA chegou a {spent}, metade do alerta de {alert}; o resto fica para a ata"
NO_OPTIONS = "a IA não devolveu nenhuma opção válida"
TIMED_OUT = f"prazo de {TIMEOUT_S:.0f} s esgotado"
CANCELLED = "cancelado"

type OnOption = Callable[[SuggestOption], None]


class Outcome(StrEnum):
    DONE = "done"                  # sugestão gravada com opções (no memorando: refeito, ou nada a refazer)
    NO_PROVIDER = "no_provider"    # sem provedor ativo com chave
    BUDGET = "budget"              # o custo da reunião já na metade do alerta (o teto do parcial)
    ERROR = "error"                # rede, provedor, prazo, resposta fora do esquema ou sem opção
    CANCELLED = "cancelled"
    GONE = "gone"                  # a reunião não existe mais (Cancelar)


@dataclass(frozen=True, slots=True)
class SuggestOption:
    idx: int
    kind: str
    text: str
    facts: tuple[tuple[int, int], ...]     # (caption_id, t_ms) das legendas citadas que estão no pedido
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class SuggestResult:
    outcome: Outcome
    meeting_id: int
    suggestion_id: int | None = None
    call_id: int | None = None
    options: tuple[SuggestOption, ...] = ()
    option_ids: tuple[int, ...] = ()       # os ids de `suggestion_options`, na ordem de `options` (o cartão avalia por eles)
    thread_summary: str | None = None
    cost_usd: float | None = None
    cost_estimated: bool = False
    first_ms: int | None = None
    total_ms: int | None = None
    raw_chars: int = 0
    input_chars: int = 0
    until_caption_id: int = 0
    t_until_ms: int = 0
    spent_usd: float = 0.0
    limit_usd: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Window:
    t_from_ms: int
    anchored: bool
    max_chars: int


@dataclass(frozen=True, slots=True)
class MemoResult:
    outcome: Outcome
    memo: str | None = None
    ratings_used: int = 0
    call_id: int | None = None
    cost_usd: float | None = None
    error: str | None = None


# ---------------------------------------------------------------- o pedido
def budget_text(spent: float, limit: float) -> str:
    return BUDGET_TEXT.format(spent=fmt_usd(spent), alert=fmt_usd(limit / partial_task.PARTIAL_BUDGET_SHARE))


def window(store: Store, meeting_id: int, t_ms: int, window_min: int) -> Window:
    """A janela de legendas do pedido feito em `t_ms`: reancorada (15 min, teto maior) na primeira e a cada 20 min; senão desde o
    fim da sugestão anterior, com ao menos 3 min e no máximo `window_min`."""
    prev = store.suggestions.last_ok(meeting_id)
    anchor_t = store.suggestions.last_anchored_t_ms(meeting_id)
    if prev is None or anchor_t is None or t_ms - anchor_t >= REANCHOR_EVERY_MS:
        return Window(max(0, t_ms - ANCHOR_WINDOW_MS), True, MAX_ANCHOR_CHARS)
    since = min(int(prev.get("t_until_ms") or 0), t_ms - MIN_WINDOW_MS)
    return Window(max(0, t_ms - window_min * 60_000, since), False, MAX_INPUT_CHARS)


def me_label(me: PersonRow | None) -> str:
    """O rótulo de quem pede nas legendas sem nome do canal L ("Nome — Cargo")."""
    if me is None:
        return ME_FALLBACK
    role = str(me.get("role") or "").strip()
    return f"{me['name']} — {role}" if role else str(me["name"])


def _asker(me: PersonRow | None) -> Person:
    if me is None:
        return Person(0, ME_FALLBACK)
    return Person(int(me["id"]), str(me["name"]), str(me.get("role") or ""), str(me.get("company") or ""))


def _speakers(store: Store, rows: list[Mapping[str, Any]]) -> tuple[Person, ...]:
    """Os falantes do R na janela, com o cargo do rótulo e a empresa da ficha (o e-mail nunca)."""
    out: dict[str, Person] = {}
    for row in rows:
        label = str(row.get("label") or "").strip()
        if row.get("channel") != "R" or not label or label == PARTICIPANT or label in out:
            continue
        name, _sep, role = label.partition(" — ")
        person = store.people.find_by_name(name.strip())
        out[label] = Person(int(person["id"]) if person else 0, name.strip(), role.strip(), str((person or {}).get("company") or ""))
    return tuple(out.values())


def rated(rows: list[OptionRow]) -> tuple[RatedOption, ...]:
    return tuple(RatedOption(str(r["kind"]), str(r["text"]), r.get("rating"), r.get("reason"), bool(r.get("used"))) for r in rows)


def suggest_ctx(store: Store, meeting: Mapping[str, Any], me: PersonRow | None, mode: SuggestMode, options_n: int, dossier: str | None,
                nudge: str | None, c: Compacted, rows: list[Mapping[str, Any]]) -> SuggestCtx:
    """O contexto do pedido (C4): o cabeçalho com os marcadores e os falantes da janela, quem pede, os blocos opcionais."""
    mid = int(meeting["id"])
    markers = tuple(Marker(int(m["id"]), int(m["t_ms"]), str(m.get("note") or "")) for m in store.list_markers(mid))
    head = replace(partial_task.meeting_ctx(store, meeting), markers=markers, speakers=_speakers(store, rows))
    previous, _last = partial_task.partial_state(store, mid)
    prev = store.suggestions.last_ok(mid)
    memo = str(store.get(SPECS["suggest.memo"])).strip() or None
    goal = str(meeting.get("goal") or "").strip() or None
    return SuggestCtx(head, _asker(me), mode, ORIENT_OPTIONS if mode == SuggestMode.ORIENT else options_n, goal, dossier or None, memo,
                      partial_task.prompt_json(previous), (prev or {}).get("thread_summary") or None, c.lines,
                      rated(store.suggestions.rated_for_meeting(mid, RATINGS_IN_PROMPT)), (nudge or "").strip() or None)


def dossier_text(store: Store, meeting_id: int) -> str:
    """U-161: as ações abertas cujo dono é um esperado ou "Você" e as três últimas reuniões com os esperados; vazio sem nada;
    cortado no fim de uma linha em 1.500 caracteres. Só leitura; nenhuma chamada à LLM."""
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return ""
    try:
        expected = [int(x) for x in json.loads(str(meeting.get("expected_people_json") or "[]"))]
    except (ValueError, TypeError):
        expected = []
    me = store.get_me()
    owners = set(expected) | ({int(me["id"])} if me else set())
    lines: list[str] = []
    if owners:
        for a in store.actions.list_open():
            if a.get("owner_person_id") not in owners:
                continue
            title = a.get("meeting_title") or a.get("meeting_title_llm") or f"reunião {a['meeting_id']}"
            owner = a.get("owner_name") or a.get("owner_text") or "sem dono"
            lines.append(f"- Ação aberta: {a.get('text') or ''} · dono: {owner} · prazo: {a.get('due') or 'sem prazo'} · reunião: {title} "
                         f"({str(a.get('started_at') or '')[:10]})")
            if len(lines) >= DOSSIER_MAX_ACTIONS:
                break
    if expected:
        ids: set[int] = set()
        for pid in expected:
            ids.update(store.people.meetings_of(pid))
        ids.discard(meeting_id)
        parts = []
        for mid in sorted(ids, reverse=True)[:DOSSIER_MAX_MEETINGS]:
            m = store.get_meeting(mid)
            if m is not None:
                parts.append(f"{m.get('title') or m.get('title_llm') or f'reunião {mid}'} ({str(m.get('started_at') or '')[:10]})")
        if parts:
            lines.append("Últimas reuniões com estes participantes: " + "; ".join(parts))
    out: list[str] = []
    total = 0
    for line in lines:
        extra = len(line) + (1 if out else 0)
        if total + extra > DOSSIER_MAX_CHARS:
            break
        out.append(line)
        total += extra
    return "\n".join(out)


def estimate_usd(store: Store, cfg: LlmJobConfig, chars_in: int, chars_out: int) -> float | None:
    """O custo pelos caracteres (÷ 3,5 por token, o contrato), sem chamar o provedor: a dica do botão e a sugestão cancelada."""
    price = price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, cfg.fast_model)
    if price is None:
        return None
    return round((chars_in / CHARS_PER_TOKEN * price.input_usd_m + chars_out / CHARS_PER_TOKEN * price.output_usd_m) / 1e6, 6)


# ---------------------------------------------------------------- a sugestão
def run_suggest(store: Store, meeting_id: int, *, mode: SuggestMode, t_ms: int, nudge: str = "", options_n: int = 3,
                dossier: str | None = None, on_option: OnOption | None = None, cancelled: Callable[[], bool] | None = None,
                provider: LLMProvider | None = None, clock: Callable[[], float] = time.monotonic) -> SuggestResult:
    """Uma sugestão (síncrona: roda numa `StreamTask`). `provider` só nos testes; senão o ativo, com a chave lida agora."""
    t0 = clock()
    stopped = cancelled or (lambda: False)
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return SuggestResult(Outcome.GONE, meeting_id)
    if provider is None and store.providers.active_with_key("llm") is None:
        return SuggestResult(Outcome.NO_PROVIDER, meeting_id, error=NO_PROVIDER)
    spent, limit = partial_task.budget(store, meeting_id)
    if spent >= limit:
        return SuggestResult(Outcome.BUDGET, meeting_id, spent_usd=spent, limit_usd=limit, error=budget_text(spent, limit))
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))   # CP10: a chave some do erro gravado e do log
    w = window(store, meeting_id, t_ms, int(store.get(SPECS["suggest.window_min"])))
    rows: list[Mapping[str, Any]] = [dict(r) for r in store.captions.list_from(meeting_id, w.t_from_ms)]
    me = store.get_me()
    c = trim(compact(rows, me_label(me)), w.max_chars)
    ctx = suggest_ctx(store, meeting, me, mode, options_n, dossier, nudge, c, rows)
    req = Request(LlmTask.SUGGEST, cfg.fast_model, cfg.build(LlmTask.SUGGEST, ctx), prompts.suggest.SCHEMA,
                  max_output_tokens=MAX_OUTPUT_TOKENS, reasoning=TASK_REASONING[LlmTask.SUGGEST], timeout_s=TIMEOUT_S)
    times = {int(r["id"]): int(r.get("t_start_ms") or 0) for r in rows}
    kinds = frozenset(str(k) for k in (KINDS_ORIENT if mode == SuggestMode.ORIENT else KINDS_SPEAK))
    emitted: list[SuggestOption] = []
    first: list[int] = []
    reader = OptionsReader()

    def take(obj: Any) -> None:
        """Uma opção fechada: validada, com os fatos que estão no pedido, entregue na hora (até o número pedido)."""
        if len(emitted) >= ctx.options:
            return
        try:
            data = validate(OPTION_SCHEMA, obj)
        except SchemaError:
            reader.dropped += 1
            return
        text = " ".join(str(data["text"]).split())
        if str(data["kind"]) not in kinds or not text:
            reader.dropped += 1
            return
        facts = tuple((i, times[i]) for i in dict.fromkeys(int(x) for x in data["facts"]) if i in c.ids and i in times)
        opt = SuggestOption(len(emitted), str(data["kind"]), text, facts, Evidence.OK if facts else Evidence.NONE)
        if not first:
            first.append(int((clock() - t0) * 1000))
        emitted.append(opt)
        if on_option is not None:
            on_option(opt)

    async def go() -> tuple[Completion | None, LlmError | None, str]:
        """(a resposta, o erro, por que parou: "", "cancelled" ou "timeout")."""
        p: LLMProvider | None = provider
        call: asyncio.Future[Completion | None] | None = None
        try:
            if p is None:
                p = build_provider(store, cfg.provider_id)[0]
            stream = p.stream(req)

            async def consume() -> Completion | None:
                async for piece in stream:
                    for obj in reader.feed(piece):
                        take(obj)
                return stream.result

            call = asyncio.ensure_future(consume())
            while not call.done():
                await asyncio.wait({call}, timeout=POLL_S)
                if call.done():
                    break
                if stopped():
                    return None, None, "cancelled"
                if clock() - t0 > TIMEOUT_S:
                    return None, None, "timeout"
            return call.result(), None, ""
        except Exception as e:
            return None, classify_exception(e), ""
        finally:
            if call is not None and not call.done():
                call.cancel()
                with suppress(BaseException):
                    await call
            if provider is None and p is not None:
                with suppress(Exception):
                    await p.aclose()

    comp, err, stop = run_async(go())
    total_ms = int((clock() - t0) * 1000)
    data: dict[str, Any] | None = None
    thread: str | None = None
    usage = Usage()
    cost: float | None = None
    estimated = False
    error: str | None = None
    if stop:
        status = SuggestStatus.CANCELLED if stop == "cancelled" else SuggestStatus.ERROR
        error = CANCELLED if stop == "cancelled" else TIMED_OUT
        cost, estimated = estimate_usd(store, cfg, req.text_chars, len(reader.text)), True
    elif err is not None or comp is None:
        status, error = SuggestStatus.ERROR, redact.GLOBAL.redact(str(err) if err is not None else "sem resposta")
    else:
        usage = comp.usage
        cost = cost_usd(usage, price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, comp.model))
        try:
            data = validate(prompts.suggest.SCHEMA, parse_json_text(comp.text))
        except (SchemaError, ValueError) as e:
            status, error = SuggestStatus.ERROR, redact.GLOBAL.redact(f"{OFF_SCHEMA}: {e}")
        else:
            if not emitted:                                        # o leitor não achou a lista: as opções do texto inteiro
                for obj in data["options"]:
                    take(obj)
            thread = " ".join(str(data["thread_summary"]).split())[:SUMMARY_LIMIT] or None
            status = SuggestStatus.OK if emitted else SuggestStatus.ERROR
            error = None if emitted else NO_OPTIONS
    alive = store.get_meeting(meeting_id) is not None
    model = comp.model if comp is not None else cfg.fast_model
    payload_req = [{"role": str(m.role), "text": m.text} for m in req.messages] if cfg.store_payloads else None
    resp = {"text": reader.text} if cfg.store_payloads else None
    call_id = store.llm_calls.insert(LlmCallInsert(meeting_id if alive else None, None, str(LlmTask.SUGGEST), cfg.provider_id, model,
                                                   cfg.version(LlmTask.SUGGEST), schema_hash(prompts.suggest.SCHEMA), usage.input_tokens,
                                                   usage.output_tokens, usage.cached_tokens, usage.audio_tokens, cost,
                                                   comp.latency_ms if comp is not None else total_ms,
                                                   LlmCallStatus.OK if status == SuggestStatus.OK else LlmCallStatus.ERROR, error,
                                                   "stream_estimated" if estimated else "stream", payload_req, resp), cfg.store_payloads)
    if not alive:
        log.info("reunião %s apagada durante a sugestão: nada gravado além da chamada", meeting_id)
        return SuggestResult(Outcome.GONE, meeting_id, call_id=call_id, cost_usd=cost)
    first_ms = first[0] if first else None
    data_json = json.dumps(data, ensure_ascii=False) if data is not None else None
    row = SuggestionInsert(meeting_id, int(t_ms), str(mode), ctx.nudge, ctx.goal, w.t_from_ms, c.until_caption_id, c.t_until_ms,
                           w.anchored, c.raw_chars, c.input_chars, thread, data_json, reader.text, cfg.provider_id, model,
                           cfg.version(LlmTask.SUGGEST), call_id, cost, estimated, first_ms, total_ms, status, error)
    try:
        sid = store.suggestions.insert(row)
        option_ids = store.suggestions.insert_options(sid, [OptionInsert(o.idx, o.kind, o.text, json.dumps(
            [{"caption_id": i, "t_ms": t} for i, t in o.facts]), o.evidence) for o in emitted])
    except sqlite3.IntegrityError:                              # apagada entre a conferência e a gravação
        return SuggestResult(Outcome.GONE, meeting_id, call_id=call_id, cost_usd=cost)
    log.info("reunião %s: sugestão %s (%s): %d legenda(s), %d -> %d caracteres, %d opção(ões), primeira em %s ms, fim em %d ms, %s%s",
             meeting_id, sid, mode, len(c.ids), c.raw_chars, c.input_chars, len(emitted), first_ms if first_ms is not None else "-",
             total_ms, fmt_usd(cost), " estimado" if estimated else "")
    outcome = {SuggestStatus.OK: Outcome.DONE, SuggestStatus.CANCELLED: Outcome.CANCELLED}.get(status, Outcome.ERROR)
    return SuggestResult(outcome, meeting_id, sid, call_id, tuple(emitted), tuple(option_ids), thread, cost, estimated, first_ms, total_ms,
                         c.raw_chars, c.input_chars, c.until_caption_id, c.t_until_ms, spent, limit, error)


# ---------------------------------------------------------------- o memorando
def memo_due(store: Store) -> bool:
    """Há 10 avaliações novas desde o último memorando (o gatilho, idempotente: o que falhou roda na parada seguinte)."""
    return store.suggestions.count_rated() - int(store.get(SPECS["suggest.memo_ratings"])) >= MEMO_MIN_RATINGS


def run_memo(store: Store, *, cancelled: Callable[[], bool] | None = None, provider: LLMProvider | None = None) -> MemoResult:
    """U-158: refaz `suggest.memo` com todas as avaliações (as mais recentes primeiro, até 200); sem 10 novas, nada."""
    total = store.suggestions.count_rated()
    if not memo_due(store):
        return MemoResult(Outcome.DONE, None, total)
    if provider is None and store.providers.active_with_key("llm") is None:
        return MemoResult(Outcome.NO_PROVIDER, error=NO_PROVIDER)
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))
    current = str(store.get(SPECS["suggest.memo"])).strip() or None
    ctx = MemoCtx(current, rated(store.suggestions.list_rated(MEMO_MAX_RATED)), default_language(store))
    req = Request(LlmTask.SUGGEST_MEMO, cfg.fast_model, cfg.build(LlmTask.SUGGEST_MEMO, ctx), prompts.suggest_memo.SCHEMA,
                  max_output_tokens=MEMO_OUTPUT_TOKENS, reasoning=TASK_REASONING[LlmTask.SUGGEST_MEMO], timeout_s=TIMEOUT_S)
    stopped = cancelled or (lambda: False)

    async def go() -> tuple[Completion | None, LlmError | None, bool]:
        p: LLMProvider | None = provider
        try:
            if p is None:                                      # sem retentativa de rede: a parada seguinte tenta de novo
                p = RetryingProvider(build_provider(store, cfg.provider_id, retrying=False)[0], attempts=1)
            call = asyncio.ensure_future(p.complete(req))
            while True:
                done, _pending = await asyncio.wait({call}, timeout=POLL_S)
                if done:
                    return call.result(), None, False
                if stopped():
                    call.cancel()
                    with suppress(BaseException):
                        await call
                    return None, None, True
        except Exception as e:
            return None, classify_exception(e), False
        finally:
            if provider is None and p is not None:
                with suppress(Exception):
                    await p.aclose()

    comp, err, was_cancelled = run_async(go())
    version, digest = cfg.version(LlmTask.SUGGEST_MEMO), schema_hash(prompts.suggest_memo.SCHEMA)
    memo: str | None = None
    error: str | None = None
    if was_cancelled:
        error = CANCELLED
    elif err is not None or comp is None:
        error = redact.GLOBAL.redact(str(err) if err is not None else "sem resposta")
    else:
        try:
            data = validate(prompts.suggest_memo.SCHEMA, comp.data if comp.data is not None else parse_json_text(comp.text))
            memo = str(data["memo"]).strip() or None
        except (SchemaError, ValueError) as e:
            error = redact.GLOBAL.redact(f"{OFF_SCHEMA}: {e}")
        if memo is None and error is None:
            error = "memorando vazio"
    usage = comp.usage if comp is not None else Usage()
    cost = (cost_usd(usage, price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, comp.model))
            if comp is not None else None)
    call_id = store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.SUGGEST_MEMO), cfg.provider_id,
                                                   comp.model if comp is not None else cfg.fast_model, version, digest, usage.input_tokens,
                                                   usage.output_tokens, usage.cached_tokens, 0, cost,
                                                   comp.latency_ms if comp is not None else 0,
                                                   LlmCallStatus.OK if memo is not None else LlmCallStatus.ERROR, error, "direct"),
                                     store_payloads=False)
    if memo is None:
        log.warning("memorando das sugestões não refeito: %s", error)
        return MemoResult(Outcome.CANCELLED if was_cancelled else Outcome.ERROR, None, total, call_id, cost, error)
    store.set_setting("suggest.memo", memo)
    store.set_setting("suggest.memo_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    store.set_setting("suggest.memo_ratings", str(total))
    log.info("memorando das sugestões refeito com %d avaliação(ões) (%s)", total, fmt_usd(cost))
    return MemoResult(Outcome.DONE, memo, total, call_id, cost)


__all__ = ["ANCHOR_WINDOW_MS", "MEMO_MIN_RATINGS", "MIN_WINDOW_MS", "REANCHOR_EVERY_MS", "TIMEOUT_S", "MemoResult", "OnOption", "Outcome",
           "SuggestOption", "SuggestResult", "Window", "budget_text", "dossier_text", "estimate_usd", "me_label", "memo_due", "rated",
           "run_memo", "run_suggest", "suggest_ctx", "window"]
