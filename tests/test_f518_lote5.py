"""F5.18, lote 5 (polimento): o FIR do `StreamResampler` desenhado uma vez, com a saída bit a bit igual à do `resample_poly`
padrão (PF-23); `cache_size` e `mmap_size` só no main das conexões em escrita, nunca nos arquivos de áudio (PF-26); os padrões
da redação em cache até `add`/`clear`, o `IN` do `gather` em lotes e um `SSLContext` por configuração de rede (PF-28); o título
fora do hash e do embedding dos trechos de transcrição, com a migração do hash no lugar e nenhum vetor refeito (PF-29)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import ssl
from typing import Any

import numpy as np
import pytest
import scipy.signal

from listener.capture import audio
from listener.llm import adapters_common as common
from listener.llm import redact
from listener.llm.fake import FakeProvider
from listener.llm.registry import NetworkConfig
from listener.model import ChunkKind, SummaryKind
from listener.rag import chunk_hash, chunker, index
from listener.store import Store
from listener.store.core import AUDIO_MMAP_BYTES, MAIN_CACHE_KIB, MAIN_MMAP_BYTES, schema_for
from listener.store.rag import ChunkMeta
from listener.store.segments import SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import MODEL, _meeting, _people, _pipeline_job, _ready, _vector_ids

sdk_tests = pytest.mark.skipif(not os.environ.get("LISTENER_SDK_TESTS"), reason="LISTENER_SDK_TESTS=1 liga os testes com os SDKs reais")


# ---------------------------------------------------------------- PF-23: o FIR do `StreamResampler`
@pytest.mark.parametrize(("sr_from", "sr_to"), [(48000, 16000), (44100, 16000), (16000, 44100)])
def test_stream_resampler_fir_designed_once_gives_the_default_output_bit_by_bit(sr_from, sr_to, monkeypatch):
    """A cada bloco de 100 ms (as taxas da captura e a do player), a saída com o filtro pré-desenhado em `window=` é bit a bit a do
    `resample_poly` padrão, que desenha o filtro de novo a cada chamada; o conjunto segue igual ao sinal inteiro."""
    real = scipy.signal.resample_poly
    r = audio.StreamResampler(sr_from, sr_to)
    rng = np.random.default_rng(7)
    x = (0.5 * rng.standard_normal(sr_from * 2)).astype(np.float32)
    whole = real(x, r.up, r.down).astype(np.float32)
    calls: list[int] = []

    def checked(buf: np.ndarray, up: int, down: int, *a: Any, **kw: Any) -> np.ndarray:
        assert kw["window"] is r._h and kw["window"] is audio.poly_fir(up, down) and not kw["window"].flags.writeable
        fast = real(buf, up, down, *a, **kw)
        default = real(buf, up, down)
        assert fast.dtype == default.dtype == np.float32 and np.array_equal(fast, default)
        calls.append(len(buf))
        return fast

    monkeypatch.setattr(scipy.signal, "resample_poly", checked)
    block = sr_from // 10
    parts = [r.process(x[i:i + block]) for i in range(0, len(x), block)]
    parts.append(r.flush())
    y = np.concatenate(parts)
    assert len(calls) == 21 and abs(len(y) - len(whole)) <= 1
    n = min(len(y), len(whole))
    assert np.max(np.abs(y[:n] - whole[:n])) < 1e-6 and r.pending_in == 0
    assert len(r._h) == 2 * 10 * max(r.up, r.down) + 1 and r._h.dtype == np.float32


def test_a_resampler_between_equal_rates_has_no_filter_and_is_the_identity():
    """R-01 da revisão do lote 5: o `firwin` recusa um corte de 1,0 (taxas iguais reduzem a 1/1); sem filtro, o `resample_poly`
    devolve a cópia, como antes do PF-23. Nenhum chamador o faz hoje (todos guardam com `!=`), mas o construtor não pode cair."""
    r = audio.StreamResampler(16000, 16000)
    x = np.random.default_rng(3).standard_normal(3200).astype(np.float32)
    y = np.concatenate([r.process(x[:1600]), r.process(x[1600:]), r.flush()])
    assert r._h is None and r.pending_in == 0 and np.array_equal(y, x)


# ---------------------------------------------------------------- PF-26: os pragmas do main
def test_main_pragmas_only_in_writing_roles_and_never_on_the_audio_files(store):
    """APP e WORKER com `cache_size` e `mmap_size` no main; o arquivo de áudio anexado fica fora (no app um mapa dele impediria o
    encolhimento da F5.6; o worker o mapeia por conta própria em `attach`); o READONLY (`--check`) nos padrões do SQLite."""
    raw = sqlite3.connect(store.base_dir / "padroes.db")
    cache_default, mmap_default = raw.execute("PRAGMA cache_size").fetchone()[0], raw.execute("PRAGMA mmap_size").fetchone()[0]
    raw.close()
    assert cache_default != -MAIN_CACHE_KIB and mmap_default == 0
    current = schema_for(store.core.current_audio_file())
    with store.connection() as c:
        assert c.execute("PRAGMA main.cache_size").fetchone()[0] == -MAIN_CACHE_KIB
        assert c.execute("PRAGMA main.mmap_size").fetchone()[0] == MAIN_MMAP_BYTES
        assert c.execute(f"PRAGMA {current}.mmap_size").fetchone()[0] == 0                      # o áudio do app sem mapa
        assert c.execute(f"PRAGMA {current}.cache_size").fetchone()[0] == cache_default
    worker = Store.open_worker(store.base_dir)
    try:
        with worker.connection() as c:
            assert c.execute("PRAGMA main.cache_size").fetchone()[0] == -MAIN_CACHE_KIB
            assert c.execute("PRAGMA main.mmap_size").fetchone()[0] == MAIN_MMAP_BYTES
            assert c.execute(f"PRAGMA {current}.mmap_size").fetchone()[0] == AUDIO_MMAP_BYTES   # como antes (F2)
    finally:
        worker.close()
    check = Store.open_readonly(store.base_dir)
    try:
        with check.connection() as c:
            assert c.execute("PRAGMA main.cache_size").fetchone()[0] == cache_default
            assert c.execute("PRAGMA main.mmap_size").fetchone()[0] == mmap_default
    finally:
        check.close()


# ---------------------------------------------------------------- PF-28: a redação, o `gather` e o contexto TLS
def test_redactor_caches_the_patterns_until_a_secret_is_added_or_cleared():
    r = redact.Redactor()
    assert r.patterns() == () and r.patterns() is r.patterns()
    key = "AIzaSy-chave-de-teste-ABCDEFGH12345678"
    r.add(key)
    first = r.patterns()
    assert first == (key, "12345678") and r.patterns() is first
    r.add("curta", None, key)                                                    # nada novo: o cache fica
    assert r.patterns() is first
    other = "sk-outra-chave-secreta-WXYZ98765"
    r.add(other)
    second = r.patterns()
    assert second is not first and second[:2] == (key, other) and set(second[2:]) == {"12345678", "XYZ98765"}
    assert r.redact(f"a {other} b ...XYZ98765 c {key}") == "a «chave» b ...«chave» c «chave»"
    r.clear()
    assert r.patterns() == () and r.redact(f"x {key}") == f"x {key}"
    filt = redact.RedactingFilter(r)                                             # a chave acrescentada depois vale no registro seguinte
    new = "sk-nova-chave-ABCDEFGH-123456"

    def record() -> logging.LogRecord:
        return logging.LogRecord("t", logging.INFO, __file__, 1, "chave %s", (new,), None)

    rec = record()
    assert filt.filter(rec) and rec.getMessage() == f"chave {new}"
    r.add(new)
    rec = record()
    assert filt.filter(rec) and rec.getMessage() == "chave «chave»"


def test_gather_reads_the_words_of_long_utterances_in_batches_of_500(store, monkeypatch):
    who = _people(store)
    mid = store.create_meeting("Longa", None, None, None)
    store.finish_meeting(mid, 3_600_000)
    job = _pipeline_job(store, mid)
    text = ("palavra " * 160).strip()                                            # 1.279 caracteres: fala longa (corte pelas palavras)
    assert len(text) > chunker.BODY_MAX - chunker.MIN_PIECE
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 3000, i * 3000 + 2900, text, "pyannote", "final", person_id=who["ana"],
                                                  words_json=json.dumps([[i * 3000, i * 3000 + 400, "palavra"]])) for i in range(1200)])
    sizes: list[int] = []
    real_q = store._q

    def spy(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if "FROM segments WHERE id IN" in sql:
            sizes.append(len(params))
        return real_q(sql, params)

    monkeypatch.setattr(store, "_q", spy)
    data = index.gather(store, mid)
    assert data is not None and len(data.utterances) == 1200 and sizes == [500, 500, 200]
    assert all(u.words == ((i * 3000, i * 3000 + 400, "palavra"),) for i, u in enumerate(data.utterances))


def test_shared_ssl_context_is_one_per_network_configuration(tmp_path):
    certifi = pytest.importorskip("certifi")
    pem = certifi.where()
    other = tmp_path / "outro.pem"
    shutil.copyfile(pem, other)
    net = NetworkConfig(proxy_url="http://proxy.local:3128", ca_bundle=pem, system_certs=False)
    ctx = common.shared_ssl_context(net)
    assert isinstance(ctx, ssl.SSLContext) and common.shared_ssl_context(net) is ctx
    assert common.shared_ssl_context(NetworkConfig(net.proxy_url, pem, False, timeout_s=9.0)) is ctx        # o timeout não molda o contexto
    assert common.shared_ssl_context(NetworkConfig(ca_bundle=pem, system_certs=False)) is ctx                 # o proxy não conta (R-04)
    assert common.shared_ssl_context(NetworkConfig(proxy_url=net.proxy_url, ca_bundle=str(other), system_certs=False)) is not ctx
    assert common.shared_ssl_context(NetworkConfig(proxy_url=net.proxy_url, ca_bundle=pem, system_certs=True)) is not ctx
    assert common.http_client_kwargs(net, 30.0)["verify"] is ctx and common.httpx2_client_kwargs(net, 5.0)["verify"] is ctx
    assert "verify" not in common.http_client_kwargs(NetworkConfig(proxy_url="http://p:1"), 5.0)                 # sem PEM, o do cliente
    missing = NetworkConfig(ca_bundle=str(tmp_path / "nao-existe.pem"), system_certs=False)
    for _ in range(2):                                                                                         # o erro não fica em cache
        with pytest.raises(FileNotFoundError):
            common.shared_ssl_context(missing)


@sdk_tests
def test_two_clients_with_the_same_network_configuration_share_the_tls_context(monkeypatch):
    certifi = pytest.importorskip("certifi")
    httpx2 = pytest.importorskip("httpx2")
    pytest.importorskip("openai")
    from listener.llm.openai import OpenAIProvider

    seen: list[dict[str, Any]] = []
    real = httpx2.AsyncClient

    def spying_init(self: Any, **kw: Any) -> None:
        seen.append(kw)
        real.__init__(self, **kw)

    monkeypatch.setattr(httpx2, "AsyncClient", type("Spy", (real,), {"__init__": spying_init}))   # o SDK confere `isinstance`
    net = NetworkConfig(proxy_url="http://proxy.local:3128", ca_bundle=certifi.where(), system_certs=False)

    async def main() -> None:
        providers = [OpenAIProvider("sk-teste", net=net) for _ in range(2)]
        for p in providers:
            assert p._client() is not None
        for p in providers:
            await p.aclose()

    asyncio.run(main())
    assert len(seen) == 2 and seen[0]["verify"] is seen[1]["verify"] is common.shared_ssl_context(net)


# ---------------------------------------------------------------- PF-29: o título fora do hash e do embedding da transcrição
def test_the_title_is_out_of_the_hash_and_the_embedding_of_transcript_chunks_only():
    d, s, t = "2026-10-25", "Ana — CFO, Bia", "Ana — CFO: bom dia\nBia: olá"
    assert chunker.embed_input("transcricao", "Orçamento 2027", d, "corpo") == "2026-10-25\ncorpo"
    assert chunker.embed_input(ChunkKind.TRANSCRIPT, "Orçamento 2027", d, "corpo") == "2026-10-25\ncorpo"
    assert chunker.embed_input("ata", "Orçamento 2027", d, "corpo") == "Orçamento 2027 · 2026-10-25\ncorpo"
    assert chunker.embed_input(ChunkKind.MEETING, "", d, "corpo") == "2026-10-25\ncorpo"
    new = chunk_hash.text_hash("transcricao", "Orçamento 2027", d, s, t)
    assert new == chunk_hash.text_hash(ChunkKind.TRANSCRIPT, "Outro", d, s, t) == chunk_hash.legacy_text_hash("transcricao", "", d, s, t)
    assert new != chunk_hash.legacy_text_hash("transcricao", "Orçamento 2027", d, s, t)
    assert new != chunk_hash.text_hash("transcricao", "Orçamento 2027", d, "Ana — CFO", t)             # os falantes ficam no hash
    for kind in (k for k in ChunkKind if k != ChunkKind.TRANSCRIPT):
        assert chunk_hash.text_hash(kind, "A", d, s, t) == chunk_hash.legacy_text_hash(kind, "A", d, s, t)
        assert chunk_hash.text_hash(kind, "A", d, s, t) != chunk_hash.text_hash(kind, "B", d, s, t)
    assert chunker.text_hash is chunk_hash.text_hash                                                   # o chunker usa a mesma função


def _legacy_hashes(store: Store, mid: int, date: str) -> None:
    """Regrava os trechos de transcrição com o hash de antes da F5.18 (o título do trecho dentro), como num banco gravado antes."""
    with store.transaction():
        for r in store.rag.list_chunks(mid):
            if r["kind"] == ChunkKind.TRANSCRIPT:
                digest = chunk_hash.legacy_text_hash(r["kind"], str(r["title"] or ""), date, str(r["speakers"] or ""), str(r["text"]))
                store._x("UPDATE rag_chunks SET text_hash = ? WHERE id = ?", (digest, int(r["id"])))


def _transcript_vectors(store: Store, mid: int) -> dict[int, int]:
    ids = {int(r["id"]) for r in store.rag.list_chunks(mid) if r["kind"] == ChunkKind.TRANSCRIPT}
    return {cid: vid for cid, vid in _vector_ids(store).items() if cid in ids}


def test_sync_text_migrates_legacy_hashes_in_place_and_renaming_keeps_the_transcript_vectors(store):
    who = _people(store)
    mid, job = _meeting(store, who)                                                               # "Orçamento 2027"
    _ready(store)
    date = str(store.get_meeting(mid)["started_at"])[:10]
    store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar o orçamento.\n", "gemini", "m",
                                         "minutes/1.0", job, None, "pt-BR"))
    store.summaries.insert(SummaryInsert(mid, SummaryKind.EXECUTIVE, "{}", "Resumo executivo curto.", "gemini", "m", "exec/1.0", job,
                                         None, "pt-BR"))
    assert index.sync_text(store, mid).total == 7                                       # 4 de transcrição, decisão, executivo, reunião
    _legacy_hashes(store, mid, date)
    legacy = {int(r["id"]): (str(r["kind"]), str(r["text_hash"])) for r in store.rag.list_chunks(mid)}
    assert index.fill_vectors(store, mid, provider=FakeProvider()).written == 7                   # os vetores conferem o hash gravado
    vectors_before = _vector_ids(store)
    version = store.rag.index_version()
    migrated = index.sync_text(store, mid)
    assert migrated == index.SyncResult(7, 7, 0, 0, 4)                                            # os 4 de transcrição regravados
    after = {int(r["id"]): (str(r["kind"]), str(r["text_hash"])) for r in store.rag.list_chunks(mid)}
    assert set(after) == set(legacy) and _vector_ids(store) == vectors_before and store.rag.count_missing(mid, MODEL) == 0
    for cid, (kind, digest) in legacy.items():
        assert (after[cid][1] != digest) == (kind == ChunkKind.TRANSCRIPT)
    assert store.rag.index_version() == version                                       # hash migrado, pessoas iguais: não sobe (R-03)
    assert index.sync_text(store, mid) == index.SyncResult(7, 7, 0, 0, 0)                         # migrado: nada a regravar
    # renomear: a transcrição fica (vetores e ids), com o título novo na coluna e no FTS; decisão, executivo e reunião saem e entram
    transcript = _transcript_vectors(store, mid)
    store.update_title(mid, "Orçamento final")
    renamed = index.sync_text(store, mid)
    assert (renamed.kept, renamed.added, renamed.removed, renamed.moved) == (4, 3, 3, 4)
    rows = store.rag.list_chunks(mid)
    assert all(r["title"] == "Orçamento final" for r in rows) and _transcript_vectors(store, mid) == transcript
    assert store.rag.count_missing(mid, MODEL) == 3 and store.rag.count_vectors(MODEL) == 4
    with store.connection() as c:
        hits = {r[0] for r in c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ("title:final",))}
    assert hits == {int(r["id"]) for r in rows}                                                   # o FTS com o título novo em todos
    # gravado com o hash legado e renomeado antes da migração: casa pelo título guardado no próprio trecho
    _legacy_hashes(store, mid, date)
    store.update_title(mid, "Orçamento 2028")
    again = index.sync_text(store, mid)
    assert (again.kept, again.added, again.removed, again.moved) == (4, 3, 3, 4)
    assert _transcript_vectors(store, mid) == transcript and all(r["title"] == "Orçamento 2028" for r in store.rag.list_chunks(mid))
    assert {r["kind"] for r in store.rag.list_chunks(mid)} == {"transcricao", "decisao", "executivo", "reuniao"}
    assert index.sync_text(store, mid) == index.SyncResult(7, 7, 0, 0, 0)


def test_update_meta_bumps_the_vector_version_only_when_the_people_of_a_kept_chunk_change(store):
    """R-03 da revisão do lote 5: cada subida da versão relê as assinaturas na busca (M-09: 217 ms com 60 mil vetores); a migração
    do hash, os tempos e o título novo não mudam o que a busca guarda, só as pessoas de um trecho mantido."""
    who = _people(store)
    mid, job = _meeting(store, who)
    _ready(store)
    index.sync_text(store, mid)
    row = store.rag.list_chunks(mid)[0]
    cid, current = int(row["id"]), tuple(json.loads(row["person_ids"] or "[]"))
    changed = () if current else (who["ana"],)
    version = store.rag.index_version()
    with store.transaction():
        store.rag.update_meta([ChunkMeta(cid, 0, 1, None, None, changed, job, text_hash="0" * 64, title="Outro título")])
    assert store.rag.index_version() == version + 1                                               # as pessoas mudaram
    with store.transaction():
        store.rag.update_meta([ChunkMeta(cid, 5, 9, None, None, changed, job, text_hash="1" * 64, title="Mais outro")])
    assert store.rag.index_version() == version + 1                           # tempos, hash e título novos, pessoas iguais: não sobe
