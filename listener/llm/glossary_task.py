"""Glossário do warm-up (handoff §4, F3 item 4, U-018): o app casa os participantes digitados com `people` localmente
(casefold, `difflib`, nome inteiro ou primeiro nome único) e pede à LLM os termos prováveis e o mapeamento do que sobrou,
numa única chamada feita numa `Task` do app (`asyncio.run` com cliente próprio). `llm_calls.meeting_id` fica nulo até a
reunião existir (o controlador religa). Sem provedor com chave: `GlossaryResult.error` e o warm-up segue sem glossário."""
from __future__ import annotations

import asyncio
import difflib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model import LlmCallStatus
from ..store.llm_calls import LlmCallInsert
from . import prompts, redact
from .base import LlmError, classify_exception
from .costs import cost_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.common import Person
from .registry import build_provider
from .schema import schema_hash
from .types import TASK_REASONING, LlmTask, Request

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.glossary")

MATCH_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class GlossaryResult:
    terms: tuple[str, ...] = ()
    people: tuple[tuple[str, int], ...] = ()        # (nome digitado, person_id)
    call_id: int | None = None
    error: str | None = None
    cost_usd: float | None = None

    @property
    def glossary_text(self) -> str:
        return ", ".join(self.terms)

    @property
    def expected_people_json(self) -> str | None:
        ids = sorted({pid for _, pid in self.people})
        return json.dumps(ids) if ids else None


def split_names(text: str | None) -> list[str]:
    """'Ana, Bia; Carlos Lima' -> ['Ana', 'Bia', 'Carlos Lima']."""
    if not text:
        return []
    return [n.strip() for n in re.split(r"[,;\n]", text) if n.strip()]


def match_people_locally(names: Sequence[str], people: Sequence[dict[str, Any]]) -> tuple[list[tuple[str, int]], list[str]]:
    """Casa cada nome com `people` sem LLM: igual (casefold), primeiro nome único ou semelhança `difflib` >= 0,85.
    Devolve (casados, não resolvidos). Uma pessoa não é usada duas vezes."""
    resolved: list[tuple[str, int]] = []
    unresolved: list[str] = []
    taken: set[int] = set()
    rows = [(int(p["id"]), str(p.get("name") or "").strip()) for p in people if p.get("name")]
    for name in names:
        key = name.casefold()
        hit: int | None = None
        exact = [pid for pid, full in rows if full.casefold() == key]            # ambiguidade conta todas as fichas, não só as livres
        if len(exact) == 1 and exact[0] not in taken:
            hit = exact[0]
        if hit is None:
            firsts = [pid for pid, full in rows if full.casefold().split(" ")[0] == key.split(" ")[0]]
            if len(firsts) == 1 and len(key.split(" ")) == 1 and firsts[0] not in taken:
                hit = firsts[0]
        if hit is None:
            scored = sorted(((difflib.SequenceMatcher(None, key, full.casefold()).ratio(), pid) for pid, full in rows if pid not in taken),
                            reverse=True)
            if scored and scored[0][0] >= MATCH_RATIO and (len(scored) == 1 or scored[1][0] < scored[0][0]):
                hit = scored[0][1]
        if hit is None:
            unresolved.append(name)
        else:
            taken.add(hit)
            resolved.append((name, hit))
    return resolved, unresolved


def merge_glossary(llm_terms: Sequence[str], user_text: str) -> str:
    """Termos da LLM mais os que o usuário já tinha, sem repetir (casefold), na ordem."""
    seen: set[str] = set()
    out: list[str] = []
    for term in [*llm_terms, *split_names(user_text)]:
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(term)
    return ", ".join(out)


def request_glossary(store: Store, title: str | None, participants: str | None) -> GlossaryResult:
    """Chamada síncrona (roda numa `Task`): casa localmente, pergunta à LLM o resto e grava em `llm_calls` (reunião nula)."""
    from ..config.defaults import SPECS

    people: list[dict[str, Any]] = [dict(p) for p in store.people.list_people()]
    resolved, unresolved = match_people_locally(split_names(participants), people)
    if not store.get(SPECS["llm.glossary_enabled"]):
        return GlossaryResult((), tuple(resolved))
    if store.providers.active_with_key("llm") is None:
        return GlossaryResult((), tuple(resolved), error="sem chave: configure um provedor de LLM nas Configurações")
    cfg = LlmJobConfig.from_store(store)
    known = tuple(Person(int(p["id"]), str(p["name"]), str(p.get("role") or ""), str(p.get("company") or "")) for p in people)
    fixed = str(store.get(SPECS["pipeline.glossary"]))
    ctx = prompts.glossary.GlossaryCtx(title, participants, unresolved, known, fixed, cfg.output_language)
    req = Request(LlmTask.GLOSSARY, cfg.fast_model, prompts.glossary.build(ctx), prompts.glossary.SCHEMA, max_output_tokens=1024,
                  reasoning=TASK_REASONING[LlmTask.GLOSSARY], timeout_s=30.0)

    async def go() -> tuple[Any, LlmError | None]:
        provider, _row = build_provider(store, cfg.provider_id)
        try:
            return await provider.complete(req), None
        except Exception as e:
            return None, classify_exception(e)
        finally:
            await provider.aclose()

    comp, err = asyncio.run(go())
    prices = store.providers.list_prices(cfg.provider_kind or None)
    version, digest = prompts.prompt_version(LlmTask.GLOSSARY), schema_hash(prompts.glossary.SCHEMA)
    if err is not None or comp is None:
        message = redact.GLOBAL.redact(str(err) if err else "sem resposta")
        call_id = store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.GLOSSARY), cfg.provider_id, cfg.fast_model, version, digest,
                                                       0, 0, 0, 0, None, 0, LlmCallStatus.ERROR, message, "direct"), store_payloads=False)
        log.warning("glossário: %s", message)
        return GlossaryResult((), tuple(resolved), call_id, message)
    cost = cost_usd(comp.usage, price_for(prices, cfg.provider_kind, comp.model))
    payload_req = [{"role": str(m.role), "text": m.text} for m in req.messages] if cfg.store_payloads else None
    u = comp.usage
    row = LlmCallInsert(None, None, str(LlmTask.GLOSSARY), cfg.provider_id, comp.model, version, digest, u.input_tokens, u.output_tokens,
                        u.cached_tokens, 0, cost, comp.latency_ms, LlmCallStatus.OK, None, "direct", payload_req,
                        {"text": comp.text} if cfg.store_payloads else None)
    call_id = store.llm_calls.insert(row, store_payloads=cfg.store_payloads)
    data = comp.data or {}
    terms = tuple(str(t).strip() for t in data.get("terms") or [] if str(t).strip())
    by_id = {int(p["id"]) for p in people}
    extra = [(str(p.get("name") or ""), int(p["person_id"])) for p in data.get("people") or []
             if isinstance(p.get("person_id"), int) and int(p["person_id"]) in by_id and p.get("name") in unresolved]
    return GlossaryResult(terms, tuple(resolved) + tuple(extra), call_id, None, cost)
