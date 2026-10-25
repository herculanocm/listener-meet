"""F5 L3, busca: a consulta FTS5 segura (aspas, sintaxe crua vira texto, controles fora, prefixo no último token da última
palavra, palavras vazias, E -> OU abaixo de 10), o destaque seguro contra HTML (`snippet()` e `preview`), a busca por palavra
e a híbrida (RRF, ordem e empates), o teto por reunião dentro de cada lista (R6-01), os filtros (pessoa, período, reunião)
nas duas listas, "por palavras" sem vetor da consulta, sem vetores do modelo ativo ou sem vetor nos trechos filtrados, o
cache das assinaturas (μ refeito, vetor novo de outro processo, trecho apagado, maior id trocado), o vetor da consulta
(cache, `llm_calls` com reunião nula, falha e tempo esgotado viram o motivo, índice sem vetores não chama a rede),
`group_by_meeting` e `limit_per_meeting`, as configurações `rag.*` com os vetores automáticos desligados e o `fmt_usd`
abaixo de um centavo."""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from listener.config import defaults
from listener.llm import costs
from listener.llm.base import TransientError
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask
from listener.model import ChunkKind, StoreRole
from listener.rag import fts, index, search, vectors
from listener.rag.search import QueryVector, SearchHit, SignatureCache
from listener.store import Store
from listener.store.rag import SearchFilters
from listener.store.segments import SegmentInsert
from tests.test_rag_index import MODEL, _launch_spy, _meeting, _people, _pipeline_job, _ready

OTHER = [(0, 40, "carla", "O roteiro do marketing digital para o lançamento do produto."),
         (40, 90, "carla", "A campanha nas redes sociais começa em novembro com orçamento reduzido."),
         (90, 150, "carla", "Falta contratar a agência de publicidade e fechar o cronograma.")]


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    yield
    search.clear_caches()


def _other_meeting(store: Store, who: dict[str, int], title: str = "Marketing digital") -> int:
    mid = store.create_meeting(title, None, None, None)
    store.finish_meeting(mid, 150_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", t0 * 1000, t1 * 1000, text, "pyannote", "final", person_id=who[p])
                                    for t0, t1, p, text in OTHER])
    return mid


def _indexed(store: Store, *mids: int) -> None:
    for mid in mids:
        index.sync_text(store, mid)
        index.fill_vectors(store, mid, provider=FakeProvider())


def _qv(text: str) -> QueryVector:
    """O vetor da consulta como o `embed_query` daria com o fake (saco de palavras), sem passar pela rede nem pelo cache."""
    from listener.llm.fake import bag_of_words

    return QueryVector(bag_of_words(text, 768), MODEL)


def test_fts_query_quotes_every_word_neutralizes_syntax_and_prefixes_the_last(store):
    q = fts.build_query("orçamento auditoria")
    assert (q.terms, q.all_terms, q.any_term) == (("orçamento", "auditoria"), '"orçamento" "auditoria"*', '"orçamento" OR "auditoria"*')
    assert fts.build_query('diz "olá"').all_terms == '"diz" """olá"""*'                          # aspas internas dobradas
    assert fts.build_query("o que foi decidido sobre o orçamento?").terms == ("decidido", "orçamento?")   # palavras vazias saem
    assert fts.build_query("o que").terms == ("o", "que")                                       # só vazias: ficam
    assert fts.build_query("de").all_terms == '"de"' and fts.build_query("dec").all_terms == '"dec"*'   # prefixo a partir de 3
    assert fts.build_query("abc-d").all_terms == '"abc-d"' and fts.build_query("e-mail").all_terms == '"e-mail"*'  # o último token
    assert fts.build_query("audit\x00oria").all_terms == '"audit" "oria"*' and fts.build_query("\x00\x01\x7f") is None  # controles
    assert fts.build_query("Orçamento orcamento ORÇAMENTO").terms == ("Orçamento",)              # sem repetição
    assert fts.build_query(" ".join(f"w{i}" for i in range(40))).terms[0] == "w8"                # as 32 últimas
    for empty in ("", "   ", "?? -- () :: ^ *", '""'):
        assert fts.build_query(empty) is None                                                   # não vai ao FTS
    who = _people(store)
    mid, _job = _meeting(store, who)
    index.sync_text(store, mid)
    nasty = ["auditoria AND sexta", "auditoria OR NOT sexta", "NEAR(auditoria sexta)", "text:auditoria", "^auditoria", "-auditoria",
             "auditoria*", "(auditoria", 'audit"oria', "auditoria) OR (1=1", "+auditoria", "a:b:c", "AND", "OR", "NOT", "NEAR"]
    for raw in nasty:
        q = fts.build_query(raw)
        assert q is not None
        store.rag.fts_hits(q.all_terms, 10)                                                     # nunca erro de sintaxe
        store.rag.fts_hits(q.any_term, 10)
    assert [h.chunk_id for h in store.rag.fts_hits(fts.build_query("text:auditoria").all_terms, 10)] == []   # coluna não é filtro
    for raw in ("auditoria \x00", "\x00auditoria", "audi\x00toria sexta"):                       # o \x00 cortava a expressão
        assert search.search(store, raw).hits, repr(raw)
    hits = store.rag.fts_hits(fts.build_query("-auditoria").all_terms, 10)
    assert len(hits) == 1 and "auditoria" in store.rag.get_chunk(hits[0].chunk_id)["text"]       # "-" não exclui: é texto


