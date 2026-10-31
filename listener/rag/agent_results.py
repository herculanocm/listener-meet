"""F5.10 (Comportamento 2 a 5): o que volta à LLM de cada consulta e de cada busca de "Perguntar à base", puro (sem banco nem
rede). A decisão da rodada validada no esquema (`decision`); cada consulta como tabela compacta (cabeçalho, colunas por tab,
`∅` para nulo, célula até 300 caracteres com tab e quebra escapados e "[+N]", e no fim "37 linhas · 12 ms" ou "200 de 5.120
linhas: refine ou agregue"); o erro com a categoria e a mensagem do SQLite, para a LLM corrigir; a busca como os blocos de
trechos do chat global, com o apelido `R<reuniao_id>`. O orçamento [chute]: 24 mil caracteres por rodada e 8 mil por
consulta (a que não cabe volta "não rodou: peça de novo"). A exaustão (D-073): a consulta repetida (SQL com os espaços
normalizados e os mesmos `params`) não roda enquanto o resultado está no contexto; é novidade a tabela que não estava (hash)
e o trecho de busca que ainda não tinha vindo. A rodada que sai do contexto vira uma linha (`Round.compact`). Cada item leva o
que vai ao `trace_json`: SQL, `params`, objetivo, linhas, ms e a categoria do erro; nunca os dados."""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..llm.prompts import rag_sql
from ..llm.schema import validate
from ..store.meetings import effective_title
from .citations import SentLine, Target
from .sandbox import CELL_CHARS
from .semantic import now_row

if TYPE_CHECKING:
    from ..llm.prompts.rag_chat import MeetingBlock
    from ..store import Store
    from ..store.rag import SearchFilters
    from .sandbox import QueryResult

ROUND_CHARS = 24_000
QUERY_CHARS = 8_000
MIN_QUERY_CHARS = 600            # menos que isto sobrando na rodada: a consulta nem roda
FOOTER_ROOM = 60
NO_NEWS_ROUNDS = 3
NULL = "∅"
NOT_RUN = "não rodou: peça de novo"
TIME_UP = "não rodou: o tempo da pergunta acabou"
NO_BASE = "não rodou: a base não abriu agora"


@dataclass(frozen=True, slots=True)
class Guards:
    """As guardas, que não limitam o raciocínio (custo, tempo e rodadas), e os tetos de cada consulta."""
    max_cost_usd: float = 0.10
    max_seconds: float = 180.0
    max_rounds: int = 30
    max_rows: int = 200
    timeout_s: float = 3.0
    strong: bool = False

    @classmethod
    def from_store(cls, store: Store) -> Guards:
        from ..config.defaults import SPECS

        g = store.get
        return cls(float(g(SPECS["rag.sql_max_cost_usd"])), 60.0 * float(g(SPECS["rag.sql_max_minutes"])),
                   int(g(SPECS["rag.sql_max_rounds"])), int(g(SPECS["rag.sql_max_rows"])), float(g(SPECS["rag.sql_timeout_s"])),
                   bool(g(SPECS["rag.sql_strong"])))


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    text: str
    html: str
    citations: tuple[dict[str, Any], ...]
    cost_usd: float | None               # rodadas, vetores das buscas e resposta; None sem preço cadastrado
    thread_id: int | None
    message_id: int | None
    question_id: int | None
    call_id: int | None                  # a resposta em `llm_calls`
    error: str | None = None
    cancelled: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def linked(self) -> int:
        return sum(1 for c in self.citations
                   if c.get("meeting_id") is not None and (c.get("t_ms") is not None or c.get("cited_ms") is None))


def describe(filters: SearchFilters | None) -> str:
    """Os filtros em texto, quando a tela não mandou o dela."""
    if filters is None or not filters.active:
        return ""
    parts = (f"pessoa {', '.join(str(p) for p in filters.person_ids)}" if filters.person_ids else "",
             f"desde {filters.date_from}" if filters.date_from else "", f"até {filters.date_to}" if filters.date_to else "",
             f"reuniões {', '.join(str(m) for m in filters.meeting_ids)}" if filters.meeting_ids else "")
    return " · ".join(p for p in parts if p)


def now_text(now: datetime) -> str:
    hoje, hora, dia, fuso, _ = now_row(now, False)
    return f"{hoje} ({dia}), {hora}, fuso {fuso}"


@dataclass(frozen=True, slots=True)
class Query:
    sql: str
    params: tuple[str, ...]
    goal: str

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        """A identidade da consulta para a exaustão: o SQL com os espaços normalizados e os mesmos `params`."""
        return " ".join(self.sql.split()), self.params


@dataclass(frozen=True, slots=True)
class Search:
    text: str
    goal: str
    meeting_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    answer: bool
    queries: tuple[Query, ...]
    searches: tuple[Search, ...]
    note: str


