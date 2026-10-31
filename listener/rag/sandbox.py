"""F5.10 (D1, D2, D3): a sandbox de leitura onde roda o SQL que a LLM escreve, numa conexão própria do arquivo principal em
`mode=ro` (o `Store(READONLY)` anexaria o áudio). A ordem é a provada no venv (SQLite 3.49.1): os limites do SQLite (`LIMITS`)
-> `temp_store = MEMORY` -> `BEGIN`, quem abre cria e preenche as tabelas e visões TEMP (`fill`), `COMMIT` -> `query_only = 1`
(ele bloqueia o TEMP também: por isso depois) -> as dependências de cada visão, gravadas com `SELECT * … LIMIT 0` -> o
autorizador (`Policy`). O prazo de cada consulta é um progress handler a cada 10.000 instruções.

O autorizador é uma lista de permissão: SELECT, READ, FUNCTION (só as de `FUNCTIONS`), RECURSIVE e o `PRAGMA data_version`
sem argumento (o FTS5 o roda por dentro); tudo o mais é negado (escrita, ATTACH, PRAGMA, transação). Ler o `main` só no
contexto de uma visão da sandbox e só o par (tabela, coluna) que a visão lê: decidir só pelo 5º argumento vazava, porque uma
CTE com o nome de uma visão (`WITH reunioes AS (SELECT api_key FROM providers)`) chega com o nome dela ali [exp]. Fora disso,
o FTS: as tabelas `rag_fts*` e `rag_chunks(id, title, speakers, text)`, que o FTS5 lê sem contexto. Os nomes valem sem
diferenciar caixa (`count(*) FROM PROVIDERS` é `providers`). A leitura sem coluna
(`count(*)`, `EXISTS`, `SELECT 1 FROM t`) chega com a coluna vazia, sem contexto e com o banco como foi escrito, nulo quando
não foi [exp, 30/10]: é ela o "banco nulo" das CTEs de D2, e por ela `SELECT count(*) FROM providers` passaria. Por isso vale
pelo nome: tabela do arquivo só se alguma visão a lê (ou é do FTS); TEMP só as da sandbox; nome que não é tabela do arquivo
(uma CTE, `json_each`) passa, porque só conta linhas do que a própria consulta montou. Nunca legíveis, nem por uma visão
(`never`): `providers`, `settings`, e-mail e notas das pessoas, notas das reuniões, o conteúdo e o erro das chamadas de LLM, as
conversas, revisões, eventos, presets, a fila, as vozes, os vetores e o catálogo do SQLite.

Revisão do diff (D-075): a coluna de uma junção com USING ou NATURAL nasce fora do `lookupName`, e o SQLite não chama o
autorizador para ela: `x FULL JOIN providers USING (api_key)` devolvia a chave [exp]. Por isso mais duas camadas: `run` recusa
as palavras USING e NATURAL fora de literais, identificadores entre aspas e comentários (`join_words`); e, sem depender do
parser, compila `EXPLAIN ` mais a instrução (com os mesmos `params`: nenhum valor entra no texto) e recusa o programa que abre
para escrita ou que lê no `main` uma página-raiz fora das tabelas que as visões leem, dos índices delas e das sombras do FTS5,
guardadas na abertura (`_roots`). Nenhuma visão lê `people` (a camada semântica a materializa); `llm_calls`, que a de custos
lê, fica com o autorizador e a recusa das palavras.

`run` recusa o que não começa por SELECT ou WITH, roda uma instrução só (o `execute` do Python recusa duas), com os valores em
`params` (sempre texto, D3), percorre o cursor dentro do prazo para contar as linhas, guarda as primeiras `max_rows` e fecha
o cursor sempre (um cursor aberto seguraria a leitura e o WAL cresceria). Do erro, a categoria (`Category`) vai para o log e
para o `trace_json`; a mensagem do SQLite volta só à LLM, porque traz nomes e valores."""
from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from ..log import get_logger
from .sql_text import READ_ONLY_WORDS, first_word, join_words

log = get_logger("rag.sandbox")

