"""F5.21 L1: a tela nas sugestões ao vivo do lado da LLM e do banco, sem Qt. A parte de imagem em cada adaptador (o Gemini
depois do texto, a OpenAI em data URL com `detail`, o Claude antes do texto e sem cair no filtro da mensagem em branco); a
capacidade pela tabela, nunca pelo cache antigo dos modelos (A-12); a v11 com a volta; o repositório da tela; o prompt
`suggest` 1.1 com o esquema, as regras e a linha do tempo da tela, e sem tela igual ao de antes; a tarefa com a tela (o aviso
depois das saídas cedo, a imagem na última mensagem, a opção que usa a tela sustentada, o resumo gravado e lido pelo pedido
seguinte, a tela gravada sem a imagem enviada, o pedido guardado com o rótulo e nunca os bytes, o tempo do clique); a tela
vazia que não vai; o custo estimado com a imagem. A revisão do diff do L0 e do L1 (R-01 a R-05): a forma e a ordem do esquema
da tela, a opção sustentada só pela tela, as regras com só os resumos anteriores, a imagem só com a tela enviada, as saídas
cedo sem a miniatura, a janela e o limite das telas anteriores na tarefa, o desconto delas no teto, o prazo contado do clique,
as chaves exatas do pedido guardado, a imagem pelo preço de entrada e o corte do resumo da tela."""
from __future__ import annotations

import asyncio
import base64
import sqlite3
from dataclasses import replace
from typing import Any

from listener.llm import suggest_task
from listener.llm.fake import FakeProvider
from listener.llm.jobconfig import LlmJobConfig, image_capable
from listener.llm.models_table import model_info
from listener.llm.prompts import suggest as ps
from listener.llm.schema import check_strict
from listener.llm.suggest_task import MAX_OUTPUT_TOKENS_SCREEN, Outcome, ScreenSent, ScreenShot, estimate_usd, run_suggest
from listener.llm.types import Capability, ImagePart, LlmTask, Message, Request, Role
from listener.model import Evidence, LlmCallStatus, ScreenSource, ScreenStatus, SuggestMode
from listener.store.llm_calls import LlmCallInsert
from listener.store.suggestions import ScreenInsert
from tests.test_live_partial import _caption
from tests.test_suggest_store import suggestion
from tests.test_suggest_task import MIN, Scripted, _meeting

