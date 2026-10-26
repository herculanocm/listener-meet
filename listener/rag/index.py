"""Índice da busca em dois tempos (F5 item 5, F5A-03, F5A-18): `sync_text` monta os trechos da reunião e os compara com os
gravados numa transação só (o FTS acompanha pelos gatilhos), e `fill_vectors` pede ao provedor só os vetores que faltam do
modelo ativo, fora de transação. O worker pesado chama `sync_text` ao concluir (a busca por palavra acha a reunião antes
da ata) e enfileira o `index`; o job `index` (classe leve, depois dos `llm`) faz os dois. `embedder_status` diz se há
embedder pronto (os pagos com chave; o local da F5.1 com o extra e a marca `.ready`); `sweep` é a varredura da abertura
(F5A-12).

F5.1, o embedder local (`llm/local_embed.py`): chave `local/multilingual-e5-small@384` (`EMBED_DIMS`), lotes de
`local_embed.BATCH` (8, A-14) um por vez, custo 0 (A-04); antes de cada lote, `defer` (a lease da gravação) encerra a rodada
com "vetores adiados: gravando" e `deferred` no resultado (A-06), e a varredura completa essas reuniões depois, sem exigir
modelo confirmado (A-07); as reuniões antigas só entram pela varredura depois do "Reindexar agora" (Q3: o local nunca é
confirmado sozinho, nem como o primeiro modelo do índice); a estimativa é sem custo, com 0,19 s por trecho até medir o
próprio modelo (A-05).

Regras duras: nunca `await` dentro de `store.transaction()`; o vetor só entra se o trecho ainda existe com o mesmo hash;
normaliza sempre; uma linha em `llm_calls` por lote (tarefa `rag_embed`, a reunião e o job do pipeline vigente); sem
embedder pronto ou sem rede, o job conclui com o FTS e a nota "N trechos sem vetor: motivo" (sem laço de retentativa: a
varredura da próxima abertura tenta de novo). `rag.auto_index` = "vetores automáticos": desligado, o `index` grava trechos
e FTS e pula os vetores ("vetores automáticos desligados"), salvo o pedido forçado ("reindexar"). As edições entram na fila
na hora, com uma espera durável de 30 s ("não antes de", renovada a cada edição; R5-07)."""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import batched
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from ..llm import local_embed
from ..log import get_logger
from ..model import ActionStatus, LlmCallStatus, SummaryKind
from ..pipeline import review
from ..store.llm_calls import LlmCallInsert
from ..store.rag import ChunkInsert, ChunkMeta, VectorInsert, person_ids_json
from . import chunker, vectors
from .chunker import ActionInput, Chunk, MeetingInput, Utterance

if TYPE_CHECKING:
    from ..llm.base import LLMProvider
    from ..llm.types import Usage
    from ..store import Store
    from ..store.rag import RagChunkRow

log = get_logger("rag.index")

EMBED_DIM = 768                  # os dois pagos em 768 (D-009): Gemini `gemini-embedding-001` e OpenAI `text-embedding-3-small`
EMBED_BATCH = 64
LOCAL = "local"                  # F5.1: o tipo do embedder local (e5-small na CPU, sem custo)
EMBED_DIMS: dict[str, int] = {LOCAL: local_embed.DIM}         # dimensão por tipo na chave do modelo (A-02); os pagos, 768
LOCAL_CHUNK_S = 0.19             # e5-small int8 com 2 threads por trecho de ~1.400 caracteres (D-051), até medir
DEFERRED = "vetores adiados: gravando"
NO_EMBEDDINGS = {"anthropic": "a Anthropic não oferece embeddings: escolha Gemini, OpenAI ou Local como ativo para embeddings "
                              "(Configurações, LLM)"}


class EmbedderStatus(NamedTuple):
    ready: bool
    reason: str                  # vazio quando pronto
    model_key: str | None        # "gemini/gemini-embedding-001@768": o `model` de `rag_vectors` (D-009)
    provider_id: str | None = None   # a linha de `providers` que atende (R5-10; hoje igual ao tipo nos pagos)


def model_key(kind: str, model: str, dim: int | None = None) -> str:
    """"gemini/gemini-embedding-001@768"; sem `dim`, a do tipo (`EMBED_DIMS`: 384 no local) ou 768 (D-009)."""
    return f"{kind}/{model.removeprefix('models/').strip()}@{int(dim or EMBED_DIMS.get(kind, EMBED_DIM))}"


def parse_model_key(key: str) -> tuple[str, str, int]:
    """"gemini/gemini-embedding-001@768" -> ("gemini", "gemini-embedding-001", 768)."""
    kind, _, rest = key.partition("/")
    model, _, dim = rest.rpartition("@")
    return kind, model, int(dim)


def is_local(key: str | None) -> bool:
    """A chave é do embedder local (F5.1): sem custo, lotes pequenos um por vez, vetores adiados durante a gravação."""
    return bool(key) and parse_model_key(str(key))[0] == LOCAL


