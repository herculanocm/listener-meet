"""Busca híbrida (F5 itens 2 a 4, F5A-20, F5A-28): a lista por palavra (FTS5, `rank` bm25 com pesos; E implícito e, com
menos de 10 resultados, OU) e a lista por sentido (assinatura binária do modelo ativo, candidatos por Hamming e reordenação
pelo cosseno exato dos float32), fundidas por RRF (k = 60), com os filtros (pessoa, período, reunião) aplicados nas duas
antes do top-k (no vetor, uma máscara antes dos candidatos). Cada lista, e a fusão, corta por reunião antes do limite
(`per_meeting`, R6-01): uma reunião com muitos trechos (ou com a palavra no título, que entra em todos com peso 3) não ocupa
as 50 linhas; o Histórico usa `HISTORY_PER_MEETING` e o chat, `rag.max_per_meeting`. Sem vetor da consulta (sem chave, sem
rede, índice sem vetores) ou sem vetores do modelo ativo nos trechos filtrados, só a lista por palavra, com o selo "busca por
palavras" e o motivo. As linhas voltam prontas para a tela: reunião, título, data, tipo, tempo, falantes, trecho destacado
(HTML seguro), posições nas duas listas e a pontuação; `group_by_meeting` agrupa para o Histórico (U-024). Tempos de cada
parte no log.

O vetor da consulta (`embed_query`) vem do embedder ativo (`rag.index.embedder_status`) com `task = query`, normalizado, numa
linha de `llm_calls` com reunião nula e `task = rag_embed` (custo 0 no local da F5.1, calculado nesta máquina, com o tempo no
log), e passa por um cache LRU por (modelo, texto) no processo. É
síncrono (`run_async`, até `QUERY_TIMEOUT_S`): a tela chama numa `Task` (no Enter ou 600 ms depois de parar de digitar) e
entrega o resultado a `search`, que só lê o banco e também roda numa `Task`; `should_stop` (a saída do app, o chat
cancelado) cancela a chamada presa na rede em até `POLL_S` (R7-03). As assinaturas ficam num `SignatureCache` por banco e
modelo, no processo do app (`signature_cache`)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np

from ..log import get_logger
from ..model import ChunkKind, LlmCallStatus
from ..store.llm_calls import LlmCallInsert
from ..store.rag import FtsHit, SearchFilters, VectorVersion
from . import fts, index, vectors

if TYPE_CHECKING:
    from ..llm.base import LLMProvider
    from ..llm.types import Embeddings
    from ..store import Store

log = get_logger("rag.search")

RRF_K = 60
LIST_K = 50                      # cada lista (palavra e sentido) até aqui antes da fusão (o L0 mediu o FTS com LIMIT 50)
DEFAULT_K = 50                   # linhas fundidas devolvidas (o Histórico)
HISTORY_PER_MEETING = 5          # teto de trechos por reunião no Histórico (o chat usa `rag.max_per_meeting`, R6-01)
MIN_AND_RESULTS = 10             # com menos que isto, a busca por palavra refaz com OU
SNIPPET_TOKENS = 16
LOAD_BATCH = 20_000              # assinaturas por consulta ao carregar o cache (200 mil x 768 = 19,2 MB no fim)
MAX_CACHES = 4
QUERY_CACHE_SIZE = 256
QUERY_TIMEOUT_S = 15.0           # a busca não espera mais que isto pelo vetor da consulta: cai para "por palavras"
QUERY_ATTEMPTS = 2
QUERY_DELAYS = (0.5,)
POLL_S = 0.2                     # `should_stop` conferido a cada 0,2 s enquanto espera a rede (R7-03)
CANCELLED = "cancelado"
NO_QUERY_VECTOR = "sem o vetor da consulta"
NO_VECTORS = "índice ainda sem vetores do modelo {model}"
FILTERED_NO_VECTORS = "trechos filtrados sem vetor do modelo (índice incompleto)"

KIND_LABELS: dict[str, str] = {
    ChunkKind.TRANSCRIPT: "transcrição", ChunkKind.DECISION: "decisão", ChunkKind.HIGHLIGHT: "destaque",
    ChunkKind.PENDING: "pendência", ChunkKind.NEXT_STEP: "próximo passo", ChunkKind.MINUTES: "ata", ChunkKind.ACTION: "ação",
    ChunkKind.EXECUTIVE: "resumo executivo", ChunkKind.MEETING: "reunião",
}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


# ---------------------------------------------------------------- resultado
@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: int
    meeting_id: int
    meeting_title: str           # o título atual da reunião (o do usuário, senão o da LLM)
    meeting_date: str            # AAAA-MM-DD
    kind: str                    # `ChunkKind`
    t_start_ms: int | None       # None = sem tempo (executivo, marca da reunião, item da ata sem carimbo)
    t_end_ms: int | None
    speakers: str
    person_ids: tuple[int, ...]
    text: str
    snippet_html: str            # escapado, termos da busca em <b>; vazio com `snippets=False`
    fts_rank: int | None         # posição na lista por palavra (1 = primeiro)
    vec_rank: int | None         # posição na lista por sentido
    score: float                 # RRF
    cosine: float | None = None
    seg_first: int | None = None  # só dica: a navegação usa o tempo (o reprocessamento troca os segmentos)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    hits: tuple[SearchHit, ...]
    hybrid: bool                 # False = "busca por palavras"
    reason: str                  # por que só por palavras (vazio na híbrida)
    fts_query: str               # a expressão FTS5 usada ("" = consulta vazia)
    fts_or: bool                 # refeita com OU (menos de 10 com E)
    missing_vectors: int = 0     # trechos sem vetor do modelo ativo (índice incompleto); 0 sem modelo
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return "busca híbrida" if self.hybrid else "busca por palavras"


@dataclass(frozen=True, slots=True)
class MeetingHits:
    meeting_id: int
    title: str
    date: str
    hits: tuple[SearchHit, ...]


def group_by_meeting(hits: Sequence[SearchHit]) -> list[MeetingHits]:
    """Trechos agrupados por reunião (U-024), na ordem do melhor trecho de cada uma; dentro, na ordem da busca."""
    groups: dict[int, list[SearchHit]] = {}
    for h in hits:
        groups.setdefault(h.meeting_id, []).append(h)
    return [MeetingHits(mid, hs[0].meeting_title, hs[0].meeting_date, tuple(hs)) for mid, hs in groups.items()]


def limit_per_meeting(hits: Sequence[SearchHit], per_meeting: int, limit: int) -> list[SearchHit]:
    """Os `limit` primeiros com no máximo `per_meeting` de cada reunião, de uma lista já pronta (o `search` já corta por reunião
    em cada lista antes do limite, `per_meeting`; isto é para recortar o resultado na tela)."""
    seen: Counter[int] = Counter()
    out: list[SearchHit] = []
    for h in hits:
        if len(out) >= limit:
            break
        if seen[h.meeting_id] >= per_meeting:
            continue
        seen[h.meeting_id] += 1
        out.append(h)
    return out


def rrf(lists: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float, tuple[int | None, ...]]]:
    """Fusão por posição (Reciprocal Rank Fusion): soma de 1 / (k + posição) em cada lista, posição 1 = primeiro. Devolve
    (id, pontuação, posição em cada lista) do melhor para o pior; empate: a melhor posição numa das listas, depois quem está
    na primeira lista (a por palavra: casamento exato), depois o menor id (determinístico)."""
    ranks: dict[int, list[int | None]] = {}
    for li, ids in enumerate(lists):
        for pos, item in enumerate(ids, start=1):
            r = ranks.setdefault(int(item), [None] * len(lists))
            if r[li] is None:
                r[li] = pos
    scored = [(item, sum(1.0 / (k + p) for p in r if p is not None), tuple(r)) for item, r in ranks.items()]
    scored.sort(key=lambda x: (-x[1], min(p for p in x[2] if p is not None), x[2][0] is None, x[0]))
    return scored


# ---------------------------------------------------------------- assinaturas na memória
class CacheView(NamedTuple):
    vids: np.ndarray             # ids de `rag_vectors` (int64), em ordem
    chunk_ids: np.ndarray        # trecho de cada um (int64)
    words: np.ndarray            # assinaturas como uint64 (n, palavras)
    mu: np.ndarray | None        # a média do modelo com que as assinaturas foram feitas


class SignatureCache:
    """Assinaturas de um modelo na memória do processo (F5A-20 item 3). A chave é `RagRepo.vector_version` (quantos, maior
    id e `mu_n`), relida a cada busca: o worker leve grava em outro processo. Carrega em lotes de `LOAD_BATCH`; depois, só o
    que entrou acima do maior id carregado (a v8 tem AUTOINCREMENT: o id não volta, R5-03), desde que a contagem feche;
    senão (algo saiu, ou o `mu` foi refeito e todas as assinaturas mudaram), recarga total.

    A sincronia roda numa leitura consistente (`Store.snapshot`): a versão, as assinaturas e o `mu` vêm do mesmo instante,
    mesmo que o leve refaça o `mu` (e regrave todas as assinaturas) no meio da carga em lotes.

    Concorrência: pensado para uma `Task` de busca por vez (a tela descarta o resultado velho pelo contador de geração);
    duas ao mesmo tempo também funcionam, porque `view` sincroniza sob um `Lock` e os vetores são trocados inteiros (quem já
    pegou a vista segue com a dele). A conexão é a da thread que chama (o `Store` abre uma por thread)."""

    def __init__(self, model: str, *, batch: int = LOAD_BATCH):
        self.model = model
        self.batch = max(1, batch)
        self.version: VectorVersion | None = None
        self.vids = np.zeros(0, np.int64)
        self.chunk_ids = np.zeros(0, np.int64)
        self.words = np.zeros((0, 0), np.uint64)
        self.mu: np.ndarray | None = None
        self.full_loads = 0                        # instrumentação (testes e log)
        self.appends = 0
        self._top = 0                              # maior id carregado
        self._lock = threading.Lock()

    def view(self, store: Store) -> CacheView:
        with self._lock, store.snapshot():            # a versão, as assinaturas e o `mu` do mesmo instante (R5-03): o leve
            self._sync(store)                         # pode refazer o `mu` e regravar todas as assinaturas no meio da carga
            return CacheView(self.vids, self.chunk_ids, self.words, self.mu)

    def _sync(self, store: Store) -> None:
        v = store.rag.vector_version(self.model)
        if v == self.version:
            return
        old = self.version
        if old is not None and v.mu_n == old.mu_n and self._top and v.size >= len(self.vids) and self._append(store, v.size):
            self.version = v
            return
        self._full(store)
        self.version = v                          # a versão lida antes: o que mudou durante a carga aparece na próxima

    def _read(self, store: Store, after: int) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        parts: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        while True:
            ids, chunks, sigs = store.rag.signatures(self.model, after, self.batch)
            if not len(ids):
                break
            parts.append((ids, chunks, vectors.as_words(sigs)))
            after = int(ids[-1])
            if len(ids) < self.batch:
                break
        return parts

    def _set(self, parts: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> None:
        if parts:
            self.vids = np.concatenate([p[0] for p in parts])
            self.chunk_ids = np.concatenate([p[1] for p in parts])
            self.words = np.ascontiguousarray(np.concatenate([p[2] for p in parts]))
        self._top = int(self.vids[-1]) if len(self.vids) else 0

    def _full(self, store: Store) -> None:
        t0 = time.perf_counter()
        self.vids, self.chunk_ids, self.words = np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 0), np.uint64)
        self._set(self._read(store, 0))
        row = store.rag.get_model(self.model)
        self.mu = row["mu"] if row is not None else None
        self.full_loads += 1
        log.info("assinaturas de %s carregadas: %d vetor(es) em %.1f ms", self.model, len(self.vids), _ms(t0))

    def _append(self, store: Store, count: int) -> bool:
        parts = self._read(store, self._top)
        if len(self.vids) + sum(len(p[0]) for p in parts) != count:
            return False                          # algo saiu: recarga total
        if parts:
            self._set([(self.vids, self.chunk_ids, self.words), *parts] if len(self.vids) else parts)
        self.appends += 1
        return True


_CACHES: OrderedDict[tuple[str, str], SignatureCache] = OrderedDict()
_CACHES_LOCK = threading.Lock()


def signature_cache(store: Store, model: str) -> SignatureCache:
    """O cache do banco e do modelo neste processo (os mais recentes ficam; trocar de modelo não acumula memória)."""
    key = (str(store.core.main_path), model)
    with _CACHES_LOCK:
        cache = _CACHES.pop(key, None) or SignatureCache(model)
        _CACHES[key] = cache
        while len(_CACHES) > MAX_CACHES:
            _CACHES.popitem(last=False)
    return cache


def clear_caches() -> None:
    with _CACHES_LOCK:
        _CACHES.clear()
    with _QUERY_LOCK:
        _QUERY_CACHE.clear()


# ---------------------------------------------------------------- vetor da consulta
@dataclass(frozen=True, slots=True, eq=False)
class QueryVector:
    vector: np.ndarray | None = field(default=None, repr=False)   # float32 normalizado; None = sem vetor
    model_key: str | None = None
    reason: str = ""             # por que não há vetor (sem chave, sem rede...)
    cached: bool = False
    cost_usd: float | None = None


_QUERY_CACHE: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()
_QUERY_LOCK = threading.Lock()


def embed_query(store: Store, text: str, *, provider: LLMProvider | None = None,
                should_stop: Callable[[], bool] | None = None) -> QueryVector:
    """O vetor da consulta pelo embedder ativo (`task = query`), normalizado, na dimensão do `model_key`; do cache LRU por
    (modelo, texto) quando já pedido neste processo (sem chamada nem custo). Sem embedder pronto ou com erro de rede ou do
    provedor, `vector` nulo com o motivo (a busca cai para "por palavras"); com o índice ainda sem nenhum vetor do modelo,
    também nulo e sem chamar a rede (a busca cairia em "por palavras" do mesmo jeito, depois de pagar e esperar, R6-06).
    `should_stop()` verdadeiro antes da chamada não chama a rede; durante, a chamada é cancelada em até `POLL_S` e o motivo
    é "cancelado" (R7-03). Uma linha em `llm_calls` por chamada, com reunião e job nulos, `task = rag_embed` e
    `strategy = query`. Síncrono: chamar fora da thread da interface."""
    from ..llm import redact
    from ..llm.aio import run_async
    from ..llm.costs import cost_usd, price_for

    text = " ".join((text or "").split())
    status = index.embedder_status(store)
    if not text:
        return QueryVector(None, status.model_key, "consulta vazia")
    if not status.ready or status.model_key is None:
        return QueryVector(None, status.model_key, status.reason)
    if not store.rag.vector_version(status.model_key).size:
        return QueryVector(None, status.model_key, NO_VECTORS.format(model=status.model_key))
    key = (status.model_key, text)
    with _QUERY_LOCK:
        hit = _QUERY_CACHE.get(key)
        if hit is not None:
            _QUERY_CACHE.move_to_end(key)
            return QueryVector(hit, status.model_key, "", cached=True, cost_usd=0.0)
    if should_stop is not None and should_stop():
        return QueryVector(None, status.model_key, CANCELLED)
    kind, model, dim = index.parse_model_key(status.model_key)
    pid = status.provider_id or kind                          # a linha de `providers` (R5-10); o preço é pelo tipo
    redact.GLOBAL.add(store.providers.key(pid))               # CP10: a chave nunca vai para o motivo nem para `llm_calls`
    t0 = time.perf_counter()
    try:
        emb = run_async(_embed(store, provider, pid, model, dim, text, should_stop))
        if emb is None:                                       # cancelada com a rede presa: a busca segue por palavras
            _record(store, pid, model, 0, None, _ms(t0), LlmCallStatus.ERROR, CANCELLED)
            log.info("vetor da consulta cancelado depois de %.0f ms", _ms(t0))
            return QueryVector(None, status.model_key, CANCELLED)
        vec = vectors.normalize(index._checked(emb.vectors, 1, dim))[0]
    except Exception as e:  # rede, chave, SDK ausente, dimensão errada: a busca segue por palavras
        reason = f"tempo esgotado ({QUERY_TIMEOUT_S:g} s)" if isinstance(e, TimeoutError) else index._reason(e)
        _record(store, pid, model, 0, None, _ms(t0), LlmCallStatus.ERROR, reason)
        log.warning("vetor da consulta indisponível: %s", reason)
        return QueryVector(None, status.model_key, reason)
    if kind == index.LOCAL:                                   # F5.1: sem custo (A-04)
        cost: float | None = 0.0
    else:
        cost = cost_usd(emb.usage, price_for(store.providers.list_prices(kind), kind, model))
    elapsed = _ms(t0)
    _record(store, pid, model, emb.usage.input_tokens, cost, elapsed, LlmCallStatus.OK, None)
    log.info("vetor da consulta (%s) em %.0f ms", status.model_key, elapsed)    # A-11: com o local, a carga da sessão vem no log dela
    vec.setflags(write=False)
    with _QUERY_LOCK:
        _QUERY_CACHE[key] = vec
        while len(_QUERY_CACHE) > QUERY_CACHE_SIZE:
            _QUERY_CACHE.popitem(last=False)
    return QueryVector(vec, status.model_key, "", cost_usd=cost)


async def _embed(store: Store, provider: LLMProvider | None, provider_id: str, model: str, dim: int, text: str,
                 should_stop: Callable[[], bool] | None = None) -> Embeddings | None:
    """O vetor de uma consulta, até `QUERY_TIMEOUT_S`; None quando `should_stop()` pediu para parar (conferido a cada
    `POLL_S`, no padrão do resumo parcial: a chamada presa na rede é cancelada, R7-03)."""
    from ..llm.registry import RetryingProvider, build_provider
    from ..llm.types import EmbedTask

    own = provider is None
    if provider is None:
        provider = RetryingProvider(build_provider(store, provider_id, role="embed", retrying=False)[0], attempts=QUERY_ATTEMPTS,
                                    delays=QUERY_DELAYS)
    call = asyncio.ensure_future(provider.embed([text], model=model, dim=dim, task=EmbedTask.QUERY))
    try:
        async with asyncio.timeout(QUERY_TIMEOUT_S):
            while not call.done():
                await asyncio.wait({call}, timeout=POLL_S)
                if should_stop is not None and should_stop() and not call.done():
                    return None
        return call.result()
    finally:
        if not call.done():
            call.cancel()
            with suppress(BaseException):
                await call
        if own:
            await provider.aclose()


def _record(store: Store, provider_id: str, model: str, tokens: int, cost: float | None, latency_ms: float, status: LlmCallStatus,
            error: str | None) -> None:
    from ..llm.types import LlmTask

    try:
        store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.RAG_EMBED), provider_id, model, None, None, int(tokens), 0, 0, 0, cost,
                                             int(latency_ms), status, error, "query"), store_payloads=False)
    except sqlite3.Error as e:  # o registro do custo nunca derruba a busca
        log.warning("não registrei a chamada do vetor da consulta: %s", e)


# ---------------------------------------------------------------- busca
def search(store: Store, query: str, filters: SearchFilters | None = None, *, query_vector: QueryVector | np.ndarray | None = None,
           k: int = DEFAULT_K, per_meeting: int | None = HISTORY_PER_MEETING, snippets: bool = True,
           cache: SignatureCache | None = None) -> SearchResult:
    """Busca híbrida (ou só por palavras, com o motivo) com os filtros nas duas listas; até `k` linhas fundidas, prontas para
    a tela, com no máximo `per_meeting` trechos de cada reunião (em cada lista antes do limite e na fusão; None = sem teto).
    `query_vector` vem de `embed_query` (None = só por palavras; um `ndarray` vale como vetor do modelo ativo).
    `snippets=False` pula o `snippet()` (o chat). Só lê o banco: rodar numa `Task`."""
    t0 = time.perf_counter()
    filters = filters or SearchFilters()
    cap = per_meeting if per_meeting is not None and per_meeting > 0 else None
    if isinstance(query_vector, np.ndarray):
        query_vector = QueryVector(query_vector, index.embedder_status(store).model_key)
    q = fts.build_query(query)
    if q is None:
        return SearchResult(query, (), False, "consulta vazia", "", False)
    timings: dict[str, float] = {}
    t = time.perf_counter()
    words, expr, used_or = _by_words(store, q, filters, snippets, cap)
    timings["fts_ms"] = _ms(t)
    t = time.perf_counter()
    meaning, reason, missing = _by_meaning(store, query_vector, filters, cache, cap)
    timings["vec_ms"] = _ms(t)
    t = time.perf_counter()
    fused = rrf([[h.chunk_id for h in words], [cid for cid, _cos in meaning]])
    rows = store.rag.hit_rows([cid for cid, _s, _r in fused])
    snips = {h.chunk_id: h.snippet for h in words}
    cosines = dict(meaning)
    seen: Counter[int] = Counter()
    found: list[SearchHit] = []
    for cid, score, ranks in fused:
        row = rows.get(cid)
        if row is None:                                       # saiu no meio do caminho
            continue
        mid = int(row["meeting_id"])
        if cap is not None and seen[mid] >= cap:              # as duas listas juntas podem passar do teto
            continue
        seen[mid] += 1
        found.append(_hit(row, score, ranks, snips.get(cid), cosines.get(cid), q.terms, snippets))
        if len(found) >= max(1, k):
            break
    hits = tuple(found)
    timings["fuse_ms"] = _ms(t)
    timings["total_ms"] = _ms(t0)
    hybrid = not reason
    log.info("busca (%s%s): %d por palavra em %.1f ms, %d por sentido em %.1f ms, %d linha(s) em %.1f ms no total%s",
             "híbrida" if hybrid else "por palavras", ", com OU" if used_or else "", len(words), timings["fts_ms"], len(meaning),
             timings["vec_ms"], len(hits), timings["total_ms"], "" if hybrid else f" ({reason})")
    return SearchResult(query, hits, hybrid, reason, expr, used_or, missing, timings)


def _by_words(store: Store, q: fts.FtsQuery, filters: SearchFilters, snippets: bool,
              cap: int | None) -> tuple[list[FtsHit], str, bool]:
    tokens = SNIPPET_TOKENS if snippets else 0
    expr, used_or = q.all_terms, False
    try:
        hits = store.rag.fts_hits(expr, LIST_K, filters, snippet_tokens=tokens, per_meeting=cap)
        if len(hits) < MIN_AND_RESULTS and q.any_term != q.all_terms:
            expr, used_or = q.any_term, True
            hits = store.rag.fts_hits(expr, LIST_K, filters, snippet_tokens=tokens, per_meeting=cap)
    except sqlite3.OperationalError as e:  # a expressão é montada aqui e segura; o FTS em si nunca derruba a busca
        log.warning("busca por palavra falhou (%s): %s", expr, e)
        return [], expr, used_or
    return hits, expr, used_or


def _by_meaning(store: Store, qv: QueryVector | None, filters: SearchFilters, cache: SignatureCache | None,
                cap: int | None) -> tuple[list[tuple[int, float]], str, int]:
    """(trecho, cosseno) do melhor para o pior, no máximo `cap` por reunião, o motivo de não haver busca por sentido (vazio =
    híbrida) e quantos trechos não têm vetor do modelo ativo."""
    if qv is None or qv.vector is None:
        return [], (qv.reason if qv is not None and qv.reason else NO_QUERY_VECTOR), 0
    key = index.embedder_status(store).model_key
    if key is None or qv.model_key != key:
        return [], "o modelo de embeddings mudou: busque de novo", 0
    view = (cache if cache is not None and cache.model == key else signature_cache(store, key)).view(store)
    missing = max(0, store.rag.count_chunks() - len(view.vids))
    if not len(view.vids):
        return [], NO_VECTORS.format(model=key), missing
    query = vectors.normalize(qv.vector)[0]
    if view.mu is not None and view.mu.shape[0] != query.shape[0]:
        return [], f"vetor da consulta com {query.shape[0]} dimensões e índice com {view.mu.shape[0]}", missing
    mask: np.ndarray | None = None
    if filters.active:
        allowed = store.rag.chunk_ids_where(filters)
        if not len(allowed):
            return [], "", missing                            # nada passa nos filtros: nenhuma das duas listas acha
        mask = np.isin(view.chunk_ids, allowed)
        if not mask.any():                                    # há trechos nos filtros, nenhum com vetor (R6-04)
            return [], FILTERED_NO_VECTORS, missing
    sig = vectors.signatures(query, view.mu)[0]
    if vectors.as_words(sig).shape[1] != view.words.shape[1]:
        return [], "assinatura da consulta de outro tamanho que a do índice", missing
    cand = vectors.candidates(view.words, sig, mask=mask)
    stored = store.rag.candidate_vectors([int(view.vids[i]) for i in cand])
    # saiu entre a carga e agora, ou o id agora é de outro trecho: fica de fora (a próxima busca recarrega o cache)
    keep = [int(i) for i in cand if stored.get(int(view.vids[i]), (-1,))[0] == int(view.chunk_ids[i])]
    if not keep:
        return [], "", missing
    pos, scores = vectors.rerank(query, np.stack([stored[int(view.vids[i])][2] for i in keep]), len(keep))
    out: list[tuple[int, float]] = []
    seen: Counter[int] = Counter()
    for p, s in zip(pos, scores, strict=True):                # todos os candidatos em ordem: o teto por reunião vem antes
        cid, mid, _vec = stored[int(view.vids[keep[int(p)]])]  # do limite da lista (R6-01)
        if cap is not None and seen[mid] >= cap:
            continue
        seen[mid] += 1
        out.append((cid, float(s)))
        if len(out) >= LIST_K:
            break
    return out, "", missing


def _hit(row: dict[str, Any], score: float, ranks: tuple[int | None, ...], snippet: str | None, cosine: float | None,
         terms: Sequence[str], snippets: bool) -> SearchHit:
    try:
        people = tuple(int(p) for p in json.loads(row.get("person_ids") or "[]"))
    except (ValueError, TypeError):
        people = ()
    text = str(row.get("text") or "")
    if not snippets:
        shown = ""
    elif snippet is not None:
        shown = fts.snippet_html(snippet)
    else:
        shown = fts.preview(text, terms)
    title = str(row.get("meeting_title") or row.get("meeting_title_llm") or row.get("title") or "")
    return SearchHit(int(row["id"]), int(row["meeting_id"]), title, str(row.get("started_at") or "")[:10], str(row["kind"]),
                     row.get("t_start_ms"), row.get("t_end_ms"), str(row.get("speakers") or ""), people, text, shown, ranks[0], ranks[1],
                     score, cosine, row.get("seg_first"))


__all__ = ["DEFAULT_K", "HISTORY_PER_MEETING", "KIND_LABELS", "RRF_K", "MeetingHits", "QueryVector", "SearchFilters", "SearchHit",
           "SearchResult", "SignatureCache", "clear_caches", "embed_query", "group_by_meeting", "limit_per_meeting", "rrf", "search",
           "signature_cache"]
