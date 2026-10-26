"""F5 L0 — medição do índice e da busca (autorizada na aprovação da F5, D-047; spec specs/features/F5-rag-historico.md).

Roda no venv do projeto (Linux, WSL ou Windows). `fts` e `sig` não importam nada do produto: recriam aqui a tabela, o FTS5,
os gatilhos e a assinatura que a migração v8 e `listener/rag/vectors.py` usam. `gemini` usa o adaptador do projeto
(`listener.llm.registry.load_provider`), porque o que se mede é o caminho do app.

  python scripts/rag_probe.py fts [N] [--repeat 20]            FTS5 com N trechos sintéticos (padrão 60 mil)
  python scripts/rag_probe.py sig [N] [DIM] [--queries 200]    assinatura binária com N vetores (padrão 200 mil; DIM 384 e 768)
  python scripts/rag_probe.py gemini [--batches 3] [--text ARQ]  embeddings reais (só com GEMINI_API_KEY no ambiente; B13)
  python scripts/rag_probe.py e5 [--passages 192] [--batch 32]  e5-small int8 contra fp32 (F5.1 L0; revisão fixada, sha256)

Cada medida imprime uma linha JSON; os números vão para specs/decisoes.md (Medições) e para a D-048.

fts     confere o FTS5 do SQLite do Python com `tokenize='unicode61 remove_diacritics 2'` (imprime `sqlite_version`); num
        arquivo temporário (WAL, como o app), cria a `rag_chunks` da v8, o `rag_fts` com conteúdo externo, os três gatilhos e
        o `rank` com `bm25(3, 2, 1)` (título, falantes, texto); insere N trechos de ~1.400 caracteres com vocabulário pt-BR em
        distribuição de Zipf, 45 por transação (uma reunião de ~1 h, como o `sync_text`), e mede: ms por trecho para
        indexar; consulta `ORDER BY rank LIMIT 50` com `snippet()` para um termo muito frequente, um médio e um raro, um E
        de dois termos com `*` no último e um OU (média e p95 de --repeat repetições); o mesmo com o JOIN em `rag_chunks`
        que a busca vai usar; apagar uma reunião (45 trechos, gatilho de remoção); `integrity-check`; "orcamento" acha
        "orçamento".
sig     vetores sintéticos anisotrópicos e normalizados: direção comum (cosseno médio ~0,75 entre textos, como o e5 e o
        Gemini) mais um resíduo com espectro em lei de potência (vizinhança com estrutura, não ruído puro); consultas
        "novas" (do mesmo gerador) e "paráfrases" (um vetor da base com ruído). Assinatura = bits do sinal de (v - mu)
        (`np.packbits`, mu dos 2 mil primeiros, como o app congela), Hamming com `np.bitwise_count` e `argpartition`,
        max(200, 1 % de n) candidatos (até 2 mil) reordenados pelo cosseno exato; recall@10 contra o exato, centralizando
        e sem centralizar, com a reordenação em float32 e em float16; MB das assinaturas; ms por consulta (uma thread);
        e, com --sqlite (padrão 50 mil linhas, 768), o tempo de carregar as assinaturas do SQLite e de ler os float32 dos
        candidatos por id.
gemini  lotes de 64 trechos de ~1.400 caracteres no `gemini-embedding-001` em 768 (RETRIEVAL_DOCUMENT): latência por lote,
        dimensão, norma, tokens estimados e o cosseno médio entre os textos (confere a premissa de anisotropia). Sem
        `GEMINI_API_KEY`, imprime "pulado: sem chave". --text ARQ usa um texto real (transcrição exportada) cortado em
        trechos; sem ele, os trechos sintéticos do `fts`.
e5      F5.1 L0 (D-050): `intfloat/multilingual-e5-small` na revisão fixada, conferido pelo sha256; o int8
        (`model_qint8_avx512_vnni.onnx`, 118 MB) vai para a pasta de modelos do app (`multilingual-e5-small/`, a mesma da
        F5.1) e o fp32 (470 MB) para uma pasta temporária, só para comparar. Trechos de ~1.400 caracteres com frases de
        reunião em português e perguntas curtas, prefixos `passage:` e `query:`, corte em 512 tokens, pooling médio com a
        máscara e L2: carga, ms por trecho com 1 e 2 threads, cortes, cosseno int8 contra fp32 (meta >= 0,99, o risco do
        int8 quantizado para AVX-512 numa CPU sem ele) e recall@10 do int8 com o fp32 como verdade.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

CHUNK_CHARS = 1400
CHUNKS_PER_MEETING = 45                  # ~1 h de reunião em trechos de 60 a 120 s
VOCAB_SIZE = 30_000
ZIPF_S = 1.0
FTS_WEIGHTS = (3.0, 2.0, 1.0)            # título, falantes, texto (o `rank` da v8)
MU_SAMPLE = 2000                         # o app congela o mu em 2 mil vetores
EMBED_BATCH = 64

def _words(text: str) -> tuple[str, ...]:
    return tuple(text.split())


# palavras reais (com acento) nas primeiras posições do Zipf; o resto do vocabulário é sintético (sílabas)
COMMON = _words("de que não o a e é do da em um para com uma os no se na por mais as dos como mas foi ao ele das tem à seu sua ou "
          "ser quando muito há nos já está eu também só pelo pela até isso ela entre era depois sem mesmo aos ter seus quem nas "
          "me esse eles estão você tinha foram essa num nem suas meu às minha têm numa pelos elas havia seja qual será nós "
          "tenho lhe deles essas esses pelas este fosse dele tu te vocês vos lhes meus minhas teu tua teus tuas nosso nossa "
          "nossos nossas dela delas esta estes estas aquele aquela aqueles aquelas isto aquilo estou está estamos estão")
CONTENT = _words("reunião projeto cliente prazo entrega orçamento decisão ação próximo semana equipe dados sistema contrato "
           "proposta relatório resultado meta mês trimestre receita custo risco fundo cota investidor auditoria parecer "
           "jurídico compliance crédito carteira cedente sacado duplicata recebível taxa juros spread caixa fluxo planilha "
           "apresentação diretoria conselho comitê aprovação revisão versão documento minuta assinatura cronograma tarefa "
           "pendência dúvida problema solução integração migração servidor banco aplicação usuário acesso senha segurança "
           "backup teste homologação produção implantação treinamento suporte chamado incidente melhoria indicador "
           "painel análise previsão cenário estratégia mercado concorrente parceria fornecedor compra venda pagamento "
           "boleto transferência conciliação extrato balanço demonstração contábil fiscal imposto tributo nota emissão "
           "registro cartório garantia aval fiança penhor alienação cessão securitização debênture emissor agente "
           "custodiante administrador gestor distribuidor regulamento assembleia quórum votação ata deliberação")
NAMES = ("Ana — CFO", "Bruno — CTO", "Carla — Diretora jurídica", "Diego — Analista", "Eduarda — Gerente de risco", "Felipe",
         "Gabriela — Controller", "Henrique — Auditor", "Isabela — Gestora", "João — Estagiário", "Karina", "Lucas — Contador")


def out(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def stats_ms(runs: list[float]) -> dict:
    s = sorted(runs)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    return {"mean_ms": round(statistics.fmean(s), 2), "p95_ms": round(p95, 2), "min_ms": round(s[0], 2), "n": len(s)}


def vocab_content() -> list[str]:
    common = set(COMMON)
    return [w for w in dict.fromkeys(CONTENT) if w not in common]


def vocabulary(rng) -> list[str]:
    """Palavras reais primeiro (as funcionais no topo do Zipf, depois as de conteúdo) e sílabas sintéticas até VOCAB_SIZE."""
    words = [*dict.fromkeys(COMMON), *vocab_content()]
    syl = ["ba", "be", "bi", "bo", "ca", "ce", "ci", "co", "da", "de", "di", "do", "fa", "fe", "ga", "go", "la", "le", "li", "lo",
           "ma", "me", "mi", "mo", "na", "ne", "ni", "no", "pa", "pe", "pi", "po", "ra", "re", "ri", "ro", "sa", "se", "si", "so",
           "ta", "te", "ti", "to", "va", "ve", "vi", "vo", "ção", "são", "ão", "ém", "ês", "ul", "ar", "er", "ir", "or"]
    seen = set(words)
    while len(words) < VOCAB_SIZE:
        w = "".join(rng.choice(syl, size=int(rng.integers(2, 5))))
        if w not in seen:
            seen.add(w)
            words.append(w)
    return words


def synthetic_chunks(n: int, seed: int = 7) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(título, falantes, texto) de ~1.400 caracteres com palavras em Zipf; devolve também o vocabulário por posição."""
    import numpy as np

    rng = np.random.default_rng(seed)
    vocab = vocabulary(rng)
    ranks = np.arange(1, len(vocab) + 1, dtype=np.float64)
    p = 1.0 / ranks ** ZIPF_S
    p /= p.sum()
    words_per_chunk = 245                 # ~1.400 caracteres com a pontuação
    tokens = rng.choice(len(vocab), size=(n, words_per_chunk), p=p)
    arr = np.array(vocab, dtype=object)
    out_rows: list[tuple[str, str, str]] = []
    title = ""
    first_content = len(dict.fromkeys(COMMON))
    n_content = len(vocab_content())
    for i in range(n):
        if i % CHUNKS_PER_MEETING == 0:
            title = "Reunião de " + " ".join(arr[rng.choice(n_content, 2) + first_content])
        ws = arr[tokens[i]]
        parts = []
        for j in range(0, words_per_chunk, 12):
            sent = " ".join(ws[j:j + 12])
            parts.append(sent[:1].upper() + sent[1:] + ".")
        text = " ".join(parts)[:CHUNK_CHARS]
        speakers = ", ".join(NAMES[k] for k in sorted(set(rng.choice(len(NAMES), 2).tolist())))
        out_rows.append((title, speakers, text))
    return out_rows, vocab


