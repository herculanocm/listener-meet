"""Chat sobre todas as reuniões (F5 item 11, Q3, Q11, U-027), no padrão do chat de uma reunião (`llm/chat_task.py`): a
pergunta (com a anterior do usuário na mesma conversa, para o seguimento) vai à busca híbrida com os filtros da tela; até
`rag.top_k` trechos, no máximo `rag.max_per_meeting` por reunião (o teto vale dentro de cada lista da busca, antes do limite,
R6-01), cada reunião com um apelido (R1, R2, na ordem de relevância); prompt `rag_chat` no modelo rápido; resposta em
streaming com cancelamento; as citações `[R1 12:03]` viram links para a reunião e a linha enviada mais próxima
(`rag/citations.py`). As falas enviadas são os segmentos vigentes que o trecho cobre AGORA, escolhidos pelo intervalo de tempo
do trecho (o reprocessamento troca os ids, F5A-06), lidos só nessas janelas (R6-02).

A conversa nasce na primeira pergunta (título = a pergunta cortada); pergunta e resposta vão para `chat_messages` com
`meeting_id` nulo, `thread_id`, `citations_json` e o custo (a chamada do chat mais o vetor da consulta); `llm_calls` com
`task = rag_chat`, reunião e job nulos e o erro redigido. `thread_html` desenha a conversa a partir das citações gravadas
(sem recasar segmentos) e `rate_answer` guarda o "citação certa?" (U-027). Tudo síncrono por `run_async`: a tela chama numa
`StreamTask` (`run_stream(partial(run_global_chat, store, thread_id, pergunta, filtros), ...)`)."""
from __future__ import annotations

import html
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from ..llm import chat_task, prompts, redact
from ..llm.aio import run_async
from ..llm.base import classify_exception
from ..llm.costs import cost_usd, price_for
from ..llm.jobconfig import LlmJobConfig
from ..llm.prompts.rag_chat import ExcerptLine, MeetingBlock, RagChatCtx
from ..llm.types import TASK_REASONING, LlmTask, Request
from ..log import get_logger
from ..model import ChatRole, ChunkKind, LlmCallStatus
from ..pipeline import review
from ..store.llm_calls import LlmCallInsert
from ..store.rag import SearchFilters
from . import citations
from .citations import SentLine, Target
from .search import SearchHit, embed_query, search

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow
    from ..store import Store

log = get_logger("rag.chat")

HISTORY_TURNS = 8                # mensagens da conversa que voltam ao prompt (como o chat da F3)
TITLE_MAX = 80
MAX_OUTPUT_TOKENS = 2048
RATE_SCHEME = "listener"


