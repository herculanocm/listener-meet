"""F5.10 L0 (D1, D2, D3): a sandbox de leitura e o autorizador, com visões de teste sobre um banco do app. Zero escrita
(INSERT, UPDATE, DELETE, DROP, CREATE, ATTACH e PRAGMA, também pela conexão crua e por uma CTE antes do INSERT); zero leitura
da lista de negação, inclusive por uma CTE com o nome de uma visão; o MATCH e o `count(*)` pela visão; a consulta infinita e
o produto cartesiano cortados no prazo; uma instrução por chamada; `LIMIT ?` com texto e o `HAVING` que pede `CAST`; as
linhas contadas pelo cursor, que fica fechado (o WAL não fica preso); as funções negadas; as categorias dos erros."""
from __future__ import annotations

import sqlite3
import time

import pytest

from listener.rag import sandbox as sb
from listener.rag.sandbox import Category, Sandbox
from listener.store import Store
from listener.store.rag import ChunkInsert

KEY = "AIzaSy-chave-da-sandbox-ABCDEFGH12345678"


def _data(store: Store) -> int:
    store.people.create_person("Ana", email="ana@exemplo.com", role="CFO", notes="anotação reservada")
    mid = store.create_meeting("Orçamento", None, None, None)
    store._x("UPDATE meetings SET notes = 'nota da reunião' WHERE id = ?", (mid,))
    store.upsert_provider("gemini", "gemini", api_key=KEY)
    store.set_setting("log.level", "DEBUG")
    store.rag.insert_chunks([ChunkInsert(mid, "transcricao", i * 1000, i * 1000 + 900, None, None, "Orçamento", "Ana", (),
                                         f"falamos do orçamento de marketing {i}", f"h{i}", None) for i in range(3)])
    return mid