IMG = ImagePart(b"\xff\xd8jpeg-da-tela", "image/jpeg", 1600, 900, "tela: janela do Teams")
SHOT = ScreenShot(ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", IMG, b"miniatura", b"copia-960", 1)


def _req(*messages: Message) -> Request:
    return Request(LlmTask.SUGGEST, "m", messages)


# ---------------------------------------------------------------- os adaptadores e a capacidade
def test_each_adapter_sends_the_image_the_way_its_api_asks():
    from listener.llm.anthropic_request import conversation
    from listener.llm.gemini import GeminiProvider
    from listener.llm.openai import OpenAIProvider
    from tests.test_llm_adapters import OpenAIStub

    b64 = base64.b64encode(IMG.data).decode("ascii")
    req = _req(Message(Role.SYSTEM, "sistema"), Message(Role.USER, "o pedido", images=(IMG,)))
    _sys, contents = GeminiProvider._contents(req)
    assert contents[-1]["parts"] == [{"text": "o pedido"}, {"inline_data": {"data": IMG.data, "mime_type": "image/jpeg"}}]
    _sys, items = OpenAIProvider("k", client=OpenAIStub())._input(req)
    assert items[-1]["content"][1] == {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}", "detail": "high"}
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
    _sys, msgs = conversation(req, "anthropic")
    assert msgs == [{"role": "user", "content": [image, {"type": "text", "text": "o pedido"}]}]
    _sys, only = conversation(_req(Message(Role.USER, "   ", images=(IMG,))), "anthropic")   # sem texto, a imagem fica
    assert only == [{"role": "user", "content": [image]}]
    assert IMG.describe() == "tela: janela do Teams, 1600x900, 0 kB" and req.image_count == 1


def test_the_image_capability_comes_from_the_table_and_never_from_the_old_model_cache(store):
    from tests.test_live_partial import _ready

    assert not image_capable(store)                                       # sem chave
    _ready(store)
    store.providers.update_provider("gemini", fast_model="gemini-2.5-flash",
                                    models_json='[{"id": "gemini-2.5-flash", "capabilities": ["text", "json"]}]')
    assert image_capable(store)                                           # A-12: o cache antigo não tem `image_in`
    store.providers.update_provider("gemini", fast_model="gemini-embedding-001")
    assert not image_capable(store)                                       # uma família sem imagem (A05: não um id sem família)
    for kind, model, ok in (("openai", "gpt-4.1-mini", True), ("openai", "gpt-5", True), ("openai", "o4-mini", True),
                            ("openai", "gpt-4o-audio-preview", False), ("openai", "gpt-3.5-turbo", False),
                            ("anthropic", "claude-haiku-5-5", True), ("gemini", "gemini-3.8-flash", True)):
        assert (Capability.IMAGE_IN in model_info(kind, model).capabilities) is ok, model      # A03: a tabela da OpenAI


# ---------------------------------------------------------------- o banco
def test_v11_on_a_v10_database_the_screens_and_the_way_back(tmp_path, monkeypatch):
    from listener.store import Store
    from listener.store import core as store_core
    from listener.store import downgrade as down
    from listener.store.schema import MAIN_MIGRATIONS

    monkeypatch.setattr(store_core, "MAIN_MIGRATIONS", MAIN_MIGRATIONS[:10])
    monkeypatch.setattr(store_core, "MAIN_SCHEMA_VERSION", 10)
    with Store(tmp_path) as old:                                          # a sugestão e a opção da v10, com as colunas dela
        mid = old.create_meeting("Orçamento", None, None, None)
        old._x("INSERT INTO suggestions(meeting_id, t_ms, mode, status, created_at) VALUES (?, 60000, 'fala', 'ok', '2026-11-08')",
               (mid,))
        sid = int(old._q("SELECT id FROM suggestions")[0][0])
        old._x("INSERT INTO suggestion_options(suggestion_id, idx, kind, text, evidence) VALUES (?, 0, 'Perguntar', 'e o prazo?', 'ok')",
               (sid,))
        oid = int(old._q("SELECT id FROM suggestion_options")[0][0])
    monkeypatch.undo()
    with Store(tmp_path) as s:
        assert s.schema_version() == 11 and s.suggestions.get_option(oid)["screen"] == 0
        s.suggestions.insert_screen(ScreenInsert(sid, ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", 1600, 900, 219_000, 1,
                                                 b"thumb", b"copy"))
    assert down.downgrade(tmp_path, 10) == [11]
    with sqlite3.connect(tmp_path / "listener.db") as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {r[1] for r in c.execute("PRAGMA table_info(suggestions)")}
        assert "suggestion_screens" not in tables and "screen_summary" not in columns
        assert "screen" not in {r[1] for r in c.execute("PRAGMA table_info(suggestion_options)")}      # S01
        assert c.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] == 1          # as sugestões ficam
    with Store(tmp_path) as again:
        assert again.schema_version() == 11 and again.suggestions.screens_for_meeting(mid) == {}


