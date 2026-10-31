"""F5.10 (Dados, Interface): os passos de uma resposta de "Perguntar à base", gravados em `chat_messages.trace_json` (v9):
cada rodada com a nota, o custo e o tempo, e cada consulta com o SQL, os `params`, o objetivo, as linhas, os ms e a
categoria do erro, e cada busca com o texto, os trechos e os ms; nunca os dados. No fim, por que parou, o tempo e o custo
(com o aviso de "sem preço cadastrado", quando a guarda de custo ficou inativa) e os filtros da pergunta, para o "Ver dados"
refazer a consulta com as mesmas reuniões (U-097). Puro, sem Qt.

L4 (Interface, D7): o "Como cheguei aqui (3 consultas · 1,2 s · US$ 0,004)" é HTML daqui, dentro do parágrafo da resposta no
`thread_html` (só `<br>` e elementos de linha: a conversa segue um parágrafo por mensagem), recolhido num link
`listener://trace?msg=<id>`; aberto, cada consulta com o objetivo, o SQL formatado, os `params`, as linhas e o tempo ou o
erro, e os links "Copiar SQL" e "Ver dados" (`listener://copysql` e `listener://data`, com a consulta em `q`). HTML por
classes, sem cor fixa (F5.4)."""
from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from ..llm.costs import fmt_usd
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


# ---------------------------------------------------------------- "Como cheguei aqui" (L4)
SCHEME = "listener"
ACTIONS = ("trace", "copysql", "data")
CLAUSE_RE = re.compile(r"\s+(?=(?:FROM|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|UNION(?: ALL)?|(?:LEFT |INNER |CROSS )?JOIN)\b)",
                       re.IGNORECASE)


def link(action: str, message_id: int, ref: str | None = None) -> str:
    return f"{SCHEME}://{action}?msg={int(message_id)}" + (f"&q={ref}" if ref else "")


def parse_link(url: str) -> tuple[str, int, str | None] | None:
    """`listener://trace?msg=<id>`, `copysql` e `data` (com `&q=<rodada.item>`) -> (ação, mensagem, consulta); None para outro."""
    parts = urlsplit(url.replace("&amp;", "&"))
    if parts.scheme != SCHEME or parts.netloc not in ACTIONS:
        return None
    qs = parse_qs(parts.query)
    try:
        message_id = int(qs["msg"][0])
    except (KeyError, IndexError, ValueError):
        return None
    ref = qs["q"][0] if qs.get("q") else None
    return None if parts.netloc != "trace" and not ref else (parts.netloc, message_id, ref)


def pretty(sql: str) -> str:
    """O SQL numa cláusula por linha (FROM, WHERE, GROUP BY, ORDER BY, JOIN...)."""
    return CLAUSE_RE.sub("\n", " ".join((sql or "").split()))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summary(steps: dict[str, Any]) -> str:
    """"Como cheguei aqui (3 consultas · 1,2 s · US$ 0,004)": as consultas e buscas que rodaram, o tempo e o custo."""
    parts = [_plural(int(steps.get("consultas") or 0), "consulta")]
    if steps.get("buscas"):
        parts.append(_plural(int(steps["buscas"]), "busca"))
    parts.append(f"{float(steps.get('ms') or 0) / 1000:.1f} s".replace(".", ","))
    parts.append("sem preço cadastrado" if steps.get("sem_preco") else fmt_usd(steps.get("custo_usd")))
    return f"Como cheguei aqui ({' · '.join(parts)})"


def _a(href: str, text: str) -> str:
    return f'<a href="{html.escape(href)}">{html.escape(text)}</a>'


def _note(text: str) -> str:
    return f'<br><span class="note">{html.escape(text)}</span>'


def _query_html(q: dict[str, Any], message_id: int) -> str:
    ref, status = str(q.get("ref") or ""), q.get("status")
    if status == "ok":
        result = f"{_plural(int(q.get('linhas') or 0), 'linha')} · {float(q.get('ms') or 0):.0f} ms"
    else:
        result = {"erro": f"erro ({q.get('erro')})", "repetida": "repetida, não rodou de novo"}.get(str(status), "não rodou")
    sql = html.escape(pretty(str(q.get("sql") or ""))).replace("\n", "<br>")
    params = f"<br>params: {html.escape(json.dumps(q.get('params') or [], ensure_ascii=False))}" if q.get("params") else ""
    links = [_a(link("copysql", message_id, ref), "Copiar SQL")]
    if status == "ok":
        links.append(_a(link("data", message_id, ref), "Ver dados"))
    goal = str(q.get("objetivo") or "consulta")
    return f'{_note(f"#{ref} {goal} · {result}")}<br><code>{sql}{params}</code><br><span class="note">{" · ".join(links)}</span>'


def _search_html(b: dict[str, Any]) -> str:
    found = f"{_plural(int(b.get('trechos') or 0), 'trecho')} ({b.get('novos') or 0} novos)" if b.get("status") == "ok" else "não rodou"
    return _note(f'#{b.get("ref")} busca "{b.get("texto") or ""}" · {found}')


def html_of(steps: dict[str, Any], message_id: int, expanded: bool = False) -> str:
    """O "Como cheguei aqui" de uma resposta: recolhido, só o resumo como link; aberto, também onde parou e cada passo."""
    out = [f'<br><span class="note">{_a(link("trace", message_id), ("▾ " if expanded else "▸ ") + summary(steps))}</span>']
    if not expanded:
        return out[0]
    stop = str(steps.get("parou") or "")
    if stop and stop != "resposta":
        out.append(_note(f"Parou: {STOPS.get(stop, stop)}."))
    if steps.get("sem_preco"):
        out.append(_note("Sem preço cadastrado para o modelo: a guarda de custo ficou inativa."))
    items = [i for r in steps.get("rodadas") or () for i in r.get("itens") or ()]
    out += [_query_html(i, message_id) if i.get("tipo") == "consulta" else _search_html(i) for i in items]
    return "".join(out)


__all__ = ["STOPS", "VERSION", "build", "filters_of", "html_of", "link", "load", "parse_link", "pretty", "queries", "query", "summary"]