# ----------------------------------------------------------------------------- fts
V8_SQL = (
    "CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT)",
    ("CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
     " kind TEXT NOT NULL, t_start_ms INTEGER, t_end_ms INTEGER, seg_first INTEGER, seg_last INTEGER, title TEXT, speakers TEXT,"
     " person_ids TEXT, text TEXT NOT NULL, text_hash TEXT NOT NULL, job_id INTEGER, created_at TEXT NOT NULL)"),
    "CREATE INDEX idx_rag_chunks_meeting ON rag_chunks(meeting_id, t_start_ms)",
    ("CREATE VIRTUAL TABLE rag_fts USING fts5(title, speakers, text, content='rag_chunks', content_rowid='id',"
     " tokenize='unicode61 remove_diacritics 2')"),
    "INSERT INTO rag_fts(rag_fts, rank) VALUES ('rank', 'bm25(3.0, 2.0, 1.0)')",
    ("CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN"
     " INSERT INTO rag_fts(rowid, title, speakers, text) VALUES (new.id, new.title, new.speakers, new.text); END"),
    ("CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN"
     " INSERT INTO rag_fts(rag_fts, rowid, title, speakers, text) VALUES ('delete', old.id, old.title, old.speakers, old.text); END"),
    ("CREATE TRIGGER rag_chunks_au AFTER UPDATE OF title, speakers, text ON rag_chunks BEGIN"
     " INSERT INTO rag_fts(rag_fts, rowid, title, speakers, text) VALUES ('delete', old.id, old.title, old.speakers, old.text);"
     " INSERT INTO rag_fts(rowid, title, speakers, text) VALUES (new.id, new.title, new.speakers, new.text); END"),
)
SNIPPET = "snippet(rag_fts, 2, char(2), char(3), '…', 12)"


