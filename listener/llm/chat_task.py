"""Chat sobre uma reunião (F3 item 10, U-019): a transcrição vai como prefixo estável do prompt (cache dos provedores),
depois o histórico curto e a pergunta; resposta em streaming (`StreamTask`); cada `[mm:ss]` da resposta é reescrito para o
segmento mais próximo (até 5 s) como link `listener://play?seg=<id>` ou fica como texto quando não há segmento; tudo em
`chat_messages` (com `citations_json` e custo) e `llm_calls`. Roda fora da thread do Qt com `run_async` e cliente próprio."""
from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model import ChatRole, LlmCallStatus
from ..store.llm_calls import LlmCallInsert
from . import prompts, redact
from .aio import run_async
from .base import LlmError, classify_exception
from .costs import cost_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.common import Line
from .registry import build_provider
from .types import TASK_REASONING, Completion, LlmTask, Request

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("llm.chat")

STAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
MAX_DISTANCE_MS = 5000
HISTORY_TURNS = 8


@dataclass(frozen=True, slots=True)
class Citation:
    t_ms: int
    segment_id: int | None


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    text: str
    html: str
    citations: tuple[Citation, ...]
    cost_usd: float | None
    call_id: int | None
    error: str | None = None


def nearest_segment(t_ms: int, lines: Sequence[Line], max_distance_ms: int = MAX_DISTANCE_MS) -> int | None:
    """O segmento mais próximo do tempo citado, até 5 s (a mesma regra do chat global, `rag.citations.nearest`)."""
    from ..rag.citations import SentLine, nearest

    best = nearest(t_ms, [SentLine(ln.t_start_ms, ln.segment_id) for ln in lines], max_distance_ms)
    return best.segment_id if best is not None else None


def rewrite_citations(text: str, lines: Sequence[Line]) -> tuple[str, tuple[Citation, ...]]:
    """`[12:03]` -> `<a href="listener://play?seg=143">[12:03]</a>` quando há segmento até 5 s; senão fica o texto. Devolve o
    HTML (texto escapado) e as citações em ordem."""
    citations: list[Citation] = []
    parts: list[str] = []
    pos = 0
    for m in STAMP_RE.finditer(text):
        t_ms = (int(m.group(1)) * 60 + int(m.group(2))) * 1000
        seg = nearest_segment(t_ms, lines)
        citations.append(Citation(t_ms, seg))
        parts.append(html.escape(text[pos:m.start()]))
        stamp = html.escape(m.group(0))
        parts.append(f'<a href="listener://play?seg={seg}">{stamp}</a>' if seg is not None else stamp)
        pos = m.end()
    parts.append(html.escape(text[pos:]))
    return "".join(parts).replace("\n", "<br>"), tuple(citations)


