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

No L0 da F5.21 (A-27), os tipos e o pedido foram para `llm/suggest_ctx.py`, o dossiê para `llm/suggest_dossier.py` e o
memorando para `llm/suggest_memo_task.py`, sem mudar nada; os nomes de antes seguem importáveis daqui.

- F5.21: com a tela (`ScreenShot`, já preparada fora daqui, A-17), o aviso `ScreenSent` sai pelo `on_screen` depois das
  saídas cedo (A-33); a imagem vai na última mensagem do usuário; os resumos das telas anteriores na janela entram na linha
  do tempo, com os caracteres deles fora do teto antes da compactação (A-35); o esquema e o teto de saída da tela; a tela
  gravada com a sugestão, a opção com `screen` e o resumo da tela na sugestão. O tempo conta do clique (`clicked_at`, A-19).

O log leva ids, contagens, tempos e custo; nunca o texto da sugestão, do fio, do objetivo, da linha nem do memorando."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..config.defaults import SPECS
from ..log import get_logger
from ..model import Evidence, LlmCallStatus, ScreenStatus, SuggestMode, SuggestStatus
from ..store.llm_calls import LlmCallInsert
from ..store.suggestions import OptionInsert, ScreenInsert, SuggestionInsert
from . import partial_task, redact
from .aio import run_async
from .base import OFF_SCHEMA, LlmError, LLMProvider, classify_exception
from .costs import cost_usd, fmt_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.suggest import (KINDS_ORIENT, KINDS_SPEAK, MAX_SCREEN_LINES, OPTION_SCHEMA, OPTION_SCHEMA_SCREEN, SCREEN_SUMMARY_LIMIT,
                              SUMMARY_LIMIT, TEXT_LIMIT, ScreenLine, ScreenRef, has_screen, schema_for)
from .registry import build_provider
from .schema import SchemaError, parse_json_text, schema_hash, validate
from .suggest_compact import compact, trim
from .suggest_ctx import (ANCHOR_WINDOW_MS, CANCELLED, MAX_ANCHOR_CHARS, MAX_INPUT_CHARS, MIN_WINDOW_MS, NO_OPTIONS, NO_PROVIDER, POLL_S,
                          REANCHOR_EVERY_MS, TIMED_OUT, TIMEOUT_S, OnOption, Outcome, ScreenSent, ScreenShot, SuggestOption,
                          SuggestResult, Window, bounded, budget_text, estimate_usd, me_label, rated, suggest_ctx, window)
from .suggest_dossier import DOSSIER_MAX_ACTIONS, DOSSIER_MAX_CHARS, DOSSIER_MAX_MEETINGS, dossier_text
from .suggest_memo_task import MEMO_MAX_RATED, MEMO_MIN_RATINGS, MEMO_OUTPUT_TOKENS, MemoResult, memo_due, run_memo
from .suggest_parse import OptionsReader
from .types import TASK_REASONING, Completion, LlmTask, Request, Usage

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.suggest")

MAX_OUTPUT_TOKENS = 700
MAX_OUTPUT_TOKENS_SCREEN = 900          # F5.21 (A-15): 4 opções, o fio e o resumo da tela passam de ~650 tokens [exp]


