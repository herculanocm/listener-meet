"""F5.18, lote 4, a parte sem tela: `list_jobs` em dois ramos indexados igual à consulta antiga (PF-13), a leitura das falas
sem `words_json` (PF-15), as reuniões só dos jobs listados (PF-16), o `sync_text` que lê fora da transação e recomeça quando
o job vigente muda (PF-17), o `refresh_mu` sem `COUNT(*)` com o `mu` congelado (PF-18), o contador `rag.vector_version`, o
cache das assinaturas que só recarrega quando ele muda, as máscaras numpy dos filtros em paridade com o SQL e um
`EmbedderStatus` por busca (PF-19)."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import numpy as np
import pytest

from listener.llm.fake import FakeProvider
from listener.model import JobKind, JobStatus, StoreRole
from listener.pipeline import review
from listener.pipeline.voices import numbering
from listener.rag import filters as rag_filters
from listener.rag import index, search, vectors
from listener.rag.search import QueryVector, SignatureCache
from listener.store import Store
from listener.store.jobs import ACTIVE, _kinds
from listener.store.rag import ChunkMeta, SearchFilters
from listener.store.segments import SegmentInsert
from tests.test_rag_index import MODEL, _meeting, _people, _pipeline_job, _ready

STATUSES = [str(s) for s in JobStatus]
KINDS = [str(k) for k in JobKind]
DATES = ("2026-08-15", "2026-09-01", "2026-10-20")


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    yield
    search.clear_caches()


# ---------------------------------------------------------------- PF-13: `list_jobs`
def _old_list_jobs(store: Store, limit: int = 50, *, kinds: Iterable[str] | None = None, exclude_kinds: Iterable[str] | None = None,
                   statuses: Iterable[str] | None = None) -> list[int]:
    """A consulta de antes da F5.18 (um `ORDER BY` por expressão sobre a tabela inteira), guardada aqui como referência."""
    clause, params = _kinds(kinds)
    not_clause, not_params = _kinds(exclude_kinds, negate=True)
    wanted = tuple(str(x) for x in statuses) if statuses is not None else ()
    status_clause = f" AND status IN ({','.join('?' * len(wanted))})" if wanted else ""
    rows = store._q(f"SELECT id FROM processing_jobs WHERE 1 = 1{clause}{not_clause}{status_clause} "  # noqa: S608
                    "ORDER BY (status NOT IN (?, ?, ?)), CASE WHEN status IN (?, ?, ?) THEN id ELSE -id END LIMIT ?",
                    (*params, *not_params, *wanted, *ACTIVE, *ACTIVE, int(limit)))
    return [int(r[0]) for r in rows]


def _synthetic_jobs(store: Store, n: int, seed: int = 7) -> None:
    """Alguns milhares de jobs com ids esparsos, estados e classes variados (a maioria terminada, como numa base real)."""
    rng = np.random.default_rng(seed)
    meetings = [store.create_meeting(f"R{i}", None, None, None) for i in range(5)]
    weights = np.array([2, 1, 1, 60, 8, 4], float)
    statuses = rng.choice(STATUSES, size=n, p=weights / weights.sum())
    ids = np.cumsum(rng.integers(1, 4, size=n))
    rows = [(int(ids[i]), int(rng.choice(meetings)), str(rng.choice(KINDS)), str(statuses[i]), "2026-11-01T10:00:00.000-03:00")
            for i in range(n)]
    with store.transaction() as c:
        c.executemany("INSERT INTO processing_jobs(id, meeting_id, kind, status, created_at) VALUES (?, ?, ?, ?, ?)", rows)


def test_list_jobs_two_indexed_branches_return_the_old_query_exactly(store):
    _synthetic_jobs(store, 3000)
    cases: list[dict[str, Any]] = [
        {}, {"limit": 500}, {"limit": 5}, {"limit": 1}, {"kinds": ("index",)}, {"exclude_kinds": ("index",)},
        {"kinds": ("llm", "index"), "limit": 20}, {"statuses": (JobStatus.PENDING,), "kinds": ("index",)},
        {"statuses": ACTIVE, "limit": 100}, {"statuses": (JobStatus.DONE, JobStatus.ERROR), "limit": 30},     # só terminados
        {"statuses": (JobStatus.RUNNING, JobStatus.PAUSED)}, {"kinds": ("meeting",), "exclude_kinds": ("llm",), "limit": 7},
        {"kinds": ("embed_range",), "statuses": (JobStatus.CANCELLED,)}, {"limit": 100_000},
        {"kinds": ("nenhum",)}, {"statuses": ("inexistente",)},
    ]
    for kw in cases:
        got = [int(j["id"]) for j in store.jobs.list_jobs(**kw)]
        assert got == _old_list_jobs(store, **kw), kw
        assert len(got) <= kw.get("limit", 50)
    rows = store.jobs.list_jobs(limit=3)
    assert rows and {"grp", "sort_key"}.isdisjoint(rows[0]) and set(rows[0]) == set(store.jobs.get(int(rows[0]["id"])) or {})
    active = [int(j["id"]) for j in store.jobs.active()]
    listed = [int(j["id"]) for j in store.jobs.list_jobs(limit=len(active) + 50)]
    assert listed[:len(active)] == active                                   # os ativos na ordem da fila, antes dos terminados
    assert listed[len(active):] == sorted(listed[len(active):], reverse=True)   # depois, os terminados do mais recente ao mais antigo


# ---------------------------------------------------------------- PF-15: as falas sem `words_json`
def _old_transcript_rows(store: Store, meeting_id: int, prefer_llm: bool = True) -> list[review.TranscriptRow]:
    """A leitura de antes da F5.18: `SELECT *` com `words_json` e um `json.loads` por fala, guardada como referência."""
    people, job_id, clusters, _ = review.reading(store, meeting_id, speakers=False)
    segments = store.segments.list_segments(meeting_id, job_id)
    names = numbering(clusters.values(), job_id, segments)
    out: list[review.TranscriptRow] = []
    for s in segments:
        name, role, pid = review._speaker(s, people, clusters, names)
        first = int(s["t_start_ms"])
        try:
            words = json.loads(s.get("words_json") or "[]")
            if words:
                first = int(words[0][0])
        except (ValueError, TypeError, IndexError):
            pass
        out.append(review.TranscriptRow(int(s["id"]), str(s.get("channel") or ""), int(s["t_start_ms"]), int(s["t_end_ms"]), name, role,
                                        str(s.get("text") or ""), max(0, first - review.PLAY_LEAD_MS), s.get("text_llm") or None,
                                        prefer_llm, pid))
    return out


WORDS = ('[[1250, 1900, "bom"], [2000, 2500, "dia"]]', None, "[]", "não é json", '[["x", 1, "w"]]', '[[null, 1, "w"]]',
         "[[1250.6, 1900, \"bom\"]]", "[1, 2]", "[[]]", '[[true, 5, "w"]]')


def test_transcript_rows_without_words_json_equal_the_old_reading(store):
    who = _people(store)
    mid = store.create_meeting("Leitura", None, None, None)
    store.finish_meeting(mid, 60_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 3000 + 1000, i * 3000 + 2900, f"fala {i}", "pyannote", "final",
                                                  person_id=who["ana"] if i % 2 else None, cluster=f"SPK_{i % 3}", words_json=w)
                                    for i, w in enumerate(WORDS)])
    store.segments.set_text_llm(mid, job, [(int(s["id"]), f"revisada {i}") for i, s in enumerate(store.segments.list_segments(mid, job))
                                           if i % 3 == 0])
    for prefer in (True, False):
        rows = review.transcript_rows(store, mid, prefer)
        assert rows == _old_transcript_rows(store, mid, prefer) and len(rows) == len(WORDS)
    plays = [r.play_from_ms for r in review.transcript_rows(store, mid)]
    assert plays[0] == 1250 - 150 and plays[6] == 1250 - 150 and plays[9] == 0         # a primeira palavra; `true` vale 1 (como antes)
    assert plays[1:6] == [i * 3000 + 1000 - 150 for i in range(1, 6)] and plays[7:9] == [7 * 3000 + 850, 8 * 3000 + 850]
    light = store.segments.list_segments_light(mid, job)
    assert "words_json" not in light[0] and light[0]["first_word_ms"] == 1250 and light[1]["first_word_ms"] is None
    assert light[4]["first_word_ms"] == "x" and light[3]["first_word_ms"] is None        # o JSON inválido não quebra a consulta


# ---------------------------------------------------------------- PF-16: só as reuniões dos jobs listados
def test_titles_for_reads_only_the_asked_meetings_in_batches(store):
    mids = [store.create_meeting(f"R{i}" if i % 2 else None, None, None, None) for i in range(5)]
    store.finish_meeting(mids[1], 1234)
    store.meetings.set_title_llm(mids[0], "Sugerido")
    rows = store.meetings.titles_for([mids[1], mids[0], mids[1], 999_999, *range(10_000, 10_700)])    # repetidos, ausentes, >500
    assert set(rows) == {mids[0], mids[1]}
    assert rows[mids[1]]["title"] == "R1" and rows[mids[1]]["duration_ms"] == 1234 and rows[mids[1]]["started_at"]
    assert rows[mids[0]]["title"] is None and rows[mids[0]]["title_llm"] == "Sugerido"
    assert set(rows[mids[0]]) == {"id", "title", "title_llm", "started_at", "duration_ms"} and store.meetings.titles_for([]) == {}


# ---------------------------------------------------------------- PF-17 e PF-18: `sync_text` e `refresh_mu`
def test_sync_text_reads_outside_the_transaction_and_restarts_when_the_job_changes(store, monkeypatch):
    who = _people(store)
    mid, job = _meeting(store, who)
    gathers: list[bool] = []
    real_gather = index.gather

    def spy_gather(st: Store, meeting_id: int) -> Any:
        gathers.append(bool(st.core._local.in_tx))                       # a leitura fica fora do `BEGIN IMMEDIATE`
        return real_gather(st, meeting_id)

    monkeypatch.setattr(index, "gather", spy_gather)
    assert index.sync_text(store, mid) == index.SyncResult(5, 0, 5, 0, 0) and gathers == [False]
    real_current = store.segments.current_job_id
    fired: list[int] = []

    def changing(meeting_id: int) -> int | None:
        value = real_current(meeting_id)
        if store.core._local.in_tx and not fired:                        # a releitura dentro da transação vê outro job
            fired.append(1)
            return (value or 0) + 1
        return value

    monkeypatch.setattr(store.segments, "current_job_id", changing)
    gathers.clear()
    assert index.sync_text(store, mid) == index.SyncResult(5, 5, 0, 0, 0)   # recomeçou uma vez e terminou igual
    assert gathers == [False, False] and fired == [1]
    assert all(c["job_id"] == job for c in store.rag.list_chunks(mid))


def test_refresh_mu_skips_the_count_once_the_mu_is_frozen(store, monkeypatch):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    counted: list[str] = []
    real_count = store.rag.count_vectors

    def spy_count(*a: Any, **k: Any) -> int:
        counted.append("x")
        return real_count(*a, **k)

    monkeypatch.setattr(store.rag, "count_vectors", spy_count)
    assert index.refresh_mu(store, MODEL, 768) is False and counted == ["x"]        # `mu_n` 4 < 2.000: ainda conta
    model = store.rag.get_model(MODEL)
    store.rag.set_model(MODEL, 768, model["mu"], vectors.MU_FREEZE)
    counted.clear()
    assert index.refresh_mu(store, MODEL, 768) is False and counted == []           # congelado: nem conta


# ---------------------------------------------------------------- PF-19: o contador, o cache, os filtros e o status
def test_vector_version_counts_every_path_that_changes_what_the_search_caches(store):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    assert store.rag.index_version() == 0 and store.get_setting("rag.vector_version") == "0"       # banco sem a chave: 0
    index.sync_text(store, mid)
    v1 = store.rag.index_version()
    assert v1 >= 1                                                                                # `insert_chunks`
    index.fill_vectors(store, mid, provider=FakeProvider())
    v2 = store.rag.index_version()
    assert v2 > v1                                                                                # `insert_vectors` e o `mu`
    assert index.sync_text(store, mid).added == 0 and store.rag.index_version() == v2               # nada mudou: nada sobe
    with store.transaction():
        store.rag.update_meta([ChunkMeta(int(store.rag.list_chunks(mid)[0]["id"]), 0, 1, None, None, (who["ana"],), None)])
    v3 = store.rag.index_version()
    assert v3 == v2 + 1                                                                           # as pessoas de um trecho mantido
    with store.transaction():
        store.rag.delete_chunks([int(store.rag.list_chunks(mid)[-1]["id"])])
    assert store.rag.index_version() == v3 + 1                                                    # a cascata dos vetores
    store.delete_meeting(mid)
    assert store.rag.index_version() == v3 + 2 and store.rag.count_vectors() == 0                 # a cascata da reunião


def test_signature_cache_reloads_only_when_the_counter_changes(store, monkeypatch):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    cache = search.signature_cache(store, MODEL)
    assert not cache.loaded
    reads: list[str] = []

    def spy(name: str) -> Any:
        real = getattr(store.rag, name)

        def wrapped(*a: Any, **k: Any) -> Any:
            reads.append(name)
            return real(*a, **k)
        return wrapped

    for name in ("signatures", "count_chunks", "meeting_dates"):
        monkeypatch.setattr(store.rag, name, spy(name))
    qv = QueryVector(vectors.normalize(np.ones(768, np.float32))[0], MODEL)
    first = search.search(store, "orçamento", query_vector=qv)
    assert first.hybrid and cache.loaded and set(reads) == {"signatures", "count_chunks", "meeting_dates"} and cache.full_loads == 1
    reads.clear()
    for _ in range(3):
        assert search.search(store, "orçamento", query_vector=qv).hybrid
    assert reads == [] and cache.full_loads == 1                                                  # só o `get_setting` do contador
    other = Store(store.base_dir, role=StoreRole.WORKER)                                         # o leve grava: o contador sobe lá
    try:
        other.rag.bump_version()
    finally:
        other.close()
    assert search.search(store, "orçamento", query_vector=qv).hybrid and cache.full_loads == 2 and "signatures" in reads
    assert cache.view(store).chunks == 5 and search.search(store, "orçamento", query_vector=qv).missing_vectors == 0


def _filter_bed(store: Store) -> tuple[dict[str, int], list[int], SignatureCache]:
    """Três reuniões em datas distintas, uma delas com trechos de pessoas variadas e um trecho sem vetor."""
    who = _people(store) | {"carla": store.people.create_person("Carla")}
    mids = []
    for i, date in enumerate(DATES):
        mid, _job = _meeting(store, who, title=f"Reunião {i}", shift_s=i * 0.5)
        store._x("UPDATE meetings SET started_at = ? WHERE id = ?", (f"{date}T10:00:00.000-03:00", mid))
        mids.append(mid)
    _ready(store)
    for mid in mids:
        index.sync_text(store, mid)
    for n, people in enumerate(([who["carla"]], [who["carla"], who["bia"]], [], [who["ana"]])):
        store._x("INSERT INTO rag_chunks(meeting_id, kind, title, speakers, person_ids, text, text_hash, created_at) VALUES "
                 "(?, 'ata', 'Reunião 2', '', ?, ?, ?, '2026-10-25T10:00:00.000-03:00')",
                 (mids[2], json.dumps(people), f"decisão {n} sobre o contrato", f"h-{n}"))
    for mid in mids:
        index.fill_vectors(store, mid, provider=FakeProvider())
    with store.transaction():                                    # um trecho de cada reunião sem vetor: "índice incompleto"
        store._x("DELETE FROM rag_vectors WHERE chunk_id IN (SELECT MIN(id) FROM rag_chunks GROUP BY meeting_id)")
    store.rag.bump_version()
    return who, mids, search.signature_cache(store, MODEL)


def test_numpy_filter_masks_match_the_sql_reference(store):
    who, mids, cache = _filter_bed(store)
    view = cache.view(store)
    assert len(view.vids) == store.rag.count_vectors(MODEL) and view.chunks == store.rag.count_chunks() == len(view.vids) + 3
    cases = [SearchFilters(person_ids=(who["ana"],)), SearchFilters(person_ids=(who["carla"],)),
             SearchFilters(person_ids=(who["bia"], 999)), SearchFilters(person_ids=(999,)), SearchFilters(meeting_ids=(mids[1],)),
             SearchFilters(meeting_ids=(mids[0], mids[2])), SearchFilters(meeting_ids=(9999,)), SearchFilters(date_from="2026-09-01"),
             SearchFilters(date_to="2026-09-01"),
             SearchFilters(date_from="2026-08-16", date_to="2026-10-19"), SearchFilters(date_from="2026-10-21"),
             SearchFilters(date_to="2026-08-14"), SearchFilters(person_ids=(who["carla"],), date_from="2026-10-01"),
             SearchFilters(person_ids=(who["ana"],), meeting_ids=(mids[2],)), SearchFilters(meeting_ids=(mids[2],), date_to="2026-09-30"),
             SearchFilters(person_ids=(who["bia"],), date_from="2026-08-15", date_to="2026-10-20", meeting_ids=(mids[0], mids[2]))]
    for f in cases:
        mask = rag_filters.vector_mask(f, view.filter_data)
        assert mask is not None and mask.shape == view.vids.shape
        want = set(store.rag.chunk_ids_where(f).tolist()) & set(view.chunk_ids.tolist())
        assert set(view.chunk_ids[mask].tolist()) == want, f
        assert store.rag.any_chunk_where(f) == bool(len(store.rag.chunk_ids_where(f)))
    assert rag_filters.vector_mask(SearchFilters(), view.filter_data) is None
    # na busca: nada passa no filtro (híbrida vazia) contra trechos filtrados sem vetor (R6-04: "por palavras", com o motivo)
    qv = QueryVector(vectors.normalize(np.ones(768, np.float32))[0], MODEL)
    assert search.search(store, "contrato", SearchFilters(meeting_ids=(9999,)), query_vector=qv).hybrid
    lone = int(store._q("SELECT MIN(id) FROM rag_chunks WHERE meeting_id = ?", (mids[0],))[0][0])
    store._x("UPDATE rag_chunks SET person_ids = '[777]' WHERE id = ?", (lone,))
    store.rag.bump_version()
    res = search.search(store, "contrato", SearchFilters(person_ids=(777,)), query_vector=qv)
    assert not res.hybrid and res.reason == search.FILTERED_NO_VECTORS


def test_embedder_status_is_computed_once_per_search(store, monkeypatch):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    monkeypatch.setenv("LISTENER_LLM", "fake")
    calls: list[int] = []
    real = index.embedder_status

    def counted(st: Store) -> Any:
        calls.append(1)
        return real(st)

    monkeypatch.setattr(index, "embedder_status", counted)
    status = index.embedder_status(store)                                      # a tela calcula um e passa aos dois (PF-19)
    qv = search.embed_query(store, "auditoria", status=status)
    res = search.search(store, "auditoria", query_vector=qv, status=status)
    assert res.hybrid and calls == [1]
    calls.clear()
    assert search.search(store, "auditoria", query_vector=qv).hybrid and calls == [1]            # sem o status: calcula um
    calls.clear()
    assert not search.search(store, "auditoria").hybrid and calls == []                           # só por palavras: nenhum
    assert search.search(store, "auditoria", query_vector=qv.vector).hybrid and len(calls) == 1   # o vetor cru: um