def test_snippet_and_preview_escape_html_before_the_bold():
    raw = "a <b>x</b> \x02orçamento\x03 & <script>y</script>\nlinha 2"
    assert fts.snippet_html(raw) == "a &lt;b&gt;x&lt;/b&gt; <b>orçamento</b> &amp; &lt;script&gt;y&lt;/script&gt; linha 2"
    text = "Ana: <i>bom dia</i>. " + "palavra " * 60 + "O ORÇAMENTO de 2027 foi aprovado & o Orçamentário também."
    shown = fts.preview(text, fts.build_query("orcament").terms)                                 # prefixo, sem acento e sem caixa
    assert shown.startswith("…") and "<b>ORÇAMENTO</b>" in shown and "<b>Orçamentário</b>" in shown and "&amp;" in shown
    assert "<i>" not in fts.preview(text, ["bom"]) and "&lt;i&gt;<b>bom</b>" in fts.preview(text, ["bom", "zzz"])
    assert fts.preview("sem nada aqui", ["orcamento"]) == "sem nada aqui"
    assert fts.preview("x " * 300, ["y"]).endswith("…") and len(fts.preview("x " * 300, ["y"])) <= fts.PREVIEW_CHARS + 1
    assert "<b>e-mail</b>" in fts.preview("mande o e-mail hoje", ["e-mail"])                       # frase do FTS: e mail
    assert fts.preview("a auditoria externa", ["auditoria", "externo"]) == "a <b>auditoria</b> externa"   # só a última é prefixo
    assert fts.preview("a auditoria externa", ["externo", "audit"]) == "a <b>auditoria</b> externa"


def test_search_by_words_and_or_fallback_snippet_and_diacritics(store, monkeypatch):
    who = _people(store)
    mid, _job = _meeting(store, who)
    index.sync_text(store, mid)
    res = search.search(store, "auditoria")
    assert not res.hybrid and res.reason == search.NO_QUERY_VECTOR and res.label == "busca por palavras"
    assert res.fts_query == '"auditoria"*' and res.fts_or is False and set(res.timings_ms) == {"fts_ms", "vec_ms", "fuse_ms", "total_ms"}
    top = res.hits[0]
    assert top.meeting_id == mid and top.meeting_title == "Orçamento 2027" and top.kind == ChunkKind.TRANSCRIPT
    assert "<b>auditoria</b>" in top.snippet_html and top.fts_rank == 1 and top.vec_rank is None and top.t_start_ms is not None
    assert top.kind_label == "transcrição" and top.person_ids == tuple(sorted(who.values())) and top.meeting_date == top.meeting_date[:10]
    plain = [h.chunk_id for h in search.search(store, "ORCAMENTO").hits]
    assert plain and plain == [h.chunk_id for h in search.search(store, "orçamento").hits]       # sem acento e sem caixa
    both = search.search(store, "auditoria futebol")                                           # E não acha nada: refaz com OU
    assert both.fts_or and both.fts_query == '"auditoria" OR "futebol"*' and both.hits and "<b>auditoria</b>" in both.hits[0].snippet_html
    assert search.search(store, "   ").hits == () and search.search(store, "?!").reason == "consulta vazia"
    assert search.search(store, "auditoria", snippets=False).hits[0].snippet_html == ""
    asked: list[str] = []
    real = store.rag.fts_hits

    def ten(match: str, limit: int, *a: Any, **kw: Any) -> list[Any]:
        asked.append(match)
        return (real(match, limit, *a, **kw) * 10)[:10]                                           # 10 com E: sem OU

    monkeypatch.setattr(store.rag, "fts_hits", ten)
    assert not search.search(store, "auditoria sexta").fts_or and asked == ['"auditoria" "sexta"*']