def test_the_screen_repo_keeps_the_thumb_and_the_copy_and_reads_the_summaries_in_the_window(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    older, _ = suggestion(store, mid, 60_000)
    newer, _ = suggestion(store, mid, 120_000)
    store._x("UPDATE suggestions SET screen_summary = ? WHERE id = ?", ("slide 1", older))
    store._x("UPDATE suggestions SET screen_summary = ? WHERE id = ?", ("slide 2", newer))
    store.suggestions.insert_screen(ScreenInsert(newer, ScreenStatus.EMPTY, ScreenSource.CURSOR, "monitor 2", 0, 0, 0, 0, b"t", None))
    rows = store.suggestions.screens_for_meeting(mid)
    assert list(rows) == [newer] and rows[newer]["status"] == "vazia" and rows[newer]["thumb"] == b"t" and rows[newer]["t_ms"] == 120_000
    assert store.suggestions.screen_copy(newer) is None
    assert store.suggestions.screen_summaries(mid, 0, 200_000, 3) == [(120_000, "slide 2"), (60_000, "slide 1")]
    assert store.suggestions.screen_summaries(mid, 90_000, 120_000, 3) == []                     # a janela e o próprio pedido fora


# ---------------------------------------------------------------- o prompt 1.1
def test_the_prompt_with_the_screen_has_its_rules_schema_and_timeline_and_without_it_stays_the_same():
    from listener.llm.prompts.common import parse_lines
    from tests.prompt_cases import CASES

    _module, _fn, ctx = CASES["suggest/speak"]
    plain = ps.build(ctx)
    assert ps.schema_for(ctx) is ps.SCHEMA and ps.RULES_SCREEN not in plain[0].text and "Tela" not in plain[-1].text
    first = ctx.lines[0]
    shot = replace(ctx, screen=ps.ScreenRef("janela do Teams", 754_000), screens=(ps.ScreenLine(first.t_start_ms, "slide do orçamento"),))
    msgs = ps.build(shot)
    text = msgs[-1].text
    assert ps.schema_for(shot) is ps.SCHEMA_SCREEN and msgs[0].text.count(ps.RULES_SCREEN) == 1
    assert "Tela de quem pede: imagem anexa (janela do Teams, em [12:34])" in text and text.endswith("Modo: fala, 3 opções.")
    timeline = text[text.index(ps.LINES_SCREENS_LABEL):].splitlines()
    screen_line = f"[{first.stamp}] Tela: slide do orçamento"
    assert timeline[1] == first.render() and timeline[2] == screen_line            # a legenda antes, no mesmo instante
    assert screen_line not in {ln.render() for ln in parse_lines(text)}            # sem "#": não se cita em `facts`
    assert ps.schema_for(replace(ctx, screens=shot.screens)) is ps.SCHEMA_SCREEN    # só os resumos já pedem o esquema da tela


# ---------------------------------------------------------------- a tarefa
def test_a_request_with_the_screen_sends_it_records_it_and_the_next_one_reads_its_summary(store):
    mid = _meeting(store)
    store.set_setting("llm.store_payloads", True)
    fake = FakeProvider()
    sent: list[ScreenSent] = []
    now = [100.0]
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=fake, screen=SHOT, on_screen=sent.append,
                    clicked_at=99.0, clock=lambda: now[0])
    assert r.outcome == Outcome.DONE and r.with_screen and r.first_ms == 1000 and [s.t_ms for s in sent] == [MIN]
    req = fake.requests[-1]
    assert req.messages[-1].images == (IMG,) and req.json_schema is ps.SCHEMA_SCREEN and req.max_output_tokens == MAX_OUTPUT_TOKENS_SCREEN
    assert [o.screen for o in r.options] == [True, False, False] and all(o.evidence == Evidence.OK for o in r.options)
    assert [o["screen"] for o in store.suggestions.options_for(r.suggestion_id)] == [1, 0, 0]
    assert store.suggestions.get(r.suggestion_id)["screen_summary"] == "Tela simulada com 1 imagem."
    row = store.suggestions.screens_for_meeting(mid)[r.suggestion_id]
    assert (row["status"], row["source"], row["app"], row["width"], row["bytes"], row["covered"]) == (
        "enviada", "janela", "janela do Teams", 1600, len(IMG.data), 1)
    assert store.suggestions.screen_copy(r.suggestion_id) == b"copia-960"
    payload, _resp = store.llm_calls.payloads(r.call_id)
    assert payload[-1]["images"] == [IMG.describe()] and "jpeg-da-tela" not in str(payload)      # A-14: o rótulo, nunca os bytes
    assert [set(m) for m in payload] == [{"role", "text"}] * (len(payload) - 1) + [{"role", "text", "images"}]      # T22
    again = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=2 * MIN, provider=fake)
    text = fake.requests[-1].messages[-1].text
    assert again.outcome == Outcome.DONE and not again.with_screen and "[01:00] Tela: Tela simulada com 1 imagem." in text
    assert fake.requests[-1].json_schema is ps.SCHEMA_SCREEN and fake.requests[-1].image_count == 0
    assert store.suggestions.get(again.suggestion_id)["screen_summary"] is None          # sem imagem, sem resumo novo


