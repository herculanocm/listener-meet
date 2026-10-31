"""F5.10 L3, sem rede nem laço: a tabela compacta (∅, tab e quebra escapados, célula até 300 com "[+N]", os rodapés "37 linhas
· 12 ms" e "200 de 5.120 linhas: refine ou agregue"), a decisão da rodada validada no esquema, os alvos citáveis de uma
consulta, as citações `R<reuniao_id>` com até 6 dígitos, os prompts `rag_sql` e `rag_answer` (o dicionário no prefixo, o
contrato por último, `responder` primeiro no enum), os passos (`trace_json`) e as configurações e guardas da F5.10."""
from __future__ import annotations

import pytest

from listener.config import defaults
from listener.llm import prompts
from listener.llm.prompts import rag_answer, rag_sql
from listener.llm.schema import SchemaError, check_strict, minimal_instance
from listener.llm.types import TASK_CLASS, LlmTask, ModelClass, Role
from listener.rag import agent_results as ar
from listener.rag import citations, trace
from listener.rag.agent_results import Guards
from listener.rag.chat_global import retrieve
from listener.rag.citations import SentLine
from listener.rag.sandbox import Category, QueryResult
from listener.store.rag import SearchFilters
from tests.test_rag_chat import _two_meetings


def _res(columns: tuple[str, ...], rows: list[tuple[object, ...]], total: int | None = None, ms: float = 12.0) -> QueryResult:
    return QueryResult(columns, tuple(rows), len(rows) if total is None else total, ms)


def test_cells_escape_tabs_and_breaks_mark_nulls_and_cut_long_text():
    assert ar.cell(None) == "∅" and ar.cell(b"\x00\x01") == "<2 bytes>" and ar.cell(0.1 + 0.2) == "0.3" and ar.cell(7) == "7"
    assert ar.cell("a\tb\nc\r") == "a\\tb\\nc\\r"
    long = "x" * 350
    assert ar.cell(long) == "x" * 300 + "[+50]"


def test_the_compact_table_and_its_footers():
    text, shown = ar.table(_res(("reuniao_id", "titulo"), [(i, f"Reunião {i}") for i in range(37)]), 8000)
    lines = text.split("\n")
    assert lines[0] == "reuniao_id\ttitulo" and lines[1] == "0\tReunião 0" and lines[-1] == "37 linhas · 12 ms" and shown == 37
    one, _ = ar.table(_res(("n",), [(1,)]), 8000)
    assert one.endswith("\n1 linha · 12 ms")
    cut, shown = ar.table(_res(("x",), [(i,) for i in range(200)], total=5120), 8000)
    assert cut.endswith("200 de 5.120 linhas: refine ou agregue") and shown == 200
    tight, shown = ar.table(_res(("t",), [("y" * 90,) for _ in range(200)]), 1000)
    assert 0 < shown < 200 and tight.endswith(f"{shown} de 200 linhas: refine ou agregue") and len(tight) <= 1000


def test_the_round_decision_is_validated_and_keys_normalize_spaces():
    searches = [{"texto": " orçamento ", "objetivo": "", "reuniao_ids": [3]}, {"texto": "  ", "objetivo": "", "reuniao_ids": []}]
    d = ar.decision({"acao": "consultar", "consultas": [{"sql": "SELECT  1\n FROM x", "params": ["a"], "objetivo": " um "}],
                     "buscas": searches, "nota": None})
    assert not d.answer and d.queries[0].key == ("SELECT 1 FROM x", ("a",)) and d.queries[0].goal == "um"
    assert d.searches == (ar.Search("orçamento", "", (3,)),) and d.note == ""                    # busca vazia some
    assert ar.decision(minimal_instance(rag_sql.SCHEMA)).answer                                # o provedor falso encerra o laço
    with pytest.raises(SchemaError):
        ar.decision({"acao": "talvez", "consultas": [], "buscas": [], "nota": None})


def test_query_items_errors_repeats_and_novelty():
    q = ar.Query("SELECT reuniao_id FROM reunioes", (), "listar")
    seen: set[int] = set()
    first = ar.query_item("1.1", q, _res(("reuniao_id",), [(5,)]), 8000, seen)
    again = ar.query_item("2.1", q, _res(("reuniao_id",), [(5,)]), 8000, seen)
    empty = ar.query_item("2.2", q, _res(("reuniao_id",), []), 8000, seen)
    bad = ar.query_item("2.3", q, QueryResult((), (), 0, 1.0, Category.SYNTAX, "no such column: x"), 8000, seen)
    assert (first.new, again.new, empty.new, bad.new) == (True, False, False, False)
    assert first.text.startswith("#1.1 listar\nSQL: SELECT reuniao_id FROM reunioes\nreuniao_id\n5\n1 linha")
    assert bad.text.endswith("erro (sintaxe): no such column: x") and bad.trace["erro"] == "sintaxe" and bad.trace["status"] == "erro"
    repeated = ar.skipped("3.1", q, "", "1.1")
    assert "repetida" in repeated.text and "#1.1" in repeated.text and repeated.trace["status"] == "repetida"
    assert ar.skipped("3.2", q, ar.NOT_RUN).text.endswith("não rodou: peça de novo")
    round_ = ar.Round(1, "nota", [first, bad])
    assert round_.text().startswith("Rodada 1 (sua nota: nota):\n\n#1.1 listar")
    round_.compact = True
    assert round_.text() == ("Rodada 1 (sua nota: nota), resumida: #1.1 listar: 1 linha; #2.3 listar: erro (sintaxe). Os resultados "
                             "saíram do contexto: rode de novo se precisar.")


