"""F5.10 (Dados, Interface): os passos de uma resposta de "Perguntar à base", gravados em `chat_messages.trace_json` (v9):
cada rodada com a nota, o custo e o tempo, e cada consulta com o SQL, os `params`, o objetivo, as linhas, os ms e a
categoria do erro, e cada busca com o texto, os trechos e os ms; nunca os dados. No fim, por que parou, o tempo e o custo
(com o aviso de "sem preço cadastrado", quando a guarda de custo ficou inativa) e os filtros da pergunta, para o "Ver dados"
refazer a consulta com as mesmas reuniões (U-097). Puro, sem Qt."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..store.rag import SearchFilters

if TYPE_CHECKING:
    from .agent_results import Round

VERSION = 1
STOPS: dict[str, str] = {
    "resposta": "a LLM teve o que precisava", "exaustao": "três rodadas seguidas sem informação nova",
    "custo": "a guarda de custo", "tempo": "a guarda de tempo", "rodadas": "a guarda de rodadas", "janela": "o contexto do modelo encheu",
    "cancelada": "você cancelou", "erro": "a LLM falhou",
}


def build(rounds: Sequence[Round], stop: str, ms: float, cost_usd: float | None, unpriced: bool, filters: SearchFilters | None,
          label: str, model: str) -> dict[str, Any]:
    items = [i.trace for r in rounds for i in r.items]
    ran = [t for t in items if t["tipo"] == "consulta" and t["status"] in ("ok", "erro")]
    searched = [t for t in items if t["tipo"] == "busca" and t["status"] == "ok"]
    active = filters is not None and filters.active
    return {"versao": VERSION, "modelo": model, "parou": stop, "ms": round(ms, 1),
            "custo_usd": None if cost_usd is None else round(cost_usd, 6), "sem_preco": unpriced,
            "filtros": {"texto": label, "pessoas": list(filters.person_ids), "de": filters.date_from, "ate": filters.date_to,
                        "reunioes": list(filters.meeting_ids)} if active and filters is not None else None,
            "consultas": len(ran), "erros": sum(1 for t in ran if t["status"] == "erro"), "buscas": len(searched),
            "linhas": sum(int(t["linhas"] or 0) for t in ran), "rodadas": [r.trace() for r in rounds]}


def load(raw: str | None) -> dict[str, Any] | None:
    try:
        data = json.loads(raw or "null")
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def filters_of(trace: dict[str, Any]) -> SearchFilters | None:
    """Os filtros com que a pergunta rodou (U-097: o "Ver dados" refaz a consulta com as mesmas reuniões)."""
    f = trace.get("filtros")
    if not isinstance(f, dict):
        return None
    try:
        return SearchFilters(tuple(int(p) for p in f.get("pessoas") or ()), f.get("de"), f.get("ate"),
                             tuple(int(m) for m in f.get("reunioes") or ()))
    except (TypeError, ValueError):
        return None


def queries(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """As consultas da resposta, na ordem, com a rodada (`ref` "2.1")."""
    return [i for r in trace.get("rodadas") or () for i in r.get("itens") or () if i.get("tipo") == "consulta"]


def query(trace: dict[str, Any], ref: str) -> dict[str, Any] | None:
    return next((q for q in queries(trace) if q.get("ref") == ref), None)


__all__ = ["STOPS", "VERSION", "build", "filters_of", "load", "queries", "query"]