def embedder_status(store: Store) -> EmbedderStatus:
    """O "ativo para embeddings" das Configurações (CP2): pronto com chave e um provedor que tem embeddings (D-009: modelo
    da linha do provedor, senão o padrão; dimensão 768), ou o local (F5.1) com o extra instalado e a marca `.ready` da
    pasta de modelos (um modelo só, 384 dimensões, sem chave). Uma função só para o índice, a busca e o `--check`."""
    from ..llm.models_table import DEFAULT_EMBED_MODELS
    from ..llm.types import ProviderKind

    row = store.providers.active_provider("embed")
    if row is None:
        return EmbedderStatus(False, "nenhum provedor ativo para embeddings (Configurações, LLM)", None)
    kind, pid = str(row["kind"]), str(row["id"])
    if kind in NO_EMBEDDINGS:
        return EmbedderStatus(False, NO_EMBEDDINGS[kind], None, pid)
    if kind == LOCAL:                                    # A-02: sem chave; pronto = o extra e a marca desta revisão
        ready, reason = local_embed.status(store.get_setting("pipeline.models_dir"))
        return EmbedderStatus(ready, reason, model_key(LOCAL, local_embed.MODEL), pid)
    try:
        default = DEFAULT_EMBED_MODELS.get(ProviderKind(kind), "")
    except ValueError:
        default = ""
    model = str(row.get("model") or default).strip()
    if not model:
        return EmbedderStatus(False, f"o provedor {pid} não tem modelo de embeddings (Configurações, LLM)", None, pid)
    key = model_key(kind, model)
    if not str(row.get("api_key") or "").strip():
        return EmbedderStatus(False, f"sem chave do provedor {pid} para embeddings (Configurações, LLM)", key, pid)
    return EmbedderStatus(True, "", key, pid)


# ---------------------------------------------------------------- texto (trechos e FTS)
@dataclass(frozen=True, slots=True)
class SyncResult:
    total: int = 0
    kept: int = 0
    added: int = 0
    removed: int = 0
    moved: int = 0               # mantidos com tempos, segmentos, pessoas ou job novos


def _words(raw: str | None) -> tuple[tuple[int, int, str], ...]:
    try:
        data = json.loads(raw or "[]")
        return tuple((int(w[0]), int(w[1]), str(w[2])) for w in data)
    except (ValueError, TypeError, IndexError):
        return ()


def gather(store: Store, meeting_id: int) -> MeetingInput | None:
    """A reunião como a leitura mostra: falas revisadas com "Nome — Cargo" do dia, ata exibida, ações vigentes da ata atual,
    executivo e participantes (quem fala; sem transcrição, os esperados). None se a reunião não existe."""
    m = store.get_meeting(meeting_id)
    if m is None:
        return None
    rows = review.transcript_rows(store, meeting_id, prefer_llm=True)
    long_ids = [r.segment_id for r in rows if len(r.shown_text) > chunker.BODY_MAX - chunker.MIN_PIECE]
    words = {int(r[0]): _words(r[1]) for r in store._q(
        f"SELECT id, words_json FROM segments WHERE id IN ({','.join('?' * len(long_ids))})", tuple(long_ids))} if long_ids else {}  # noqa: S608
    utterances = tuple(Utterance(r.segment_id, r.t_start_ms, r.t_end_ms, f"{r.name} — {r.role}" if r.role else r.name, r.person_id,
                                 r.shown_text, words.get(r.segment_id, ())) for r in rows)
    who: dict[int, str] = {}
    for u in utterances:
        if u.person_id is not None and u.person_id not in who:
            who[u.person_id] = u.speaker
    people = {int(p["id"]): dict(p) for p in store.people.list_people()}
    if not who:                                          # sem transcrição: os esperados do warm-up (nomes cadastrados)
        try:
            expected = [int(pid) for pid in json.loads(str(m.get("expected_people_json") or "[]"))]
        except (ValueError, TypeError):
            expected = []
        who = {pid: str(people[pid]["name"]) for pid in expected if pid in people}
    participants = tuple(who.values())
    if not participants and m.get("expected_participants"):
        participants = tuple(p.strip() for p in str(m["expected_participants"]).replace(";", ",").split(",") if p.strip())
    minutes = store.summaries.current(meeting_id, SummaryKind.MINUTES)
    actions: list[ActionInput] = []
    if minutes is not None:
        for a in store.actions.list_for_meeting(meeting_id, summary_id=int(minutes["id"])):
            if str(a.get("status") or ActionStatus.OPEN) not in (ActionStatus.OPEN, ActionStatus.DONE):
                continue
            owner_pid = a.get("owner_person_id")
            owner = str(people[int(owner_pid)]["name"]) if owner_pid is not None and int(owner_pid) in people else a.get("owner_text")
            actions.append(ActionInput(str(a.get("text") or ""), owner, int(owner_pid) if owner_pid is not None else None, a.get("due"),
                                       a.get("t_ms"), a.get("segment_id")))
    executive = store.summaries.display_text(store.summaries.current(meeting_id, SummaryKind.EXECUTIVE))
    return MeetingInput(str(m.get("title") or m.get("title_llm") or ""), str(m.get("started_at") or "")[:10], m.get("duration_ms"),
                        participants, tuple(who), utterances, store.summaries.display_text(minutes), tuple(actions), executive)