def test_citable_lines_need_the_meeting_and_take_the_time_and_the_speech():
    res = _res(("reuniao_id", "inicio", "inicio_ms", "fala_id"), [(5, "00:01", 1000, 77), (5, None, None, None), (6, "00:02", 2000, None),
                                                                 ("x", "00:03", 3000, 1)])
    assert ar.targets(res) == {5: [SentLine(1000, 77)], 6: [SentLine(2000, None)]}
    assert ar.targets(_res(("titulo",), [("a",)])) == {}


def test_aliases_with_up_to_six_digits():
    assert [(i.alias, i.cited_ms) for i in citations.parse_bracket("R123456 12:03") or []] == [("R123456", 723_000)]
    target = citations.Target(123456, "Grande", "2026-10-30", (SentLine(723_000, 9),))
    html, cites = citations.rewrite("Feito [R123456 12:05] e [R1234567 00:01].", {"R123456": target})
    assert "listener://open?m=123456&amp;t=723000&amp;seg=9" in html and "[R1234567 00:01]" in html and len(cites) == 1


def test_the_prompts_of_the_rounds_and_of_the_answer():
    assert check_strict(rag_sql.SCHEMA) == [] and rag_sql.SCHEMA["properties"]["acao"]["enum"][0] == "responder"
    assert prompts.prompt_version(LlmTask.RAG_SQL) == "rag_sql/1.0" and prompts.prompt_version(LlmTask.RAG_ANSWER) == "rag_answer/1.0"
    assert TASK_CLASS[LlmTask.RAG_SQL] == TASK_CLASS[LlmTask.RAG_ANSWER] == ModelClass.FAST
    assert prompts.schema_for(LlmTask.RAG_ANSWER) is None
    ctx = rag_sql.RagSqlCtx("DICIONÁRIO", [("user", "antes"), ("assistant", "resposta")], "2026-10-30 (sexta), 14:05, fuso -03:00",
                            "pessoa: Ana", "quantas?", "Rodada 1: ...", 2, "pt-BR")
    msgs = rag_sql.build(ctx)
    assert [m.role for m in msgs] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT, Role.USER]
    assert msgs[0].text.startswith(rag_sql.INSTRUCTIONS) and rag_sql.CONTRACT in msgs[0].text and msgs[1].text.endswith("DICIONÁRIO")
    assert "Filtros da aba Reuniões: pessoa: Ana" in msgs[-1].text and msgs[-1].text.endswith("Rodada 2: responda com o JSON "
                                                                                                  "(consultar ou responder).")
    assert rag_sql.build(ctx, "Seja breve.")[0].text.startswith(f"Seja breve. {rag_sql.CONTRACT}")
    answer = rag_answer.build(rag_answer.RagAnswerCtx((), "agora", "", "quantas?", "R5 = Orçamento — 2026-10-20", "", "fim", "en"))
    assert answer[0].text.startswith(rag_answer.INSTRUCTIONS) and "inglês" in answer[0].text and "Voz 2" in answer[0].text
    assert rag_answer.NOTHING in answer[-1].text and answer[-1].text.endswith("Pergunta: quantas?") and "Sem filtros" in answer[-1].text


def test_the_trace_keeps_the_steps_and_the_filters_but_never_the_data():
    q = ar.Query("SELECT titulo FROM reunioes WHERE titulo LIKE ?", ("Orç%",), "títulos")
    item = ar.query_item("1.1", q, _res(("titulo",), [("Orçamento secreto",)]), 8000, set())
    steps = trace.build([ar.Round(1, "", [item], 3.0, 0.001)], "resposta", 1500.0, 0.002, False,
                        SearchFilters(person_ids=(3,), date_from="2026-10-01"), "pessoa: Ana", "fake-fast")
    assert steps["consultas"] == 1 and steps["linhas"] == 1 and steps["filtros"]["texto"] == "pessoa: Ana" and steps["parou"] == "resposta"
    assert "Orçamento secreto" not in str(steps) and trace.query(steps, "1.1")["sql"] == q.sql
    assert trace.filters_of(steps) == SearchFilters(person_ids=(3,), date_from="2026-10-01")
    assert trace.load("[1]") is None and trace.load("x") is None and trace.filters_of({"filtros": None}) is None


def test_the_settings_and_guards_of_the_ask(store):
    assert defaults.RAG_SQL_KEYS == ("rag.sql_strong", "rag.sql_max_rows", "rag.sql_timeout_s", "rag.sql_max_cost_usd",
                                     "rag.sql_max_minutes", "rag.sql_max_rounds")
    assert defaults.RAG_KEYS == ("rag.auto_index", "rag.top_k", "rag.max_per_meeting")       # a página Busca de antes, intacta
    assert Guards.from_store(store) == Guards(0.10, 180.0, 30, 200, 3.0, False)
    store.set_setting("rag.sql_max_minutes", "5")
    store.set_setting("rag.sql_strong", "sim")
    assert Guards.from_store(store).max_seconds == 300.0 and Guards.from_store(store).strong
    assert defaults.validate_setting("rag.sql_max_rows", "10") and defaults.validate_setting("rag.sql_timeout_s", "31")


def test_retrieve_names_the_meetings_by_id_for_the_ask(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, b, _who = _two_meetings(store)
    got = retrieve(store, "auditoria", None, lambda mid: f"R{mid}")
    assert got.hybrid and set(got.aliases) <= {f"R{a}", f"R{b}"} and got.aliases and all(t.lines for t in got.targets.values())
    assert got.blocks[0].render().startswith(f"[R{got.blocks[0].meeting_id}] ")