def test_an_empty_screen_does_not_go_and_the_early_exits_send_no_screen(store):
    mid = _meeting(store)
    fake = FakeProvider()
    sent: list[ScreenSent] = []
    empty = ScreenShot(ScreenStatus.EMPTY, ScreenSource.CURSOR, "monitor 2", None, b"miniatura-preta", None, 0)
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=fake, screen=empty, on_screen=sent.append)
    assert r.outcome == Outcome.DONE and not r.with_screen and len(sent) == 1 and fake.requests[-1].image_count == 0
    assert fake.requests[-1].json_schema is ps.SCHEMA and store.suggestions.screens_for_meeting(mid)[r.suggestion_id]["status"] == "vazia"
    store.set_setting("llm.cost_alert_usd", 1.0)                          # o teto: a saída cedo não mostra a tela
    store.llm_calls.insert(LlmCallInsert(mid, None, "partial", "gemini", "fake-fast", "partial/2.0", None, 1, 1, 0, 0, 0.6, 5,
                                         LlmCallStatus.OK))
    blocked = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=2 * MIN, provider=fake, screen=SHOT, on_screen=sent.append)
    assert blocked.outcome == Outcome.BUDGET and len(sent) == 1


def test_a_cancelled_request_estimates_the_image_too(store):
    mid = _meeting(store)
    stop: dict[str, Any] = {"now": False}
    scripted = Scripted(['{"options": [{"kind": "Perguntar", '], hang=True, after=lambda _i: stop.update(now=True))
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=scripted, screen=SHOT, cancelled=lambda: stop["now"])
    req = scripted.requests[-1]
    cfg = LlmJobConfig.from_store(store)
    expected = estimate_usd(store, cfg, req.text_chars, len('{"options": [{"kind": "Perguntar", '), 1)
    assert r.outcome == Outcome.CANCELLED and r.cost_estimated and r.cost_usd == expected
    assert expected is not None and expected > (estimate_usd(store, cfg, req.text_chars, len('{"options": [{"kind": "Perguntar", ')) or 0)
    assert asyncio.iscoroutinefunction(scripted.aclose)


def test_the_check_reports_the_screen_and_its_gates(store):
    """F5.21 L3: o bloco da tela no `--check` (o Aceite): os estados, do clique à primeira opção só dos que foram com a tela e
    terminaram bem, a parcela das opções que citam a tela, os bytes e as cópias."""
    from listener.live.report import suggestions_report
    from listener.model import SuggestStatus

    mid = store.create_meeting("Orçamento", None, None, None)
    assert suggestions_report(store, mid) == {"requests": 0}
    a, (a1, _a2) = suggestion(store, mid, 60_000)
    b, _ = suggestion(store, mid, 120_000)
    c, _ = suggestion(store, mid, 180_000, status=SuggestStatus.CANCELLED)
    for sid, first in ((a, 3000), (b, 9000), (c, 50_000)):
        store._x("UPDATE suggestions SET first_ms = ? WHERE id = ?", (first, sid))
    store._x("UPDATE suggestion_options SET screen = 1 WHERE id = ?", (a1,))
    for sid, status, keep in ((a, ScreenStatus.SENT, b"copia"), (b, ScreenStatus.EMPTY, None), (c, ScreenStatus.SENT, None)):
        store.suggestions.insert_screen(ScreenInsert(sid, status, ScreenSource.WINDOW, "janela do Teams", 1600, 900,
                                                     200_000 if status == ScreenStatus.SENT else 0, 1, b"t", keep))
    out = suggestions_report(store, mid)["screen"]
    assert (out["requests"], out["by_status"], out["copies"], out["covered"]) == (3, {"enviada": 2, "vazia": 1}, 1, 3)
    assert out["first_ms"]["n"] == 1 and out["first_ms"]["p50"] == 3000 and out["cites_share"] == 0.5 and out["bytes_avg"] == 200_000
    assert out["gates"]["first_p50"]["ok"] and out["gates"]["cites"]["ok"] and out["gates"]["first_p95"]["ok"]