def _key_counts(items: list[tuple[str, str]]) -> list[tuple[str, str, int]]:
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[str, str, int]] = []
    for kind, digest in items:
        n = seen.get((kind, digest), 0)
        seen[(kind, digest)] = n + 1
        out.append((kind, digest, n))
    return out


def sync_text(store: Store, meeting_id: int) -> SyncResult:
    """Lê, monta e compara numa transação só (pesado e leve não se atropelam), casando por (tipo, `text_hash`, ocorrência):
    os mantidos ganham tempos, segmentos, pessoas e job novos sem perder os vetores; os novos entram (o FTS pelo gatilho);
    os que sumiram saem, com os vetores pela cascata."""
    with store.transaction():
        data = gather(store, meeting_id)
        if data is None:
            return SyncResult()
        job_id = store.segments.current_job_id(meeting_id)
        built = chunker.build_chunks(data)
        old = store.rag.list_chunks(meeting_id)
        old_keys = dict(zip(_key_counts([(str(r["kind"]), str(r["text_hash"])) for r in old]), old, strict=True))
        new_keys = _key_counts([(str(c.kind), c.text_hash) for c in built])
        kept = [(old_keys[key], c) for key, c in zip(new_keys, built, strict=True) if key in old_keys]
        kept_ids = {int(row["id"]) for row, _c in kept}
        moved = [ChunkMeta(int(row["id"]), c.t_start_ms, c.t_end_ms, c.seg_first, c.seg_last, c.person_ids, job_id)
                 for row, c in kept if _meta_changed(row, c, job_id)]
        removed = [int(r["id"]) for r in old if int(r["id"]) not in kept_ids]
        added = [_insert(meeting_id, c, job_id) for key, c in zip(new_keys, built, strict=True) if key not in old_keys]
        store.rag.delete_chunks(removed)
        store.rag.update_meta(moved)
        store.rag.insert_chunks(added)
    result = SyncResult(len(built), len(kept), len(added), len(removed), len(moved))
    log.info("reunião %s: %d trecho(s); %d mantido(s) (%d com tempos novos), %d novo(s), %d removido(s)", meeting_id, result.total,
             result.kept, result.moved, result.added, result.removed)
    return result


def _meta_changed(row: RagChunkRow, c: Chunk, job_id: int | None) -> bool:
    return (row.get("t_start_ms"), row.get("t_end_ms"), row.get("seg_first"), row.get("seg_last"), row.get("person_ids"),
            row.get("job_id")) != (c.t_start_ms, c.t_end_ms, c.seg_first, c.seg_last, person_ids_json(c.person_ids), job_id)


def _insert(meeting_id: int, c: Chunk, job_id: int | None) -> ChunkInsert:
    return ChunkInsert(meeting_id, str(c.kind), c.t_start_ms, c.t_end_ms, c.seg_first, c.seg_last, c.title, c.speakers, c.person_ids,
                       c.text, c.text_hash, job_id)


# ---------------------------------------------------------------- vetores
AUTO_OFF = "vetores automáticos desligados"


@dataclass(slots=True)
class FillResult:
    model_key: str | None
    wanted: int = 0                      # trechos sem vetor do modelo no começo
    written: int = 0
    missing: int = 0                     # trechos sem vetor do modelo no fim
    reason: str = ""                     # por que faltou (sem embedder, sem rede, vetores automáticos desligados...)
    calls: int = 0
    cost_usd: float = 0.0
    batches: int = 0
    pauses: int = 0                      # quantas vezes a fila foi pausada no meio (os lotes no ar terminaram antes)
    deferred: bool = False               # F5.1 (A-06): o local parou porque a gravação começou; a varredura completa depois
    retry: bool = False                  # R10-04: o local não abriu ou falhou no cálculo; a varredura tenta de novo, uma vez
    errors: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        """"N trechos sem vetor: motivo" para o `timings_json` do job e o log; vazio quando nada faltou. Adiados pela gravação:
        "vetores adiados: gravando (N trechos)"."""
        if not self.missing:
            return ""
        chunks = f"{self.missing} {'trecho' if self.missing == 1 else 'trechos'}"
        if self.deferred:
            return f"{DEFERRED} ({chunks})"
        return f"{chunks} sem vetor: {self.reason or 'motivo desconhecido'}"