def decision(data: Mapping[str, Any]) -> Decision:
    """A resposta da rodada, validada no esquema de `rag_sql` (`SchemaError` fora dele)."""
    d = validate(rag_sql.SCHEMA, data)
    queries = tuple(Query(str(q["sql"]), tuple(str(p) for p in q["params"]), str(q["objetivo"]).strip()) for q in d["consultas"])
    searches = tuple(Search(str(b["texto"]).strip(), str(b["objetivo"]).strip(), tuple(int(i) for i in b["reuniao_ids"]))
                     for b in d["buscas"] if str(b["texto"]).strip())
    return Decision(d["acao"] == "responder", queries, searches, str(d.get("nota") or "").strip())


@dataclass(slots=True)
class Item:
    """Uma consulta ou busca de uma rodada: o texto que volta à LLM, se trouxe novidade, o resumo de uma linha e o trace."""
    ref: str                         # "2.1": rodada 2, item 1
    text: str
    new: bool
    summary: str
    trace: dict[str, Any]


@dataclass(slots=True)
class Round:
    n: int
    note: str
    items: list[Item]
    ms: float = 0.0
    cost_usd: float | None = None
    compact: bool = False

    def text(self) -> str:
        head = f"Rodada {self.n}" + (f" (sua nota: {self.note})" if self.note else "")
        if self.compact:
            gist = "; ".join(f"#{i.ref} {i.summary}" for i in self.items) or "nenhuma consulta"
            return f"{head}, resumida: {gist}. Os resultados saíram do contexto: rode de novo se precisar."
        return "\n\n".join([f"{head}:", *(i.text for i in self.items)]) if self.items else f"{head}: nenhuma consulta nem busca."

    def trace(self) -> dict[str, Any]:
        return {"n": self.n, "nota": self.note, "ms": round(self.ms, 1), "custo_usd": self.cost_usd, "itens": [i.trace for i in self.items]}


