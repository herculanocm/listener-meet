"""F5 L3, chat sobre todas as reuniões: as citações por apelido (todas as formas da regex, só as linhas enviadas, até 5 s,
`[R1]` liga só à reunião, apelido inexistente e reunião apagada ficam como texto, redesenho das citações gravadas), o prompt
`rag_chat` (apelidos na ordem de relevância, falas pelos segmentos vigentes no intervalo do trecho, linhas de ata, ação,
executivo e reunião), e o chat global ponta a ponta com o fake (streaming, cancelamento antes e durante, conversa criada,
seguimento usa a pergunta anterior, mensagens e custo gravados, `llm_calls` com reunião nula, erro redigido, sem trechos,
sem provedor, avaliação U-027 e o HTML da conversa)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from listener.llm import chat_task, prompts
from listener.llm.base import AuthError
from listener.llm.fake import FakeProvider
from listener.llm.prompts import rag_chat
from listener.llm.types import LlmTask, Role
from listener.model import ChunkKind, SummaryKind
from listener.rag import chat_global, citations, index, search
from listener.rag.citations import CitedItem, OpenLink, SentLine, Target
from listener.store import Store
from listener.store.rag import SearchFilters
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import KEY, _meeting, _people, _pipeline_job, _segments
from tests.test_rag_search import _other_meeting

QUESTION = "o que foi decidido sobre a auditoria?"


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    yield
    search.clear_caches()


def _llm_ready(store: Store) -> None:
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast", strong_model="fake-strong")
    store.providers.set_price("gemini", "fake-fast", 0.30, 2.50, 0.075)


def _two_meetings(store: Store) -> tuple[int, int, dict[str, int]]:
    who = _people(store) | {"carla": store.people.create_person("Carla", role="Gerente")}
    a, _ = _meeting(store, who)
    b = _other_meeting(store, who)
    _llm_ready(store)
    for mid in (a, b):
        index.sync_text(store, mid)
        index.fill_vectors(store, mid, provider=FakeProvider())
    return a, b, who


def test_parse_bracket_accepts_every_form_and_rejects_free_text():
    def parse(inner: str) -> list[tuple[str, int | None]] | None:
        items = citations.parse_bracket(inner)
        return None if items is None else [(i.alias, i.cited_ms) for i in items]

    assert parse("R1 12:03") == [("R1", 723_000)] and parse("R1") == [("R1", None)] and parse("r2 00:07") == [("R2", 7_000)]
    assert parse("R1 12:03; R2 05:10") == [("R1", 723_000), ("R2", 310_000)]
    assert parse("R1 1:02:03") == [("R1", 3_723_000)] and parse("R1 75:03") == [("R1", 4_503_000)]
    for sep in ("R1, 12:03", "R1 - 12:03", "R1 \u2013 12:03", "R1\u201412:03", "R1;12:03"):
        assert parse(sep) == [("R1", 723_000)], sep
    assert parse("R1 12:03, 15:20") == [("R1", 723_000), ("R1", 920_000)]                     # o mesmo apelido
    assert parse("R1 12:03\u201312:40") == [("R1", 723_000)] and parse("R1 12:03 - 12:40; R2") == [("R1", 723_000), ("R2", None)]
    assert parse("R1, R3") == [("R1", None), ("R3", None)] and parse("R01 00:05") == [("R1", 5_000)]
    for bad in ("R1 fala da Ana", "R1 12:3", "R1 12:03 e mais", "R1 1203"):
        assert parse(bad) is None, bad
    assert citations.to_ms("0:00:07") == 7_000 and CitedItem("R3", 61_000).raw == "R3 01:01" and CitedItem("R3", None).raw == "R3"


def test_rewrite_links_only_sent_lines_within_five_seconds_and_never_drops_a_citation():
    target = Target(5, "Planejamento do orçamento de 2027 com a diretoria", "2026-10-24",
                    (SentLine(723_000, 143), SentLine(730_000, 144), SentLine(900_000, None)))
    text = ("Fechado [R1 12:05] e [R1 12:20]; ver [R1]; ata [R1 15:00]; outra [R9 01:00]; várias [R1 12:03; R2 00:10]; "
            "texto [sem citação] e [12:03].\n<b>fim</b>")
    html, cites = citations.rewrite(text, {"R1": target})
    label = "Planejamento do orçamento de…, 24/10"
    assert target.label == label and citations.meeting_label("", "2026-01-02") == "Reunião sem título, 02/01"
    assert f'[<a href="listener://open?m=5&amp;t=723000&amp;seg=143">{label}, 12:03</a>]' in html       # 12:05 -> 12:03 (2 s)
    assert f"[{label}, 12:20]" in html                                                                # a 10 s da linha: sem link
    assert f'[<a href="listener://open?m=5">{label}</a>]' in html                                     # [R1]: só a reunião
    assert f'[<a href="listener://open?m=5&amp;t=900000">{label}, 15:00</a>]' in html                 # item da ata, sem segmento
    assert "[R9 01:00]" in html and f'[<a href="listener://open?m=5&amp;t=723000&amp;seg=143">{label}, 12:03</a>; R2 00:10]' in html
    assert "[sem citação] e [12:03]." in html and html.endswith("<br>&lt;b&gt;fim&lt;/b&gt;")
    assert [(c["alias"], c["meeting_id"], c["t_ms"], c["segment_id"], c["cited_ms"]) for c in cites] == [
        ("R1", 5, 723_000, 143, 725_000), ("R1", 5, None, None, 740_000), ("R1", 5, None, None, None), ("R1", 5, 900_000, None, 900_000),
        ("R9", None, None, None, 60_000), ("R1", 5, 723_000, 143, 723_000), ("R2", None, None, None, 10_000)]
    plain, _ = citations.rewrite(text, {"R1": target}, as_html=False)
    assert plain.startswith(f"Fechado [{label}, 12:03] e [{label}, 12:20]; ver [{label}]") and "<b>fim</b>" in plain
    assert citations.parse_open_link("listener://open?m=5&amp;t=723000&amp;seg=143") == OpenLink(5, 723_000, 143)
    assert citations.parse_open_link("listener://open?m=5") == OpenLink(5) and citations.parse_open_link("listener://play?seg=1") is None
    assert citations.parse_open_link("listener://open?t=1") is None and citations.open_link(5, 0) == "listener://open?m=5&t=0"
    # redesenho das citações gravadas: título atual, sem recasar; reunião apagada ou citação que não confere ficam como vieram
    again = citations.render_saved(text, cites, {5: ("Orçamento final", "2026-10-24")})
    assert '[<a href="listener://open?m=5&amp;t=723000&amp;seg=143">Orçamento final, 24/10, 12:03</a>]' in again
    assert "[Orçamento final, 24/10, 12:20]" in again and '[<a href="listener://open?m=5">Orçamento final, 24/10</a>]' in again
    gone = citations.render_saved(text, cites, {})
    assert "[R1 12:05] e [R1 12:20]; ver [R1]" in gone and "href" not in gone
    assert citations.render_saved("[R1 12:05] [R2]", [{"alias": "R7", "meeting_id": 5, "t_ms": 1}], {5: ("x", "")}) == "[R1 12:05] [R2]"
    assert chat_task.nearest_segment(735_001, [prompts.common.Line(11, 730_000, "Ana", "x")]) is None    # a F3 segue igual


def test_excerpts_use_the_current_segments_by_time_aliases_by_relevance_and_lines_for_the_other_kinds(store):
    who = _people(store)
    mid, job = _meeting(store, who)
    store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar a auditoria. [00:30]\n", "gemini", "m",
                                         "minutes/1.0", job, None, "pt-BR"))
    store.summaries.insert(SummaryInsert(mid, SummaryKind.EXECUTIVE, "{}", "Resumo:\nauditoria até sexta.", "gemini", "m", "executive/1.0",
                                         job, None, "pt-BR"))
    index.sync_text(store, mid)
    old_first = store.rag.list_chunks(mid)[0]["seg_first"]
    job2 = _pipeline_job(store, mid)                                                       # reprocessada: ids novos, mesmo tempo
    _segments(store, mid, job2, who)
    store.segments.swap_job(mid, job2)
    hits = search.search(store, "auditoria").hits
    blocks, targets = chat_global.build_excerpts(store, hits)
    assert [b.alias for b in blocks] == ["R1"] and targets["R1"].meeting_id == mid and blocks[0].participants == "Ana — CFO, Bia"
    text = blocks[0].render()
    first = store.segments.list_segments(mid, job2)
    assert text.startswith(f"[R1] Orçamento 2027 — {blocks[0].date} — Ana — CFO, Bia\n")
    assert f"#{first[2]['id']} [01:10] Ana — CFO: Precisamos aprovar a auditoria até sexta." in text and f"#{old_first} " not in text
    assert "decision [00:30]: Aprovar a auditoria." in text and "executive summary: Resumo: auditoria até sexta." in text
    kinds = {ln.kind for ln in blocks[0].lines}
    assert ChunkKind.DECISION in kinds and ChunkKind.EXECUTIVE in kinds
    sent = targets["R1"].lines
    assert SentLine(70_000, int(first[2]["id"])) in sent and SentLine(30_000, None) in sent       # falas e o item da ata com tempo
    ctx = rag_chat.RagChatCtx(blocks, [("user", "antes"), ("assistant", "resposta")], "e agora?", "pt-BR")
    msgs = rag_chat.build(ctx)
    assert [m.role for m in msgs] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT, Role.USER]
    assert "[R<n> mm:ss]" in msgs[0].text and "Brazilian Portuguese" in msgs[0].text and "[R1] Orçamento 2027" in msgs[1].text
    assert msgs[-1].text == "e agora?" and prompts.prompt_version(LlmTask.RAG_CHAT) == "rag_chat/1.0"
    assert prompts.schema_for(LlmTask.RAG_CHAT) is None
    assert rag_chat.NO_EXCERPTS in rag_chat.build(rag_chat.RagChatCtx([], [], "x", "en"))[1].text
    # dois de uma reunião e um da outra: apelidos na ordem do melhor trecho
    b = _other_meeting(store, who | {"carla": store.people.create_person("Carla")})
    index.sync_text(store, b)
    hits = search.search(store, "campanha auditoria").hits
    blocks, _targets = chat_global.build_excerpts(store, hits)
    assert [x.meeting_id for x in blocks] == list(dict.fromkeys(h.meeting_id for h in hits))
    assert [x.alias for x in blocks] == ["R1", "R2"]


def _spy_search(monkeypatch: Any) -> list[str]:
    queries: list[str] = []
    real = chat_global.search

    def spy(store: Store, query: str, *a: Any, **kw: Any) -> Any:
        queries.append(query)
        return real(store, query, *a, **kw)

    monkeypatch.setattr(chat_global, "search", spy)
    return queries


def test_global_chat_end_to_end_streaming_follow_up_records_rating_and_html(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, b, _who = _two_meetings(store)
    queries = _spy_search(monkeypatch)
    pieces: list[str] = []
    ans = chat_global.run_global_chat(store, None, QUESTION, None, pieces.append)
    assert ans.error is None and not ans.cancelled and "".join(pieces) == ans.text and ans.text.startswith("Resposta simulada sobre 2")
    assert ans.hybrid and ans.search_reason == "" and set(ans.aliases.values()) == {a, b} and ans.aliases["R1"] == a
    assert 2 <= ans.excerpts <= 6 and queries == [QUESTION]                                       # até 3 por reunião (padrão)
    thread = store.chat.get_thread(ans.thread_id)
    assert thread["title"] == QUESTION
    first = ans.citations[0]
    assert (first["alias"], first["meeting_id"]) == ("R1", a) and first["t_ms"] is not None and first["segment_id"] is not None
    assert ans.citations[1]["alias"] == "R2" and ans.citations[1]["meeting_id"] == b and ans.citations[1]["t_ms"] is not None
    assert (ans.citations[2]["meeting_id"], ans.citations[2]["t_ms"]) == (a, None) and ans.citations[3]["meeting_id"] is None
    assert ans.linked == 3 and f"listener://open?m={a}&amp;t=" in ans.html and "[R99 00:00]" in ans.html
    msgs = store.chat.list_for_thread(ans.thread_id)
    assert [(m["role"], m["meeting_id"]) for m in msgs] == [("user", None), ("assistant", None)]
    assert [m["id"] for m in msgs] == [ans.question_id, ans.message_id]
    assert json.loads(msgs[1]["citations_json"]) == list(ans.citations) and msgs[1]["cost_usd"] == ans.cost_usd
    call = store.llm_calls.get(ans.call_id)
    assert (call["task"], call["meeting_id"], call["job_id"], call["strategy"], call["prompt_version"]) == ("rag_chat", None, None,
                                                                                                             "stream", "rag_chat/1.0")
    embeds = store._q("SELECT cost_usd FROM llm_calls WHERE task = 'rag_embed' AND meeting_id IS NULL")
    assert len(embeds) == 1 and ans.cost_usd == pytest.approx(call["cost_usd"] + embeds[0][0], abs=1e-6)  # o chat e o vetor
    req, _resp = store.llm_calls.payloads(ans.call_id)
    assert "[R1] Orçamento 2027" in req[1]["text"] and "[R2] Marketing digital" in req[1]["text"]
    # seguimento: a busca usa a pergunta anterior; o histórico volta com as citações legíveis (os apelidos não valem mais)
    second = chat_global.run_global_chat(store, ans.thread_id, "e o prazo?", None)
    assert second.error is None and second.thread_id == ans.thread_id and queries[-1] == f"{QUESTION}\ne o prazo?"
    req2, _ = store.llm_calls.payloads(second.call_id)
    history = [m for m in req2 if m["role"] == "assistant"][1:]
    assert history and "Orçamento 2027, " in history[0]["text"] and "[R1 " not in history[0]["text"] and req2[-1]["text"] == "e o prazo?"
    assert store.llm_calls.get(second.call_id)["strategy"] == "direct" and len(store.chat.list_for_thread(ans.thread_id)) == 4
    # avaliação U-027 e o HTML da conversa
    assert chat_global.rate_answer(store, ans.message_id, 1) and store.chat.get(ans.message_id)["rating"] == 1
    assert not chat_global.rate_answer(store, ans.question_id, 1) and not chat_global.rate_answer(store, 99999, 0)
    page = chat_global.thread_html(store, ans.thread_id)
    assert page.count("<b>Você:</b>") == 2 and page.count("<b>LLM:</b>") == 2 and f"listener://open?m={a}&amp;t=" in page
    assert "Citação certa: sim" in page and "Citação certa? " in page
    link = chat_global.rate_link(ans.message_id, None)
    assert chat_global.parse_rate_link(link) == (ans.message_id, None)
    assert chat_global.parse_rate_link("listener://rate?msg=3&v=1") == (3, 1)
    assert chat_global.parse_rate_link("listener://rate?msg=3&v=7") is None and chat_global.parse_rate_link("listener://open?m=1") is None
    assert chat_global.rate_answer(store, ans.message_id, None) and store.chat.get(ans.message_id)["rating"] is None
    assert "Citação" not in chat_global.thread_html(store, ans.thread_id, rating=False)
    store.set_setting("rag.max_per_meeting", 1)
    third = chat_global.run_global_chat(store, None, "campanha e auditoria", None)
    assert third.excerpts == 2 and third.thread_id != ans.thread_id                               # um por reunião; conversa nova


def test_global_chat_cancel_before_and_during_streaming(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    _two_meetings(store)
    before = chat_global.run_global_chat(store, None, QUESTION, None, lambda _p: None, lambda: True)
    assert before.cancelled and before.thread_id is None and before.message_id is None
    assert store.chat.list_threads() == [] and store._q("SELECT COUNT(*) FROM llm_calls WHERE task = 'rag_chat'")[0][0] == 0
    seen: list[str] = []
    during = chat_global.run_global_chat(store, None, QUESTION, None, seen.append, lambda: bool(seen))
    assert during.cancelled and len(seen) == 1 and during.text == seen[0] and during.cost_usd is None and during.message_id is not None
    assert store.chat.get(during.message_id)["text"] == seen[0]                                   # a parte que chegou fica


def test_global_chat_without_provider_without_excerpts_and_with_a_provider_error(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    _two_meetings(store)
    with pytest.raises(ValueError, match="pergunta"):
        chat_global.run_global_chat(store, None, "   ")
    with pytest.raises(ValueError, match="conversa"):
        chat_global.run_global_chat(store, 4242, QUESTION)
    nothing = chat_global.run_global_chat(store, None, QUESTION, SearchFilters(meeting_ids=(9999,)))
    assert nothing.excerpts == 0 and nothing.text == "Não encontrei isso nas reuniões." and nothing.citations == ()
    req, _ = store.llm_calls.payloads(nothing.call_id)
    assert rag_chat.NO_EXCERPTS in req[1]["text"]
    failing = FakeProvider()
    failing.fail_next(AuthError(f"chave {KEY} recusada"), LlmTask.RAG_CHAT)
    monkeypatch.setattr(chat_task, "build_provider", lambda _store, _pid: (failing, {}))
    err = chat_global.run_global_chat(store, nothing.thread_id, "e agora?")
    assert err.error is not None and KEY not in err.error and err.message_id is None and err.question_id is not None
    call = store.llm_calls.get(err.call_id)
    assert (call["status"], call["meeting_id"], call["task"]) == ("erro", None, "rag_chat") and KEY not in call["error"]
    assert [m["role"] for m in store.chat.list_for_thread(nothing.thread_id)] == ["user", "assistant", "user"]
    store.providers.set_active("gemini", llm=False)
    with pytest.raises(ValueError, match="provedor"):
        chat_global.run_global_chat(store, None, QUESTION)
