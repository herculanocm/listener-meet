"""F5.20 (U-158), dividido da `llm/suggest_task.py` no L0 da F5.21 (A-27), sem mudar nada: o memorando do que o usuário
prefere nas sugestões. Com 10 avaliações novas desde o último, uma chamada sem reunião (o custo do mês) refaz `suggest.memo`
só com estilo e abordagem; cancelável a cada 0,2 s, sem retentativa de rede (a parada seguinte tenta de novo). O log leva
contagens e custo, nunca o memorando."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..config.defaults import SPECS
from ..config.language import default_language
from ..log import get_logger
from ..model import LlmCallStatus
from ..store.llm_calls import LlmCallInsert
from . import prompts, redact
from .aio import run_async
from .base import OFF_SCHEMA, LlmError, LLMProvider, classify_exception
from .costs import cost_usd, fmt_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.suggest_memo import MemoCtx
from .registry import RetryingProvider, build_provider
from .schema import SchemaError, parse_json_text, schema_hash, validate
from .suggest_ctx import CANCELLED, NO_PROVIDER, POLL_S, TIMEOUT_S, Outcome, rated
from .types import TASK_REASONING, Completion, LlmTask, Request, Usage

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.suggest")

MEMO_MIN_RATINGS = 10
MEMO_MAX_RATED = 200
MEMO_OUTPUT_TOKENS = 400


@dataclass(frozen=True, slots=True)
class MemoResult:
    outcome: Outcome
    memo: str | None = None
    ratings_used: int = 0
    call_id: int | None = None
    cost_usd: float | None = None
    error: str | None = None


def memo_due(store: Store) -> bool:
    """Há 10 avaliações novas desde o último memorando (o gatilho, idempotente: o que falhou roda na parada seguinte): as feitas
    depois de `suggest.memo_at` (R-08: uma reunião excluída não trava a conta, como travaria a diferença das contagens)."""
    return store.suggestions.count_rated_since(str(store.get(SPECS["suggest.memo_at"])) or None) >= MEMO_MIN_RATINGS


def run_memo(store: Store, *, cancelled: Callable[[], bool] | None = None, provider: LLMProvider | None = None) -> MemoResult:
    """U-158: refaz `suggest.memo` com todas as avaliações (as mais recentes primeiro, até 200); sem 10 novas, nada."""
    total = store.suggestions.count_rated()
    if not memo_due(store):
        return MemoResult(Outcome.DONE, None, total)
    if provider is None and store.providers.active_with_key("llm") is None:
        return MemoResult(Outcome.NO_PROVIDER, error=NO_PROVIDER)
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))
    started = datetime.now().astimezone().isoformat(timespec="milliseconds")   # o formato do `rated_at`: a conta seguinte parte daqui
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
    store.set_setting("suggest.memo_at", started)
    store.set_setting("suggest.memo_ratings", str(total))
    log.info("memorando das sugestões refeito com %d avaliação(ões) (%s)", total, fmt_usd(cost))
    return MemoResult(Outcome.DONE, memo, total, call_id, cost)


__all__ = ["MEMO_MAX_RATED", "MEMO_MIN_RATINGS", "MEMO_OUTPUT_TOKENS", "MemoResult", "memo_due", "run_memo"]
