"""F5.10 (D1, D2, D3): a sandbox de leitura onde roda o SQL que a LLM escreve, numa conexão própria do arquivo principal em
`mode=ro` (o `Store(READONLY)` anexaria o áudio). A ordem é a provada no venv (SQLite 3.49.1): os limites do SQLite (`LIMITS`)
-> `temp_store = MEMORY` -> `BEGIN`, quem abre cria e preenche as tabelas e visões TEMP (`fill`), `COMMIT` -> `query_only = 1`
(ele bloqueia o TEMP também: por isso depois) -> as dependências de cada visão, gravadas com `SELECT * … LIMIT 0` -> o
autorizador (`Policy`). O prazo de cada consulta é um progress handler a cada 10.000 instruções.

O autorizador é uma lista de permissão: SELECT, READ, FUNCTION (só as de `FUNCTIONS`), RECURSIVE e o `PRAGMA data_version`
sem argumento (o FTS5 o roda por dentro); tudo o mais é negado (escrita, ATTACH, PRAGMA, transação). Ler o `main` só no
contexto de uma visão da sandbox e só o par (tabela, coluna) que a visão lê: decidir só pelo 5º argumento vazava, porque uma
CTE com o nome de uma visão (`WITH reunioes AS (SELECT api_key FROM providers)`) chega com o nome dela ali [exp]. Fora disso,
o FTS: as tabelas `rag_fts*` e `rag_chunks(id, title, speakers, text)`, que o FTS5 lê sem contexto. A leitura sem coluna
(`count(*)`, `EXISTS`, `SELECT 1 FROM t`) chega com a coluna vazia, sem contexto e com o banco como foi escrito, nulo quando
não foi [exp, 30/10]: é ela o "banco nulo" das CTEs de D2, e por ela `SELECT count(*) FROM providers` passaria. Por isso vale
pelo nome: tabela do arquivo só se alguma visão a lê (ou é do FTS); TEMP só as da sandbox; nome que não é tabela do arquivo
(uma CTE, `json_each`) passa, porque só conta linhas do que a própria consulta montou. Nunca legíveis, nem por uma visão
(`never`): `providers`, `settings`, e-mail e notas das pessoas, notas das reuniões, o conteúdo e o erro das chamadas de LLM, as
conversas, revisões, eventos, presets, a fila, as vozes, os vetores e o catálogo do SQLite.

`run` recusa o que não começa por SELECT ou WITH, roda uma instrução só (o `execute` do Python recusa duas), com os valores em
`params` (sempre texto, D3), percorre o cursor dentro do prazo para contar as linhas, guarda as primeiras `max_rows` e fecha
o cursor sempre (um cursor aberto seguraria a leitura e o WAL cresceria). Do erro, a categoria (`Category`) vai para o log e
para o `trace_json`; a mensagem do SQLite volta só à LLM, porque traz nomes e valores."""
from __future__ import annotations

import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from ..log import get_logger

log = get_logger("rag.sandbox")

LIMITS: tuple[tuple[int, int], ...] = (
    (sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000), (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
    (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 50), (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20), (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 200),
    (sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 200))
PROGRESS_STEPS = 10_000          # o prazo é conferido a cada tantas instruções da máquina virtual do SQLite
FETCH = 500                      # linhas por `fetchmany` ao percorrer o cursor
BUSY_TIMEOUT_S = 5.0
FIRST_WORD_RE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*(\w+)", re.DOTALL)
READ_ONLY_WORDS = frozenset({"select", "with"})
ONLY_READS = "só consultas de leitura: comece por SELECT ou WITH (uma instrução por consulta)"
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
    """Tabela ou coluna que nenhuma consulta lê, nem por uma visão (D2)."""
    return table in NEVER_TABLES or table.startswith(NEVER_PREFIXES) or (table, column) in NEVER_COLUMNS


def first_word(sql: str) -> str:
    """A primeira palavra do SQL, depois de espaços e comentários, em minúsculas ("" sem palavra)."""
    m = FIRST_WORD_RE.match(sql or "")
    return m.group(1).lower() if m else ""


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
        self.deps = dict(deps)
        self.temp_names = temp_names
        self.main_tables = main_tables
        self.dep_tables = frozenset(t for pairs in self.deps.values() for t, _c in pairs if not never(t))

    def __call__(self, action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            allowed = self.can_read(arg1 or "", arg2 or "", db, source)
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = (arg2 or "").lower() in FUNCTIONS
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = arg1 == "data_version" and arg2 is None
        else:
            allowed = False
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    def can_read(self, table: str, column: str, db: str | None, source: str | None) -> bool:
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


class Sandbox:
    """Uma conexão só de leitura com as TEMP de quem abriu e o autorizador ligado; `run` roda uma consulta."""

    def __init__(self, conn: sqlite3.Connection, policy: Policy, views: tuple[str, ...]):
        self.conn = conn
        self.policy = policy
        self.views = views
        self._deadline = math.inf
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
            policy = Policy(_record(conn, views), frozenset(str(n) for n, _kind in names), main)
        except BaseException:
            conn.close()
            raise
        return cls(conn, policy, views)

    def _tick(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def run(self, sql: str, params: Sequence[object] = (), *, max_rows: int, timeout_s: float) -> QueryResult:
        """Uma consulta de leitura com prazo: as primeiras `max_rows` linhas e o total, ou a categoria do erro."""
        t0 = time.perf_counter()
        if first_word(sql) not in READ_ONLY_WORDS:
            return QueryResult((), (), 0, _ms(t0), Category.DENIED, ONLY_READS)
        self._deadline = time.monotonic() + max(0.05, float(timeout_s))
        cur: sqlite3.Cursor | None = None
        try:
            cur = self.conn.execute(sql, tuple(str(p) for p in params))
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
            self._deadline = math.inf
            if cur is not None:
                cur.close()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


__all__ = ["FUNCTIONS", "LIMITS", "NEVER_COLUMNS", "NEVER_PREFIXES", "NEVER_TABLES", "ONLY_READS", "Category", "Policy", "QueryResult",
           "Sandbox", "category", "first_word", "never"]