def test_rrf_order_and_ties():
    fused = search.rrf([[1, 2, 3], [3, 1, 4]])
    assert [f[0] for f in fused] == [1, 3, 2, 4]
    assert fused[0][2] == (1, 2) and fused[3][2] == (None, 3) and fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert [f[0] for f in search.rrf([[2], [1]])] == [2, 1]                                     # empate: a lista por palavra antes
    assert [f[0] for f in search.rrf([[7, 5], [5, 7]])] == [5, 7]                               # empate total: o menor id
    assert [f[0] for f in search.rrf([[9, 8], [], [8]])] == [8, 9] and search.rrf([[], []]) == []


def test_hybrid_search_fuses_both_lists_and_finds_by_meaning(store):
    who = _people(store) | {"carla": store.people.create_person("Carla", role="Gerente")}
    a, _ = _meeting(store, who)
    b = _other_meeting(store, who)
    _ready(store)
    _indexed(store, a, b)
    res = search.search(store, "campanha redes sociais", query_vector=_qv("campanha redes sociais"))
    assert res.hybrid and res.reason == "" and res.missing_vectors == 0 and res.label == "busca híbrida"
    top = res.hits[0]
    assert top.meeting_id == b and top.fts_rank == 1 and top.vec_rank == 1 and "<b>campanha</b>" in top.snippet_html
    assert top.score == pytest.approx(2 / 61) and top.cosine is not None and top.cosine > 0.2
    by_meaning = [h for h in res.hits if h.fts_rank is None]
    assert by_meaning and all(h.vec_rank is not None for h in by_meaning)                      # achados só pelo sentido
    assert [h.score for h in res.hits] == sorted((h.score for h in res.hits), reverse=True)
    assert len(search.search(store, "campanha", query_vector=_qv("campanha"), k=3).hits) == 3
    groups = search.group_by_meeting(res.hits)
    assert groups[0].meeting_id == b and {g.meeting_id for g in groups} == {a, b} and groups[0].title == "Marketing digital"
    assert sum(len(g.hits) for g in groups) == len(res.hits)


def test_filters_apply_to_both_lists_before_the_top_k(store):
    who = _people(store) | {"carla": store.people.create_person("Carla", role="Gerente")}
    a, _ = _meeting(store, who)
    b = _other_meeting(store, who)
    store._x("UPDATE meetings SET started_at = ? WHERE id = ?", ("2026-09-01T10:00:00.000-03:00", b))
    _ready(store)
    _indexed(store, a, b)
    q = "orçamento campanha"
    qv = _qv(q)

    def check(f: SearchFilters, ok: Any) -> list[SearchHit]:
        res = search.search(store, q, f, query_vector=qv)
        assert res.hybrid and res.hits and all(ok(h) for h in res.hits)
        assert any(h.vec_rank is not None for h in res.hits) and any(h.fts_rank is not None for h in res.hits)
        return list(res.hits)

    check(SearchFilters(meeting_ids=(b,)), lambda h: h.meeting_id == b)
    check(SearchFilters(person_ids=(who["carla"],)), lambda h: h.meeting_id == b and who["carla"] in h.person_ids)
    check(SearchFilters(person_ids=(who["ana"], 999)), lambda h: h.meeting_id == a)                    # qualquer uma das pessoas
    check(SearchFilters(date_from="2026-08-01", date_to="2026-09-01"), lambda h: h.meeting_id == b)    # inclusivo
    check(SearchFilters(date_from="2026-09-02"), lambda h: h.meeting_id == a)
    none = search.search(store, q, SearchFilters(meeting_ids=(9999,)), query_vector=qv)
    assert none.hits == () and none.hybrid                                    # nada passa no filtro: não é falta de vetor
    with pytest.raises(ValueError, match="AAAA-MM-DD"):
        SearchFilters(date_from="01/09/2026")
    assert not SearchFilters().active and SearchFilters(meeting_ids=(a,)).active
    # R6-04: trechos nos filtros, nenhum com vetor: "por palavras" com o motivo (não "híbrida" vazia)
    with store.transaction():
        store._x("DELETE FROM rag_vectors WHERE chunk_id IN (SELECT id FROM rag_chunks WHERE meeting_id = ?)", (b,))
    only_b = search.search(store, "campanha redes sociais", SearchFilters(meeting_ids=(b,)), query_vector=_qv("campanha redes sociais"))
    assert not only_b.hybrid and only_b.reason == search.FILTERED_NO_VECTORS and only_b.hits
    assert all(h.vec_rank is None for h in only_b.hits) and {h.meeting_id for h in only_b.hits} == {b}