# ---------------------------------------------------------------- a sugestão
def run_suggest(store: Store, meeting_id: int, *, mode: SuggestMode, t_ms: int, nudge: str = "", options_n: int = 3,
                dossier: str | None = None, on_option: OnOption | None = None, cancelled: Callable[[], bool] | None = None,
                provider: LLMProvider | None = None, clock: Callable[[], float] = time.monotonic, screen: ScreenShot | None = None,
                on_screen: Callable[[ScreenSent], None] | None = None, clicked_at: float | None = None) -> SuggestResult:
    """Uma sugestão (síncrona: roda numa `StreamTask`). `provider` só nos testes; senão o ativo, com a chave lida agora.
    F5.21: `screen` é a tela já preparada; `clicked_at`, o instante do clique no mesmo relógio (o print e o preparo contam)."""
    t0 = clicked_at if clicked_at is not None else clock()
    stopped = cancelled or (lambda: False)
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return SuggestResult(Outcome.GONE, meeting_id)
    if provider is None and store.providers.active_with_key("llm") is None:
        return SuggestResult(Outcome.NO_PROVIDER, meeting_id, error=NO_PROVIDER)
    spent, limit = partial_task.budget(store, meeting_id)
    if spent >= limit:
        return SuggestResult(Outcome.BUDGET, meeting_id, spent_usd=spent, limit_usd=limit, error=budget_text(spent, limit))
    if screen is not None and on_screen is not None:          # A-33: depois das saídas cedo, a miniatura entra no chat
        on_screen(ScreenSent(int(t_ms), screen))
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))   # CP10: a chave some do erro gravado e do log
    w = window(store, meeting_id, t_ms, int(store.get(SPECS["suggest.window_min"])))
    rows: list[Mapping[str, Any]] = [dict(r) for r in store.captions.list_from(meeting_id, w.t_from_ms)]
    me = store.get_me()
    screens = tuple(ScreenLine(st, summary) for st, summary in
                    reversed(store.suggestions.screen_summaries(meeting_id, w.t_from_ms, int(t_ms), MAX_SCREEN_LINES)))
    budget = max(w.max_chars // 2, w.max_chars - sum(len(s.render()) + 1 for s in screens))   # A-35: as telas fora do teto
    c = trim(compact(rows, me_label(me), bool(meeting.get("is_in_room"))), budget)
    image = screen.image if screen is not None and screen.status == ScreenStatus.SENT else None
    ctx = replace(suggest_ctx(store, meeting, me, mode, options_n, dossier, nudge, c, rows),
                  screen=ScreenRef(screen.app, int(t_ms)) if screen is not None and image is not None else None, screens=screens)
    with_screen = has_screen(ctx)
    messages = cfg.build(LlmTask.SUGGEST, ctx)
    if image is not None:                                     # a imagem na última mensagem do usuário (o texto antes, A-11)
        messages = (*messages[:-1], replace(messages[-1], images=(image,)))
    schema = schema_for(ctx)
    req = Request(LlmTask.SUGGEST, cfg.fast_model, messages, schema,
                  max_output_tokens=MAX_OUTPUT_TOKENS_SCREEN if with_screen else MAX_OUTPUT_TOKENS,
                  reasoning=TASK_REASONING[LlmTask.SUGGEST], timeout_s=TIMEOUT_S)
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
            data = validate(OPTION_SCHEMA_SCREEN if with_screen else OPTION_SCHEMA, bounded(obj))
        except SchemaError:
            reader.dropped += 1
            return
        text = " ".join(str(data["text"]).split())
        if str(data["kind"]) not in kinds or not text:
            reader.dropped += 1
            return
        facts = tuple((i, times[i]) for i in dict.fromkeys(int(x) for x in data["facts"]) if i in c.ids and i in times)
        uses = bool(data.get("screen")) and with_screen       # A-42: a tela também sustenta a opção
        opt = SuggestOption(len(emitted), str(data["kind"]), text, facts, Evidence.OK if facts or uses else Evidence.NONE, uses)
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
    screen_summary: str | None = None
    usage = Usage()
    cost: float | None = None
    estimated = False
    error: str | None = None
    if stop:
        status = SuggestStatus.CANCELLED if stop == "cancelled" else SuggestStatus.ERROR
        error = CANCELLED if stop == "cancelled" else TIMED_OUT
        cost, estimated = estimate_usd(store, cfg, req.text_chars, len(reader.text), req.image_count), True
    elif err is not None or comp is None:
        status, error = SuggestStatus.ERROR, redact.GLOBAL.redact(str(err) if err is not None else "sem resposta")
    else:
        usage = comp.usage
        cost = cost_usd(usage, price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, comp.model))
        try:
            data = validate(schema, bounded(parse_json_text(comp.text)))
        except (SchemaError, ValueError) as e:
            status, error = SuggestStatus.ERROR, redact.GLOBAL.redact(f"{OFF_SCHEMA}: {e}")
        else:
            if not emitted:                                        # o leitor não achou a lista: as opções do texto inteiro
                for obj in data["options"]:
                    take(obj)
            thread = " ".join(str(data["thread_summary"]).split())[:SUMMARY_LIMIT] or None
            if image is not None:                                  # o resumo descreve a imagem anexa (7b, os pedidos seguintes)
                screen_summary = " ".join(str(data.get("screen_summary") or "").split())[:SCREEN_SUMMARY_LIMIT] or None
            status = SuggestStatus.OK if emitted else SuggestStatus.ERROR
            error = None if emitted else NO_OPTIONS
    alive = store.get_meeting(meeting_id) is not None
    model = comp.model if comp is not None else cfg.fast_model
    payload_req = ([{"role": str(m.role), "text": m.text, **({"images": [i.describe() for i in m.images]} if m.images else {})}
                    for m in req.messages] if cfg.store_payloads else None)      # A-14: o rótulo, nunca os bytes
    resp = {"text": reader.text} if cfg.store_payloads else None
    call_id = store.llm_calls.insert(LlmCallInsert(meeting_id if alive else None, None, str(LlmTask.SUGGEST), cfg.provider_id, model,
                                                   cfg.version(LlmTask.SUGGEST), schema_hash(schema), usage.input_tokens,
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
                           cfg.version(LlmTask.SUGGEST), call_id, cost, estimated, first_ms, total_ms, status, error, screen_summary)
    try:
        sid = store.suggestions.insert(row)
        option_ids = store.suggestions.insert_options(sid, [OptionInsert(o.idx, o.kind, o.text, json.dumps(
            [{"caption_id": i, "t_ms": t} for i, t in o.facts]), o.evidence, o.screen) for o in emitted])
        if screen is not None:
            store.suggestions.insert_screen(ScreenInsert(sid, screen.status, screen.source, screen.app, image.width if image else 0,
                                                         image.height if image else 0, len(image.data) if image else 0,
                                                         screen.covered, screen.thumb, screen.copy))
    except sqlite3.IntegrityError:                              # apagada entre a conferência e a gravação
        return SuggestResult(Outcome.GONE, meeting_id, call_id=call_id, cost_usd=cost)
    log.info("reunião %s: sugestão %s (%s): %d legenda(s), %d -> %d caracteres, %d opção(ões), primeira em %s ms, fim em %d ms, %s%s%s",
             meeting_id, sid, mode, len(c.ids), c.raw_chars, c.input_chars, len(emitted), first_ms if first_ms is not None else "-",
             total_ms, fmt_usd(cost), " estimado" if estimated else "",
             f", tela {screen.status} {len(image.data) // 1024 if image else 0} kB, {len(screens)} anterior(es)" if screen else
             f", {len(screens)} tela(s) anterior(es)" if screens else "")
    outcome = {SuggestStatus.OK: Outcome.DONE, SuggestStatus.CANCELLED: Outcome.CANCELLED}.get(status, Outcome.ERROR)
    return SuggestResult(outcome, meeting_id, sid, call_id, tuple(emitted), tuple(option_ids), thread, cost, estimated, first_ms, total_ms,
                         c.raw_chars, c.input_chars, c.until_caption_id, c.t_until_ms, spent, limit, error, image is not None)


__all__ = ["ANCHOR_WINDOW_MS", "CANCELLED", "DOSSIER_MAX_ACTIONS", "DOSSIER_MAX_CHARS", "DOSSIER_MAX_MEETINGS", "MAX_ANCHOR_CHARS",
           "MAX_INPUT_CHARS", "MAX_OUTPUT_TOKENS", "MAX_OUTPUT_TOKENS_SCREEN", "MEMO_MAX_RATED", "MEMO_MIN_RATINGS",
           "MEMO_OUTPUT_TOKENS", "MIN_WINDOW_MS", "NO_OPTIONS", "NO_PROVIDER", "POLL_S", "REANCHOR_EVERY_MS", "SUMMARY_LIMIT",
           "TEXT_LIMIT", "TIMED_OUT", "TIMEOUT_S", "MemoResult", "OnOption", "OptionsReader", "Outcome", "ScreenSent", "ScreenShot",
           "SuggestOption", "SuggestResult", "Window", "bounded", "budget_text", "dossier_text", "estimate_usd", "me_label",
           "memo_due", "rated", "run_memo", "run_suggest", "suggest_ctx", "window"]