# ---------------------------------------------------------------- a revisão do diff do L0 e do L1 (R-03 a R-05)
def _answer(options: list[dict[str, Any]], summary: str = "slide do orçamento") -> str:
    import json

    return json.dumps({"options": options, "thread_summary": "fio", "screen_summary": summary}, ensure_ascii=False)


def _ask(text: str, screen: bool) -> dict[str, Any]:
    return {"kind": "Perguntar", "text": text, "facts": [], "screen": screen}


def test_the_screen_schema_is_strict_and_keeps_the_order_the_stream_reads():
    """R-03: sem `screen` no `required`, a OpenAI em `strict` recusaria todo pedido com a tela; o `OptionsReader` lê as opções
    antes do resto; o `maxLength` do resumo é o que o Gemini recebe (P06)."""
    assert check_strict(ps.SCHEMA_SCREEN) == []
    assert list(ps.SCHEMA_SCREEN["properties"]) == ["options", "thread_summary", "screen_summary"]
    assert list(ps.OPTION_SCHEMA_SCREEN["properties"]) == ["kind", "text", "facts", "screen"]
    assert ps.SCHEMA_SCREEN["properties"]["screen_summary"]["maxLength"] == ps.SCREEN_SUMMARY_LIMIT


def test_the_screen_alone_sustains_an_option_and_only_a_sent_screen_goes(store):
    """R-04 (T09, A-42): a opção que se apoia só na tela sai sustentada; (T01) com a tela vazia ou falha, a imagem não vai."""
    mid = _meeting(store)
    s = Scripted([_answer([_ask("A margem do slide já tem a exceção?", True)])])
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=s, screen=SHOT, options_n=1)
    assert r.options[0].screen and r.options[0].facts == () and r.options[0].evidence == Evidence.OK
    for status in (ScreenStatus.EMPTY, ScreenStatus.FAILED):
        s = Scripted([_answer([_ask("a", True)])])
        r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=2 * MIN, provider=s, screen=replace(SHOT, status=status), options_n=1)
        assert s.requests[-1].image_count == 0 and not r.with_screen and store.suggestions.get(r.suggestion_id)["screen_summary"] is None


def test_only_previous_screens_still_bring_the_rules_and_never_a_new_summary(store):
    """R-04 (P05, A-39): com só os resumos anteriores, as regras da tela vão (o que está nela é dado, nunca instrução); (T10) sem
    imagem, o resumo que a LLM inventar não é gravado."""
    mid = _meeting(store)
    run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=Scripted([_answer([_ask("a", True)])]), screen=SHOT, options_n=1)
    s = Scripted([_answer([_ask("b", False)], "inventado")])
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=2 * MIN, provider=s, options_n=1)
    assert ps.RULES_SCREEN in s.requests[-1].messages[0].text and s.requests[-1].json_schema is ps.SCHEMA_SCREEN
    assert store.suggestions.get(r.suggestion_id)["screen_summary"] is None


def test_the_early_exits_never_show_the_screen(store):
    """T04: a reunião apagada e sem provedor (o teto está no teste das saídas cedo acima) não põem a miniatura no chat."""
    sent: list[ScreenSent] = []
    mid = _meeting(store)
    store.delete_meeting(mid)
    gone = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=FakeProvider(), screen=SHOT, on_screen=sent.append)
    store.providers.update_provider("gemini", api_key="")
    other = store.create_meeting("Sem chave", None, None, None)
    none = run_suggest(store, other, mode=SuggestMode.SPEAK, t_ms=MIN, screen=SHOT, on_screen=sent.append)
    assert (gone.outcome, none.outcome, sent) == (Outcome.GONE, Outcome.NO_PROVIDER, [])