def test_words_only_without_query_vector_or_without_vectors_of_the_active_model(store, monkeypatch):
    who = _people(store)
    mid, _job = _meeting(store, who)
    index.sync_text(store, mid)
    no_key = search.embed_query(store, "auditoria")
    assert no_key.vector is None and no_key.reason.startswith("sem chave do provedor gemini")
    res = search.search(store, "auditoria", query_vector=no_key)
    assert not res.hybrid and res.reason == no_key.reason and res.hits
    _ready(store)
    monkeypatch.setenv("LISTENER_LLM", "fake")
    empty = search.embed_query(store, "auditoria")                         # chave, mas índice sem vetores: nem chama a rede (R6-06)
    assert empty.vector is None and empty.reason == search.NO_VECTORS.format(model=MODEL) and store._q("SELECT id FROM llm_calls") == []
    res = search.search(store, "auditoria", query_vector=empty)
    assert not res.hybrid and res.reason == empty.reason and res.hits
    res = search.search(store, "auditoria", query_vector=_qv("auditoria"))                        # vetor de fora, índice vazio
    assert not res.hybrid and res.reason == search.NO_VECTORS.format(model=MODEL) and res.missing_vectors == 5 and res.hits
    index.fill_vectors(store, mid, provider=FakeProvider())
    qv = search.embed_query(store, "auditoria")
    assert qv.vector is not None and not qv.cached and search.search(store, "auditoria", query_vector=qv).hybrid
    other = QueryVector(qv.vector, "openai/text-embedding-3-small@768")
    assert search.search(store, "auditoria", query_vector=other).reason == "o modelo de embeddings mudou: busque de novo"
    assert search.search(store, "auditoria", query_vector=qv.vector).hybrid                       # o vetor puro vale do modelo ativo
    small = search.search(store, "auditoria", query_vector=QueryVector(np.ones(8, np.float32), MODEL))
    assert not small.hybrid and small.reason == "vetor da consulta com 8 dimensões e índice com 768" and small.hits


