"""F5.10 L3 (Comportamento 1 a 9, D-073): o laço de "Perguntar à base" com o provedor roteirizado. Rodadas até a resposta,
com as consultas na sandbox, a busca por sentido (`R<reuniao_id>`), as citações ligadas, o progresso, as chamadas em
`llm_calls` e os passos em `trace_json`; a consulta repetida não roda de novo; três rodadas sem novidade pedem a resposta; as
guardas de tempo, custo (inativa sem preço) e rodadas com relógio e custo falsos; o contexto cheio resume as rodadas antigas
(e a consulta delas roda de novo); o orçamento da rodada; cancelar entre rodadas e com a rede presa; erro do provedor e
pedido grande demais; os filtros no começo da resposta. Sem o roteiro, o provedor falso responde na 1ª rodada."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from listener.llm import chat_task
from listener.llm.base import AuthError, RequestTooLargeError
from listener.llm.fake import FakeProvider
from listener.llm.registry import RetryingProvider
from listener.llm.types import LlmTask, ModelInfo, Request
from listener.rag import agent, search, semantic
from listener.rag.agent_results import Guards
from listener.rag.sandbox import Sandbox
from listener.store import Store
from listener.store.rag import SearchFilters
from tests.test_rag_chat import QUESTION, _slow_network, _two_meetings
from tests.test_rag_index import KEY

RESPOND: dict[str, Any] = {"acao": "responder", "consultas": [], "buscas": [], "nota": None}
ANA = ("SELECT reuniao_id, inicio, inicio_ms, fala_id, falante, texto FROM falas WHERE falante LIKE ? ORDER BY reuniao_id, inicio_ms",
       ("Ana%",), "as falas da Ana")


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    semantic.CACHE.clear()
    yield
    search.clear_caches()
    semantic.CACHE.clear()


def consult(*queries: tuple[str, tuple[str, ...], str], searches: tuple[tuple[str, list[int]], ...] = (), note: str | None = None
            ) -> dict[str, Any]:
    return {"acao": "consultar", "consultas": [{"sql": sql, "params": list(p), "objetivo": goal} for sql, p, goal in queries],
            "buscas": [{"texto": t, "objetivo": "achar", "reuniao_ids": ids} for t, ids in searches], "nota": note}


class Script:
    """As rodadas roteirizadas (a próxima decisão a cada `rag_sql`; depois delas, `responder`) e a resposta."""

    def __init__(self, decisions: list[dict[str, Any]], answer: str | Callable[[Request], str] = "Resposta.",
                 on_round: Callable[[int], None] | None = None):
        self.decisions, self.answer_text, self.on_round, self.rounds = list(decisions), answer, on_round, 0

    def sql(self, req: Request) -> dict[str, Any]:
        self.rounds += 1
        if self.on_round is not None:
            self.on_round(self.rounds)
        return self.decisions.pop(0) if self.decisions else RESPOND

    def answer(self, req: Request) -> str:
        return self.answer_text(req) if callable(self.answer_text) else self.answer_text


async def _no_sleep(_s: float) -> None:
    return None


def _provider(monkeypatch: pytest.MonkeyPatch, script: Script) -> FakeProvider:
    fake = FakeProvider(scripts={LlmTask.RAG_SQL: script.sql, LlmTask.RAG_ANSWER: script.answer})
    wrapped = RetryingProvider(fake, sleep=_no_sleep)
    monkeypatch.setattr(chat_task, "build_provider", lambda _store, _pid: (wrapped, {}))
    return fake


def _ask(store: Store, *a: Any, **kw: Any) -> agent.AgentAnswer:
    return agent.run_agent(store, None, QUESTION, *a, **kw)


def _items(ans: agent.AgentAnswer) -> list[list[str]]:
    return [[i["status"] for i in r["itens"]] for r in ans.trace["rodadas"]]


def _tasks(store: Store) -> list[str]:
    return [str(r[0]) for r in store._q("SELECT task FROM llm_calls WHERE task IN ('rag_sql', 'rag_answer') ORDER BY id")]


def test_rounds_until_the_answer_with_linked_citations_progress_calls_and_trace(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, b, _who = _two_meetings(store)
    script = Script([consult(ANA, searches=(("auditoria", []),), note="primeiro as falas")],
                    lambda req: f"A Ana abriu falando do orçamento [R{a} 00:00] e da auditoria [R{a} 01:10]; ver [R{b}] e [R999999 00:01].")
    fake = _provider(monkeypatch, script)
    pieces: list[str] = []
    seen: list[dict[str, Any]] = []
    ans = _ask(store, None, "", pieces.append, None, seen.append)
    assert ans.error is None and not ans.cancelled and "".join(pieces) == ans.text and ans.text.startswith("A Ana abriu")
    assert ans.trace["parou"] == "resposta" and (ans.trace["consultas"], ans.trace["buscas"]) == (1, 1) and script.rounds == 2
    first, second, third, ghost = ans.citations
    assert (first["alias"], first["meeting_id"], first["t_ms"]) == (f"R{a}", a, 0) and first["segment_id"] is not None
    assert (second["t_ms"], third["meeting_id"], third["t_ms"], ghost["meeting_id"]) == (70_000, b, None, None) and ans.linked == 3
    assert f"listener://open?m={a}&amp;t=70000" in ans.html and "[R999999 00:01]" in ans.html
    msgs = store.chat.list_for_thread(ans.thread_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and json.loads(msgs[1]["trace_json"]) == ans.trace
    assert _tasks(store) == ["rag_sql", "rag_sql", "rag_answer"] and seen[0] == {"respondendo": False, "rodada": 1, "consultas": 1,
                                                                                    "buscas": 1}
    assert seen[-1]["respondendo"] and ans.cost_usd is not None and ans.cost_usd == ans.trace["custo_usd"] > 0
    query = ans.trace["rodadas"][0]["itens"][0]
    assert (query["sql"], query["params"], query["objetivo"], query["linhas"], query["erro"]) == (ANA[0], ["Ana%"], ANA[2], 3, None)
    second_round = fake.calls(LlmTask.RAG_SQL)[1].messages[-1].text
    assert "#1.1 as falas da Ana" in second_round and "Ana\tBom dia, vamos falar do orçamento de 2027." in second_round
    assert '#1.2 busca "auditoria"' in second_round and f"[R{a}] Orçamento 2027" in second_round
    assert fake.calls(LlmTask.RAG_SQL)[0].cache_prefix and "Dicionário de dados" in fake.calls(LlmTask.RAG_SQL)[0].messages[1].text
    told = fake.calls(LlmTask.RAG_ANSWER)[0].messages[-1].text
    assert f"R{a} = Orçamento 2027 — " in told and "Como as rodadas terminaram: a LLM teve o que precisava" in told


def test_without_a_script_the_fake_answers_in_the_first_round(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    _two_meetings(store)
    ans = _ask(store)
    assert ans.error is None and ans.text == "resposta simulada para rag_answer" and ans.trace["parou"] == "resposta"
    assert ans.trace["rodadas"] == [] and _tasks(store) == ["rag_sql", "rag_answer"]


def test_a_repeated_query_does_not_run_again_while_its_result_is_in_context(store, monkeypatch):
    _two_meetings(store)
    runs: list[str] = []
    real = Sandbox.run

    def counted(self: Sandbox, sql: str, *a: Any, **kw: Any) -> Any:
        runs.append(sql)
        return real(self, sql, *a, **kw)

    monkeypatch.setattr(Sandbox, "run", counted)
    again = (("SELECT   reuniao_id, inicio, inicio_ms,  fala_id, falante, texto\nFROM falas WHERE falante LIKE ?  "
             "ORDER BY reuniao_id, inicio_ms"), ("Ana%",), "de novo")
    _provider(monkeypatch, Script([consult(ANA), consult(again), consult((ANA[0], ("Bia%",), "as da Bia"))]))
    ans = _ask(store)
    assert _items(ans) == [["ok"], ["repetida"], ["ok"]] and len(runs) == 2                  # outros params: roda
    assert ans.trace["parou"] == "resposta" and ans.trace["consultas"] == 2


def test_three_rounds_without_news_ask_for_the_answer(store, monkeypatch):
    _two_meetings(store)
    nobody = ("SELECT reuniao_id FROM falas WHERE falante = ?", ("Ninguém",), "vazia")
    broken = ("SELECT nada FROM falas", (), "com erro")
    script = Script([consult(nobody), consult(broken), consult(), consult(ANA)])
    fake = _provider(monkeypatch, script)
    ans = _ask(store)
    assert ans.trace["parou"] == "exaustao" and script.rounds == 3 and _items(ans) == [["ok"], ["erro"], []]
    assert ans.trace["rodadas"][1]["itens"][0]["erro"] == "sintaxe"
    third = fake.calls(LlmTask.RAG_SQL)[2].messages[-1].text
    assert "#2.1 com erro" in third and "erro (sintaxe): no such column: nada" in third               # o erro volta para corrigir
    told = fake.calls(LlmTask.RAG_ANSWER)[0].messages[-1].text
    assert "pararam por três rodadas seguidas sem informação nova: diga isso na resposta e o que faltou" in told


def _fresh(i: int) -> tuple[str, tuple[str, ...], str]:
    return ("SELECT ? AS rodada, count(*) AS reunioes FROM reunioes", (str(i),), f"rodada {i}")


def test_the_guards_of_time_cost_and_rounds_with_a_fake_clock_and_cost(store, monkeypatch):
    _two_meetings(store)
    clock = [0.0]

    def tick(_n: int) -> None:
        clock[0] += 100.0

    script = Script([consult(_fresh(i)) for i in range(10)], on_round=tick)
    _provider(monkeypatch, script)
    timed = _ask(store, guards=Guards(max_seconds=150.0), clock=lambda: clock[0])
    assert timed.trace["parou"] == "tempo" and script.rounds == 2 and _items(timed) == [["ok"], ["nao_rodou"]]
    assert "o tempo da pergunta acabou" in store.llm_calls.payloads(timed.call_id)[0][-1]["text"]
    store.providers.set_price("gemini", "fake-fast", 5_000.0, 5_000.0, 0.0)                 # cada rodada passa da guarda
    script = Script([consult(_fresh(i)) for i in range(10)])
    _provider(monkeypatch, script)
    priced = _ask(store, guards=Guards(max_cost_usd=0.01))
    assert priced.trace["parou"] == "custo" and script.rounds == 1 and priced.trace["custo_usd"] > 0.01
    store._x("DELETE FROM llm_prices WHERE provider = 'gemini'")                             # sem preço: a guarda fica inativa
    script = Script([consult(_fresh(i)) for i in range(10)])
    _provider(monkeypatch, script)
    unpriced = _ask(store, guards=Guards(max_cost_usd=0.01, max_rounds=3))
    assert unpriced.trace["parou"] == "rodadas" and script.rounds == 3 and len(unpriced.trace["rodadas"]) == 3
    assert unpriced.trace["sem_preco"] and unpriced.cost_usd is None and unpriced.trace["custo_usd"] is None


def test_the_round_budget_and_the_context_window(store, monkeypatch):
    _two_meetings(store)
    wide = (("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3000) SELECT x, printf('%.80c', 'a') AS t FROM n "
            "WHERE x > CAST(? AS INTEGER)"), ("0",), "larga")
    queries = [(wide[0], (str(i),), f"larga {i}") for i in range(5)]
    _provider(monkeypatch, Script([consult(*queries)]))
    ans = _ask(store)
    assert _items(ans) == [["ok", "ok", "ok", "nao_rodou", "nao_rodou"]]                     # 24 mil por rodada, 8 mil cada
    assert ans.trace["rodadas"][0]["itens"][0]["mostradas"] < 200 < ans.trace["rodadas"][0]["itens"][0]["linhas"]
    big = Script([consult(queries[0]), consult(queries[0])])
    fake = _provider(monkeypatch, big)
    _ask(store)
    first = sum(len(m.text) for m in fake.calls(LlmTask.RAG_SQL)[0].messages)
    window = int((first + 3000) / 3.5 / agent.WINDOW_SHARE)                                  # a 1ª cabe; a 2ª, só resumida
    monkeypatch.setattr(agent, "model_info", lambda _k, m: ModelInfo(m, m, window, 8192, frozenset()))
    fake = _provider(monkeypatch, Script([consult(queries[0]), consult(queries[0])]))
    ans = _ask(store)
    assert _items(ans) == [["ok"], ["ok"]]                                                   # resumida, a consulta roda de novo
    told = fake.calls(LlmTask.RAG_SQL)[1].messages[-1].text
    assert "Rodada 1, resumida: #1.1 larga 0: 3.000 linhas. Os resultados saíram do contexto" in told


def test_cancel_between_rounds_and_with_the_network_stuck(store, monkeypatch):
    a, _b, _who = _two_meetings(store)
    stop = threading.Event()

    def cancel_after_first(n: int) -> None:
        if n == 1:
            stop.set()

    _provider(monkeypatch, Script([consult(ANA), consult(ANA)], on_round=cancel_after_first))
    ans = _ask(store, cancelled=stop.is_set)
    assert ans.cancelled and ans.message_id is None and ans.trace["parou"] == "cancelada" and _tasks(store) == ["rag_sql"]
    assert [m["role"] for m in store.chat.list_for_thread(ans.thread_id)] == ["user"]           # a pergunta fica
    monkeypatch.undo()
    _slow_network(monkeypatch)
    stuck = threading.Event()
    timer = threading.Timer(0.3, stuck.set)
    timer.start()
    t0 = time.monotonic()
    try:
        late = agent.run_agent(store, None, QUESTION, cancelled=stuck.is_set)
    finally:
        timer.cancel()
    assert time.monotonic() - t0 < 1.5 and late.cancelled and late.message_id is None and late.error is None
    calls = store._q("SELECT task, error FROM llm_calls WHERE task = 'rag_sql' ORDER BY id DESC LIMIT 1")
    assert tuple(calls[0]) == ("rag_sql", "cancelado") and a


def test_provider_error_too_large_request_and_the_filters_prefix(store, monkeypatch):
    _a, b, _who = _two_meetings(store)
    fake = _provider(monkeypatch, Script([]))
    fake.fail_next(AuthError(f"chave {KEY} recusada"), LlmTask.RAG_SQL)
    err = _ask(store)
    assert err.error is not None and KEY not in err.error and err.message_id is None and err.trace["parou"] == "erro"
    assert KEY not in str(store._q("SELECT error FROM llm_calls WHERE task = 'rag_sql' ORDER BY id DESC LIMIT 1")[0][0])
    fake = _provider(monkeypatch, Script([consult(ANA)]))
    calls = {"n": 0}
    real = fake.complete

    async def second_too_large(req: Request) -> Any:
        if req.task == LlmTask.RAG_SQL:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RequestTooLargeError("request too large")
        return await real(req)

    monkeypatch.setattr(fake, "complete", second_too_large)
    big = _ask(store)
    assert big.trace["parou"] == "janela" and big.error is None and big.message_id is not None
    _provider(monkeypatch, Script([consult(("SELECT count(*) AS n FROM reunioes", (), "quantas"))], "Uma reunião."))
    pieces: list[str] = []
    only = agent.run_agent(store, None, QUESTION, SearchFilters(meeting_ids=(b,)), "reunião Marketing", pieces.append)
    assert only.text == "Considerando os filtros: reunião Marketing.\n\nUma reunião." == "".join(pieces)
    assert only.trace["filtros"]["reunioes"] == [b] and only.trace["rodadas"][0]["itens"][0]["linhas"] == 1
    assert agent.describe(SearchFilters(person_ids=(3,), date_from="2026-10-01")) == "pessoa 3 · desde 2026-10-01"


def test_validation_like_the_global_chat(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    _two_meetings(store)
    with pytest.raises(ValueError, match="pergunta"):
        agent.run_agent(store, None, "  ")
    with pytest.raises(ValueError, match="conversa"):
        agent.run_agent(store, 4242, QUESTION)
    store.providers.set_active("gemini", llm=False)
    with pytest.raises(ValueError, match="provedor"):
        agent.run_agent(store, None, QUESTION)
    assert store.chat.list_threads() == []
