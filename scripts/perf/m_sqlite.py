"""M-09 `sqlite` (PF-26): a base sintética de `scripts/rag_probe.py` (os `--n` trechos de ~1.400 caracteres com vocabulário
em Zipf na `rag_chunks` da v8 com o `rag_fts`, e as assinaturas de `--n` vetores de 768 numa `rag_vectors`) num arquivo
temporário em WAL; depois, intercaladas, as duas condições: a conexão padrão de hoje e a conexão com `PRAGMA cache_size =
-32768` e `PRAGMA mmap_size = 268435456`. Cada rodada abre uma conexão nova por condição (o cache de páginas do SQLite
começa frio; o do sistema de arquivos fica quente, como no app) e mede, com `--repeat` repetições, as consultas do FTS com
o JOIN que a busca usa (termo médio, onipresente e raro), a carga das assinaturas e a leitura de 200 candidatos por id.
Saída: ms por consulta nas duas condições; o resumo é o menor das medianas."""
from __future__ import annotations

import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from .common import Report, best_of_medians, interleaved

PRAGMAS = ("PRAGMA cache_size = -32768", "PRAGMA mmap_size = 268435456")
DIM = 768
CANDIDATES = 200
MODEL = "gemini/gemini-embedding-001@768"
COMMON_FIRST = 2                                  # "não", quase todo trecho (`rag_probe.cmd_fts`)


def build(n: int, tmp: Path) -> tuple[Path, dict[str, str], float]:
    """A base do `rag_probe` (FTS e vetores) num arquivo só; devolve o caminho, os termos e os segundos de montagem."""
    import rag_probe as rp

    t0 = time.perf_counter()
    rows, vocab = rp.synthetic_chunks(n)
    path = tmp / "perf.db"
    c = sqlite3.connect(str(path), autocommit=True)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")
    for sql in rp.V8_SQL:
        c.execute(sql)
    meetings = (n + rp.CHUNKS_PER_MEETING - 1) // rp.CHUNKS_PER_MEETING
    c.executemany("INSERT INTO meetings(id, title) VALUES (?, ?)", [(m + 1, f"m{m}") for m in range(meetings)])
    sql = ("INSERT INTO rag_chunks(meeting_id, kind, t_start_ms, t_end_ms, title, speakers, person_ids, text, text_hash, job_id, "
           "created_at) VALUES (?, 'transcricao', ?, ?, ?, ?, '[]', ?, ?, 1, '2026-10-25T10:00:00.000-03:00')")
    c.execute("BEGIN")
    c.executemany(sql, [(i // rp.CHUNKS_PER_MEETING + 1, i * 80_000, (i + 1) * 80_000, t, s, x, str(i)) for i, (t, s, x) in enumerate(rows)])
    c.execute("COMMIT")
    vecs, _draw = rp.make_vectors(n, DIM, seed=DIM)
    sigs = rp.signatures(vecs, vecs[:min(n, rp.MU_SAMPLE)].mean(axis=0))
    c.execute("CREATE TABLE rag_vectors (id INTEGER PRIMARY KEY, chunk_id INTEGER NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, "
              "vector BLOB NOT NULL, sig BLOB NOT NULL, created_at TEXT NOT NULL, UNIQUE (chunk_id, model))")
    c.execute("CREATE INDEX idx_rag_vectors_model ON rag_vectors(model, id)")
    c.execute("BEGIN")
    c.executemany("INSERT INTO rag_vectors(chunk_id, model, dim, vector, sig, created_at) VALUES (?, ?, ?, ?, ?, 'x')",
                  ((i + 1, MODEL, DIM, vecs[i].tobytes(), sigs[i].tobytes()) for i in range(n)))
    c.execute("COMMIT")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    common = len(dict.fromkeys(rp.COMMON))
    terms = {"medio": vocab[common + 60], "onipresente": vocab[COMMON_FIRST], "raro": vocab[min(len(vocab) - 1, 20_000)]}
    return path, terms, round(time.perf_counter() - t0, 1)


def queries(terms: dict[str, str]) -> dict[str, tuple[str, tuple[Any, ...]]]:
    import rag_probe as rp

    joined = (f"SELECT c.id, c.meeting_id, c.t_start_ms, c.kind, {rp.SNIPPET} FROM rag_fts JOIN rag_chunks c "  # noqa: S608
              "ON c.id = rag_fts.rowid WHERE rag_fts MATCH ? ORDER BY rank LIMIT 50")
    out: dict[str, tuple[str, tuple[Any, ...]]] = {f"fts_{k}": (joined, (f'"{v}"',)) for k, v in terms.items()}
    out["sig_load"] = ("SELECT id, sig FROM rag_vectors WHERE model = ? ORDER BY id", (MODEL,))
    return out


def measure(path: Path, with_pragmas: bool, qs: dict[str, tuple[str, tuple[Any, ...]]], repeat: int, n: int) -> dict[str, float]:
    """Uma conexão nova; a mediana de `repeat` repetições de cada consulta, em ms."""
    import numpy as np

    c = sqlite3.connect(str(path), autocommit=True)
    try:
        if with_pragmas:
            for sql in PRAGMAS:
                c.execute(sql)
        result: dict[str, float] = {}
        for name, (sql, params) in qs.items():
            runs = []
            for _ in range(repeat):
                t0 = time.perf_counter()
                c.execute(sql, params).fetchall()
                runs.append((time.perf_counter() - t0) * 1000)
            result[name] = round(statistics.median(runs), 3)
        rng = np.random.default_rng(5)
        runs = []
        k = min(CANDIDATES, n)
        sql = f"SELECT id, vector FROM rag_vectors WHERE id IN ({','.join('?' * k)})"  # noqa: S608
        for _ in range(repeat):
            want = sorted(int(i) for i in rng.choice(n, k, replace=False) + 1)
            t0 = time.perf_counter()
            c.execute(sql, want).fetchall()
            runs.append((time.perf_counter() - t0) * 1000)
        result["vec_fetch_200"] = round(statistics.median(runs), 3)
        return result
    finally:
        c.close()


def run(args: Any, report: Report) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perf-sqlite-"))
    try:
        path, terms, build_s = build(int(args.n), tmp)
        qs = queries(terms)
        report.add("sqlite_setup", n=int(args.n), build_s=build_s, db_mb=round(sum(f.stat().st_size for f in tmp.iterdir()) / 1e6, 1),
                   sqlite=sqlite3.sqlite_version, repeat=args.repeat, pragmas=PRAGMAS)
        samples: dict[str, list[float]] = {}
        for run_idx, condition in interleaved(["padrao", "pragmas"], args.runs):
            result = measure(path, condition == "pragmas", qs, int(args.repeat), int(args.n))
            report.add("sqlite_run", condition=condition, run=run_idx + 1, ms=result)
            for k, v in result.items():
                samples.setdefault(f"{condition}:{k}", []).append(v)
        best = best_of_medians(samples)
        gain = {k: round(100 * (1 - best[f"pragmas:{k}"] / best[f"padrao:{k}"]), 1) for k in [*qs, "vec_fetch_200"]
                if best.get(f"padrao:{k}") and best.get(f"pragmas:{k}") is not None}
        report.finish(best_ms=best, gain_pct_with_pragmas=gain, n=int(args.n))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