def fts5_available() -> tuple[bool, str]:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')")
        c.execute("INSERT INTO t VALUES ('orçamento aprovado')")
        hit = c.execute("SELECT count(*) FROM t WHERE t MATCH 'orcamento'").fetchone()[0]
        c.close()
    except sqlite3.Error as e:
        return False, str(e)
    return hit == 1, "ok" if hit == 1 else "remove_diacritics não casou"


def time_query(c: sqlite3.Connection, sql: str, params: tuple, repeat: int) -> tuple[dict, int]:
    runs: list[float] = []
    rows = 0
    for _ in range(repeat):
        t0 = time.perf_counter()
        rows = len(c.execute(sql, params).fetchall())
        runs.append((time.perf_counter() - t0) * 1000)
    return stats_ms(runs), rows


def cmd_fts(args) -> None:
    ok, why = fts5_available()
    out("fts5", sqlite_version=sqlite3.sqlite_version, python=sys.version.split()[0], fts5=ok, detail=why)
    if not ok:
        return
    n = args.n or 60_000
    t0 = time.perf_counter()
    rows, vocab = synthetic_chunks(n)
    out("fts_corpus", chunks=n, gen_s=round(time.perf_counter() - t0, 1), mean_chars=round(statistics.fmean(len(r[2]) for r in rows)),
        vocab=len(vocab))
    tmp = Path(tempfile.mkdtemp(prefix="rag-probe-"))
    path = tmp / "fts.db"
    c = sqlite3.connect(str(path), autocommit=True)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")
    c.execute("PRAGMA foreign_keys = ON")
    for sql in V8_SQL:
        c.execute(sql)
    meetings = (n + CHUNKS_PER_MEETING - 1) // CHUNKS_PER_MEETING
    c.executemany("INSERT INTO meetings(id, title) VALUES (?, ?)", [(m + 1, f"m{m}") for m in range(meetings)])
    sql = ("INSERT INTO rag_chunks(meeting_id, kind, t_start_ms, t_end_ms, title, speakers, person_ids, text, text_hash, job_id, "
           "created_at) VALUES (?, 'transcricao', ?, ?, ?, ?, '[]', ?, ?, 1, '2026-10-25T10:00:00.000-03:00')")
    per_meeting: list[float] = []
    t_all = time.perf_counter()
    for m in range(meetings):
        batch = rows[m * CHUNKS_PER_MEETING:(m + 1) * CHUNKS_PER_MEETING]
        t1 = time.perf_counter()
        c.execute("BEGIN IMMEDIATE")
        c.executemany(sql, [(m + 1, i * 80_000, (i + 1) * 80_000, t, s, x, f"{m}-{i}") for i, (t, s, x) in enumerate(batch)])
        c.execute("COMMIT")
        per_meeting.append((time.perf_counter() - t1) * 1000 / max(1, len(batch)))
    total_s = time.perf_counter() - t_all
    size = sum(f.stat().st_size for f in tmp.iterdir())
    k = max(1, len(per_meeting) // 20)
    out("fts_index", chunks=n, total_s=round(total_s, 1), ms_per_chunk=round(total_s * 1000 / n, 3),
        ms_per_chunk_first_5pct=round(statistics.fmean(per_meeting[:k]), 3), ms_per_chunk_last_5pct=round(statistics.fmean(per_meeting[-k:]), 3),
        db_mb=round(size / 1e6, 1))
    common = len(dict.fromkeys(COMMON))
    frequent = vocab[common]                          # a palavra de conteúdo mais frequente ("reunião", posição ~150)
    medium = vocab[common + 60]                       # posição ~210 do Zipf
    rare = vocab[20_000]
    terms = {"muito_frequente": vocab[2], "frequente": frequent, "medio": medium, "raro": rare}   # vocab[2] = "não": quase todo trecho
    plain = f"SELECT rowid, {SNIPPET} FROM rag_fts WHERE rag_fts MATCH ? ORDER BY rank LIMIT 50"  # noqa: S608
    joined = (f"SELECT c.id, c.meeting_id, c.t_start_ms, c.kind, {SNIPPET} FROM rag_fts JOIN rag_chunks c "  # noqa: S608
              "ON c.id = rag_fts.rowid WHERE rag_fts MATCH ? ORDER BY rank LIMIT 50")
    for label, term in terms.items():
        q = f'"{term}"'
        hits = c.execute("SELECT count(*) FROM rag_fts WHERE rag_fts MATCH ?", (q,)).fetchone()[0]
        s_plain, got = time_query(c, plain, (q,), args.repeat)
        s_join, _ = time_query(c, joined, (q,), args.repeat)
        out("fts_query", kind=label, term=term, matches=hits, returned=got, plain=s_plain, joined=s_join)
    q_and = f'"{frequent}" "{medium[:4]}"*'
    q_or = f'"{medium}" OR "{rare}"'
    for label, q in (("e_com_prefixo", q_and), ("ou", q_or)):
        hits = c.execute("SELECT count(*) FROM rag_fts WHERE rag_fts MATCH ?", (q,)).fetchone()[0]
        s_join, got = time_query(c, joined, (q,), args.repeat)
        out("fts_query", kind=label, query=q, matches=hits, returned=got, joined=s_join)
    with_acc = c.execute("SELECT count(*) FROM rag_fts WHERE rag_fts MATCH ?", ('"orçamento"',)).fetchone()[0]
    without = c.execute("SELECT count(*) FROM rag_fts WHERE rag_fts MATCH ?", ('"orcamento"',)).fetchone()[0]
    out("fts_diacritics", orcamento=without, orçamento=with_acc, ok=bool(without and without == with_acc))
    t1 = time.perf_counter()
    c.execute("BEGIN IMMEDIATE")
    deleted = c.execute("DELETE FROM meetings WHERE id = 1").rowcount
    c.execute("COMMIT")
    left = c.execute("SELECT count(*) FROM rag_chunks WHERE meeting_id = 1").fetchone()[0]
    out("fts_delete_meeting", meetings=deleted, chunks_left=left, ms=round((time.perf_counter() - t1) * 1000, 2))
    t1 = time.perf_counter()
    try:
        c.execute("INSERT INTO rag_fts(rag_fts, rank) VALUES ('integrity-check', 1)")
        integrity = "ok"
    except sqlite3.Error as e:
        integrity = str(e)
    out("fts_integrity", result=integrity, s=round(time.perf_counter() - t1, 2),
        docsize=c.execute("SELECT count(*) FROM rag_fts_docsize").fetchone()[0], chunks=c.execute("SELECT count(*) FROM rag_chunks").fetchone()[0])
    c.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


# ----------------------------------------------------------------------------- sig
def make_vectors(n: int, dim: int, seed: int, mean_cos: float = 0.75, decay: float = 1.0):
    """Base anisotrópica e normalizada: sqrt(mean_cos) * c + sqrt(1 - mean_cos) * r, com c comum a todos e r unitário de
    espectro k^-decay numa base ortonormal aleatória (a vizinhança tem estrutura). Devolve (vecs, gerador de resíduos)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    c = rng.standard_normal(dim).astype(np.float32)
    c /= np.linalg.norm(c)
    basis, _ = np.linalg.qr(rng.standard_normal((dim, dim)))
    scale = (np.arange(1, dim + 1, dtype=np.float64) ** (-decay / 2.0)).astype(np.float32)
    basis = (basis.astype(np.float32) * scale[None, :]).T              # linha k = direção k com o desvio dela

    def residual(m: int, rng2) -> np.ndarray:
        r = rng2.standard_normal((m, dim), dtype=np.float32) @ basis
        return r / np.linalg.norm(r, axis=1, keepdims=True)

    a, b = np.float32(np.sqrt(mean_cos)), np.float32(np.sqrt(1.0 - mean_cos))
    vecs = np.empty((n, dim), np.float32)
    for i in range(0, n, 20_000):
        m = min(20_000, n - i)
        vecs[i:i + m] = a * c + b * residual(m, rng)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs, (lambda m, rng2: a * c + b * residual(m, rng2))


def signatures(vecs, mu):
    import numpy as np

    bits = np.packbits((vecs - mu) > 0, axis=1)
    pad = (-bits.shape[1]) % 8
    if pad:
        bits = np.pad(bits, ((0, 0), (0, pad)))
    return np.ascontiguousarray(bits).view(np.uint64)


def n_candidates(n: int) -> int:
    return min(n, 2000, max(200, n // 100))


def search(vecs, sigs, mu, q, k: int, rerank_base=None):
    """Hamming nas assinaturas, `argpartition` dos candidatos e cosseno exato neles (rerank_base: a base usada na reordenação)."""
    import numpy as np

    qs = signatures(q[None, :], mu)[0]
    dist = np.bitwise_count(sigs ^ qs).sum(axis=1, dtype=np.uint16)
    m = n_candidates(len(vecs))
    cand = np.argpartition(dist, m - 1)[:m]
    base = vecs if rerank_base is None else rerank_base
    scores = base[cand].astype(np.float32) @ q
    top = np.argpartition(-scores, k - 1)[:k]
    return cand[top[np.argsort(-scores[top])]]


def cmd_sig(args) -> None:
    import numpy as np

    n = args.n or 200_000
    dims = [args.dim] if args.dim else [384, 768]
    rng_q = np.random.default_rng(99)
    for dim in dims:
        t0 = time.perf_counter()
        vecs, draw = make_vectors(n, dim, seed=dim, decay=args.decay)
        sample = vecs[np.random.default_rng(1).choice(n, 2000, replace=False)]
        sims = sample[:1000] @ sample[1000:].T
        out("sig_base", n=n, dim=dim, decay=args.decay, gen_s=round(time.perf_counter() - t0, 1), mean_cos=round(float(sims.mean()), 3),
            p99_cos=round(float(np.quantile(sims, 0.99)), 3), mean_norm=round(float(np.linalg.norm(vecs.mean(axis=0))), 3))
        nq = args.queries
        new_q = draw(nq, rng_q)
        new_q /= np.linalg.norm(new_q, axis=1, keepdims=True)
        src = rng_q.choice(n, nq, replace=False)
        para = vecs[src] + rng_q.standard_normal((nq, dim)).astype(np.float32) * np.float32(0.35 / np.sqrt(dim))
        para /= np.linalg.norm(para, axis=1, keepdims=True)
        vecs16 = vecs.astype(np.float16)
        for qlabel, queries in (("nova", new_q), ("parafrase", para)):
            exact = np.argpartition(-(vecs @ queries.T), 9, axis=0)[:10].T                # top-10 exato por consulta (sem ordem)
            for centered in (True, False):
                mu = vecs[:MU_SAMPLE].mean(axis=0) if centered else np.zeros(dim, np.float32)
                sigs = signatures(vecs, mu)
                hits32 = hits16 = 0
                runs: list[float] = []
                for i in range(nq):
                    t1 = time.perf_counter()
                    got = search(vecs, sigs, mu, queries[i], 10)
                    runs.append((time.perf_counter() - t1) * 1000)
                    truth = set(exact[i].tolist())
                    hits32 += len(truth & set(got.tolist()))
                    hits16 += len(truth & set(search(vecs, sigs, mu, queries[i], 10, rerank_base=vecs16).tolist()))
                out("sig_recall", n=n, dim=dim, decay=args.decay, queries=qlabel, centered=centered, candidates=n_candidates(n),
                    recall10_f32=round(hits32 / (10 * nq), 3), recall10_f16=round(hits16 / (10 * nq), 3), sig_mb=round(sigs.nbytes / 1e6, 1),
                    f32_mb=round(vecs.nbytes / 1e6, 1), query=stats_ms(runs) | {"what": "Hamming + argpartition + reordenação em memória"})
        if dim == 768 and args.sqlite:
            sqlite_fetch(vecs[:args.sqlite], signatures(vecs[:args.sqlite], vecs[:MU_SAMPLE].mean(axis=0)))
        del vecs, vecs16


def sqlite_fetch(vecs, sigs) -> None:
    """Carga das assinaturas do SQLite (o cache do app) e leitura dos float32 dos candidatos por id."""
    import numpy as np

    tmp = Path(tempfile.mkdtemp(prefix="rag-probe-"))
    c = sqlite3.connect(str(tmp / "vec.db"), autocommit=True)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("CREATE TABLE rag_vectors (id INTEGER PRIMARY KEY, chunk_id INTEGER NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, "
              "vector BLOB NOT NULL, sig BLOB NOT NULL, created_at TEXT NOT NULL, UNIQUE (chunk_id, model))")
    c.execute("CREATE INDEX idx_rag_vectors_model ON rag_vectors(model, id)")
    model = "gemini/gemini-embedding-001@768"
    t0 = time.perf_counter()
    c.execute("BEGIN")
    c.executemany("INSERT INTO rag_vectors(chunk_id, model, dim, vector, sig, created_at) VALUES (?, ?, ?, ?, ?, 'x')",
                  ((i + 1, model, vecs.shape[1], vecs[i].tobytes(), sigs[i].tobytes()) for i in range(len(vecs))))
    c.execute("COMMIT")
    insert_s = time.perf_counter() - t0
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = sum(f.stat().st_size for f in tmp.iterdir())
    t0 = time.perf_counter()
    rows = c.execute("SELECT id, sig FROM rag_vectors WHERE model = ? ORDER BY id", (model,)).fetchall()
    ids = np.fromiter((r[0] for r in rows), np.int64, len(rows))
    loaded = np.frombuffer(b"".join(r[1] for r in rows), np.uint8).reshape(len(rows), -1)
    load_ms = (time.perf_counter() - t0) * 1000
    rng = np.random.default_rng(5)
    res = {}
    for m in (200, 2000):
        runs = []
        for _ in range(20):
            want = sorted(rng.choice(len(vecs), m, replace=False) + 1)
            t1 = time.perf_counter()
            got = c.execute(f"SELECT id, vector FROM rag_vectors WHERE id IN ({','.join('?' * m)})", want).fetchall()  # noqa: S608
            np.frombuffer(b"".join(r[1] for r in got), np.float32)
            runs.append((time.perf_counter() - t1) * 1000)
        res[str(m)] = stats_ms(runs)
    out("sig_sqlite", rows=len(vecs), db_mb=round(size / 1e6, 1), insert_s=round(insert_s, 1), load_sigs_ms=round(load_ms, 1),
        loaded=loaded.shape, ids_ok=bool(ids[0] == 1), fetch_candidates=res)
    c.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


# ----------------------------------------------------------------------------- gemini
def cmd_gemini(args) -> None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        out("gemini", skipped="pulado: sem chave (defina GEMINI_API_KEY; medir no Windows, B13)")
        return
    import asyncio

    import numpy as np

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from listener.llm.registry import NetworkConfig, load_provider
    from listener.llm.types import EmbedTask

    if args.text:
        raw = " ".join(Path(args.text).read_text(encoding="utf-8").split())
        texts = [raw[i:i + CHUNK_CHARS] for i in range(0, len(raw), CHUNK_CHARS)]
    else:
        texts = [f"{t} · 2026-10-25\n{x}" for t, _s, x in synthetic_chunks(EMBED_BATCH * args.batches)[0]]
    texts = texts[:EMBED_BATCH * args.batches]
    provider = load_provider("gemini", key, NetworkConfig())

    async def go() -> list[np.ndarray]:
        got: list[np.ndarray] = []
        try:
            for i in range(0, len(texts), EMBED_BATCH):
                batch = texts[i:i + EMBED_BATCH]
                t0 = time.perf_counter()
                emb = await provider.embed(batch, model="gemini-embedding-001", dim=768, task=EmbedTask.DOCUMENT)
                vecs = np.asarray(emb.vectors, np.float32)
                out("gemini_batch", texts=len(batch), chars=sum(len(t) for t in batch), latency_s=round(time.perf_counter() - t0, 2),
                    dim=int(vecs.shape[1]), norm_mean=round(float(np.linalg.norm(vecs, axis=1).mean()), 4),
                    tokens_est=emb.usage.input_tokens)
                got.append(vecs)
        finally:
            await provider.aclose()
        return got

    vecs = np.concatenate(asyncio.run(go()))
    vn = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    sims = vn @ vn.T
    off = sims[~np.eye(len(vn), dtype=bool)]
    out("gemini_anisotropy", vectors=len(vn), mean_cos=round(float(off.mean()), 3), p99_cos=round(float(np.quantile(off, 0.99)), 3),
        mean_norm=round(float(np.linalg.norm(vn.mean(axis=0))), 3), source="texto real" if args.text else "sintético (Zipf)")


# ----------------------------------------------------------------------------- e5 (F5.1 L0, D-050)
# repositório, revisão e o int8 e o tokenizer vêm do produto (`listener/llm/local_embed.py`, a fonte única); o fp32 fica aqui,
# só para comparar (o app usa o int8, D-051)
E5_FP32 = ("onnx/model.onnx", "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665")
E5_MAX_TOKENS = 512
E5_SENTENCES = (
    "O comitê aprovou o orçamento de 2027 com corte de dez por cento nas despesas de viagem.",
    "A Ana ficou de mandar a planilha revisada do fluxo de caixa até sexta-feira.",
    "O Rodrigo explicou que a auditoria externa começa na segunda quinzena de novembro.",
    "Decidimos adiar a migração do servidor para depois do fechamento contábil.",
    "A cota sênior do FIDC teve rentabilidade acima do CDI no trimestre.",
    "Ficou combinado que o jurídico revisa o contrato de cessão antes da assinatura.",
    "O time de dados vai entregar o painel de inadimplência na próxima reunião.",
    "Houve discussão sobre o risco de concentração nos dez maiores cedentes.",
    "A Beatriz pediu um levantamento dos custos de licenças de software por área.",
    "O prazo do relatório para a CVM foi confirmado para o dia quinze.",
    "Precisamos alinhar com o banco custodiante a nova régua de cobrança.",
    "A taxa de subordinação mínima continua em vinte e cinco por cento.",
    "O Carlos sugeriu automatizar a conciliação diária das carteiras.",
    "A reunião de alinhamento do fundo foi marcada para quinta às dez horas.",
    "Os indicadores de atraso acima de noventa dias subiram levemente em setembro.",
    "A diretoria quer ver um plano de contingência para queda da plataforma.",
    "O fornecedor de nuvem propôs um desconto se o contrato for de três anos.",
    "Ficou pendente a definição do responsável pelo projeto de segurança da informação.",
    "O comitê de crédito recusou a operação por falta de garantias suficientes.",
    "A Mariana vai apresentar a análise de sensibilidade com juros mais altos.",
    "Combinamos revisar a política de investimentos no primeiro trimestre.",
    "A integração com o sistema do administrador fiduciário ainda tem erros de arredondamento.",
    "O treinamento sobre LGPD será obrigatório para todos os analistas.",
    "A equipe comercial pediu material novo para os investidores qualificados.",
    "O backup dos bancos de dados passou a rodar toda madrugada às duas horas.",
    "A área de riscos recomendou reduzir a exposição ao setor de varejo.",
    "O próximo passo é validar os números com a controladoria antes de publicar.",
    "Os atrasos na entrega do relatório mensal foram causados pela troca de sistema.",
    "O Paulo lembrou que a assembleia de cotistas precisa de quórum qualificado.",
    "Ficou decidido contratar uma consultoria para revisar os processos de cobrança.",
)
E5_GOLD = (                                                              # pergunta e o índice da frase que a responde
    ("quem ficou responsável pela planilha de fluxo de caixa", 1), ("quando começa a auditoria externa", 2),
    ("o que foi decidido sobre o orçamento", 0), ("qual a rentabilidade da cota sênior", 4), ("prazo do relatório da CVM", 9),
    ("por que o comitê recusou a operação de crédito", 18), ("qual é a subordinação mínima", 11),
    ("plano de contingência da plataforma", 15), ("desconto do fornecedor de nuvem", 16), ("treinamento de LGPD", 22),
    ("exposição ao varejo", 25), ("assembleia de cotistas e quórum", 28), ("consultoria para cobrança", 29),
    ("migração do servidor", 3), ("conciliação diária automatizada", 12), ("painel de inadimplência", 6),
)
E5_QUERIES = tuple(q for q, _ in E5_GOLD)


def e5_spec() -> tuple[str, str, dict[str, tuple[str, str]]]:
    """(repositório, revisão, {variante: (arquivo, sha256)}) do e5: o int8 e o tokenizer do produto, o fp32 daqui."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from listener.llm import local_embed

    files = {"int8": (local_embed.ONNX_FILE, local_embed.FILES[local_embed.ONNX_FILE]), "fp32": E5_FP32,
             "tokenizer": (local_embed.TOKENIZER_FILE, local_embed.FILES[local_embed.TOKENIZER_FILE])}
    return local_embed.REPO, local_embed.REVISION, files


def e5_fetch(repo: str, revision: str, filename: str, sha256: str, local_dir: Path) -> Path:
    """Um arquivo do e5 na revisão fixada, conferido pelo sha256 (o download pula o que já está certo)."""
    import hashlib

    from huggingface_hub import hf_hub_download

    path = Path(hf_hub_download(repo, filename, revision=revision, local_dir=str(local_dir)))
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != sha256:
        raise SystemExit(f"sha256 diferente em {path}")
    return path


def e5_texts(n: int, seed: int = 7) -> list[str]:
    """Trechos de ~1.400 caracteres com frases de reunião em português, em ordens diferentes."""
    import random

    rnd = random.Random(seed)  # noqa: S311 (amostra reprodutível, não é segredo)
    texts = []
    for _ in range(n):
        parts: list[str] = []
        while sum(len(p) + 1 for p in parts) < CHUNK_CHARS:
            parts.append(rnd.choice(E5_SENTENCES))
        texts.append(" ".join(parts))
    return texts


def e5_embed(sess, tok, texts: list[str], prefix: str, batch: int):
    """Pooling médio com a máscara e L2, como a F5.1 faz: devolve (vetores, quantos passaram de 512 tokens)."""
    import numpy as np

    names = {i.name for i in sess.get_inputs()}
    out_vecs, cut = [], 0
    for i in range(0, len(texts), batch):
        encs = tok.encode_batch([prefix + t for t in texts[i:i + batch]])
        cut += sum(1 for e in encs if e.overflowing)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = sess.run(None, feed)[0]
        vec = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        out_vecs.append(vec / np.linalg.norm(vec, axis=1, keepdims=True))
    return np.concatenate(out_vecs).astype(np.float32), cut


def cmd_e5(args) -> None:
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    models = Path(args.models_dir or os.environ.get("LISTENER_MODELS_DIR") or
                  (Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "listener-meet" / "models"))
    int8_dir = models / "multilingual-e5-small"                       # a mesma pasta que a F5.1 usa
    fp32_dir = Path(args.fp32_dir) if args.fp32_dir else Path(tempfile.mkdtemp(prefix="e5-fp32-"))
    t0 = time.perf_counter()
    repo, revision, files = e5_spec()
    tok_path = e5_fetch(repo, revision, *files["tokenizer"], int8_dir)
    paths = {"int8": e5_fetch(repo, revision, *files["int8"], int8_dir), "fp32": e5_fetch(repo, revision, *files["fp32"], fp32_dir)}
    out("e5_files", revision=revision, int8=str(paths["int8"]), fp32=str(paths["fp32"]), seconds=round(time.perf_counter() - t0, 1),
        device=ort.get_device(), ort=ort.__version__)
    tok = Tokenizer.from_file(str(tok_path))
    tok.enable_truncation(max_length=E5_MAX_TOKENS)
    pad = tok.token_to_id("<pad>")
    tok.enable_padding(pad_id=1 if pad is None else pad, pad_token="<pad>")  # noqa: S106 (o token de enchimento, não senha)
    texts = e5_texts(args.passages)
    sentences = list(E5_SENTENCES)
    vecs: dict[str, tuple] = {}
    for variant, path in paths.items():
        for threads in (1, 2):
            so = ort.SessionOptions()
            so.intra_op_num_threads, so.inter_op_num_threads = threads, 1
            t = time.perf_counter()
            sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
            load_s = time.perf_counter() - t
            sample = texts if threads == 2 else texts[:args.batch]       # 1 thread: só a velocidade, numa amostra
            t = time.perf_counter()
            pv, cut = e5_embed(sess, tok, sample, "passage: ", args.batch)
            embed_s = time.perf_counter() - t
            t = time.perf_counter()
            qv, _ = e5_embed(sess, tok, list(E5_QUERIES), "query: ", args.batch)
            query_ms = 1000 * (time.perf_counter() - t) / len(E5_QUERIES)
            out("e5_variant", variant=variant, threads=threads, load_s=round(load_s, 2), passages=len(sample), batch=args.batch,
                ms_per_passage=round(1000 * embed_s / len(sample), 1), ms_per_query=round(query_ms, 1), truncated=cut,
                dim=int(pv.shape[1]))
            if threads == 2:
                sv, _ = e5_embed(sess, tok, sentences, "passage: ", args.batch)
                vecs[variant] = (pv, qv, sv)
            del sess
    (p8, q8, s8), (p32, q32, s32) = vecs["int8"], vecs["fp32"]
    cos = (p8 * p32).sum(axis=1)

    def recall(q_a, d_a, q_b, d_b, top: int) -> float:
        a = np.argsort(-(q_a @ d_a.T), axis=1)[:, :top]
        b = np.argsort(-(q_b @ d_b.T), axis=1)[:, :top]
        return float(np.mean([len(set(x) & set(y)) / top for x, y in zip(a, b, strict=True)]))

    gold = np.array([i for _, i in E5_GOLD])

    def hits(q, s, top: int) -> float:
        ranked = np.argsort(-(q @ s.T), axis=1)[:, :top]
        return float(np.mean([g in r for g, r in zip(gold, ranked, strict=True)]))

    out("e5_gold", queries=len(gold), int8_top1=round(hits(q8, s8, 1), 3), int8_top3=round(hits(q8, s8, 3), 3),
        fp32_top1=round(hits(q32, s32, 1), 3), fp32_top3=round(hits(q32, s32, 3), 3))
    off = p32 @ p32.T
    out("e5_int8_vs_fp32", cos_mean=round(float(cos.mean()), 4), cos_min=round(float(cos.min()), 4),
        cos_p5=round(float(np.quantile(cos, 0.05)), 4), meta_cos=">= 0,99 (risco da spec)",
        recall_at_5_frases_distintas=round(recall(q32, s32, q8, s8, 5), 3),
        recall_at_10_trechos_quase_iguais=round(recall(q32, p32, q8, p8, 10), 3),
        fp32_mean_cos_between_passages=round(float(off[~np.eye(len(p32), dtype=bool)].mean()), 3))


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fts")
    p.add_argument("n", nargs="?", type=int, default=0)
    p.add_argument("--repeat", type=int, default=20)
    p = sub.add_parser("sig")
    p.add_argument("n", nargs="?", type=int, default=0)
    p.add_argument("dim", nargs="?", type=int, default=0)
    p.add_argument("--queries", type=int, default=200)
    p.add_argument("--sqlite", type=int, default=50_000, help="linhas no teste de leitura do SQLite (0 = pula)")
    p.add_argument("--threads", type=int, default=1, help="threads do BLAS (o app consulta numa Task)")
    p.add_argument("--decay", type=float, default=1.0, help="expoente do espectro do resíduo (1 = estrutura forte; 0,5 = quase isotrópico)")
    p = sub.add_parser("gemini")
    p.add_argument("--batches", type=int, default=3)
    p.add_argument("--text", default=None)
    p = sub.add_parser("e5")
    p.add_argument("--passages", type=int, default=192)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--models-dir", default=None, help="pasta de modelos do app (o int8 fica em multilingual-e5-small/)")
    p.add_argument("--fp32-dir", default=None, help="onde baixar o fp32 só para comparar (padrão: pasta temporária)")
    args = ap.parse_args()
    threads = str(getattr(args, "threads", 1))
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, threads)                # antes do primeiro `import numpy`
    out("env", python=sys.version.split()[0], platform=sys.platform, cores=os.cpu_count(), sqlite=sqlite3.sqlite_version, threads=threads)
    {"fts": cmd_fts, "sig": cmd_sig, "gemini": cmd_gemini, "e5": cmd_e5}[args.cmd](args)


if __name__ == "__main__":
    main()