def refresh_mu(store: Store, key: str, dim: int) -> bool:
    """Refaz o `mu` do modelo quando os vetores dobraram (até 2 mil, depois congela) e regrava todas as assinaturas a partir
    dos float32, numa transação que também decide se é a hora (dois processos não refazem com contas velhas; sem rede).
    Reentrante: dentro da transação do lote, vale junto com ele."""
    with store.transaction():
        row = store.rag.get_model(key)
        if not vectors.mu_due(store.rag.count_vectors(key), int(row["mu_n"]) if row is not None else 0):
            return False
        ids, mat = store.rag.vectors_by_model(key)
        if not len(ids):
            return False
        mu = mat.mean(axis=0).astype(np.float32)
        sigs = vectors.signatures(mat, mu)
        store.rag.set_signatures([(int(vid), sigs[i].tobytes()) for i, vid in enumerate(ids)])
        store.rag.set_model(key, dim, mu, len(ids))
    log.info("%s: média refeita com %d vetor(es)%s", key, len(ids), " (congelada)" if len(ids) >= vectors.MU_FREEZE else "")
    return True


def fill_vectors(store: Store, meeting_id: int, *, provider: LLMProvider | None = None, status: EmbedderStatus | None = None,
                 on_progress: Callable[[int, int], None] | None = None, check: Callable[[], None] | None = None,
                 paused: Callable[[], bool] | None = None, wait: Callable[[], None] | None = None,
                 batch_size: int | None = None, defer: Callable[[], bool] | None = None) -> FillResult:
    """Pede os vetores que faltam do modelo ativo em lotes de `batch_size` num `TaskGroup` sob `llm.max_parallel`, fora de
    transação; grava lote a lote. Antes de cada lote, dentro do laço asyncio e sem bloquear: `check` (heartbeat e
    cancelamento; se levantar, os lotes seguintes não saem e a exceção sobe depois dos que estavam no ar) e `paused` (a fila
    foi pausada: os lotes seguintes não saem, os do ar terminam). Na pausa, `wait` espera FORA do laço (bloqueante, como o
    `checkpoint` do runner: "entre tarefas, nunca no meio", F3) e a rodada recomeça com o que ainda falta (R5-04). Erro de
    rede ou de provedor não sobe: para os lotes seguintes e vira o motivo da nota. `on_progress(gravados, pedidos)`.
    O local (F5.1) vai em lotes de `local_embed.BATCH`, um por vez, sem custo (A-04, A-14), e consulta `defer` (a lease da
    gravação) antes de cada lote: verdadeiro encerra a rodada com os vetores adiados (A-06; os pagos não disputam a CPU e
    ignoram `defer`)."""
    from ..llm.aio import run_async

    status = status or embedder_status(store)
    key = status.model_key
    if not status.ready or key is None:
        n = store.rag.count_missing(meeting_id, key) if key else store.rag.count_chunks(meeting_id)
        return FillResult(key, n, 0, n, status.reason)
    result = FillResult(key, store.rag.count_missing(meeting_id, key))
    meeting = store.get_meeting(meeting_id) or {}
    date = str(meeting.get("started_at") or "")[:10]
    job_id = store.segments.current_job_id(meeting_id)
    size = max(1, batch_size or (local_embed.BATCH if is_local(key) else EMBED_BATCH))
    while True:
        rows = store.rag.missing_vectors(meeting_id, key)
        if not rows:
            break
        plan = _Plan(store, meeting_id, key, status.provider_id or parse_model_key(key)[0], date, job_id, list(batched(rows, size)),
                     result, on_progress, check, paused, defer if is_local(key) else None)
        run_async(plan.run(provider))
        if plan.stop_exc is not None:
            result.missing = store.rag.count_missing(meeting_id, key)
            raise plan.stop_exc
        if plan.deferred:
            result.deferred, result.reason = True, DEFERRED
            log.info("reunião %s: gravação em curso; os vetores do local ficam para depois dela", meeting_id)
            break
        if plan.stop or not plan.paused:
            break
        result.pauses += 1
        log.info("reunião %s: fila pausada no meio dos vetores; os lotes no ar terminaram e a rodada espera", meeting_id)
        if wait is None:                                          # sem quem espere, a rodada para aqui (o resto fica na nota)
            result.reason = "fila pausada"
            break
        wait()                                                    # bloqueante, fora do laço asyncio; levanta no cancelamento
    result.missing = store.rag.count_missing(meeting_id, key)
    if result.missing and not result.reason:
        result.reason = "o trecho mudou durante o pedido"            # o próximo `index` pede de novo
    return result


