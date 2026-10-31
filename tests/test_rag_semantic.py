"""F5.10 L1 (D4, Escopo 1 e 2): a camada semântica, o dicionário e o cache. `falas.falante` é o `speaker_of` da leitura (vozes
sem pessoa numeradas, mescla, canal L, reunião sem job e reprocessada); o `dia` é o local (22:30 de -03:00 fica no dia, não
vira o seguinte em UTC); os filtros da aba Reuniões limitam as visões; o cache é refeito em cada caminho de escrita (voz,
mescla, pessoa, título, reprocessar, excluir, ata) e depois de 10 min; o dicionário descreve toda coluna, todo exemplo roda,
toda tabela do arquivo está classificada e nenhuma dependência toca a lista de negação, nem por uma CTE com o nome de uma
visão da camada."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from listener.model import ActionStatus, ClusterStatus, HEAVY_JOB_KINDS, JobKind, JobStatus, SummaryKind
from listener.pipeline import review
from listener.rag import dictionary, index, semantic
from listener.rag.sandbox import Category, Sandbox, never
from listener.store import Store
from listener.store.actions import ActionInsert
from listener.store.rag import SearchFilters
from listener.store.segments import ClusterInsert, SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import _meeting, _people, _pipeline_job
from tests.test_voices import _voice_meeting

NOW = datetime(2026, 10, 30, 14, 5, tzinfo=timezone(timedelta(hours=-3)))
ATA = "\n".join(("## Contexto", "", "Orçamento de 2027.", "", "## Decisões", "", "- Aprovar a auditoria [01:10]", ""))


@pytest.fixture(autouse=True)
def fresh_cache():
    semantic.CACHE.clear()
    yield
    semantic.CACHE.clear()


def _open(store: Store, filters: SearchFilters | None = None) -> Sandbox:
    return semantic.open_sandbox(store, filters, now=NOW)


def _rows(box: Sandbox, sql: str, *params: str) -> list[tuple[Any, ...]]:
    res = box.run(sql, params, max_rows=1000, timeout_s=5)
    assert res.ok, res.message
    return list(res.rows)


def _same_as_the_reading(store: Store, mid: int) -> list[str]:
    """`falas` igual à leitura (`review.transcript_rows`, a regra da F5.5): falante, pessoa, cargo e texto de cada fala."""
    with _open(store) as box:
        got = _rows(box, "SELECT fala_id, falante, pessoa_id, cargo, texto, inicio FROM falas WHERE reuniao_id = CAST(? AS INTEGER) "
                         "ORDER BY inicio_ms, fala_id", str(mid))
    want = [(r.segment_id, r.name, r.person_id, r.role or None, r.shown_text, r.stamp) for r in review.transcript_rows(store, mid)]
    assert got == want
    return [str(r[1]) for r in got]


def _summary(store: Store, mid: int, job: int | None, kind: SummaryKind = SummaryKind.MINUTES, text: str = "# Ata") -> int:
    return store.summaries.insert(SummaryInsert(mid, kind, "{}", text, "gemini", "m", "minutes/2.0", job, None, "pt"))


# ---------------------------------------------------------------- falas = speaker_of
def test_speech_follows_the_reading_with_numbered_voices_and_a_merge(store):
    ana = store.people.create_person("Ana", role="CFO")
    mid, _job = _voice_meeting(store, ana)
    assert _same_as_the_reading(store, mid) == ["Voz 1", "Ana", "Voz 2", "Voz 1"]
    first, second = (c.cluster_id for c in review.pending(review.review_items(store, mid)))
    review.merge(store, second, first)                                       # a mescla: a segunda voz leva o número do alvo
    assert _same_as_the_reading(store, mid) == ["Voz 1", "Ana", "Voz 1", "Voz 1"]
    review.confirm(store, first, ana)                                        # confirmada: as duas viram a Ana
    assert _same_as_the_reading(store, mid) == ["Ana"] * 4
    with _open(store) as box:
        part = _rows(box, "SELECT falante, pessoa_id, cargo, canal, falas, fala_min, primeira_fala FROM participacao")
    assert part == [("Ana", ana, "CFO", "R", 4, 2.3, "00:00")]


def test_speech_of_channel_l_a_meeting_without_job_and_a_reprocessed_one(store):
    me = store.upsert_me("Eu", role="Gerente")
    mid = store.create_meeting("Sala", None, None, None, is_in_room=True)
    store.finish_meeting(mid, 60_000)
    job = _pipeline_job(store, mid)
    user = store.segments.insert_clusters([ClusterInsert(mid, job, "L", "L", 5000, None, None, ClusterStatus.AUTO, me)])
    voice = store.segments.insert_clusters([ClusterInsert(mid, job, "L", "SPK_0", 5000, None, None, ClusterStatus.IGNORED)])
    store.segments.insert_segments([SegmentInsert(mid, job, "L", 0, 1000, "oi", "canal", "final", "L", user["L"]),
                                    SegmentInsert(mid, job, "L", 2000, 3000, "eu aqui", "pyannote", "final", "SPK_0", voice["SPK_0"]),
                                    SegmentInsert(mid, job, "L", 4000, 5000, "sem voz", "canal", "final")])
    assert _same_as_the_reading(store, mid) == ["Eu", "Voz 1", "Desconhecido"]
    loose = store.create_meeting("Importada", None, None, None)                # sem job nenhum: todas as falas, sem números
    store.segments.insert_segments([SegmentInsert(loose, None, "R", 0, 1000, "a", "pyannote", "final", "SPK_1"),   # type: ignore[arg-type]
                                    SegmentInsert(loose, None, "R", 1000, 2000, "b", "usuario", "final", person_id=me)])  # type: ignore[arg-type]
    assert store.segments.current_job_id(loose) is None
    assert _same_as_the_reading(store, loose) == ["Voz SPK_1", "Eu"]
    who = _people(store)
    again, old = _meeting(store, who, "Reprocessada")
    new = store.jobs.enqueue(again, JobKind.REPROCESS)
    store.jobs.claim(pid=1, kinds=HEAVY_JOB_KINDS)
    store.jobs.finish(new, JobStatus.DONE)
    store.segments.insert_segments([SegmentInsert(again, new, "R", 0, 9000, "a nova transcrição", "pyannote", "final",
                                                  person_id=who["bia"])])
    assert store.segments.current_job_id(again) == new != old
    assert _same_as_the_reading(store, again) == ["Bia"]                     # só o job vigente, não as falas do anterior
    with _open(store) as box:
        flags = _rows(box, "SELECT reuniao_id, tem_falas, tem_ata FROM reunioes ORDER BY reuniao_id")
    assert flags == [(mid, 1, 0), (loose, 1, 0), (again, 1, 0)]


def test_the_local_day_of_a_meeting_late_in_the_evening_and_the_meeting_columns(store):
    mid = store.create_meeting("Fechamento", None, None, "Ana, Bia")
    store.finish_meeting(mid, 5_430_000)
    store._x("UPDATE meetings SET started_at = ?, title_llm = 'Sugestão', platform = 'ms-teams.exe', language = 'pt' WHERE id = ?",
             ("2026-10-29T22:30:00.000-03:00", mid))
    untitled = store.create_meeting(None, None, None, None)
    store._x("UPDATE meetings SET title_llm = 'Só da LLM' WHERE id = ?", (untitled,))
    with _open(store) as box:
        row = _rows(box, "SELECT * FROM reunioes WHERE reuniao_id = CAST(? AS INTEGER)", str(mid))[0]
        titles = _rows(box, "SELECT titulo FROM reunioes ORDER BY reuniao_id")
        now = _rows(box, "SELECT * FROM agora")
        utc = _rows(box, "SELECT date('2026-10-29T22:30:00.000-03:00')")
    assert row == (mid, "Fechamento", "2026-10-29", "22:30", "2026-10", "quinta", 90.5, "gravada", "ms-teams.exe", 0, "pt", "Ana, Bia",
                   0, 0)
    assert utc == [("2026-10-30",)]                                          # o que o `date()` do SQLite faria: o dia seguinte
    assert titles == [("Fechamento",), ("Só da LLM",)] and now == [("2026-10-30", "14:05", "sexta", "-03:00")]


# ---------------------------------------------------------------- as outras visões e os filtros
def test_actions_minutes_items_summaries_markers_and_costs(store):
    who = _people(store)
    mid, job = _meeting(store, who)
    old = _summary(store, mid, job)
    store.actions.insert_many([ActionInsert(mid, old, "Ação velha", None, None, None, None, None, job)])
    sid = _summary(store, mid, job, text=ATA)                       # a velha e a ação dela ficam superadas
    store.actions.insert_many([ActionInsert(mid, sid, "Mandar a planilha", "Bia", who["bia"], "2026-10-20", 200_000, None, job),
                               ActionInsert(mid, sid, "Revisar contrato", "Fornecedor", None, None, None, None, job,
                                            ActionStatus.DONE)])
    _summary(store, mid, job, SummaryKind.EXECUTIVE, "Resumo executivo")
    store.add_marker(mid, 65_000, "importante")
    index.sync_text(store, mid)
    with _open(store) as box:
        acts = _rows(box, "SELECT texto, dono, dono_pessoa_id, prazo, estado, inicio FROM acoes ORDER BY acao_id")
        late = _rows(box, "SELECT count(*) FROM acoes a, agora g WHERE a.estado = 'aberta' AND a.prazo < g.hoje")
        kinds = _rows(box, "SELECT tipo, texto FROM resumos ORDER BY tipo")
        marks = _rows(box, "SELECT inicio, inicio_ms, nota FROM marcadores")
        items = _rows(box, "SELECT tipo, texto, inicio FROM ata_itens ORDER BY tipo")
        found = _rows(box, "SELECT reuniao_id, inicio FROM trechos WHERE busca MATCH ? ORDER BY relevancia", "auditoria")
    assert acts == [("Mandar a planilha", "Bia", who["bia"], "2026-10-20", "aberta", "03:20"),
                    ("Revisar contrato", "Fornecedor", None, None, "feita", None)] and late == [(1,)]
    assert kinds == [("ata", ATA), ("executivo", "Resumo executivo")] and marks == [("01:05", 65_000, "importante")]
    assert items == [("ata", "Orçamento de 2027.", None), ("decisao", "Aprovar a auditoria", "01:10")]
    assert found and found[0][0] == mid


def test_the_filters_of_the_meetings_tab_limit_every_view(store):
    from listener.store.llm_calls import LlmCallInsert
    from listener.model import LlmCallStatus

    who = _people(store)
    a, _ = _meeting(store, who)
    store._x("UPDATE meetings SET started_at = '2026-09-01T10:00:00.000-03:00' WHERE id = ?", (a,))
    b = store.create_meeting("Só da Ana", None, None, None)
    job = _pipeline_job(store, b)
    store.segments.insert_segments([SegmentInsert(b, job, "R", 0, 1000, "oi", "pyannote", "final", person_id=who["ana"])])
    for mid in (a, None):
        store.llm_calls.insert(LlmCallInsert(mid, None, "chat", "gemini", "m", None, None, 10, 5, 0, 0, 0.01, 5, LlmCallStatus.OK))
    with _open(store) as box:
        assert _rows(box, "SELECT count(*) FROM reunioes") == [(2,)] and _rows(box, "SELECT count(*) FROM custos_llm") == [(2,)]
    for filters, meetings in ((SearchFilters(date_from="2026-10-01"), [b]), (SearchFilters(person_ids=(who["bia"],)), [a]),
                              (SearchFilters(meeting_ids=(b,)), [b])):
        with _open(store, filters) as box:
            assert [r[0] for r in _rows(box, "SELECT reuniao_id FROM reunioes")] == meetings
            assert {r[0] for r in _rows(box, "SELECT DISTINCT reuniao_id FROM falas")} == set(meetings)
            assert _rows(box, "SELECT count(*) FROM custos_llm") == [(1 if meetings == [a] else 0,)]   # sem reunião fica fora


# ---------------------------------------------------------------- o cache (D4)
def _rebuilt(store: Store, change: Any) -> bool:
    first, _hit = semantic.CACHE.get(store)
    assert semantic.CACHE.get(store) == (first, True)
    time.sleep(0.01)                                                         # `updated_at` em ms: a edição cai noutro instante
    change()
    second, hit = semantic.CACHE.get(store)
    return not hit and second is not first


def test_the_cache_is_rebuilt_on_every_write_path_and_after_ten_minutes(store):
    ana = store.people.create_person("Ana", role="CFO")
    mid, job = _voice_meeting(store, ana)
    other, _ = _voice_meeting(store, ana, "Outra")
    first, second = (c.cluster_id for c in review.pending(review.review_items(store, mid)))
    paths = {
        "voz": lambda: review.confirm(store, first, ana),
        "mescla": lambda: review.merge(store, second, first),
        "pessoa": lambda: store.people.update_person(ana, "Ana Maria", role="CFO"),
        "título": lambda: store.update_title(mid, "Novo título"),
        "reprocessar": lambda: _pipeline_job(store, mid),
        "excluir": lambda: store.delete_meeting(other),
        "ata": lambda: _summary(store, mid, job),
    }
    for name, change in paths.items():
        assert _rebuilt(store, change), name
    with _open(store) as box:                                                # o reprocessamento ainda sem falas: o job novo vale
        assert _rows(box, "SELECT titulo, tem_ata, tem_falas FROM reunioes") == [("Novo título", 1, 0)]
        assert _rows(box, "SELECT nome FROM pessoas") == [("Ana Maria",)] and _rows(box, "SELECT count(*) FROM falas") == [(0,)]
    clock = [1000.0]
    semantic.CACHE.clear()
    layer, _ = semantic.CACHE.get(store, clock=lambda: clock[0])
    clock[0] += semantic.CACHE_TTL_S - 1
    assert semantic.CACHE.get(store, clock=lambda: clock[0]) == (layer, True)
    clock[0] += 2
    assert semantic.CACHE.get(store, clock=lambda: clock[0])[1] is False      # 10 min depois, mesmo sem escrita


def test_the_build_stops_when_asked(store):
    ana = store.people.create_person("Ana")
    _voice_meeting(store, ana)
    with pytest.raises(semantic.BuildStopped):
        semantic.build(store, lambda: True)


# ---------------------------------------------------------------- o dicionário e a política com as visões reais
def test_the_dictionary_describes_every_column_and_every_example_runs(store):
    who = _people(store)
    mid, _job = _meeting(store, who)
    index.sync_text(store, mid)
    names = [v.name for v in dictionary.VIEWS]
    assert names == list(semantic.VIEWS)
    with _open(store) as box:
        assert set(box.views) == set(names)
        for v in dictionary.VIEWS:
            res = box.run(f"SELECT * FROM {v.name} LIMIT 0", (), max_rows=1, timeout_s=5)  # noqa: S608
            assert res.columns == tuple(c.name for c in v.columns), v.name
            assert all(c.text and c.kind in dictionary.TYPES for c in v.columns)
        for question, sql, params in dictionary.EXAMPLES:
            assert box.run(sql, params, max_rows=50, timeout_s=5).ok, question
    text = dictionary.render()
    assert all(f"\n{n}: " in f"\n{text}" for n in names) and "Exemplos:" in text and len(text) < 16_000


def test_every_main_table_is_classified_and_no_dependency_touches_the_deny_list(store):
    with _open(store) as box:
        deps = box.policy.deps
    tables = {str(r[0]) for r in store._q("SELECT name FROM sqlite_master WHERE type = 'table'")}
    visible = {t for pairs in deps.values() for t, _c in pairs}
    assert tables <= visible | set(dictionary.DENIED) | dictionary.FTS_INTERNAL
    assert not visible & set(dictionary.DENIED) and not visible & dictionary.FTS_INTERNAL
    assert [(v, t, c) for v, pairs in deps.items() for t, c in pairs if never(t, c)] == []
    assert {"providers", "settings", "chat_messages", "revisions", "events", "presets", "processing_jobs", "clusters",
            "rag_vectors", "job_artifacts", "voice_profiles", "voice_embeddings"} <= set(dictionary.DENIED)
    assert deps["pessoas"] == frozenset() and "people" in dictionary.DENIED                   # D-075: nenhuma visão lê people
    assert all(t != "people" for pairs in deps.values() for t, _c in pairs)


@pytest.mark.parametrize("secret", ["api_key FROM providers", "value FROM settings", "email FROM people", "notes FROM people",
                                    "notes FROM meetings", "error FROM llm_calls", "text FROM chat_messages"])
def test_no_cte_named_like_a_view_of_the_layer_reads_the_deny_list(store, secret):
    store.people.create_person("Ana", email="ana@exemplo.com", notes="reservado")
    with _open(store) as box:
        for view in semantic.VIEWS:
            res = box.run(f"WITH {view} AS (SELECT {secret}) SELECT * FROM {view}", (), max_rows=5, timeout_s=5)  # noqa: S608
            assert res.error == Category.DENIED and not res.rows, (view, secret)