def test_signature_cache_mu_change_new_vector_from_another_process_deleted_chunk_and_reused_id(store):
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    _indexed(store, mid)
    cache = SignatureCache(MODEL, batch=2)                                                        # carga em lotes de 2
    view = cache.view(store)
    assert (len(view.vids), cache.full_loads, cache.appends) == (5, 1, 0) and view.words.shape == (5, 12)     # 768 bits
    assert cache.view(store).vids is view.vids and cache.full_loads == 1                          # mesma versão: nada lido
    # vetor novo gravado por outro processo (o worker leve): entra sem recarga total
    other = Store(store.base_dir, role=StoreRole.WORKER)
    try:
        silent = other.create_meeting("Sem fala", None, None, None)
        other.finish_meeting(silent, 1000)
        _pipeline_job(other, silent)
        _indexed(other, silent)
    finally:
        other.close()
    view = cache.view(store)
    assert (len(view.vids), cache.full_loads, cache.appends) == (6, 1, 1)
    assert set(view.chunk_ids.tolist()) == {c["id"] for c in store.rag.list_chunks(mid) + store.rag.list_chunks(silent)}
    # o μ refeito muda todas as assinaturas: recarga total, com o μ novo
    model = store.rag.get_model(MODEL)
    store.rag.set_model(MODEL, 768, np.asarray(model["mu"]) * 0.5, 99)
    view = cache.view(store)
    assert cache.full_loads == 2 and np.allclose(view.mu, np.asarray(model["mu"]) * 0.5)
    # trecho apagado: o vetor sai pela cascata e some do cache
    gone = int(store.rag.list_chunks(mid)[0]["id"])
    with store.transaction():
        store.rag.delete_chunks([gone])
    view = cache.view(store)
    assert cache.full_loads == 3 and gone not in view.chunk_ids.tolist() and len(view.vids) == 5
    # o maior id sai e entra outro vetor: com o AUTOINCREMENT da v8 (R5-03) o id não se repete, a versão muda pelo maior id
    # e a contagem não fecha com o que entrou acima do maior carregado: recarga total (sem depender do relógio, R6-10)
    before = cache.version
    top_vid = int(view.vids.max())
    with store.transaction():
        store.rag.delete_chunks([int(view.chunk_ids[int(np.argmax(view.vids))])])
    store._x("INSERT INTO rag_chunks(meeting_id, kind, title, speakers, person_ids, text, text_hash, created_at) "
             "VALUES (?, 'ata', 'Sem fala', '', '[]', 'um texto bem diferente', 'h-novo', '2026-10-25T10:00:00.000-03:00')", (silent,))
    fresh = int(store._q("SELECT MAX(id) FROM rag_chunks")[0][0])
    index.fill_vectors(store, silent, provider=FakeProvider())
    fresh_vid = int(store._q("SELECT id FROM rag_vectors WHERE chunk_id = ?", (fresh,))[0][0])
    after = store.rag.vector_version(MODEL)
    assert fresh_vid > top_vid and after.size == before.size and after.max_id == fresh_vid      # id novo, nunca o reaproveitado
    view = cache.view(store)
    _vids, _chunks, sigs = store.rag.signatures(MODEL)
    assert cache.full_loads == 4 and fresh in view.chunk_ids.tolist() and np.array_equal(view.words, vectors.as_words(sigs))
    assert top_vid not in view.vids.tolist() and cache.version == after


