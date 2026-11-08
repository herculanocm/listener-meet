"""F5.21 L1: a tela nas sugestões ao vivo do lado da LLM e do banco, sem Qt. A parte de imagem em cada adaptador (o Gemini
depois do texto, a OpenAI em data URL com `detail`, o Claude antes do texto e sem cair no filtro da mensagem em branco); a
capacidade pela tabela, nunca pelo cache antigo dos modelos (A-12); a v11 com a volta; o repositório da tela; o prompt
`suggest` 1.1 com o esquema, as regras e a linha do tempo da tela, e sem tela igual ao de antes; a tarefa com a tela (o aviso
depois das saídas cedo, a imagem na última mensagem, a opção que usa a tela sustentada, o resumo gravado e lido pelo pedido
seguinte, a tela gravada sem a imagem enviada, o pedido guardado com o rótulo e nunca os bytes, o tempo do clique); a tela
vazia que não vai; o custo estimado com a imagem."""
from __future__ import annotations

import asyncio
import base64
import sqlite3
from dataclasses import replace
from typing import Any

from listener.llm.fake import FakeProvider
from listener.llm.jobconfig import LlmJobConfig, image_capable
from listener.llm.prompts import suggest as ps
from listener.llm.suggest_task import MAX_OUTPUT_TOKENS_SCREEN, Outcome, ScreenSent, ScreenShot, estimate_usd, run_suggest
from listener.llm.types import ImagePart, LlmTask, Message, Request, Role
from listener.model import Evidence, LlmCallStatus, ScreenSource, ScreenStatus, SuggestMode
from listener.store.llm_calls import LlmCallInsert
from listener.store.suggestions import ScreenInsert
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
    store.providers.update_provider("gemini", fast_model="gpt-4o-audio-preview")
    assert not image_capable(store)                                       # uma família sem imagem


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