def fmt_count(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def cell(value: Any) -> str:
    """Uma célula: `∅` para nulo, os bytes pelo tamanho, tab e quebra escapados e o corte em 300 com "[+N]" (N conta também o
    que a sandbox já cortou na coleta, `Cut.rest`)."""
    if value is None:
        return NULL
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    rest = int(getattr(value, "rest", 0))
    text = format(value, ".10g") if isinstance(value, float) else str(value)
    text = text.replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > CELL_CHARS:
        rest, text = rest + len(text) - CELL_CHARS, text[:CELL_CHARS]
    return f"{text}[+{rest}]" if rest else text


def table(res: QueryResult, cap: int) -> tuple[str, int]:
    """(a tabela compacta até `cap` caracteres, quantas linhas couberam)."""
    lines = ["\t".join(res.columns)]
    used = len(lines[0]) + FOOTER_ROOM
    for row in res.rows:
        line = "\t".join(cell(v) for v in row)
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1
    shown = len(lines) - 1
    if shown >= res.total:
        lines.append(f"{fmt_count(res.total)} {'linha' if res.total == 1 else 'linhas'} · {res.ms:.0f} ms")
    else:
        lines.append(f"{fmt_count(shown)} de {fmt_count(res.total)} linhas: refine ou agregue")
    return "\n".join(lines), shown


def _head(ref: str, q: Query) -> str:
    params = ", ".join(f'"{p}"' for p in q.params)
    return f"#{ref} {q.goal or 'consulta'}\nSQL: {' '.join(q.sql.split())}" + (f"\nparams: [{params}]" if q.params else "")


def _query_trace(ref: str, q: Query, status: str, res: QueryResult | None = None, shown: int = 0) -> dict[str, Any]:
    return {"tipo": "consulta", "ref": ref, "sql": q.sql, "params": list(q.params), "objetivo": q.goal, "status": status,
            "linhas": res.total if res is not None else None, "mostradas": shown, "ms": res.ms if res is not None else None,
            "erro": str(res.error) if res is not None and res.error is not None else None}


def query_item(ref: str, q: Query, res: QueryResult, cap: int, seen: set[int]) -> Item:
    """A consulta que rodou: a tabela (ou o erro, para a LLM corrigir); é novidade a tabela não vazia que ainda não veio."""
    head = _head(ref, q)
    if not res.ok:
        text = f"{head}\nerro ({res.error}): {res.message[:500]}"
        return Item(ref, text, False, f"{q.goal or 'consulta'}: erro ({res.error})", _query_trace(ref, q, "erro", res))
    body, shown = table(res, max(0, cap - len(head) - 1))
    digest = hash((res.columns, res.rows[:shown], res.total))
    new = res.total > 0 and digest not in seen
    seen.add(digest)
    summary = f"{q.goal or 'consulta'}: {fmt_count(res.total)} {'linha' if res.total == 1 else 'linhas'}"
    return Item(ref, f"{head}\n{body}", new, summary, _query_trace(ref, q, "ok", res, shown))


def skipped(ref: str, q: Query, why: str, earlier: str | None = None) -> Item:
    """A consulta que não rodou: repetida (`earlier` é onde está o resultado) ou sem orçamento, tempo ou base."""
    if earlier is not None:
        text = f"#{ref} {q.goal or 'consulta'}: repetida, não rodou de novo (o resultado está em #{earlier})"
        return Item(ref, text, False, "repetida", _query_trace(ref, q, "repetida"))
    return Item(ref, f"#{ref} {q.goal or 'consulta'}: {why}", False, why, _query_trace(ref, q, "nao_rodou"))


def _search_trace(ref: str, s: Search, status: str, found: int = 0, new: int = 0, ms: float | None = None,
                  hybrid: bool | None = None) -> dict[str, Any]:
    return {"tipo": "busca", "ref": ref, "texto": s.text, "objetivo": s.goal, "reuniao_ids": list(s.meeting_ids), "status": status,
            "trechos": found, "novos": new, "ms": ms, "hibrida": hybrid}


def search_item(ref: str, s: Search, blocks: Sequence[MeetingBlock], cap: int, seen: set[tuple[Any, ...]], ms: float,
                hybrid: bool) -> Item:
    """A busca: os trechos em blocos por reunião (`[R<reuniao_id>] título — dia — participantes` e as linhas); é novidade a
    linha que ainda não tinha vindo em outra busca."""
    lines = [(b.meeting_id, ln.t_ms, ln.segment_id, ln.text) for b in blocks for ln in b.lines]
    fresh = [k for k in lines if k not in seen]
    seen.update(lines)
    head = f'#{ref} busca "{s.text}"' + (f" ({s.goal})" if s.goal else "") + f": {len(lines)} trecho(s), {len(fresh)} novo(s)"
    body = "\n\n".join(b.render() for b in blocks) or "nenhum trecho"
    text = f"{head}\n{body}"
    if len(text) > cap:
        text = text[:max(0, cap - 40)].rstrip() + "\n[trechos cortados: refine a busca]"
    summary = f'busca "{s.text[:60]}": {len(lines)} trecho(s)'
    return Item(ref, text, bool(fresh), summary, _search_trace(ref, s, "ok", len(lines), len(fresh), ms, hybrid))


def search_skipped(ref: str, s: Search, why: str) -> Item:
    return Item(ref, f'#{ref} busca "{s.text}": {why}', False, why, _search_trace(ref, s, "nao_rodou"))


def targets(res: QueryResult) -> dict[int, list[SentLine]]:
    """As linhas citáveis de uma consulta: com `reuniao_id`, e o momento por `inicio_ms` (e `fala_id`) quando a linha tem."""
    cols = {c: i for i, c in enumerate(res.columns)}
    if "reuniao_id" not in cols:
        return {}
    mi, ti, fi = cols["reuniao_id"], cols.get("inicio_ms"), cols.get("fala_id")
    out: dict[int, list[SentLine]] = {}
    for row in res.rows:
        if not isinstance(row[mi], int):
            continue
        lines = out.setdefault(int(row[mi]), [])
        t = row[ti] if ti is not None else None
        if isinstance(t, int):
            seg = row[fi] if fi is not None else None
            lines.append(SentLine(t, seg if isinstance(seg, int) else None))
    return out


def rounds_text(rounds: Sequence[Round]) -> str:
    return "\n\n".join(r.text() for r in rounds)


def citable(store: Store, lines: Mapping[int, Collection[SentLine]]) -> dict[str, Target]:
    """As reuniões citáveis (`R<reuniao_id>`) que ainda existem, com as linhas que vieram com momento (da consulta e da busca)."""
    out: dict[str, Target] = {}
    for mid, sent in sorted(lines.items()):
        m = store.get_meeting(mid)
        if m is not None:
            ordered = tuple(sorted(sent, key=lambda ln: (ln.t_ms, ln.segment_id or 0)))
            out[f"R{mid}"] = Target(mid, effective_title(m) or "", str(m.get("started_at") or "")[:10], ordered)
    return out


def legend(targets: Mapping[str, Target]) -> str:
    """"R12 = Orçamento — 2026-10-03", uma reunião citável por linha (o pedido da resposta)."""
    return "\n".join(f"{alias} = {t.title or '(sem título)'} — {t.date}" for alias, t in targets.items())


@dataclass(slots=True)
class Seen:
    """O que já veio nesta pergunta: as consultas com o resultado no contexto (chave -> ref), as tabelas e as linhas de busca."""
    queries: dict[tuple[str, tuple[str, ...]], str] = field(default_factory=dict)
    tables: set[int] = field(default_factory=set)
    lines: set[tuple[Any, ...]] = field(default_factory=set)

    def forget(self, refs: Collection[str]) -> None:
        """A rodada resumida: as consultas dela podem rodar de novo."""
        self.queries = {k: v for k, v in self.queries.items() if v not in refs}


__all__ = [
    "CELL_CHARS",
    "MIN_QUERY_CHARS",
    "NOT_RUN",
    "NO_NEWS_ROUNDS",
    "NULL",
    "QUERY_CHARS",
    "ROUND_CHARS",
    "AgentAnswer",
    "Decision",
    "Guards",
    "Item",
    "Query",
    "Round",
    "Search",
    "Seen",
    "cell",
    "citable",
    "decision",
    "describe",
    "fmt_count",
    "legend",
    "now_text",
    "query_item",
    "rounds_text",
    "search_item",
    "search_skipped",
    "skipped",
    "table",
    "targets",
]