def test_the_task_reads_the_last_3_screens_of_the_window_and_discounts_them_from_the_ceiling(store):
    """T17 e T18: a tela antes da janela e a quarta mais antiga ficam fora; as 3 vão na ordem do tempo (a linha do tempo
    ordena: o `reversed` da tarefa não muda o texto). T05 e T06 (A-35): com as legendas acima do teto, os caracteres delas
    ficam no teto menos as linhas das telas, e acima da metade dele."""
    mid = _meeting(store)
    for i in range(60):                                                   # 60 falas de ~250 caracteres: acima dos 12.000
        _caption(store, mid, 5 * MIN + i * 14_000, f"fala {i} " + "x" * 240, label="Ana — CFO" if i % 2 else None,
                 channel="R" if i % 2 else "L")
    for minute in (1, 16, 17, 18, 19):
        sid, _ = suggestion(store, mid, minute * MIN)
        store._x("UPDATE suggestions SET screen_summary = ? WHERE id = ?", (f"s{minute:02d} " + "y" * 446, sid))
    fake = FakeProvider()
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20 * MIN, provider=fake)
    text = fake.requests[-1].messages[-1].text
    kept = [ps.ScreenLine(m * MIN, f"s{m:02d} " + "y" * 446).render() for m in (17, 18, 19)]
    assert "s01 " not in text and "s16 " not in text and all(line in text for line in kept)
    assert [text.index(line) for line in kept] == sorted(text.index(line) for line in kept)
    ceiling = suggest_task.MAX_ANCHOR_CHARS
    budget = ceiling - sum(len(line) + 1 for line in kept)
    assert "fala 0 " not in text and "fala 59 " in text                     # o teto cortou pelo começo
    assert ceiling // 2 < budget - 400 < r.input_chars <= budget
    other = store.create_meeting("Com folga no limite", None, None, None)          # T17: só a janela tira a de antes dela
    _caption(store, other, 19 * MIN, "Seguimos com o orçamento.")
    for minute, mark in ((1, "tela-antiga"), (19, "tela-nova")):
        sid, _ = suggestion(store, other, minute * MIN)
        store._x("UPDATE suggestions SET screen_summary = ? WHERE id = ?", (mark, sid))
    run_suggest(store, other, mode=SuggestMode.SPEAK, t_ms=20 * MIN, provider=fake)
    text = fake.requests[-1].messages[-1].text
    assert "tela-antiga" not in text and "[19:00] Tela: tela-nova" in text


def test_the_30_s_count_from_the_click_and_not_from_the_request(store):
    """T13 (A-19): o print e o preparo contam; com o clique 29,95 s antes, 0,1 s de resposta já estoura o prazo."""
    mid = _meeting(store)
    now = [100.0]
    polls = [0]

    def tick(_i: int) -> None:
        now[0] += 0.1

    def stop() -> bool:                                                   # o prazo contado do pedido cairia aqui, cancelado
        polls[0] += 1
        return polls[0] > 5

    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, clock=lambda: now[0], clicked_at=now[0] - 29.95, cancelled=stop,
                    provider=Scripted(['{"options": ['], hang=True, after=tick), screen=SHOT)
    assert r.outcome == Outcome.ERROR and r.error == suggest_task.TIMED_OUT


def test_the_image_costs_input_tokens_and_a_long_screen_summary_is_cut_not_the_suggestion(store):
    """A06: a imagem pelo preço de entrada (2.400 tokens a 0,30 por milhão); A07: a OpenAI e o Claude não recebem o
    `maxLength`, e o resumo da tela longo é cortado em vez de reprovar a sugestão inteira."""
    mid = _meeting(store)
    cfg = LlmJobConfig.from_store(store)
    assert estimate_usd(store, cfg, 3500, 350, 1) == 0.00127                           # (1.000 + 2.400) x 0,30 + 100 x 2,50
    long = {"options": [_ask("e a margem?", True)], "thread_summary": "fio", "screen_summary": "y" * 900}
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=FakeProvider(scripts={LlmTask.SUGGEST: long}), screen=SHOT)
    assert r.outcome == Outcome.DONE and store.suggestions.get(r.suggestion_id)["screen_summary"] == "y" * ps.SCREEN_SUMMARY_LIMIT