LIMITS: tuple[tuple[int, int], ...] = (
    (sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000), (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
    (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 50), (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20), (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 200),
    (sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 200))
PROGRESS_STEPS = 10_000          # o prazo é conferido a cada tantas instruções da máquina virtual do SQLite
FETCH = 500                      # linhas por `fetchmany` ao percorrer o cursor
BUSY_TIMEOUT_S = 5.0
ONLY_READS = "só consultas de leitura: comece por SELECT ou WITH (uma instrução por consulta)"
NO_JOIN_WORDS = "use JOIN … ON: USING e NATURAL não são aceitos"
OUTSIDE_VIEWS = "a consulta lê uma tabela fora das visões do dicionário: use só as visões"
READ_OPS = frozenset({"OpenRead", "ReopenIdx"})
P2_IN_REGISTER = 0x10            # OPFLAG_P2ISREG (0x02 é OPFLAG_SEEKEQ, que a busca por igualdade num índice usa) [exp]
FTS_PREFIX = "rag_fts"
FTS_CONTENT = ("rag_chunks", frozenset({"id", "title", "speakers", "text"}))   # o que o FTS5 lê da tabela de conteúdo
NEVER_TABLES = frozenset({"providers", "settings", "revisions", "events", "presets", "processing_jobs", "clusters", "rag_vectors",
                          "job_artifacts"})
NEVER_PREFIXES = ("chat_", "voice_", "sqlite_", "pragma_")
NEVER_COLUMNS = frozenset({("people", "email"), ("people", "notes"), ("meetings", "notes"), ("llm_calls", "request_blob"),
                           ("llm_calls", "response_blob"), ("llm_calls", "error")})
# as funções aceitas; fora da lista (e negadas por nome em D2): load_extension, fts5, fts3_tokenizer, randomblob, zeroblob, sqlite_*
FUNCTIONS = frozenset({
    "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "ifnull", "iif", "instr", "length", "like",
    "likelihood", "likely", "lower", "ltrim", "max", "min", "nullif", "octet_length", "printf", "quote", "random", "replace", "round",
    "rtrim", "sign", "substr", "substring", "trim", "typeof", "unhex", "unicode", "unlikely", "upper",
    "date", "time", "datetime", "julianday", "unixepoch", "strftime", "timediff",
    "avg", "count", "group_concat", "string_agg", "sum", "total",
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value", "last_value", "nth_value",
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "ceil", "ceiling", "cos", "cosh", "degrees", "exp", "floor", "ln", "log",
    "log10", "log2", "mod", "pi", "pow", "power", "radians", "sin", "sinh", "sqrt", "tan", "tanh", "trunc",
    "json", "json_array", "json_array_length", "json_extract", "json_object", "json_quote", "json_type", "json_valid", "->", "->>",
    "json_group_array", "json_group_object",
    "match", "bm25", "highlight", "snippet"})


class Category(StrEnum):
    """A categoria do erro de uma consulta: o que o log e o `trace_json` guardam (D8)."""
    SYNTAX = "sintaxe"
    DENIED = "negado"
    TIMEOUT = "prazo"
    LIMIT = "limite"


def category(e: BaseException) -> Category:
    """A categoria pela mensagem do SQLite (a mensagem em si nunca vai ao log: traz nomes e valores)."""
    text = str(e).lower()
    if "interrupted" in text:
        return Category.TIMEOUT
    if any(k in text for k in ("not authorized", "prohibited", "readonly", "read-only", "attempt to write")):
        return Category.DENIED
    if any(k in text for k in ("too big", "too many", "too large", "too long", "too complex", "too deep")):
        return Category.LIMIT
    return Category.SYNTAX


def never(table: str, column: str = "") -> bool:
    """Tabela ou coluna que nenhuma consulta lê, nem por uma visão (D2); sem diferenciar caixa."""
    table, column = table.lower(), column.lower()
    return table in NEVER_TABLES or table.startswith(NEVER_PREFIXES) or (table, column) in NEVER_COLUMNS


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]   # as primeiras `max_rows`
    total: int                          # todas as linhas, contadas percorrendo o cursor
    ms: float
    error: Category | None = None
    message: str = ""                   # a mensagem do SQLite: volta à LLM, nunca ao log

    @property
    def ok(self) -> bool:
        return self.error is None