def _fill(mid: int):
    def fill(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TEMP TABLE _r(reuniao_id INTEGER PRIMARY KEY, titulo TEXT)")
        conn.execute("INSERT INTO temp._r VALUES (?, ?)", (mid, "Orçamento"))
        conn.execute("CREATE TEMP VIEW reunioes AS SELECT reuniao_id, titulo FROM temp._r")
        conn.execute("CREATE TEMP VIEW pessoas AS SELECT id AS pessoa_id, name AS nome, role AS cargo FROM main.people")
        conn.execute("CREATE TEMP VIEW trechos AS SELECT c.id AS trecho_id, c.meeting_id AS reuniao_id, c.text AS texto, "
                     "f.rag_fts AS busca, f.rank AS relevancia FROM main.rag_fts f JOIN main.rag_chunks c ON c.id = f.rowid "
                     "JOIN temp._r r ON r.reuniao_id = c.meeting_id")
    return fill


@pytest.fixture
def box(store: Store):
    mid = _data(store)
    with Sandbox.open(store.main_path, _fill(mid)) as opened:
        yield opened


def _run(box: Sandbox, sql: str, *params: str, rows: int = 200, timeout_s: float = 3.0) -> sb.QueryResult:
    return box.run(sql, params, max_rows=rows, timeout_s=timeout_s)


def test_the_views_read_through_the_policy_and_their_dependencies_were_recorded(box):
    assert box.views == ("reunioes", "pessoas", "trechos")
    assert box.policy.deps["pessoas"] == {("people", "id"), ("people", "name"), ("people", "role")}
    assert box.policy.deps["reunioes"] == frozenset() and {"_r", "reunioes", "pessoas", "trechos"} <= box.policy.temp_names
    res = _run(box, "SELECT nome, cargo FROM pessoas")
    assert res.ok and res.columns == ("nome", "cargo") and res.rows == (("Ana", "CFO"),) and res.total == 1
    assert _run(box, "SELECT count(*) FROM pessoas").rows == ((1,),)                     # coluna vazia, sem contexto [exp]
    match = _run(box, "SELECT trecho_id FROM trechos WHERE busca MATCH ? ORDER BY relevancia", "orcamento")    # sem acento
    assert match.ok and match.total == 3
    assert _run(box, "WITH t AS (SELECT * FROM trechos WHERE busca MATCH ?) SELECT count(*) FROM t", "marketing").rows == ((3,),)
    assert _run(box, "  -- comentário\n/* outro */ WITH x AS (SELECT titulo FROM reunioes) SELECT titulo FROM x").rows == (("Orçamento",),)


@pytest.mark.parametrize("sql", [
    "INSERT INTO temp._r VALUES (9, 'x')", "UPDATE temp._r SET titulo = 'x'", "DELETE FROM temp._r WHERE 1 = 1", "DROP VIEW reunioes",
    "CREATE TEMP TABLE z(a)", "ATTACH 'outro.db' AS outro", "PRAGMA query_only = 0", "PRAGMA main.journal_mode = DELETE",
    "BEGIN", "VACUUM", "REPLACE INTO temp._r VALUES (1, 'x')"])
def test_nothing_writes_neither_through_run_nor_through_the_raw_connection(box, store, sql):
    res = _run(box, sql)
    assert res.error == Category.DENIED and res.message == sb.ONLY_READS                 # a primeira palavra já barra
    with pytest.raises(sqlite3.DatabaseError):
        box.conn.execute(sql)                                                            # e o autorizador, sem o filtro
    assert _run(box, "SELECT count(*) FROM reunioes").rows == ((1,),) and box.views == ("reunioes", "pessoas", "trechos")


def test_a_cte_before_a_write_is_denied_by_the_authorizer(box):
    res = _run(box, "WITH x AS (SELECT 9 AS a) INSERT INTO temp._r SELECT a, 'x' FROM x")
    assert res.error == Category.DENIED and "not authorized" in res.message
    assert _run(box, "SELECT count(*) FROM reunioes").rows == ((1,),)


@pytest.mark.parametrize("sql", [
    "WITH reunioes AS (SELECT api_key FROM providers) SELECT * FROM reunioes",            # o vazamento pelo 5º argumento
    "WITH pessoas AS (SELECT value FROM settings) SELECT * FROM pessoas",
    "WITH pessoas AS (SELECT email FROM people) SELECT * FROM pessoas",
    "WITH pessoas AS (SELECT notes FROM people) SELECT * FROM pessoas",
    "WITH reunioes AS (SELECT notes FROM meetings) SELECT * FROM reunioes",
    "WITH trechos AS (SELECT api_key AS texto FROM providers) SELECT texto FROM trechos",
    "SELECT api_key FROM providers", "SELECT count(*) FROM providers", "SELECT count(*) FROM settings",
    "SELECT name FROM sqlite_master", "SELECT name FROM sqlite_temp_master", "SELECT * FROM pragma_table_info('providers')",
    "SELECT email FROM people", "SELECT nome FROM pessoas, providers WHERE providers.api_key IS NOT NULL",
    "SELECT * FROM rag_vectors", "SELECT title FROM meetings", "SELECT meeting_id FROM rag_chunks",
    # a leitura sem coluna chega com o banco nulo, como a de uma CTE [exp]: nem a contagem de uma tabela negada passa
    "SELECT 1 FROM settings", "SELECT EXISTS (SELECT 1 FROM revisions)", "SELECT count(*) FROM main.providers",
    "WITH v AS (SELECT 1 FROM providers) SELECT count(*) FROM v", "SELECT count(*) FROM captions", "SELECT count(*) FROM meetings",
    "SELECT count(*) FROM pragma_table_info('providers')", "SELECT count(*) FROM sqlite_master"])
def test_the_deny_list_is_unreadable_also_through_a_cte_named_like_a_view(box, sql):
    res = _run(box, sql)
    assert res.error == Category.DENIED and not res.rows and KEY not in res.message


def test_counting_rows_of_a_cte_a_view_or_a_temp_table_works(box):
    assert _run(box, "WITH x AS (SELECT 1 AS a UNION ALL SELECT 2) SELECT count(*) FROM x").rows == ((2,),)
    assert _run(box, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT count(*) FROM n").rows == ((3,),)
    assert _run(box, "SELECT count(*) FROM _r").rows == ((1,),) and _run(box, "SELECT EXISTS (SELECT 1 FROM pessoas)").rows == ((1,),)
    assert _run(box, "SELECT count(*) FROM trechos").rows == ((3,),) and _run(box, "SELECT count(*) FROM rag_chunks").rows == ((3,),)
    assert _run(box, "SELECT count(*) FROM json_each('[1, 2]')").rows == ((2,),)        # monta o que conta; as colunas, não


@pytest.mark.parametrize("fn", ["load_extension('x')", "fts5(NULL)", "fts3_tokenizer('simple')", "randomblob(8)", "zeroblob(8)",
                                "sqlite_version()", "sqlite_source_id()", "sqlite_compileoption_get(0)"])
def test_the_denied_functions(box, fn):
    assert _run(box, f"SELECT {fn}").error == Category.DENIED


def test_the_usual_functions_and_the_fts_helpers_work(box):
    res = _run(box, "SELECT upper(nome), length(nome), strftime('%Y', '2026-10-30'), printf('%02d', 7) FROM pessoas")
    assert res.rows == (("ANA", 3, "2026", "07"),)
    res = _run(box, "SELECT count(*), round(avg(trecho_id), 1) FROM trechos WHERE busca MATCH ?", "orçamento")
    assert res.rows == ((3, 2.0),)


def test_an_endless_query_and_a_cartesian_product_stop_at_the_deadline(box):
    endless = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n"
    product = ("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 100000) "
               "SELECT count(*) FROM n a, n b, pessoas c")
    for sql in (endless, product):
        t0 = time.monotonic()
        res = _run(box, sql, timeout_s=0.4)
        assert res.error == Category.TIMEOUT and time.monotonic() - t0 < 2.0
    streamed = _run(box, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT x FROM n", rows=5, timeout_s=0.3)
    assert streamed.error == Category.TIMEOUT                                            # percorrer o cursor também tem prazo
    assert _run(box, "SELECT count(*) FROM reunioes").ok                                 # a conexão segue usável


def test_one_statement_per_call_and_params_are_text(box):
    two = _run(box, "SELECT 1; SELECT 2")
    assert two.error == Category.SYNTAX and "one statement" in two.message
    assert _run(box, "SELECT 1;").rows == ((1,),)
    assert _run(box, "SELECT reuniao_id FROM reunioes LIMIT ?", "2").ok                   # D3: LIMIT com texto funciona
    assert _run(box, "SELECT count(*) FROM pessoas HAVING count(*) > ?", "0").rows == ()  # texto contra número: vazio
    assert _run(box, "SELECT count(*) FROM pessoas HAVING count(*) > CAST(? AS INTEGER)", "0").rows == ((1,),)
    assert _run(box, "SELECT ?, ?", "a").error == Category.SYNTAX
    assert _run(box, "SELECT " + ", ".join(["?"] * 60), *("x" * 60)).error == Category.LIMIT
    assert box.run("SELECT typeof(?)", (7,), max_rows=5, timeout_s=1).rows == (("text",),)   # todo valor vai como texto


def test_limits_of_the_sqlite(box):
    assert _run(box, "SELECT " + " + ".join(["1"] * 6000)).error == Category.LIMIT                 # SQL_LENGTH
    assert _run(box, " UNION ALL ".join(["SELECT 1"] * 25)).error == Category.LIMIT              # COMPOUND_SELECT
    assert _run(box, "SELECT 'a' LIKE ?", "%" * 300).error == Category.LIMIT                     # LIKE_PATTERN_LENGTH
    assert _run(box, "SELECT nada FROM pessoas").error == Category.SYNTAX
    assert _run(box, "SELEC 1").error == Category.DENIED and _run(box, "").error == Category.DENIED


def test_rows_are_counted_through_the_cursor_and_the_cursor_is_closed(box, store):
    res = _run(box, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5000) SELECT x FROM n", rows=200)
    assert res.ok and len(res.rows) == 200 and res.total == 5000 and res.rows[-1] == (200,)
    store.set_setting("log.level", "INFO")                                               # o app grava depois da consulta...
    busy, _log, _done = store._q("PRAGMA wal_checkpoint(TRUNCATE)")[0]
    assert busy == 0                                                                      # ...e nenhum leitor preso segura o WAL


def test_categories_first_word_and_never():
    assert sb.category(sqlite3.OperationalError("interrupted")) == Category.TIMEOUT
    assert sb.category(sqlite3.DatabaseError("access to providers.api_key is prohibited")) == Category.DENIED
    assert sb.category(sqlite3.OperationalError("attempt to write a readonly database")) == Category.DENIED
    assert sb.category(sqlite3.DataError("query string is too large")) == Category.LIMIT
    assert sb.category(sqlite3.OperationalError("no such table: x")) == Category.SYNTAX
    assert sb.first_word("  -- a\n/* b */ select 1") == "select" and sb.first_word("/* só comentário */") == ""
    assert sb.never("providers") and sb.never("chat_threads") and sb.never("people", "email") and sb.never("sqlite_sequence")
    assert not sb.never("people", "name") and not sb.never("rag_chunks", "text")


# ---------------------------------------------------------------- revisão do diff (D-075): memória, prazo e células
ENDLESS = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n"
MB = 1024 * 1024


class _Memory:
    """Um leitor de memória falso: a base na primeira leitura (o começo da consulta) e `later` nas seguintes."""

    def __init__(self, base: int, later: int | None):
        self.base, self.later, self.calls = base, later, 0

    def __call__(self) -> int | None:
        self.calls += 1
        return self.base if self.calls == 1 else self.later


def test_memory_growth_stops_the_query_with_the_limit_category(store):
    mid = _data(store)
    grows = _Memory(100 * MB, 100 * MB + sb.MEMORY_LIMIT + 1)
    with Sandbox.open(store.main_path, _fill(mid), memory_rss=grows) as box:
        t0 = time.monotonic()
        res = box.run(ENDLESS, (), max_rows=5, timeout_s=10)
        assert res.error == Category.LIMIT and res.message == sb.OUT_OF_MEMORY and time.monotonic() - t0 < 2 and grows.calls >= 2
        box.memory_rss = _Memory(100 * MB, 100 * MB + sb.MEMORY_LIMIT - 1)              # abaixo do teto: só o prazo corta
        assert box.run(ENDLESS, (), max_rows=5, timeout_s=0.3).error == Category.TIMEOUT
        box.memory_rss = _Memory(100 * MB, None)                                          # a plataforma não leu: só o prazo
        assert box.run(ENDLESS, (), max_rows=5, timeout_s=0.3).error == Category.TIMEOUT
        assert box.run("SELECT count(*) FROM pessoas", (), max_rows=5, timeout_s=1).rows == ((1,),)


def test_cancel_is_seen_within_a_few_instructions_and_the_column_limit(box):
    assert sb.PROGRESS_STEPS == 1000
    t0 = time.monotonic()
    res = box.run(ENDLESS, (), max_rows=5, timeout_s=10, should_stop=lambda: time.monotonic() > t0 + 0.2)
    assert res.error == Category.TIMEOUT and time.monotonic() - t0 < 1.0                      # o Cancelar não espera o prazo
    assert _run(box, "SELECT " + ", ".join(str(i) for i in range(70))).error == Category.LIMIT    # SQLITE_LIMIT_COLUMN 64


def test_cells_are_cut_while_collecting_and_blobs_keep_only_their_size(box):
    from listener.rag import agent_results as ar

    res = _run(box, "SELECT printf('%.1000c', 'a') AS t, x'00ff' AS b, 'curto' AS c")
    text, blob, short = res.rows[0]
    assert isinstance(text, sb.Cut) and len(text) == sb.CELL_CHARS and text.rest == 700 and blob == "<2 bytes>" and short == "curto"
    assert ar.cell(text) == "a" * 300 + "[+700]" and ar.cell(short) == "curto"