def test_embed_query_caches_records_calls_without_meeting_and_falls_back(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    mid, _job = _meeting(store, _people(store))
    _ready(store)
    _indexed(store, mid)                                                                         # com vetor nenhum, nem pede
    qv = search.embed_query(store, "  Orçamento   de 2027 ")
    assert qv.vector is not None and qv.vector.shape == (768,) and abs(float(np.linalg.norm(qv.vector)) - 1.0) < 1e-5
    assert (qv.model_key, qv.cached, qv.reason) == (MODEL, False, "") and qv.cost_usd is not None and qv.cost_usd > 0
    calls = store._q("SELECT meeting_id, job_id, task, status, strategy, provider, model, cost_usd FROM llm_calls WHERE meeting_id IS NULL")
    assert [tuple(c) for c in calls] == [(None, None, "rag_embed", "ok", "query", "gemini", "gemini-embedding-001", qv.cost_usd)]
    again = search.embed_query(store, "Orçamento de 2027")                                       # mesmo texto (espaços à parte)
    assert again.cached and again.cost_usd == 0.0 and np.array_equal(again.vector, qv.vector)
    assert len(store._q("SELECT id FROM llm_calls WHERE meeting_id IS NULL")) == 1              # do cache: nenhuma chamada
    with pytest.raises(ValueError):
        again.vector[0] = 1.0                                                                    # o cache não muda por fora
    fake = FakeProvider()
    fake.fail_next(TransientError("rede: caiu"), LlmTask.RAG_EMBED)
    failed = search.embed_query(store, "outra consulta", provider=fake)
    assert failed.vector is None and failed.reason == "sem rede (caiu)"
    row = store._q("SELECT meeting_id, task, status, error FROM llm_calls ORDER BY id DESC LIMIT 1")[0]
    assert tuple(row) == (None, "rag_embed", "erro", "sem rede (caiu)")
    monkeypatch.setattr(search, "QUERY_TIMEOUT_S", 0.01)
    slow = search.embed_query(store, "consulta lenta", provider=FakeProvider(delay_s=0.3))
    assert slow.vector is None and slow.reason == "tempo esgotado (0.01 s)"
    assert search.embed_query(store, "   ").reason == "consulta vazia"
    store.providers.update_provider("gemini", api_key="")
    assert search.embed_query(store, "Orçamento de 2027").reason.startswith("sem chave")         # sem embedder: nem o cache


def _hit(cid: int, mid: int) -> SearchHit:
    return SearchHit(cid, mid, f"r{mid}", "2026-10-25", "transcricao", 0, 1, "", (), "", "", cid, None, 1.0 / cid)


def test_group_by_meeting_and_limit_per_meeting():
    hits = [_hit(1, 10), _hit(2, 20), _hit(3, 10), _hit(4, 10), _hit(5, 30), _hit(6, 10), _hit(7, 20)]
    groups = search.group_by_meeting(hits)
    assert [(g.meeting_id, [h.chunk_id for h in g.hits]) for g in groups] == [(10, [1, 3, 4, 6]), (20, [2, 7]), (30, [5])]
    assert [h.chunk_id for h in search.limit_per_meeting(hits, 2, 10)] == [1, 2, 3, 5, 7]
    assert [h.chunk_id for h in search.limit_per_meeting(hits, 3, 4)] == [1, 2, 3, 4]
    assert [h.chunk_id for h in search.limit_per_meeting(hits, 1, 12)] == [1, 2, 5]


def test_rag_settings_and_the_auto_index_switch(qtbot, qapp, platform, store, monkeypatch):
    """`rag.auto_index` = vetores automáticos (decisão da revisão do L0 a L2): desligado, o `index` entra do mesmo jeito e
    grava trechos e FTS; só os vetores ficam de fora, com a nota, salvo o "reindexar" (forçado)."""
    from listener.pipeline.index_steps import IndexJobRunner
    from listener.ui.controller import Controller

    specs = defaults.SPECS
    assert defaults.RAG_KEYS == ("rag.auto_index", "rag.top_k", "rag.max_per_meeting")
    assert (specs["rag.auto_index"].default, specs["rag.top_k"].default, specs["rag.max_per_meeting"].default) == (True, 12, 3)
    assert all(specs[k].phase == "F5" for k in defaults.RAG_KEYS)
    assert specs["rag.top_k"].error("3") and specs["rag.top_k"].error("31") and specs["rag.top_k"].error("30") is None
    per = specs["rag.max_per_meeting"]
    assert per.error("0") and per.error("11") and per.error("1") is None
    assert "Vetores automáticos" in specs["rag.auto_index"].label and "custo" in specs["rag.auto_index"].label
    who = _people(store)
    mid, _job = _meeting(store, who)
    _ready(store)
    store.set_setting("rag.auto_index", False)

    def run_index(job_id: int) -> dict[str, Any]:
        job = store.jobs.claim(pid=3, kinds=("llm", "index"))
        assert job is not None and int(job["id"]) == job_id
        IndexJobRunner(store, job, provider=FakeProvider(), heartbeat_thread=False).run()
        return json.loads(store.jobs.get(job_id)["timings_json"])["index"]

    job = index.enqueue_index(store, mid, "pipeline")                                             # entra mesmo desligado
    info = run_index(job)
    assert info["chunks"] == 5 and info["vectors"] == 0 and info["note"] == "5 trechos sem vetor: vetores automáticos desligados"
    assert store.rag.count_chunks(mid) == 5 and store.llm_calls.list_for_meeting(mid) == []       # o FTS foi; vetor, não
    assert index.sweep(store) == 0                                                                # sem vetores automáticos
    info = run_index(index.enqueue_index(store, mid, "reindex", force=True))                       # o pedido do usuário pede
    assert info["vectors"] == 5 and info["note"] == ""
    c = Controller(qapp, platform, store)
    c.tray = None
    launched = _launch_spy(c, monkeypatch)
    try:
        c.on_edited(mid, "review", now=True)
        assert launched == [True] and store.jobs.has_pending(kinds=("index",))                    # edição: fila e worker
    finally:
        c.emergency_stop()


def test_fmt_usd_shows_four_places_below_one_cent():
    assert costs.fmt_usd(0.0031) == "US$ 0,0031" and costs.fmt_usd(0.00004) == "US$ 0,0000"
    assert costs.fmt_usd(0.3249) == "US$ 0,32" and costs.fmt_usd(0.01) == "US$ 0,01" and costs.fmt_usd(12.5) == "US$ 12,50"
    assert costs.fmt_usd(0) == "US$ 0,00" and costs.fmt_usd(None) == "sem preço cadastrado"
