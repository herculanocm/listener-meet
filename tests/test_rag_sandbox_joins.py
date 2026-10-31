"""F5.10, revisão do diff (D-075), com a camada semântica real: a coluna de uma junção com USING ou NATURAL nasce fora do
`lookupName` e o SQLite não chamava o autorizador para ela (`x FULL JOIN providers USING (api_key)` devolvia a chave). Três
camadas: a recusa das palavras USING e NATURAL fora de literais, aspas e comentários; nenhuma visão lendo `main.people`; e o
programa compilado (`EXPLAIN`) que só abre as páginas-raiz das visões. Cada par negado, por toda forma de junção, dá negado sem
linha; o oráculo de igualdade também; USING dentro de literal, nome entre aspas ou comentário roda; sem a recusa das
palavras, o programa compilado sozinho nega `providers` e `people`; as consultas legítimas passam. C3: os nomes valem sem
diferenciar caixa."""
from __future__ import annotations

from typing import Any

import pytest

from listener.model import ChatRole, LlmCallStatus
from listener.rag import sandbox, semantic
from listener.rag.sandbox import Category, Sandbox
from listener.rag.sql_text import join_words
from listener.store import Store
from listener.store.llm_calls import LlmCallInsert
from tests.test_rag_index import _meeting, _people

KEY = "AIzaSy-chave-das-juncoes-ABCDEFGH12345678"
SECRETS = (KEY, "ana@exemplo.com", "NOTA-DA-PESSOA", "NOTA-DA-REUNIAO", "PEDIDO-GUARDADO", "CONVERSA-PRIVADA", "ERRO-GUARDADO",
           "CREATE TABLE")
PAIRS = [("providers", "api_key"), ("settings", "value"), ("people", "email"), ("people", "notes"), ("meetings", "notes"),
         ("chat_messages", "text"), ("llm_calls", "error"), ("llm_calls", "request_blob"), ("llm_calls", "response_blob"),
         ("sqlite_master", "sql")]
FORMS = ["FROM x FULL JOIN {t} USING ({c})", "FROM {t} LEFT JOIN x USING ({c})", "FROM x RIGHT JOIN {t} USING ({c})",
         "FROM x INNER JOIN {t} USING ({c})", "FROM x NATURAL JOIN {t}", "FROM x NATURAL FULL JOIN {t}"]


@pytest.fixture(autouse=True)
def fresh_cache():
    semantic.CACHE.clear()
    yield
    semantic.CACHE.clear()


@pytest.fixture
def box(store: Store):
    who = _people(store)
    store.people.update_person(who["ana"], "Ana", email="ana@exemplo.com", role="CFO")
    store._x("UPDATE people SET notes = 'NOTA-DA-PESSOA' WHERE id = ?", (who["ana"],))
    mid, _job = _meeting(store, who)
    store._x("UPDATE meetings SET notes = 'NOTA-DA-REUNIAO' WHERE id = ?", (mid,))
    store.upsert_provider("gemini", "gemini", api_key=KEY)
    store.set_setting("log.level", "DEBUG")
    store.llm_calls.insert(LlmCallInsert(mid, None, "chat", "gemini", "m", None, None, 1, 1, 0, 0, 0.001, 5, LlmCallStatus.ERROR,
                                         "ERRO-GUARDADO", "direct", {"t": "PEDIDO-GUARDADO"}, {"t": "PEDIDO-GUARDADO"}))
    store.chat.add(None, ChatRole.USER, "CONVERSA-PRIVADA", thread_id=store.chat.create_thread("t"))
    with semantic.open_sandbox(store) as opened:
        yield opened


def _run(box: Sandbox, sql: str, *params: str) -> sandbox.QueryResult:
    return box.run(sql, params, max_rows=50, timeout_s=3)


def _clean(res: sandbox.QueryResult) -> bool:
    return res.error == Category.DENIED and not res.rows and not any(s in res.message for s in SECRETS)


def test_join_words_reads_the_sql_like_the_tokenizer():
    for sql in ("SELECT a FROM x JOIN y USING (a)", "select * from x natural join y", "SELECT 1 FROM x\nUsInG(a)", "x NATURAL\tFULL y"):
        assert join_words(sql), sql
    for sql in ("SELECT 'USING' AS u", "SELECT 'it''s NATURAL' AS t", 'SELECT "using" FROM t', "SELECT [natural] FROM t",
                "SELECT `Using` FROM t", "SELECT 1 -- USING\n", "SELECT 1 /* NATURAL JOIN */", "SELECT x'55534947' AS b",
                "SELECT usingx, natural_ FROM t", "SELECT $using", "SELECT 'unterminated USING", "SELECT éUSING FROM t"):
        assert not join_words(sql), sql                                                      # `$` e `é` são de nome, como no SQLite
    assert join_words("SELECT :natural")                                                     # o `:` separa: recusa a mais, inofensiva