async def ask(store: Store, provider_id: str, req: Request, emit: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> tuple[Completion | None, str, LlmError | None]:
    """Uma pergunta ao provedor (o chat de uma reunião e o chat global da F5): em streaming quando `emit` é dado, parando no
    próximo pedaço quando `cancelled()` (o `Completion` fica nulo e o texto é o que chegou); erro do provedor classificado e
    devolvido, nunca levantado. Provedor sem SDK ou sem chave levanta antes de chamar."""
    provider, _row = build_provider(store, provider_id)
    collected: list[str] = []
    try:
        if emit is None:
            comp = await provider.complete(req)
            return comp, comp.text, None
        stream = provider.stream(req)
        async for piece in stream:
            collected.append(piece)
            emit(piece)
            if cancelled is not None and cancelled():
                break
        return stream.result, "".join(collected), None
    except Exception as e:
        return None, "".join(collected), classify_exception(e)
    finally:
        await provider.aclose()


def run_chat(store: Store, meeting_id: int, question: str, emit: Callable[[str], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> ChatAnswer:
    """Pergunta -> resposta (em streaming quando `emit` é dado); grava pergunta e resposta em `chat_messages`."""
    from ..pipeline.llm_steps import build_context

    question = question.strip()
    if not question:
        raise ValueError("escreva uma pergunta")
    parent = store.segments.current_job_id(meeting_id)
    if parent is None:
        raise ValueError("a reunião ainda não foi processada")
    if store.providers.active_with_key("llm") is None:
        raise ValueError("nenhum provedor de LLM ativo com chave: configure um nas Configurações")
    cfg = LlmJobConfig.from_store(store)
    ctx = build_context(store, meeting_id, parent, cfg.output_language)
    history = [(str(m["role"]), str(m["text"])) for m in store.chat.list_for_meeting(meeting_id, HISTORY_TURNS)]
    chat_ctx = prompts.chat.ChatCtx(ctx.meeting_ctx, ctx.lines, history, question)
    req = Request(LlmTask.CHAT, cfg.fast_model, prompts.chat.build(chat_ctx), None, max_output_tokens=2048,
                  reasoning=TASK_REASONING[LlmTask.CHAT], timeout_s=120.0, cache_prefix=True)
    store.chat.add(meeting_id, ChatRole.USER, question)
    comp, text, err = run_async(ask(store, cfg.provider_id, req, emit, cancelled))
    version = prompts.prompt_version(LlmTask.CHAT)
    task = str(LlmTask.CHAT)
    if err is not None:
        message = redact.GLOBAL.redact(str(err))
        row = LlmCallInsert(meeting_id, parent, task, cfg.provider_id, cfg.fast_model, version, None, 0, 0, 0, 0, None, 0,
                            LlmCallStatus.ERROR, message, "direct")
        call_id = store.llm_calls.insert(row, store_payloads=False)
        log.warning("chat: %s", message)
        return ChatAnswer(text, html.escape(text), (), None, call_id, message)
    text = text or (comp.text if comp else "")
    model = comp.model if comp else cfg.fast_model
    usage = comp.usage if comp else None
    prices = store.providers.list_prices(cfg.provider_kind or None)
    cost = cost_usd(usage, price_for(prices, cfg.provider_kind, model)) if usage else None
    payload_req = [{"role": str(m.role), "text": m.text} for m in req.messages] if cfg.store_payloads else None
    row = LlmCallInsert(meeting_id, parent, task, cfg.provider_id, model, version, None, usage.input_tokens if usage else 0,
                        usage.output_tokens if usage else 0, usage.cached_tokens if usage else 0, 0, cost, comp.latency_ms if comp else 0,
                        LlmCallStatus.OK, None, "stream" if emit else "direct", payload_req, {"text": text} if cfg.store_payloads else None)
    call_id = store.llm_calls.insert(row, store_payloads=cfg.store_payloads)
    rendered, citations = rewrite_citations(text, ctx.lines)
    cites = [{"t_ms": c.t_ms, "segment_id": c.segment_id} for c in citations]
    store.chat.add(meeting_id, ChatRole.ASSISTANT, text, cfg.provider_id, model, cites, cost)
    return ChatAnswer(text, rendered, citations, cost, call_id)


def history_html(store: Store, meeting_id: int, lines: Sequence[Line] | None = None) -> str:
    """Conversa guardada como HTML (citações reescritas de novo quando há linhas; senão como texto)."""
    out: list[str] = []
    for m in store.chat.list_for_meeting(meeting_id):
        text = str(m["text"])
        body = rewrite_citations(text, lines)[0] if lines else html.escape(text).replace("\n", "<br>")
        who = "Você" if str(m["role"]) == ChatRole.USER else "LLM"
        out.append(f"<p><b>{who}:</b> {body}</p>")
    return "".join(out)


def transcript_lines(store: Store, meeting_id: int) -> tuple[Line, ...]:
    from ..pipeline.llm_steps import build_context

    parent = store.segments.current_job_id(meeting_id)
    if parent is None:
        return ()
    return build_context(store, meeting_id, parent, "pt-BR").lines


def citation_segment(link: str) -> int | None:
    m = re.search(r"seg=(\d+)", link)
    return int(m.group(1)) if m else None


def play_range(store: Store, segment_id: int) -> tuple[str, int, int] | None:
    """(canal, t0, t1) do segmento citado, a partir da primeira palavra menos 150 ms (R-14)."""
    from ..pipeline.review import PLAY_LEAD_MS

    rows = store._q("SELECT channel, t_start_ms, t_end_ms, words_json FROM segments WHERE id = ?", (segment_id,))
    if not rows:
        return None
    r: Any = rows[0]
    first = int(r["t_start_ms"])
    try:
        import json

        words = json.loads(r["words_json"] or "[]")
        if words:
            first = int(words[0][0])
    except (ValueError, TypeError, IndexError):
        pass
    t0 = max(0, first - PLAY_LEAD_MS)
    return str(r["channel"] or "R"), t0, max(int(r["t_end_ms"]), t0 + 500)