class Policy:
    """O autorizador (D2): uma lista de permissão com as dependências de cada visão, gravadas antes de ligar."""

    def __init__(self, deps: Mapping[str, frozenset[tuple[str, str]]], temp_names: frozenset[str], main_tables: frozenset[str]):
        self.deps = {v.lower(): frozenset((t.lower(), c.lower()) for t, c in pairs) for v, pairs in deps.items()}
        self.temp_names = frozenset(n.lower() for n in temp_names)
        self.main_tables = frozenset(n.lower() for n in main_tables)
        self.dep_tables = frozenset(t for pairs in self.deps.values() for t, _c in pairs if not never(t))

    def __call__(self, action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            allowed = self.can_read((arg1 or "").lower(), (arg2 or "").lower(), db.lower() if db is not None else None,
                                    source.lower() if source is not None else None)
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = (arg2 or "").lower() in FUNCTIONS
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = (arg1 or "").lower() == "data_version" and arg2 is None
        else:
            allowed = False
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    def can_read(self, table: str, column: str, db: str | None, source: str | None) -> bool:
        """Os nomes já em minúsculas (`__call__`)."""
        if never(table, column):
            return False
        if column == "":                                   # leitura sem coluna: o banco vem como foi escrito [exp]
            return self.can_count(table, db)
        if db == "temp":
            return table in self.temp_names
        if db != "main":
            return False
        if table.startswith(FTS_PREFIX) or (table == FTS_CONTENT[0] and column in FTS_CONTENT[1]):
            return True
        return source in self.deps and (table, column) in self.deps[source]

    def can_count(self, table: str, db: str | None) -> bool:
        """`count(*)`, `EXISTS` e `SELECT 1 FROM t`: só a contagem de linhas, sem o contexto da visão."""
        if db == "temp" or (db is None and table in self.temp_names):
            return table in self.temp_names
        if table in self.main_tables:
            return table in self.dep_tables or table.startswith(FTS_PREFIX)
        return db is None                                  # uma CTE, uma função de tabela: não é tabela do arquivo


type Fill = Callable[[sqlite3.Connection], None]


def _connect(main_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(main_path).as_uri()}?mode=ro", uri=True, autocommit=True, timeout=BUSY_TIMEOUT_S)
    for limit, value in LIMITS:
        conn.setlimit(limit, value)
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _record(conn: sqlite3.Connection, views: Sequence[str]) -> dict[str, frozenset[tuple[str, str]]]:
    """(tabela, coluna) do `main` que cada visão lê, gravados com `SELECT * … LIMIT 0` (o nome vem do próprio catálogo)."""
    seen: dict[str, set[tuple[str, str]]] = {v: set() for v in views}

    def record(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        if action == sqlite3.SQLITE_READ and db == "main" and source in seen:
            seen[source].add((arg1 or "", arg2 or ""))
        return sqlite3.SQLITE_OK

    conn.set_authorizer(record)
    try:
        for view in views:
            conn.execute(f'SELECT * FROM temp."{view}" LIMIT 0').close()   # noqa: S608 - nome lido do sqlite_temp_master
    finally:
        conn.set_authorizer(None)
    return {v: frozenset(pairs) for v, pairs in seen.items()}


def _roots(conn: sqlite3.Connection, deps: Mapping[str, frozenset[tuple[str, str]]]) -> frozenset[int]:
    """As páginas-raiz que uma consulta pode abrir no `main`: as tabelas que as visões leem, os índices delas e as sombras do
    FTS5 (`rag_fts_*`), lidas antes do autorizador."""
    tables = {t.lower() for pairs in deps.values() for t, _c in pairs if not never(t)}
    rows = conn.execute("SELECT tbl_name, rootpage FROM main.sqlite_master WHERE type IN ('table', 'index')").fetchall()
    return frozenset(int(root) for tbl, root in rows
                     if root and (str(tbl).lower() in tables or str(tbl).lower().startswith(f"{FTS_PREFIX}_")))


def _allowed_op(row: Sequence[Any], roots: frozenset[int]) -> bool:
    """Uma instrução do programa compilado (`EXPLAIN`): nada aberto para escrita; leitura só na TEMP (P3 = 1) ou no `main`
    (P3 = 0) numa página-raiz de `roots`, nunca com a raiz vinda de um registrador."""
    op, p2, p3, p5 = str(row[1]), row[3], row[4], int(row[6] or 0)
    if op == "OpenWrite":
        return False
    if op not in READ_OPS:
        return True
    if p5 & P2_IN_REGISTER or p3 not in (0, 1):
        return False
    return p3 == 1 or p2 in roots


class Sandbox:
    """Uma conexão só de leitura com as TEMP de quem abriu e o autorizador ligado; `run` roda uma consulta."""

    def __init__(self, conn: sqlite3.Connection, policy: Policy, views: tuple[str, ...], roots: frozenset[int] = frozenset()):
        self.conn = conn
        self.policy = policy
        self.views = views
        self.roots = roots
        self._deadline = math.inf
        self._stop: Callable[[], bool] | None = None
        conn.set_authorizer(policy)
        conn.set_progress_handler(self._tick, PROGRESS_STEPS)

    @classmethod
    def open(cls, main_path: Path, fill: Fill) -> Self:
        """A conexão na ordem de D1: limites, TEMP na memória, `fill` numa transação, `query_only`, dependências, política."""
        conn = _connect(main_path)
        try:
            conn.execute("BEGIN")
            fill(conn)
            conn.execute("COMMIT")
            conn.execute("PRAGMA query_only = 1")
            names = conn.execute("SELECT name, type FROM temp.sqlite_master WHERE type IN ('table', 'view')").fetchall()
            main = frozenset(str(r[0]) for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type IN ('table', 'view')"))
            views = tuple(str(n) for n, kind in names if kind == "view")
            deps = _record(conn, views)
            policy = Policy(deps, frozenset(str(n) for n, _kind in names), main)
            roots = _roots(conn, deps)
        except BaseException:
            conn.close()
            raise
        return cls(conn, policy, views, roots)

    def _tick(self) -> int:
        return 1 if time.monotonic() > self._deadline or (self._stop is not None and self._stop()) else 0

    def run(self, sql: str, params: Sequence[object] = (), *, max_rows: int, timeout_s: float,
            should_stop: Callable[[], bool] | None = None) -> QueryResult:
        """Uma consulta de leitura com prazo: as primeiras `max_rows` linhas e o total, ou a categoria do erro. `should_stop()`
        verdadeiro (a pergunta cancelada) interrompe a consulta como o prazo."""
        t0 = time.perf_counter()
        if first_word(sql) not in READ_ONLY_WORDS:
            return QueryResult((), (), 0, _ms(t0), Category.DENIED, ONLY_READS)
        if join_words(sql):
            return QueryResult((), (), 0, _ms(t0), Category.DENIED, NO_JOIN_WORDS)
        args = tuple(str(p) for p in params)
        self._deadline = time.monotonic() + max(0.05, float(timeout_s))
        self._stop = should_stop
        cur: sqlite3.Cursor | None = None
        try:
            if not self._reads_only_views(sql, args):
                return QueryResult((), (), 0, _ms(t0), Category.DENIED, OUTSIDE_VIEWS)
            cur = self.conn.execute(sql, args)
            columns = tuple(str(d[0]) for d in cur.description or ())
            kept: list[tuple[Any, ...]] = []
            total = 0
            while batch := cur.fetchmany(FETCH):
                total += len(batch)
                if len(kept) < max_rows:
                    kept += batch[:max_rows - len(kept)]
            return QueryResult(columns, tuple(tuple(r) for r in kept), total, _ms(t0))
        except (sqlite3.Error, sqlite3.Warning) as e:
            return QueryResult((), (), 0, _ms(t0), category(e), str(e))
        finally:
            self._deadline, self._stop = math.inf, None
            if cur is not None:
                cur.close()

    def _reads_only_views(self, sql: str, args: tuple[str, ...]) -> bool:
        """A camada que não depende do parser: o programa compilado só abre o que as visões leem. O prefixo fixo `EXPLAIN ` é
        a única composição de texto SQL da sandbox; os valores seguem em `params`."""
        cur = self.conn.execute("EXPLAIN " + sql, args)
        try:
            program = cur.fetchall()
        finally:
            cur.close()
        return all(_allowed_op(row, self.roots) for row in program)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


__all__ = ["FUNCTIONS", "LIMITS", "NEVER_COLUMNS", "NEVER_PREFIXES", "NEVER_TABLES", "NO_JOIN_WORDS", "ONLY_READS", "OUTSIDE_VIEWS",
           "Category", "Policy", "QueryResult", "Sandbox", "category", "first_word", "join_words", "never"]