class _Plan:
    """Uma rodada de `fill_vectors`: os lotes, o semáforo e o que parou a rodada."""

    def __init__(self, store: Store, meeting_id: int, key: str, provider_id: str, date: str, job_id: int | None,
                 batches: list[tuple[RagChunkRow, ...]], result: FillResult, on_progress: Callable[[int, int], None] | None,
                 check: Callable[[], None] | None, paused: Callable[[], bool] | None, defer: Callable[[], bool] | None = None):
        from ..config.defaults import SPECS

        self.store = store
        self.meeting_id = meeting_id
        self.key = key
        self.kind, self.model, self.dim = parse_model_key(key)
        self.local = self.kind == LOCAL
        self.provider_id = provider_id
        self.date = date
        self.job_id = job_id
        self.batches = batches
        self.result = result
        self.on_progress = on_progress
        self.check = check
        self.is_paused = paused
        self.defer = defer
        # o local calcula na CPU desta máquina, um lote por vez (A-04); os pagos, sob `llm.max_parallel`
        self.max_parallel = 1 if self.local else max(1, int(store.get(SPECS["llm.max_parallel"])))
        self.prices = [] if self.local else store.providers.list_prices(self.kind)
        self.stop = False                                          # erro do provedor: nenhum lote novo, nota com o motivo
        self.paused = False                                        # fila pausada: nenhum lote novo, espera fora do laço
        self.deferred = False                                      # gravação em curso com o local: nenhum lote novo (A-06)
        self.stop_exc: BaseException | None = None

    async def run(self, provider: LLMProvider | None) -> None:
        own = provider is None
        if provider is None:
            try:
                from ..llm.registry import build_provider

                provider = build_provider(self.store, self.provider_id, role="embed")[0]
            except Exception as e:  # SDK ausente, chave apagada no meio: vira a nota, sem derrubar o job
                self.stop = True
                self.result.reason = _reason(e)
                return
        sem = asyncio.Semaphore(self.max_parallel)
        try:
            async with asyncio.TaskGroup() as tg:
                for i, batch in enumerate(self.batches):
                    tg.create_task(self._one(provider, sem, i, batch))
        finally:
            if own:
                await provider.aclose()

    def _gate(self) -> bool:
        """Antes de cada lote, dentro do laço e sem bloquear: heartbeat e cancelamento (`check`), a pausa da fila e, no local,
        a gravação em curso (`defer`, A-06). False = este lote não sai."""
        if self.stop or self.paused or self.deferred or self.stop_exc is not None:
            return False
        try:
            if self.check is not None:
                self.check()
            if self.is_paused is not None and self.is_paused():
                self.paused = True
                return False
            if self.defer is not None and self.defer():
                self.deferred = True
                return False
        except BaseException as e:  # cancelamento (ou o banco): sobe depois dos lotes no ar
            self.stop, self.stop_exc = True, e
            return False
        return True

    async def _one(self, provider: LLMProvider, sem: asyncio.Semaphore, idx: int, rows: tuple[RagChunkRow, ...]) -> None:
        """Um lote: nunca levanta dentro do `TaskGroup` (as irmãs seguem até o fim delas). Falha do provedor vira o motivo da
        nota; falha ao gravar (banco) para os lotes seguintes e sobe depois, com a mensagem dela."""
        from ..llm import redact
        from ..llm.base import classify_exception
        from ..llm.types import EmbedTask

        async with sem:
            if not self._gate():
                return
            texts = [chunker.embed_input(str(r.get("title") or ""), self.date, str(r["text"])) for r in rows]
            t0 = time.perf_counter()
            try:
                emb = await provider.embed(texts, model=self.model, dim=self.dim, task=EmbedTask.DOCUMENT)
                mat = _checked(emb.vectors, len(rows), self.dim)
            except Exception as e:
                err = classify_exception(e)
                message = redact.GLOBAL.redact(str(err))
                if not self.stop:
                    self.stop = True
                    self.result.reason = _reason(err)
                if self.local and isinstance(err, local_embed.LocalEmbedError):
                    self.result.retry = True                       # R10-04: passageira (memória, carga): a varredura refaz
                self.result.errors.append(message)
                log.warning("reunião %s: lote %d de embeddings falhou: %s", self.meeting_id, idx + 1, message)
                try:
                    self._call(len(rows), 0, None, int((time.perf_counter() - t0) * 1000), LlmCallStatus.ERROR, message)
                except Exception as db_error:  # o banco também falhou: o job falha com esta mensagem
                    self.stop_exc = self.stop_exc or db_error
                return
        try:
            self._store(rows, emb.usage, mat, int((time.perf_counter() - t0) * 1000))
        except Exception as e:  # banco ocupado além do `busy_timeout`, por exemplo: o job falha e o retry pede de novo
            self.stop = True
            if self.stop_exc is None:
                self.stop_exc = e

    def _store(self, rows: tuple[RagChunkRow, ...], usage: Usage, mat: np.ndarray, latency: int) -> None:
        """Grava o lote (fora do semáforo, sem `await`): primeiro a chamada paga em `llm_calls`; depois, numa transação só,
        o `mu` vigente lido de novo, os vetores normalizados assinados com ele e, se os vetores do modelo dobraram, o `mu`
        novo com todas as assinaturas (R5-06: nenhuma assinatura fica com um `mu` que outro processo já trocou)."""
        from ..llm.costs import cost_usd, price_for

        cost = 0.0 if self.local else cost_usd(usage, price_for(self.prices, self.kind, self.model))     # o local não custa (A-04)
        self._call(len(rows), usage.input_tokens, cost, latency, LlmCallStatus.OK, None)
        self.result.cost_usd += cost or 0.0
        unit = vectors.normalize(mat)
        with self.store.transaction():
            row = self.store.rag.get_model(self.key)
            sigs = vectors.signatures(unit, row["mu"] if row is not None else None)
            written = self.store.rag.insert_vectors(self.key, self.dim, [
                VectorInsert(int(r["id"]), str(r["text_hash"]), unit[i].tobytes(), sigs[i].tobytes()) for i, r in enumerate(rows)])
            refresh_mu(self.store, self.key, self.dim)
        self.result.written += written
        self.result.batches += 1
        if self.on_progress is not None:
            self.on_progress(self.result.written, self.result.wanted)

    def _call(self, n: int, tokens: int, cost: float | None, latency: int, status: LlmCallStatus, error: str | None) -> None:
        from ..llm.types import LlmTask

        self.result.calls += 1
        self.store.llm_calls.insert(LlmCallInsert(self.meeting_id, self.job_id, str(LlmTask.RAG_EMBED), self.provider_id, self.model,
                                                  None, None, int(tokens), 0, 0, 0, cost, latency, status, error, f"batch:{n}"),
                                    store_payloads=False)


