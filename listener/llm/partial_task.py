"""Resumo parcial ao vivo (F4 item 7, Q6, CP10), no padrão do chat: roda fora da thread do Qt (uma `StreamTask`, QThread com
`run_async` e cliente próprio), uma chamada por vez (o controlador só lança outra quando esta volta), cancelável entre
esperas de 0,2 s (Cancelar a gravação, sair do app).

Lê o parcial atual da reunião e as legendas novas desde ele (`captions.id` acima do `last_caption_id` guardado no
`data_json`), em ordem de tempo e até ~20 mil caracteres (o que sobrar fica para o próximo intervalo, sem buraco no
resumo acumulado), e manda ao modelo rápido com o contexto da reunião e o parcial anterior. Grava `llm_calls` (tarefa
`partial`, com `meeting_id`; chave lida na hora; erro redigido) e `summaries` kind `partial` (o anterior fica superado)
com `t_until_ms` = fim da última legenda usada, `call_id`, `data_json` (resumo, decisões e ações com o `t_ms` da legenda
citada) e `text_llm` em Markdown. Antes da chamada, o orçamento (Q6): com o custo de LLM da reunião em
`PARTIAL_BUDGET_SHARE` do `llm.cost_alert_usd` ou mais, não chama e devolve `budget` (o controlador para o parcial nessa
reunião: o resto fica para a ata). Reunião apagada durante a chamada (Cancelar): a chamada fica em `llm_calls` sem
reunião (o custo do mês continua certo) e nada mais é gravado. F5.3 (A-02): no idioma da reunião."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..config.defaults import SPECS
from ..config.language import meeting_language, output_code
from ..log import get_logger
from ..model import LlmCallStatus, SummaryKind
from ..store.captions import CaptionRow
from ..store.llm_calls import LlmCallInsert
from ..store.summaries import SummaryInsert
from . import prompts, redact
from .aio import run_async
from .base import LlmError, LLMProvider, classify_exception
from .costs import cost_usd, fmt_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.common import Line, MeetingCtx, Person, stamp
from .prompts.partial import PartialCtx
from .registry import RetryingProvider, build_provider
from .schema import schema_hash
from .types import TASK_REASONING, Completion, LlmTask, Request

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.partial")

PARTIAL_BUDGET_SHARE = 0.5         # Q6: o parcial para na metade do alerta de custo da reunião, para sobrar para a ata
MAX_INPUT_CHARS = 20_000           # legendas novas por chamada (~5 mil tokens, ~30 min de fala)
MAX_CAPTION_CHARS = 600
MAX_OUTPUT_TOKENS = 2048
TIMEOUT_S = 60.0
POLL_S = 0.2
NO_PROVIDER = ("sem provedor de LLM com chave: o resumo parcial fica de fora (Configurações, Chaves de acesso); as legendas "
               "seguem")
HEADINGS = {"pt": ("Possíveis decisões", "Possíveis ações", "dono"), "en": ("Possible decisions", "Possible actions", "owner"),
            "es": ("Posibles decisiones", "Posibles acciones", "responsable")}


class Outcome(StrEnum):
    DONE = "done"                  # parcial novo gravado
    NOTHING = "nothing"            # nenhuma legenda nova desde o último
    NO_PROVIDER = "no_provider"    # sem provedor ativo com chave
    BUDGET = "budget"              # Q6: custo da reunião já na metade do alerta
    ERROR = "error"                # rede, provedor, resposta fora do esquema
    CANCELLED = "cancelled"
    GONE = "gone"                  # a reunião não existe mais (Cancelar)


@dataclass(frozen=True, slots=True)
class PartialResult:
    outcome: Outcome
    meeting_id: int
    summary_id: int | None = None
    call_id: int | None = None
    t_until_ms: int | None = None
    captions: int = 0              # legendas usadas nesta chamada
    left: int = 0                  # legendas novas que ficaram para a próxima (acima do limite de entrada)
    cost_usd: float | None = None
    spent_usd: float = 0.0         # custo de LLM da reunião antes da chamada
    limit_usd: float = 0.0         # PARTIAL_BUDGET_SHARE * llm.cost_alert_usd
    error: str | None = None
    last_caption_id: int = 0       # a última legenda coberta pelo parcial gravado


def budget(store: Store, meeting_id: int) -> tuple[float, float]:
    """(custo de LLM já gasto pela reunião, teto do parcial = `PARTIAL_BUDGET_SHARE` do alerta); chamada sem preço conta 0."""
    total, _missing = store.llm_calls.cost_for_meeting(meeting_id)
    return float(total or 0.0), PARTIAL_BUDGET_SHARE * float(store.get(SPECS["llm.cost_alert_usd"]))


def budget_text(spent: float, limit: float) -> str:
    return (f"resumo parcial parado nesta reunião: o custo de LLM chegou a {fmt_usd(spent)}, metade do alerta de "
            f"{fmt_usd(limit / PARTIAL_BUDGET_SHARE)}; o resto fica para a ata")


def partial_state(store: Store, meeting_id: int) -> tuple[dict[str, Any], int]:
    """(`data_json` do parcial atual, `last_caption_id` dele); ({}, 0) antes do primeiro."""
    row = store.summaries.current(meeting_id, SummaryKind.PARTIAL)
    try:
        data = json.loads(str((row or {}).get("data_json") or "{}"))
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    return data, int(data.get("last_caption_id") or 0)


def select_captions(rows: Sequence[CaptionRow], max_chars: int = MAX_INPUT_CHARS) -> tuple[list[CaptionRow], int]:
    """As legendas novas pela ordem dos ids até o limite de caracteres (ao menos uma), devolvidas em ordem de tempo; devolve
    (usadas, quantas sobraram). Pelos ids porque o próximo parcial começa depois do maior id usado e L e R entram fora da
    ordem de tempo: escolher pelo tempo deixaria para trás uma legenda de id menor que nunca mais seria lida."""
    used: list[CaptionRow] = []
    total = 0
    for r in sorted(rows, key=lambda r: int(r["id"])):
        n = min(len(str(r.get("text") or "")), MAX_CAPTION_CHARS) + 40
        if used and total + n > max_chars:
            break
        used.append(r)
        total += n
    used.sort(key=lambda r: (int(r.get("t_start_ms") or 0), int(r["id"])))
    return used, len(rows) - len(used)


def _speaker(row: CaptionRow, me: str) -> str:
    """O rótulo da legenda; sem ele (nomes desligados), o usuário no canal L e "Participante" no R."""
    label = str(row.get("label") or "").strip()
    return label or (me if row.get("channel") == "L" else "Participante")


def meeting_ctx(store: Store, meeting: Mapping[str, Any]) -> MeetingCtx:
    """Contexto da reunião para o parcial: título, data, participantes esperados, glossário (sem marcadores) e o idioma
    dela (F5.3 A-02)."""
    people = {int(p["id"]): p for p in store.people.list_people()}
    expected: list[Person] = []
    with suppress(ValueError, TypeError):
        for pid in json.loads(str(meeting.get("expected_people_json") or "[]")):
            p = people.get(int(pid))
            if p is not None:
                expected.append(Person(int(pid), str(p["name"]), str(p.get("role") or ""), str(p.get("company") or "")))
    title = meeting.get("title") or meeting.get("title_llm")
    participants = meeting.get("expected_participants")
    return MeetingCtx(str(title) if title else None, str(meeting.get("started_at") or "")[:10], str(participants) if participants else None,
                      tuple(expected), (), output_code(meeting_language(store, meeting)), str(meeting.get("glossary") or ""))


def normalize(raw: Mapping[str, Any], times: Mapping[int, int]) -> dict[str, Any]:
    """Saída validada -> `data_json`: textos sem espaços sobrando, sem repetir item, `caption_id` desconhecido vira nulo e o
    conhecido ganha o `t_ms` da legenda."""
    out: dict[str, Any] = {"summary": str(raw.get("summary") or "").strip(), "decisions": [], "actions": []}
    for kind in ("decisions", "actions"):
        seen: set[str] = set()
        for it in raw.get(kind) or ():
            if not isinstance(it, Mapping):
                continue
            text = " ".join(str(it.get("text") or "").split())
            if not text or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            cid = it.get("caption_id")
            cid = int(cid) if isinstance(cid, int) and int(cid) in times else None
            item: dict[str, Any] = {"text": text, "caption_id": cid, "t_ms": times.get(cid) if cid is not None else None}
            if kind == "actions":
                item["owner"] = " ".join(str(it.get("owner") or "").split()) or None
            out[kind].append(item)
    return out


def render_markdown(data: Mapping[str, Any], language: str = "pt-BR") -> str:
    """Markdown do projeto (CP2): o resumo e as duas listas, com `[mm:ss]` da legenda citada."""
    code = (language or "pt").lower().split("-")[0]
    h_dec, h_act, w_owner = HEADINGS.get(code, HEADINGS["en"])
    out = [str(data.get("summary") or "").strip(), ""]
    for heading, kind in ((h_dec, "decisions"), (h_act, "actions")):
        out += [f"## {heading}", ""]
        items = data.get(kind) or ()
        for it in items:
            t = it.get("t_ms")
            owner = f" · {w_owner}: {it['owner']}" if kind == "actions" and it.get("owner") else ""
            out.append(f"- {it.get('text')}{owner}" + (f" [{stamp(int(t))}]" if t is not None else ""))
        if not items:
            out.append("- —")
        out.append("")
    return "\n".join(out).strip() + "\n"


def run_partial(store: Store, meeting_id: int, *, provider: LLMProvider | None = None,
                cancelled: Callable[[], bool] | None = None) -> PartialResult:
    """Um parcial (síncrono: roda numa `StreamTask`). `provider` só nos testes; senão o ativo, com a chave lida agora."""
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return PartialResult(Outcome.GONE, meeting_id)
    previous, last_id = partial_state(store, meeting_id)
    rows = store.captions.list_for_meeting(meeting_id, after_id=last_id)
    if not rows:
        return PartialResult(Outcome.NOTHING, meeting_id)
    if provider is None and store.providers.active_with_key("llm") is None:
        return PartialResult(Outcome.NO_PROVIDER, meeting_id, error=NO_PROVIDER)
    spent, limit = budget(store, meeting_id)
    if spent >= limit:
        return PartialResult(Outcome.BUDGET, meeting_id, spent_usd=spent, limit_usd=limit, error=budget_text(spent, limit))
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))   # CP10: a chave some do erro gravado e do log, como no job `llm`
    used, left = select_captions(rows, MAX_INPUT_CHARS)
    me_row = store.get_me()
    me = str(me_row["name"]) if me_row else "Usuário"
    lines = [Line(int(r["id"]), int(r["t_start_ms"] or 0), _speaker(r, me), " ".join(str(r.get("text") or "").split())[:MAX_CAPTION_CHARS])
             for r in used]
    ctx = meeting_ctx(store, meeting)
    previous_json: str | None = None
    if previous:                                           # só o que a LLM devolve: sem `t_ms` nem os metadados do projeto
        prev = {"summary": previous.get("summary") or ""}
        for kind in ("decisions", "actions"):
            prev[kind] = [{k: v for k, v in it.items() if k != "t_ms"} for it in previous.get(kind) or () if isinstance(it, dict)]
        previous_json = json.dumps(prev, ensure_ascii=False, separators=(",", ":"))
    req = Request(LlmTask.PARTIAL, cfg.fast_model, prompts.partial.build(PartialCtx(ctx, previous_json, lines)), prompts.partial.SCHEMA,
                  max_output_tokens=MAX_OUTPUT_TOKENS, reasoning=TASK_REASONING[LlmTask.PARTIAL], timeout_s=TIMEOUT_S)

    async def go() -> tuple[Completion | None, LlmError | None, bool]:
        own = provider is None
        p: LLMProvider | None = provider
        try:
            if p is None:                                  # sem retentativa de rede: o próximo intervalo é a nova tentativa
                p = RetryingProvider(build_provider(store, cfg.provider_id, retrying=False)[0], attempts=1)
            call = asyncio.ensure_future(p.complete(req))
            while True:
                done, _pending = await asyncio.wait({call}, timeout=POLL_S)
                if done:
                    return call.result(), None, False
                if cancelled is not None and cancelled():
                    call.cancel()
                    with suppress(BaseException):
                        await call
                    return None, None, True
        except Exception as e:
            return None, classify_exception(e), False
        finally:
            if own and p is not None:
                with suppress(Exception):
                    await p.aclose()

    comp, err, was_cancelled = run_async(go())
    alive = store.get_meeting(meeting_id) is not None
    call_mid = meeting_id if alive else None
    version, digest = prompts.prompt_version(LlmTask.PARTIAL), schema_hash(prompts.partial.SCHEMA)
    payload_req = [{"role": str(m.role), "text": m.text} for m in req.messages] if cfg.store_payloads else None
    if was_cancelled or err is not None or comp is None:
        message = "cancelado" if was_cancelled else redact.GLOBAL.redact(str(err) if err is not None else "sem resposta")
        call_id = store.llm_calls.insert(LlmCallInsert(call_mid, None, str(LlmTask.PARTIAL), cfg.provider_id, cfg.fast_model, version,
                                                       digest, 0, 0, 0, 0, None, 0, LlmCallStatus.ERROR, message, "direct", payload_req,
                                                       None), cfg.store_payloads)
        if was_cancelled:
            return PartialResult(Outcome.CANCELLED, meeting_id, call_id=call_id)
        log.warning("reunião %s: resumo parcial falhou: %s", meeting_id, message)
        return PartialResult(Outcome.ERROR if alive else Outcome.GONE, meeting_id, call_id=call_id, spent_usd=spent, limit_usd=limit,
                             error=message)
    u = comp.usage
    cost = cost_usd(u, price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, comp.model))
    resp = {"text": comp.text, "data": comp.data, "finish": comp.finish} if cfg.store_payloads else None
    call_id = store.llm_calls.insert(LlmCallInsert(call_mid, None, str(LlmTask.PARTIAL), cfg.provider_id, comp.model, version, digest,
                                                u.input_tokens, u.output_tokens, u.cached_tokens, u.audio_tokens, cost, comp.latency_ms,
                                                LlmCallStatus.OK, None, "direct", payload_req, resp), cfg.store_payloads)
    if not alive:
        log.info("reunião %s apagada durante o resumo parcial: nada gravado além da chamada", meeting_id)
        return PartialResult(Outcome.GONE, meeting_id, call_id=call_id, cost_usd=cost)
    times = {int(r["id"]): int(r["t_start_ms"] or 0) for r in used}
    for kind in ("decisions", "actions"):                  # itens anteriores mantidos pela LLM guardam o tempo de antes
        for it in previous.get(kind) or ():
            if isinstance(it, dict) and isinstance(it.get("caption_id"), int) and isinstance(it.get("t_ms"), int):
                times.setdefault(int(it["caption_id"]), int(it["t_ms"]))
    raw = comp.data or {}
    cited = {int(it["caption_id"]) for kind in ("decisions", "actions") for it in raw.get(kind) or ()
             if isinstance(it, Mapping) and isinstance(it.get("caption_id"), int)}
    times.update(store.captions.start_times(meeting_id, cited - times.keys()))
    data = normalize(raw, times)
    prev_until = int((store.summaries.current(meeting_id, SummaryKind.PARTIAL) or {}).get("t_until_ms") or 0)
    t_until = max(prev_until, *(int(r["t_end_ms"] or 0) for r in used))    # uma legenda atrasada não faz o "até" recuar
    data.update(last_caption_id=max(int(r["id"]) for r in used), captions=len(used), left=left,
                t_from_ms=min(int(r["t_start_ms"] or 0) for r in used),
                interval_min=int(store.get(SPECS["live.summary_interval_min"])))   # o `--check` conta pelo que valia
    try:
        summary_id = store.summaries.insert(SummaryInsert(meeting_id, SummaryKind.PARTIAL, json.dumps(data, ensure_ascii=False),
                                                          render_markdown(data, ctx.output_language), cfg.provider_id, comp.model, version,
                                                          None, call_id, ctx.output_language, t_until_ms=t_until))
    except sqlite3.IntegrityError:                         # apagada entre a conferência e a gravação
        return PartialResult(Outcome.GONE, meeting_id, call_id=call_id, cost_usd=cost)
    log.info("reunião %s: resumo parcial até %s (%d legenda(s), %d para a próxima, %s)", meeting_id, stamp(t_until), len(used), left,
             fmt_usd(cost))
    return PartialResult(Outcome.DONE, meeting_id, summary_id=summary_id, call_id=call_id, t_until_ms=t_until, captions=len(used),
                         left=left, cost_usd=cost, spent_usd=spent, limit_usd=limit, last_caption_id=int(data["last_caption_id"]))