@pytest.mark.parametrize("table,column", PAIRS)
@pytest.mark.parametrize("form", FORMS)
def test_every_denied_pair_through_using_or_natural_is_denied_without_rows(box, table, column, form):
    res = _run(box, f"WITH x({column}) AS (SELECT NULL WHERE 0) SELECT {column} {form.format(t=table, c=column)}")
    assert _clean(res) and res.message == sandbox.NO_JOIN_WORDS


def test_the_equality_oracle_is_denied(box):
    res = _run(box, "WITH x(email) AS (VALUES ('ana@exemplo.com'), ('outro@x.com')) SELECT x.email FROM x JOIN people USING (email)")
    assert _clean(res)


def test_using_inside_literals_quoted_names_and_comments_runs(box):
    for sql in ("SELECT 'USING' AS u", 'SELECT "natural" FROM (SELECT 1 AS "natural")', "SELECT [using] FROM (SELECT 2 AS [using])",
                "SELECT `Natural` FROM (SELECT 3 AS `Natural`)", "SELECT 1 -- USING (reuniao_id)\n", "SELECT 1 /* NATURAL JOIN */",
                "SELECT x'4E41' AS b", "SELECT 'it''s USING' AS t"):
        assert _run(box, sql).ok, sql


def test_without_the_word_check_the_compiled_program_alone_denies_providers_and_people(box, monkeypatch):
    monkeypatch.setattr(sandbox, "join_words", lambda _sql: False)
    for table, column in (("providers", "api_key"), ("people", "email"), ("people", "notes"), ("meetings", "notes"),
                          ("sqlite_master", "sql"), ("chat_messages", "text")):
        res = _run(box, f"WITH x({column}) AS (SELECT NULL WHERE 0) SELECT {column} FROM x FULL JOIN {table} USING ({column})")  # noqa: S608
        assert _clean(res), table
    oracle = _run(box, "WITH x(email) AS (VALUES ('ana@exemplo.com')) SELECT x.email FROM x JOIN people USING (email)")
    assert _clean(oracle) and oracle.message == sandbox.OUTSIDE_VIEWS
    assert _run(box, "SELECT count(*) FROM falas").ok                                      # o legítimo segue


@pytest.mark.parametrize("sql", [
    "SELECT * FROM reunioes", "SELECT * FROM pessoas", "SELECT * FROM participacao", "SELECT * FROM falas WHERE reuniao_id = 1",
    "SELECT * FROM marcadores WHERE reuniao_id = 1", "SELECT * FROM acoes", "SELECT * FROM ata_itens WHERE reuniao_id = 1",
    "SELECT * FROM resumos WHERE reuniao_id = 1", "SELECT * FROM custos_llm WHERE reuniao_id = 1", "SELECT * FROM agora",
    "SELECT trecho_id FROM trechos WHERE busca MATCH 'auditoria' ORDER BY relevancia",
    "SELECT p.falante, r.titulo FROM participacao p JOIN reunioes r ON r.reuniao_id = p.reuniao_id",
    "SELECT f.texto FROM falas f JOIN marcadores m ON m.reuniao_id = f.reuniao_id", "SELECT count(*) FROM rag_chunks"])
def test_legit_queries_pass_the_compiled_check(box, sql):
    res = _run(box, sql)
    assert res.ok, res.message


@pytest.mark.parametrize("sql", ["SELECT count(*) FROM PROVIDERS", "SELECT count(*) FROM Settings", "SELECT count(*) FROM SQLITE_MASTER",
                                 "SELECT count(*) FROM Chat_Messages", "SELECT count(*) FROM Meetings", "SELECT 1 FROM MAIN.Providers",
                                 "SELECT EXISTS (SELECT 1 FROM PEOPLE)", "SELECT count(*) FROM Revisions"])
def test_names_ignore_case(box, sql):
    assert _clean(_run(box, sql))


def test_view_names_in_another_case_still_work(box):
    rows: Any = _run(box, "SELECT count(*) FROM REUNIOES").rows
    assert rows == ((1,),) and _run(box, "SELECT count(*) FROM Falas").ok