def _checked(raw: np.ndarray, n: int, dim: int) -> np.ndarray:
    """Uma linha por trecho, na dimensão do modelo (a do `model_key`); senão é erro do provedor, não vetor errado no banco."""
    mat = np.asarray(raw, np.float32)
    if mat.ndim != 2 or mat.shape != (n, dim):
        raise ValueError(f"o provedor devolveu {mat.shape} para {n} trecho(s) em {dim} dimensões")
    return mat


def _reason(e: BaseException) -> str:
    """Motivo curto para a nota: "sem rede (...)", "tempo esgotado" ou a mensagem do provedor, já redigida."""
    from ..llm import redact
    from ..llm.base import classify_exception

    err = classify_exception(e)
    text = redact.GLOBAL.redact(str(err))
    if err.retryable and text.startswith("rede"):
        return f"sem rede ({text.removeprefix('rede: ')})"
    return text


# ---------------------------------------------------------------- fila
EDIT_DELAY_S = 30.0                  # edição de falantes, título, ata ou ações: o `index` espera 30 s sem edição (R5-07)


def auto_index_enabled(store: Store) -> bool:
    """`rag.auto_index` (Q1): "vetores automáticos" (o custo dos embeddings). Desligado, os jobs `index` continuam e gravam
    trechos e FTS (locais e grátis); só a etapa de vetores fica de fora, salvo no pedido forçado ("reindexar")."""
    from ..config.defaults import SPECS

    return bool(store.get(SPECS["rag.auto_index"]))


def enqueue_index(store: Store, meeting_id: int, reason: str, *, force: bool = False, delay_s: float | None = None,
                  now: str | None = None) -> int | None:
    """Ponto único de entrada do `index` (F5A-04): um pendente por reunião; devolve o id do job novo, ou None quando já havia
    um pendente (que fica com a espera e o `force` deste pedido). `delay_s` é a espera durável das edições (R5-07): o job
    entra já, com "não antes de" agora + `delay_s`, renovado a cada edição; 0 tira a espera ("Concluir", fechar a revisão).
    `force` é o pedido do usuário ("reindexar", L5): pede os vetores mesmo com os vetores automáticos desligados."""
    job_id = store.jobs.enqueue_index(meeting_id, reason, force=force, delay_s=delay_s, now=now)
    if job_id is not None:
        log.info("reunião %s: índice na fila (job %s, %s%s)", meeting_id, job_id, reason,
                 f", espera {delay_s:.0f} s" if delay_s else "")
    return job_id


def model_confirmed(store: Store, key: str) -> bool:
    """A varredura pode completar o modelo nas reuniões antigas? Os pagos, como o `RagRepo.model_confirmed` diz (o primeiro
    do índice ou o "reindexar agora", R5-09); o local, só depois do "Reindexar agora" (F5.1 Q3: a varredura sozinha ocuparia
    a CPU por minutos sem o usuário pedir, mesmo sendo o primeiro modelo do índice)."""
    if is_local(key):
        row = store.rag.get_model(key)
        return bool(row is not None and row.get("backfill"))
    return store.rag.model_confirmed(key)


def sweep(store: Store, should_stop: Callable[[], bool] | None = None) -> int:
    """Varredura da abertura (F5A-12): reuniões com pipeline concluído e nenhum trecho (todo índice grava ao menos o trecho
    `reuniao`, logo a reunião sem fala não volta a cada abertura), reuniões cujo último `index` terminou em erro (uma vez
    por abertura, R5-07), as que adiaram os vetores do local durante uma gravação (F5.1 A-07: também ao parar ou cancelar a
    gravação, sem exigir modelo confirmado), as em que o local falhou de passagem (R10-04: memória, carga; uma tentativa por
    varredura, sem laço) e, só com os vetores automáticos ligados, o embedder pronto e o modelo confirmado
    (`model_confirmed`), as que têm trecho sem vetor dele (as anteriores à F5, as que ficaram sem rede). `should_stop` (a
    saída do app) interrompe entre as consultas e entre as reuniões (R5-02). Devolve quantos `index` entraram na fila."""
    stop = should_stop or (lambda: False)
    meetings = set(store.rag.meetings_without_chunks())
    if stop():
        return 0
    meetings |= set(store.jobs.failed_index_meetings())
    meetings |= set(store.jobs.deferred_index_meetings())
    meetings |= set(store.jobs.retry_index_meetings())
    if stop():
        return 0
    status = embedder_status(store)
    if auto_index_enabled(store) and status.ready and status.model_key and model_confirmed(store, status.model_key):
        store.rag.mark_backfill(status.model_key, parse_model_key(status.model_key)[2])   # o primeiro fica confirmado de vez
        meetings |= set(store.rag.meetings_missing_vectors(status.model_key))
    n = 0
    for mid in sorted(meetings):
        if stop():
            log.info("varredura do índice interrompida (saída do app) depois de %d reunião(ões)", n)
            break
        if enqueue_index(store, mid, "sweep") is not None:
            n += 1
    if n:
        log.info("varredura do índice: %d reunião(ões) na fila", n)
    return n


