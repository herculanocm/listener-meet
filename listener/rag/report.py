"""Medição do G5 no `--check` (F5 item 13, F5A-27), só leitura (`Store.open_readonly`, nada de ML nem de rede): por reunião,
os trechos por tipo, os vetores por modelo, os trechos sem vetor do modelo ativo e o último job `index` (estado, motivo e a
nota "N trechos sem vetor: motivo"); no fim, um objeto geral com a cobertura do índice (reuniões processadas com trechos) e
dos vetores do modelo ativo, o `rag_fts_docsize` contra `rag_chunks` (o `integrity-check` do FTS5 é um INSERT e falha em
`mode=ro`), o tempo de 5 buscas por palavra fixas (a meta do G5: até 300 ms com 50 reuniões), o U-027 (respostas do chat
global, avaliadas, certas e o percentual contra o alvo de 8 em 10 sobre 5 ou mais reuniões) e o custo da F5 somado por
`llm_calls` (o vetor da consulta, os lotes do índice e o chat global), nunca por `chat_messages`. Banco anterior à v8:
"esquema anterior à F5"."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from ..model import ChatRole, JobKind

if TYPE_CHECKING:
    from ..store import Store

PRE_F5 = "esquema anterior à F5"
F5_SCHEMA = 8
FTS_PROBES = ("reunião", "orçamento", "prazo", "decisão", "próximos passos")
TARGET_FTS_MS = 300.0
TARGET_FTS_MEETINGS = 50
TARGET_CITATION_PCT = 80.0
TARGET_RATED = 10
TARGET_CITED_MEETINGS = 5


def _f5(store: Store) -> bool:
    return store.schema_version() >= F5_SCHEMA


def _params(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def last_index(store: Store, meeting_id: int) -> dict[str, Any] | None:
    """O último job `index` da reunião: estado, motivo, forçado, quando terminou, o erro e o resumo do `timings_json`."""
    rows = store._q("SELECT id, status, params_json, timings_json, error, finished_at FROM processing_jobs "
                    "WHERE meeting_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (meeting_id, str(JobKind.INDEX)))
    if not rows:
        return None
    r = rows[0]
    params, info = _params(r["params_json"]), _params(r["timings_json"]).get("index") or {}
    return {"job_id": int(r["id"]), "status": r["status"], "reason": params.get("reason"), "force": bool(params.get("force")),
            "finished_at": r["finished_at"], "error": r["error"], "note": info.get("note") or "", "chunks": info.get("chunks"),
            "vectors": info.get("vectors"), "missing": info.get("missing"), "cost_usd": info.get("cost_usd")}


def meeting_rag(store: Store, meeting_id: int) -> dict[str, Any]:
    """O bloco `rag` de uma reunião no relatório do `--check`."""
    if not _f5(store):
        return {"error": PRE_F5}
    from .index import embedder_status

    key = embedder_status(store).model_key
    kinds = store.rag.chunks_by_kind(meeting_id)
    return {"chunks": kinds, "chunks_total": sum(kinds.values()), "vectors": store.rag.vector_counts(meeting_id), "active_model": key,
            "missing_active": store.rag.count_missing(meeting_id, key) if key else None, "last_index": last_index(store, meeting_id)}


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def fts_probes(store: Store) -> list[dict[str, Any]]:
    """5 buscas por palavra fixas, como o Histórico faz (destaque e teto por reunião): o tempo e quantos trechos."""
    from .search import search

    out: list[dict[str, Any]] = []
    for q in FTS_PROBES:
        t0 = time.perf_counter()
        res = search(store, q)
        out.append({"query": q, "ms": round((time.perf_counter() - t0) * 1000, 1), "hits": len(res.hits), "or": res.fts_or})
    return out


def citations_u027(store: Store) -> dict[str, Any]:
    """U-027: respostas do chat global, avaliadas, certas, o percentual e as reuniões citadas nas avaliadas."""
    rows = store._q("SELECT rating, citations_json FROM chat_messages WHERE role = ? AND thread_id IS NOT NULL", (str(ChatRole.ASSISTANT),))
    rated = [r for r in rows if r["rating"] is not None]
    correct = sum(1 for r in rated if int(r["rating"]) == 1)
    cited: set[int] = set()
    for r in rated:
        try:
            cites = json.loads(r["citations_json"] or "[]")
        except ValueError:
            cites = []
        cited |= {int(c["meeting_id"]) for c in cites if isinstance(c, dict) and c.get("meeting_id") is not None}
    pct = _pct(correct, len(rated))
    ok = None if len(rated) < TARGET_RATED else (pct is not None and pct >= TARGET_CITATION_PCT and len(cited) >= TARGET_CITED_MEETINGS)
    return {"answers": len(rows), "rated": len(rated), "correct": correct, "pct": pct, "meetings_cited": len(cited),
            "target": f">= {TARGET_CITATION_PCT:g} % em {TARGET_RATED} respostas sobre {TARGET_CITED_MEETINGS} ou mais reuniões", "ok": ok}


def costs(store: Store) -> dict[str, Any]:
    """Custo da F5 por `llm_calls`: o vetor da consulta (`rag_embed`, `query`), os lotes do índice (`rag_embed`, `batch:`) e o
    chat global (`rag_chat`); chamadas e as sem preço."""
    rows = store._q("SELECT CASE WHEN task = 'rag_chat' THEN 'chat' WHEN strategy = 'query' THEN 'query' ELSE 'index' END AS part, "
                    "COUNT(*), SUM(cost_usd), SUM(cost_usd IS NULL) FROM llm_calls WHERE task IN ('rag_embed', 'rag_chat') GROUP BY part")
    parts = {str(r[0]): {"calls": int(r[1]), "usd": round(float(r[2] or 0.0), 6), "without_price": int(r[3] or 0)} for r in rows}
    total = sum(float(v["usd"]) for v in parts.values())
    return {**parts, "total_usd": round(total, 6)}


def overall_rag(store: Store) -> dict[str, Any]:
    """O objeto geral do G5 no fim do `--check`."""
    if not _f5(store):
        return {"error": PRE_F5}
    from .index import embedder_status

    status = embedder_status(store)
    cov = store.rag.coverage(status.model_key)
    processed = len(store.rag.processed_meetings())
    docsize = int(store._q("SELECT COUNT(*) FROM rag_fts_docsize")[0][0])
    probes = fts_probes(store)
    worst = max((p["ms"] for p in probes), default=None)
    u027 = citations_u027(store)
    vectors = cov.get("vectors")
    return {
        "schema": store.schema_version(),
        "embedder": {"ready": status.ready, "reason": status.reason, "model": status.model_key},
        "meetings": {"processed": processed, "indexed": cov["meetings"], "pct": _pct(cov["meetings"], processed)},
        "chunks": cov["chunks"],
        "vectors": {"by_model": store.rag.vector_counts(), "active": vectors, "missing_active": cov.get("missing"),
                    "pct": _pct(int(vectors or 0), cov["chunks"]) if vectors is not None else None},
        "fts": {"docsize": docsize, "chunks": cov["chunks"], "ok": docsize == cov["chunks"]},
        "fts_probes": probes,
        "u027": u027,
        "costs": costs(store),
        "gate": {"fts_ms": {"target": f"<= {TARGET_FTS_MS:g} ms com {TARGET_FTS_MEETINGS} reuniões", "value": worst,
                            "meetings": cov["meetings"],
                            "ok": None if worst is None or cov["meetings"] < TARGET_FTS_MEETINGS else worst <= TARGET_FTS_MS},
                 "citations": {"target": u027["target"], "value": u027["pct"], "ok": u027["ok"]}},
    }


__all__ = ["FTS_PROBES", "PRE_F5", "citations_u027", "costs", "fts_probes", "last_index", "meeting_rag", "overall_rag"]