def test_the_meeting_deleted_just_before_the_screen_is_recorded_leaves_nothing(store, monkeypatch):
    """T15: apagada entre a sugestão e a tela, a gravação da tela falha pela chave estrangeira e a cascata não deixa sobra."""
    mid = _meeting(store)
    original = store.suggestions.insert_screen

    def late(row: ScreenInsert) -> None:
        store.delete_meeting(mid)
        original(row)

    monkeypatch.setattr(store.suggestions, "insert_screen", late)
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=MIN, provider=FakeProvider(), screen=SHOT)
    assert r.outcome == Outcome.GONE and store._q("SELECT COUNT(*) FROM suggestion_screens")[0][0] == 0


def test_the_check_splits_the_requests_with_the_screen_and_holds_each_gate(store):
    """R-03 e R-08 da revisão do L3: os tempos, a utilidade e o custo por pedido da F5.20 só dos pedidos sem a tela enviada;
    no bloco da tela, a utilidade e o custo com os alvos, a `falhou` fora das enviadas, o p50 e o p95 (não a média nem o
    máximo) entre os alvos das duas entregas, a parcela das que citam a tela, as fontes, os bytes de toda tela enviada e as
    cópias só desta reunião."""
    from listener.live.report import suggestions_report
    from listener.model import SuggestStatus

    def screen(sid: int, status: ScreenStatus, source: ScreenSource, size: int, copy: bytes | None = None) -> None:
        store.suggestions.insert_screen(ScreenInsert(sid, status, source, "janela do Teams", 1600, 900, size, 0, b"t", copy))

    mid = store.create_meeting("Orçamento", None, None, None)
    plain, (p1, _p2) = suggestion(store, mid, 10_000)
    store._x("UPDATE suggestions SET first_ms = 2000 WHERE id = ?", (plain,))
    store._x("UPDATE suggestion_options SET rating = 1 WHERE id = ?", (p1,))
    firsts = [3500] * 11 + [7000] * 8 + [30_000]                   # p50 3,5 s e p95 7 s; a média 6,2 s e o máximo 30 s
    cited = 0
    for k, first in enumerate(firsts):
        sid, (a, b) = suggestion(store, mid, 60_000 + k * 1000)
        store._x("UPDATE suggestions SET first_ms = ?, cost_usd = 0.002 WHERE id = ?", (first, sid))
        for oid in (a, b)[:1 if cited >= 16 else 2]:
            if cited < 18:
                store._x("UPDATE suggestion_options SET screen = 1 WHERE id = ?", (oid,))
                cited += 1
        if k < 10:
            store._x("UPDATE suggestion_options SET rating = 1 WHERE id = ?", (a,))
        screen(sid, ScreenStatus.SENT, ScreenSource.WINDOW, 200_000, b"copia" if k < 5 else None)
    gone, _ = suggestion(store, mid, 900_000, status=SuggestStatus.CANCELLED)
    screen(gone, ScreenStatus.SENT, ScreenSource.CURSOR, 100_000)
    failed, _ = suggestion(store, mid, 950_000)
    store._x("UPDATE suggestions SET first_ms = 50000 WHERE id = ?", (failed,))
    screen(failed, ScreenStatus.FAILED, ScreenSource.FOREGROUND, 0)
    other = store.create_meeting("Outra", None, None, None)
    elsewhere, _ = suggestion(store, other, 60_000)
    screen(elsewhere, ScreenStatus.SENT, ScreenSource.WINDOW, 200_000, b"copia")
    out = suggestions_report(store, mid)
    assert out["first_ms"]["n"] == 2 and out["cost_per_request_usd"] == 0.001        # o sem tela e o da tela que falhou
    sc = out["screen"]
    assert (sc["requests"], sc["by_status"], sc["by_source"]) == (22, {"enviada": 21, "falhou": 1},
                                                                    {"janela": 20, "cursor": 1, "primeiro_plano": 1})
    assert (sc["first_ms"]["n"], sc["first_ms"]["p50"], sc["first_ms"]["p95"], sc["first_ms"]["max"]) == (20, 3500, 7000, 30_000)
    assert sc["cites_share"] == 0.45 and sc["useful_share"] == 0.5 and sc["copies"] == 5
    assert sc["bytes_avg"] == round((20 * 200_000 + 100_000) / 21) and sc["cost_per_request_usd"] == round((20 * 0.002 + 0.001) / 21, 6)
    assert all(g["ok"] for g in sc["gates"].values()), sc["gates"]