# ---------------------------------------------------------------- "completar" e "reindexar" (L5, comportamento 4)
CHARS_PER_TOKEN_EMBED = 4.0          # tokens ≈ caracteres da entrada do embedding ÷ 4 (estimativa antes de pedir)
DEFAULT_BATCH_S = 2.0                # sem lote medido em `llm_calls`, 2 s por lote de 64


@dataclass(frozen=True, slots=True)
class ReindexEstimate:
    """O que "reindexar" (ou completar) custaria no modelo ativo: os trechos sem vetor dele, tokens, US$ e minutos."""
    model_key: str | None
    ready: bool
    reason: str
    chunks: int = 0                  # trechos sem vetor do modelo ativo
    meetings: int = 0                # reuniões com algum desses trechos
    without_chunks: int = 0          # reuniões processadas ainda sem trechos (o FTS é grátis; os vetores delas vêm depois)
    tokens: int = 0
    cost_usd: float | None = None    # None = sem preço cadastrado para o modelo
    batches: int = 0
    seconds: float = 0.0
    measured: bool = False           # latência dos lotes medida (`rag_embed`) ou o padrão de 2 s (0,19 s por trecho no local)

    @property
    def local(self) -> bool:
        """O embedder local (F5.1): sem custo; os textos "com custo" ficam só para os pagos (A-05)."""
        return is_local(self.model_key)

    def text(self) -> str:
        """"1.234 trechos sem vetor em 12 reuniões · ≈ 456.789 tokens · ≈ US$ 0,07 · ≈ 3 min" (o que as Configurações mostram);
        no local, "1.234 trechos sem vetor em 12 reuniões · sem custo · ≈ 4 min nesta máquina"."""
        from ..llm.costs import fmt_usd

        if not self.chunks:
            base = "nenhum trecho sem vetor do modelo ativo"
        elif self.local:
            base = (f"{_n(self.chunks)} {'trecho' if self.chunks == 1 else 'trechos'} sem vetor em {self.meetings} "
                    f"{'reunião' if self.meetings == 1 else 'reuniões'} · sem custo · ≈ {_duration(self.seconds)} nesta máquina"
                    + ("" if self.measured else f" (pela estimativa de {LOCAL_CHUNK_S:.2f} s por trecho)".replace(".", ",")))
        else:
            cost = f"≈ {fmt_usd(self.cost_usd)}" if self.cost_usd is not None else "sem preço cadastrado"
            minutes = max(1, round(self.seconds / 60)) if self.seconds else 0
            base = (f"{_n(self.chunks)} {'trecho' if self.chunks == 1 else 'trechos'} sem vetor em {self.meetings} "
                    f"{'reunião' if self.meetings == 1 else 'reuniões'} · ≈ {_n(self.tokens)} tokens · {cost} · ≈ {minutes} min"
                    + ("" if self.measured else " (pela estimativa de 2 s por lote)"))
        if self.without_chunks and self.ready:          # completar e reindexar pedem os vetores delas também (`force`, R8-05)
            base += f"; mais {self.without_chunks} reunião(ões) ainda sem trechos, que também recebem vetores"
            if not self.local:                          # R10-07: o local não tem custo a saber
                base += " (o custo delas só se sabe depois do corte em trechos)"
        elif self.without_chunks:
            base += f"; mais {self.without_chunks} reunião(ões) ainda sem trechos (entram com o índice por palavra)"
        return base