@dataclass(frozen=True, slots=True)
class GlobalChatAnswer:
    text: str
    html: str                            # escapado, citações como links `listener://open?...`
    citations: tuple[dict[str, Any], ...]  # [{alias, meeting_id, t_ms, segment_id, cited_ms}], o que `chat_messages` guardou
    cost_usd: float | None               # a chamada do chat mais o vetor da consulta; None sem preço cadastrado
    thread_id: int | None
    message_id: int | None               # a resposta em `chat_messages` (avaliação U-027)
    question_id: int | None
    call_id: int | None
    error: str | None = None
    cancelled: bool = False
    hybrid: bool = False                 # False = a busca foi só por palavras
    search_reason: str = ""
    aliases: dict[str, int] = field(default_factory=dict)   # "R1" -> reunião
    excerpts: int = 0                    # trechos enviados

    @property
    def linked(self) -> int:
        """Citações com link (reunião ou ponto)."""
        return sum(1 for c in self.citations
                   if c.get("meeting_id") is not None and (c.get("t_ms") is not None or c.get("cited_ms") is None))


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def thread_title(question: str) -> str:
    """Título da conversa: a primeira pergunta cortada em `TITLE_MAX` caracteres, em fim de palavra."""
    text = " ".join(question.split())
    if len(text) <= TITLE_MAX:
        return text
    cut = text.rfind(" ", 0, TITLE_MAX)
    return (text[:cut] if cut > TITLE_MAX // 2 else text[:TITLE_MAX - 1]).rstrip(" ,;:") + "…"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _speaker(row: TranscriptRow) -> str:
    return f"{row.name} — {row.role}" if row.role else row.name


def _windows(hits: Sequence[SearchHit]) -> list[tuple[int, int]]:
    """O intervalo de tempo de cada trecho da transcrição (fim pelo menos 1 ms depois do começo)."""
    return [(int(h.t_start_ms), max(int(h.t_end_ms if h.t_end_ms is not None else h.t_start_ms), int(h.t_start_ms) + 1))
            for h in hits if h.kind == ChunkKind.TRANSCRIPT and h.t_start_ms is not None]


def load_citations(raw: str | None) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def _meetings(store: Store, ids: Iterable[int]) -> dict[int, tuple[str | None, str | None]]:
    out: dict[int, tuple[str | None, str | None]] = {}
    for mid in sorted(set(ids)):
        m = store.get_meeting(mid)
        if m is not None:
            out[mid] = (m.get("title") or m.get("title_llm"), str(m.get("started_at") or "")[:10])
    return out


# ---------------------------------------------------------------- trechos enviados
def build_excerpts(store: Store, hits: Sequence[SearchHit]) -> tuple[list[MeetingBlock], dict[str, Target]]:
    """Os trechos escolhidos como blocos por reunião (apelido na ordem do melhor trecho de cada uma) e, por apelido, a reunião
    e as linhas enviadas com tempo (o que uma citação pode ligar). Falas: os segmentos vigentes que cruzam o intervalo do
    trecho, lidos só nessas janelas (sem `words_json`; R6-02: ler a reunião inteira custava ~55 ms por reunião de 2 h), sem
    repetir, em ordem de tempo; sem nenhum (reprocessada e ainda não reindexada), o texto do trecho. Ata, ação, executivo e
    marca da reunião: uma linha com o tipo, o tempo quando há e o texto. Participantes: quem fala na reunião inteira."""
    by_meeting: dict[int, list[SearchHit]] = {}
    for h in hits:
        by_meeting.setdefault(h.meeting_id, []).append(h)
    blocks: list[MeetingBlock] = []
    targets: dict[str, Target] = {}
    for mid, mhits in by_meeting.items():
        meeting = store.get_meeting(mid)
        if meeting is None:                                  # apagada entre a busca e o prompt
            continue
        alias = f"R{len(blocks) + 1}"
        windows = _windows(mhits)
        rows = review.transcript_rows_between(store, mid, windows, prefer_llm=True) if windows else []
        lines, sent = _lines(mhits, rows)
        title = str(meeting.get("title") or meeting.get("title_llm") or "")
        date = str(meeting.get("started_at") or "")[:10]
        people = ", ".join(review.participants(store, mid))
        blocks.append(MeetingBlock(alias, mid, title, date, people or _clean(str(meeting.get("expected_participants") or "")), lines))
        targets[alias] = Target(mid, title, date, sent)
    return blocks, targets


def _lines(hits: Sequence[SearchHit], rows: Sequence[TranscriptRow]) -> tuple[tuple[ExcerptLine, ...], tuple[SentLine, ...]]:
    speech: dict[int, TranscriptRow] = {}
    other: list[ExcerptLine] = []
    for h in hits:
        if h.kind == ChunkKind.TRANSCRIPT and h.t_start_ms is not None:
            t0 = int(h.t_start_ms)
            t1 = max(int(h.t_end_ms if h.t_end_ms is not None else t0), t0 + 1)
            cover = [r for r in rows if r.t_start_ms < t1 and r.t_end_ms > t0]
            if cover:
                speech.update((r.segment_id, r) for r in cover)
                continue
        other.append(ExcerptLine(h.t_start_ms, _clean(h.text), kind=h.kind))
    ordered = sorted(speech.values(), key=lambda r: (r.t_start_ms, r.segment_id))
    lines = (*(ExcerptLine(r.t_start_ms, _clean(r.shown_text), _speaker(r), r.segment_id) for r in ordered), *other)
    sent = (*(SentLine(r.t_start_ms, r.segment_id) for r in ordered), *(SentLine(int(ln.t_ms)) for ln in other if ln.t_ms is not None))
    return lines, sent


def _history(store: Store, rows: Sequence[Any]) -> list[tuple[str, str]]:
    """As últimas mensagens da conversa; as respostas com as citações legíveis entre parênteses (título e data da reunião) e
    as não resolvidas sem o apelido, porque os apelidos de uma pergunta não valem na seguinte (R6-05)."""
    recent = list(rows)[-HISTORY_TURNS:]
    saved = {int(r["id"]): load_citations(r.get("citations_json")) for r in recent if str(r["role"]) == ChatRole.ASSISTANT}
    meetings = _meetings(store, (mid for cites in saved.values() for mid in citations.saved_meeting_ids(cites)))
    out: list[tuple[str, str]] = []
    for r in recent:
        text = str(r["text"])
        if int(r["id"]) in saved:
            text = citations.history_text(text, saved[int(r["id"])], meetings)
        out.append((str(r["role"]), text))
    return out


def _language(store: Store, cfg: LlmJobConfig, blocks: Sequence[MeetingBlock]) -> str:
    """`llm.output_language`; `auto` = o idioma da reunião mais relevante (R1), senão pt-BR."""
    from ..pipeline.llm_steps import resolve_language

    if cfg.output_language != "auto":
        return cfg.output_language
    top = store.get_meeting(blocks[0].meeting_id) if blocks else None
    return resolve_language("auto", (top or {}).get("language"))


# ---------------------------------------------------------------- pergunta
def run_global_chat(store: Store, thread_id: int | None, question: str, filters: SearchFilters | None = None,
                    emit: Callable[[str], None] | None = None, cancelled: Callable[[], bool] | None = None) -> GlobalChatAnswer:
    """Pergunta -> resposta sobre todas as reuniões (em streaming quando `emit` é dado). `cancelled()` vale em todo o caminho
    (R7-03): no vetor da consulta e na espera da LLM, também antes do primeiro pedaço, a cada `POLL_S`; antes de chamar a
    LLM não grava nada; antes do primeiro pedaço, a pergunta fica e nenhuma resposta vazia é gravada; no meio, a parte que
    chegou fica. `thread_id` None cria a conversa na primeira pergunta. Sem provedor de LLM com chave, `ValueError` com o
    caminho nas Configurações; sem trecho relevante, a LLM ainda responde (diz que não achou)."""
    from ..config.defaults import SPECS

    question = (question or "").strip()
    if not question:
        raise ValueError("escreva uma pergunta")
    if store.providers.active_with_key("llm") is None:
        raise ValueError("nenhum provedor de LLM ativo com chave: configure um nas Configurações (LLM)")
    if thread_id is not None and store.chat.get_thread(thread_id) is None:
        raise ValueError("esta conversa não existe mais: comece uma nova")
    cfg = LlmJobConfig.from_store(store)
    redact.GLOBAL.add(store.providers.key(cfg.provider_id))   # CP10: a chave some do erro gravado e do log, como no parcial
    t0 = time.perf_counter()
    previous = store.chat.list_for_thread(thread_id) if thread_id is not None else []
    last = next((str(m["text"]) for m in reversed(previous) if str(m["role"]) == ChatRole.USER), "")
    query = f"{last}\n{question}" if last else question               # seguimento: "e o prazo?" depois de "o que decidimos..."
    qv = embed_query(store, query, should_stop=cancelled)
    found = search(store, query, filters, query_vector=qv, k=int(store.get(SPECS["rag.top_k"])),
                   per_meeting=int(store.get(SPECS["rag.max_per_meeting"])), snippets=False)
    picked = list(found.hits)
    blocks, targets = build_excerpts(store, picked)
    aliases = {b.alias: b.meeting_id for b in blocks}
    search_ms = _ms(t0)
    if cancelled is not None and cancelled():
        return GlobalChatAnswer("", "", (), None, thread_id, None, None, None, None, True, found.hybrid, found.reason, aliases, len(picked))
    ctx = RagChatCtx(blocks, _history(store, previous), question, _language(store, cfg, blocks))
    req = Request(LlmTask.RAG_CHAT, cfg.fast_model, prompts.rag_chat.build(ctx), None, max_output_tokens=MAX_OUTPUT_TOKENS,
                  reasoning=TASK_REASONING[LlmTask.RAG_CHAT], timeout_s=120.0)
    with store.transaction():
        if thread_id is None:
            thread_id = store.chat.create_thread(thread_title(question))
        question_id = store.chat.add(None, ChatRole.USER, question, thread_id=thread_id)
    t1 = time.perf_counter()
    try:
        comp, text, err = run_async(chat_task.ask(store, cfg.provider_id, req, emit, cancelled))
    except Exception as e:  # SDK ausente, chave apagada no meio: a pergunta fica, com o erro na tela e em `llm_calls`
        comp, text, err = None, "", classify_exception(e)
    stopped = comp is None and cancelled is not None and cancelled()     # parou no meio do streaming (a parte que chegou fica)
    version = prompts.prompt_version(LlmTask.RAG_CHAT)
    task = str(LlmTask.RAG_CHAT)
    common = (found.hybrid, found.reason, aliases, len(picked))
    if err is not None:
        message = redact.GLOBAL.redact(str(err))
        call_id = store.llm_calls.insert(LlmCallInsert(None, None, task, cfg.provider_id, cfg.fast_model, version, None, 0, 0, 0, 0, None,
                                                       int(_ms(t1)), LlmCallStatus.ERROR, message, "direct"), store_payloads=False)
        log.warning("chat global: %s", message)
        return GlobalChatAnswer(text, html.escape(text).replace("\n", "<br>"), (), None, thread_id, None, question_id, call_id, message,
                                stopped, *common)
    if stopped and not text:                     # cancelada antes do primeiro pedaço (R7-03): sem resposta vazia na conversa
        call_id = store.llm_calls.insert(LlmCallInsert(None, None, task, cfg.provider_id, cfg.fast_model, version, None, 0, 0, 0, 0, None,
                                                       int(_ms(t1)), LlmCallStatus.ERROR, chat_task.CANCELLED,
                                                       "stream" if emit else "direct"), store_payloads=False)
        log.info("chat global: pergunta cancelada antes da resposta, em %.1f ms", _ms(t1))
        return GlobalChatAnswer("", "", (), None, thread_id, None, question_id, call_id, None, True, *common)
    text = text or (comp.text if comp else "")
    model = comp.model if comp else cfg.fast_model
    usage = comp.usage if comp else None
    prices = store.providers.list_prices(cfg.provider_kind or None)
    cost = cost_usd(usage, price_for(prices, cfg.provider_kind, model)) if usage else None
    payload_req = [{"role": str(m.role), "text": m.text} for m in req.messages] if cfg.store_payloads else None
    call_id = store.llm_calls.insert(
        LlmCallInsert(None, None, task, cfg.provider_id, model, version, None, usage.input_tokens if usage else 0,
                      usage.output_tokens if usage else 0, usage.cached_tokens if usage else 0, 0, cost,
                      comp.latency_ms if comp else int(_ms(t1)), LlmCallStatus.OK, None, "stream" if emit else "direct", payload_req,
                      {"text": text} if cfg.store_payloads else None), store_payloads=cfg.store_payloads)
    rendered, cites = citations.rewrite(text, targets)
    total = None if cost is None else round(cost + (qv.cost_usd or 0.0), 6)
    message_id = store.chat.add(None, ChatRole.ASSISTANT, text, cfg.provider_id, model, cites, total, thread_id=thread_id)
    log.info("chat global: %d trecho(s) de %d reunião(ões), %s, em %.1f ms; resposta em %.1f ms%s; %d citação(ões)", len(picked),
             len(blocks), "busca híbrida" if found.hybrid else f"busca por palavras ({found.reason})", search_ms, _ms(t1),
             " (interrompida)" if stopped else "", len(cites))
    return GlobalChatAnswer(text, rendered, tuple(cites), total, thread_id, message_id, question_id, call_id, None, stopped, *common)


# ---------------------------------------------------------------- conversa e avaliação
def rate_link(message_id: int, rating: int | None) -> str:
    return f"{RATE_SCHEME}://rate?msg={int(message_id)}&v={'' if rating is None else int(rating)}"


def parse_rate_link(url: str) -> tuple[int, int | None] | None:
    """`listener://rate?msg=<id>&v=1|0|` -> (mensagem, avaliação; vazio desfaz); None para outro link."""
    parts = urlsplit(url.replace("&amp;", "&"))
    if parts.scheme != RATE_SCHEME or parts.netloc != "rate":
        return None
    qs = parse_qs(parts.query, keep_blank_values=True)
    try:
        msg = int(qs["msg"][0])
        raw = qs.get("v", [""])[0]
        value = int(raw) if raw != "" else None
    except (KeyError, IndexError, ValueError):
        return None
    return (msg, value) if value in (None, 0, 1) else None


def _rating_html(message_id: int, rating: Any) -> str:
    def a(label: str, value: int | None) -> str:
        return f'<a href="{html.escape(rate_link(message_id, value))}">{label}</a>'

    if rating is None:
        return f'<br><span style="color:#888">Citação certa? {a("sim", 1)} · {a("não", 0)}</span>'
    return f'<br><span style="color:#888">Citação certa: {"sim" if int(rating) == 1 else "não"} · {a("desfazer", None)}</span>'


def thread_html(store: Store, thread_id: int, *, rating: bool = True) -> str:
    """A conversa como HTML: perguntas escapadas; respostas redesenhadas das citações gravadas (título e data atuais da
    reunião, sem recasar segmentos; reunião apagada fica como texto) e, com `rating`, o "citação certa? sim · não" (U-027)
    como links `listener://rate?msg=<id>&v=1|0` (`parse_rate_link`, `rate_answer`)."""
    rows = store.chat.list_for_thread(thread_id)
    saved = {int(r["id"]): load_citations(r.get("citations_json")) for r in rows if str(r["role"]) == ChatRole.ASSISTANT}
    meetings = _meetings(store, (mid for cites in saved.values() for mid in citations.saved_meeting_ids(cites)))
    out: list[str] = []
    for r in rows:
        if int(r["id"]) not in saved:
            out.append(f"<p><b>Você:</b> {html.escape(str(r['text'])).replace(chr(10), '<br>')}</p>")
            continue
        body = citations.render_saved(str(r["text"]), saved[int(r["id"])], meetings)
        out.append(f"<p><b>LLM:</b> {body}{_rating_html(int(r['id']), r.get('rating')) if rating else ''}</p>")
    return "".join(out)


def rate_answer(store: Store, message_id: int, rating: int | None) -> bool:
    """U-027 "citação certa?": 1 sim, 0 não, None desfaz; só em respostas do chat global (False para outra mensagem)."""
    row = store.chat.get(message_id)
    if row is None or str(row["role"]) != ChatRole.ASSISTANT or row.get("thread_id") is None:
        return False
    return store.chat.set_rating(message_id, rating)


__all__ = ["GlobalChatAnswer", "build_excerpts", "load_citations", "parse_rate_link", "rate_answer", "rate_link", "run_global_chat",
           "thread_html", "thread_title"]