def _n(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _duration(seconds: float) -> str:
    """"40 s" abaixo de um minuto, senão "3 min" (o local é rápido: uma reunião leva segundos)."""
    return f"{max(1, round(seconds))} s" if seconds < 59.5 else f"{round(seconds / 60)} min"


def local_seconds_per_chunk(store: Store, model: str, last: int = 20) -> float | None:
    """Segundos por trecho medidos nos últimos lotes do local (`rag_embed`, `batch:N` com a latência do lote; A-05: a
    estimativa usa o próprio modelo assim que ele tiver medida); None sem lote medido."""
    from ..llm.types import LlmTask

    rows = store._q("SELECT latency_ms, strategy FROM llm_calls WHERE status = ? AND task = ? AND model = ? AND strategy LIKE ? "
                    "ORDER BY id DESC LIMIT ?", (str(LlmCallStatus.OK), str(LlmTask.RAG_EMBED), model, "batch:%", int(last)))
    total_ms = 0.0
    chunks = 0
    for latency, strategy in rows:
        try:
            n = int(str(strategy).removeprefix("batch:"))
        except ValueError:
            continue
        if n > 0:
            total_ms += float(latency or 0)
            chunks += n
    return total_ms / chunks / 1000 if chunks else None


def reindex_estimate(store: Store) -> ReindexEstimate:
    """A estimativa do "reindexar tudo" e da troca do embedder (L5, comportamento 4): os trechos sem vetor do modelo ativo
    (os que já têm vetor ficam: só os que faltam são pedidos), tokens ≈ caracteres da entrada ÷ 4, custo pela tabela de
    preços do modelo e minutos pelos lotes de 64 de cada reunião (sob `llm.max_parallel`) e pela latência média dos lotes
    já medidos do modelo (`rag_embed`, `batch:`), ou 2 s por lote sem medida. O local (F5.1, A-05): custo 0 e os segundos
    por trecho medidos no próprio modelo, ou 0,19 s por trecho sem medida. Só leitura."""
    from ..config.defaults import SPECS
    from ..llm.costs import price_for
    from ..llm.types import LlmTask

    status = embedder_status(store)
    without = len(store.rag.meetings_without_chunks())
    if status.model_key is None:
        return ReindexEstimate(None, False, status.reason, without_chunks=without)
    stats = store.rag.missing_stats(status.model_key)
    chunks = sum(n for _mid, n, _chars in stats)
    tokens = math.ceil(sum(chars for _mid, _n, chars in stats) / CHARS_PER_TOKEN_EMBED)
    kind, model, _dim = parse_model_key(status.model_key)
    if kind == LOCAL:
        per_chunk = local_seconds_per_chunk(store, model)
        batches = sum(math.ceil(n / local_embed.BATCH) for _mid, n, _chars in stats)
        return ReindexEstimate(status.model_key, status.ready, status.reason, chunks, len(stats), without, tokens, 0.0, batches,
                               chunks * (per_chunk if per_chunk is not None else LOCAL_CHUNK_S), per_chunk is not None)
    price = price_for(store.providers.list_prices(kind), kind, model)
    cost = round(tokens * price.input_usd_m / 1_000_000, 6) if price is not None else None
    parallel = max(1, int(store.get(SPECS["llm.max_parallel"])))
    batches = sum(math.ceil(n / EMBED_BATCH) for _mid, n, _chars in stats)
    rounds = sum(math.ceil(math.ceil(n / EMBED_BATCH) / parallel) for _mid, n, _chars in stats)
    latency = store.llm_calls.avg_latency_ms(task=str(LlmTask.RAG_EMBED), model=model, strategy_prefix="batch:")
    per_batch = latency / 1000 if latency else DEFAULT_BATCH_S
    return ReindexEstimate(status.model_key, status.ready, status.reason, chunks, len(stats), without, tokens, cost, batches,
                           rounds * per_batch, latency is not None)


def complete_index(store: Store) -> int:
    """"Completar índice" (L5): põe na fila, com `force` (vale com os vetores automáticos desligados), as reuniões com trecho
    sem vetor do modelo ativo e as processadas ainda sem trechos. Não confirma o modelo para a varredura (isso é do
    "reindexar tudo", que mostra a estimativa antes). Devolve quantos `index` novos entraram."""
    status = embedder_status(store)
    meetings = set(store.rag.meetings_without_chunks())
    if status.ready and status.model_key:
        meetings |= set(store.rag.meetings_missing_vectors(status.model_key))
    return sum(1 for mid in sorted(meetings) if enqueue_index(store, mid, "reindex", force=True) is not None)


def reindex_all(store: Store) -> int:
    """"Reindexar agora" (L5): confirma o modelo ativo para completar as reuniões antigas (a varredura passa a pedir os
    vetores dele, R5-09) e põe todas as reuniões processadas na fila com `force` (vale mesmo com os vetores automáticos
    desligados). Devolve quantos `index` novos entraram (os já pendentes ficam com o `force`)."""
    status = embedder_status(store)
    if status.model_key:
        store.rag.mark_backfill(status.model_key, parse_model_key(status.model_key)[2])
    return sum(1 for mid in store.rag.processed_meetings() if enqueue_index(store, mid, "reindex", force=True) is not None)


__all__ = ["AUTO_OFF", "DEFERRED", "EDIT_DELAY_S", "EMBED_DIM", "EMBED_DIMS", "LOCAL", "EmbedderStatus", "FillResult", "ReindexEstimate",
           "SyncResult", "auto_index_enabled", "complete_index", "embedder_status", "enqueue_index", "fill_vectors", "gather", "is_local",
           "local_seconds_per_chunk", "model_confirmed", "model_key", "parse_model_key", "refresh_mu", "reindex_all", "reindex_estimate",
           "sweep", "sync_text"]
