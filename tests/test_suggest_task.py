"""F5.20 L1 (C8): a tarefa da sugestão ao vivo e a do memorando, com o fake. A janela (reancorada na primeira e aos 20 min,
desde a anterior com ao menos 3 min, no teto de `suggest.window_min`); o dossiê (as ações abertas dos esperados e as suas, sem
as de terceiros; as três últimas reuniões; o corte); a sugestão gravada com as opções, os fatos e o `t_ms`; o id desconhecido
"sem evidência"; cada opção entregue antes do fim da resposta; a compactação medida; o fio, as avaliações, o memorando, a
linha e o objetivo no pedido seguinte; sem provedor; o teto; fora do esquema no fim com as opções mantidas; sem opção;
cancelada no meio e no prazo com o custo estimado; a reunião apagada durante a chamada; o memorando só com 10 avaliações."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from listener.llm import redact, suggest_task
from listener.llm.base import LlmStream, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.suggest_task import Outcome, dossier_text, run_memo, run_suggest, window
from listener.llm.types import Capability, Completion, LlmTask, ProviderKind, Request, Usage
from listener.model import ActionStatus, Evidence, LlmCallStatus, SuggestMode, SuggestStatus, SummaryKind
from listener.store.actions import ActionInsert
from listener.store.llm_calls import LlmCallInsert
from listener.store.summaries import SummaryInsert
from listener.llm.jobconfig import LlmJobConfig
from tests.test_live_partial import _caption, _ready
from tests.test_suggest_store import suggestion

MIN = 60_000


class Scripted:
    """Um provedor de teste que entrega os pedaços dados pelo `stream`; antes de cada um, espera `gate(i)` (até 2 s); com
    `hang`, fica parado depois do último (para o cancelamento e o prazo)."""

    id = "fake"
    kind = ProviderKind.GEMINI
    capabilities = frozenset({Capability.TEXT, Capability.STREAMING})

    def __init__(self, pieces: list[str], gate: Callable[[int], bool] | None = None, hang: bool = False,
                 after: Callable[[int], None] | None = None):
        self.pieces, self.gate, self.hang, self.after = pieces, gate, hang, after
        self.requests: list[Request] = []

    def stream(self, req: Request) -> LlmStream:
        self.requests.append(req)

        async def chunks() -> AsyncIterator[str]:
            for i, piece in enumerate(self.pieces):
                if self.gate is not None:
                    for _ in range(200):
                        if self.gate(i):
                            break
                        await asyncio.sleep(0.01)
                    else:                                         # R-05: o portão fechado é falha, não um "segue mesmo assim"
                        raise AssertionError(f"o pedaço {i} esperou 2 s por uma condição que não veio")
                yield piece
                if self.after is not None:
                    self.after(i)
            if self.hang:
                await asyncio.sleep(3600)

        def finish(text: str) -> Completion:
            return Completion(text, None, Usage(400, 120), "fake-fast", 7)

        return LlmStream(chunks(), finish)

    async def aclose(self) -> None:
        return None


def _meeting(store: Any, *, goal: str | None = None) -> int:
    store.upsert_me("Carla", role="CTO", company="Beta")
    _ready(store)
    mid = store.create_meeting("Orçamento", None, None, "Ana", goal=goal)
    _caption(store, mid, 1000, "Bom dia, vamos ao orçamento de 2027.")
    _caption(store, mid, 5000, "O corte é de oito por cento.")
    _caption(store, mid, 9000, "Ok.", label=None, channel="L")
    _caption(store, mid, 12_000, "Precisamos de uma exceção para dados.", label=None, channel="L")
    return mid


def _options(*items: tuple[str, str, list[int]], summary: str = "o fio") -> str:
    return json.dumps({"options": [{"kind": k, "text": t, "facts": f} for k, t, f in items], "thread_summary": summary}, ensure_ascii=False)


def _user_text(provider: Any, i: int = -1) -> str:
    return provider.requests[i].messages[1].text


# ---------------------------------------------------------------- a janela e o dossiê
def test_the_window_reanchors_first_and_every_20_min_and_otherwise_starts_at_the_previous(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    assert window(store, mid, 20 * MIN, 8) == suggest_task.Window(5 * MIN, True, suggest_task.MAX_ANCHOR_CHARS)
    suggestion(store, mid, 20 * MIN, anchored=True, t_until_ms=int(19.5 * MIN))
    assert window(store, mid, 25 * MIN, 8) == suggest_task.Window(int(19.5 * MIN), False, suggest_task.MAX_INPUT_CHARS)
    assert window(store, mid, 21 * MIN, 8).t_from_ms == 18 * MIN                     # ao menos 3 min de legenda
    assert window(store, mid, 35 * MIN, 8).t_from_ms == 27 * MIN                     # no máximo `suggest.window_min`
    assert window(store, mid, 40 * MIN, 8) == suggest_task.Window(25 * MIN, True, suggest_task.MAX_ANCHOR_CHARS)


def test_the_dossier_has_the_open_actions_of_the_expected_and_yours_and_the_last_meetings(store):
    me = store.upsert_me("Carla", role="CTO")
    ana, bruno = store.people.create_person("Ana Souza", role="CFO"), store.people.create_person("Bruno")
    old = []
    for i in range(4):
        mid = store.create_meeting(f"Reunião {i}", None, None, None, expected_people_json=json.dumps([ana]))
        old.append(mid)
    sid = store.summaries.insert(SummaryInsert(old[0], SummaryKind.MINUTES, "{}", "## Ata", "fake", "m", "minutes/2.0", None, None,
                                               "pt-BR"))
    store.actions.insert_many([ActionInsert(old[0], sid, "mandar a planilha", None, ana, "2026-11-10", 1000, None, None),
                               ActionInsert(old[0], sid, "rever o contrato", None, me, None, 2000, None, None),
                               ActionInsert(old[0], sid, "ligar para o banco", None, bruno, None, 3000, None, None),
                               ActionInsert(old[0], sid, "já feita", None, ana, None, 4000, None, None, ActionStatus.DONE)])
    now = store.create_meeting("Agora", None, None, None, expected_people_json=json.dumps([ana]))
    text = dossier_text(store, now)
    assert "- Ação aberta: mandar a planilha · dono: Ana Souza · prazo: 2026-11-10 · reunião: Reunião 0" in text
    assert "rever o contrato · dono: Carla · prazo: sem prazo" in text and "ligar para o banco" not in text and "já feita" not in text
    assert text.splitlines()[-1].startswith("Últimas reuniões com estes participantes: Reunião 3 (")
    assert "Reunião 1" in text and "Reunião 0 (" not in text.splitlines()[-1] and "Agora" not in text
    store.actions.insert_many([ActionInsert(old[0], sid, "x" * 200, None, ana, None, 5000 + i, None, None) for i in range(12)])
    cut = dossier_text(store, now)
    assert len(cut) <= suggest_task.DOSSIER_MAX_CHARS and all(line.startswith(("- Ação aberta", "Últimas")) for line in cut.splitlines())
    assert dossier_text(store, store.create_meeting("Sozinha", None, None, None)).count("Ação aberta") == 1     # só a sua
    assert dossier_text(store, 999) == ""


# ---------------------------------------------------------------- a sugestão
def test_a_suggestion_is_recorded_with_options_facts_and_times(store):
    mid = _meeting(store, goal="fechar o corte")
    seen: list[Any] = []
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, options_n=3, nudge="mais curta", on_option=seen.append,
                    provider=FakeProvider())
    assert r.outcome == Outcome.DONE and len(r.options) == len(r.option_ids) == 3 and seen == list(r.options)
    assert [o.kind for o in r.options] == ["Perguntar", "Posicionar", "Propor o próximo passo"]
    assert r.options[0].facts == ((1, 1000), (4, 12_000)) and r.options[0].evidence == Evidence.OK
    assert r.first_ms is not None and r.total_ms is not None and r.cost_usd is not None and not r.cost_estimated
    assert r.raw_chars > r.input_chars and r.thread_summary == "Fio simulado com 2 legendas."
    row = store.suggestions.get(r.suggestion_id)
    assert (row["status"], row["mode"], row["nudge"], row["goal"], row["anchored"]) == ("ok", "fala", "mais curta", "fechar o corte", 1)
    assert json.loads(store.suggestions.options_for(r.suggestion_id)[0]["facts_json"]) == [{"caption_id": 1, "t_ms": 1000},
                                                                                            {"caption_id": 4, "t_ms": 12_000}]
    call = store.llm_calls.get(r.call_id)
    assert (call["task"], call["status"], call["strategy"], call["meeting_id"]) == ("suggest", "ok", "stream", mid)


def test_an_unknown_caption_id_leaves_the_option_without_evidence(store):
    mid = _meeting(store)
    fake = FakeProvider(scripts={LlmTask.SUGGEST: json.loads(_options(("Perguntar", "E o prazo?", [9999, 1]), ("Pedir tempo", "Um minuto.",
                                                                                                               [9999])))})
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=fake)
    assert [o.facts for o in r.options] == [((1, 1000),), ()] and [o.evidence for o in r.options] == [Evidence.OK, Evidence.NONE]


def test_each_option_is_delivered_before_the_answer_ends(store):
    mid = _meeting(store)
    text = _options(("Perguntar", "Primeira.", [1]), ("Posicionar", "Segunda.", []))
    cut = text.index("}, {") + 1                                                    # a primeira opção inteira no primeiro pedaço
    seen: list[Any] = []
    slow = Scripted([text[:cut], text[cut:]], gate=lambda i: i == 0 or len(seen) == 1)  # o resto só chega depois da primeira na tela
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, options_n=2, on_option=seen.append, provider=slow)
    assert r.outcome == Outcome.DONE and [o.text for o in seen] == ["Primeira.", "Segunda."]


def test_the_next_request_carries_the_thread_ratings_memo_nudge_and_goal(store):
    mid = _meeting(store, goal="fechar o corte")
    store.set_setting("suggest.memo", "Prefere perguntas curtas.")
    fake = FakeProvider()
    first = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=fake)
    store.suggestions.set_rating(first.option_ids[0], 0, "longa demais")
    store.suggestions.set_used(first.option_ids[1], True)
    run_suggest(store, mid, mode=SuggestMode.ORIENT, t_ms=40_000, nudge="em pergunta", dossier="- Ação aberta: x", provider=fake)
    text = fake.requests[-1].messages[1].text
    assert "Fio da reunião até a sugestão anterior:\nFio simulado com 2 legendas." in text
    assert f"- Ruim (longa demais) · Perguntar: {first.options[0].text}" in text and f"- Usei · Posicionar: {first.options[1].text}" in text
    assert "O que esta pessoa prefere nas sugestões (memorando):\nPrefere perguntas curtas." in text
    assert "Como sugerir agora: em pergunta" in text
    assert "Objetivo dela nesta reunião: fechar o corte" in text and "Dossiê dos participantes:\n- Ação aberta: x" in text
    assert "Quem pede a sugestão: (1) Carla — CTO, Beta" in text and text.endswith("Modo: situar, 3 itens.")
    assert "Falantes identificados nesta reunião (person_id, nome — cargo, empresa): (0) Ana — CFO" in text


def test_no_provider_and_the_budget_stop_before_any_call(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    assert run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=1000).outcome == Outcome.NO_PROVIDER
    _ready(store)
    store.set_setting("llm.cost_alert_usd", 1.0)
    store.llm_calls.insert(LlmCallInsert(mid, None, "partial", "gemini", "fake-fast", "partial/2.0", None, 1, 1, 0, 0, 0.6, 5,
                                         LlmCallStatus.OK))
    fake = FakeProvider()
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=1000, provider=fake)
    assert r.outcome == Outcome.BUDGET and "metade do alerta de US$ 1,00" in (r.error or "") and fake.requests == []
    assert run_suggest(store, 999, mode=SuggestMode.SPEAK, t_ms=1000, provider=fake).outcome == Outcome.GONE


def test_off_schema_at_the_end_keeps_the_options_already_shown(store):
    mid = _meeting(store)
    broken = json.dumps({"options": [{"kind": "Perguntar", "text": "E o prazo?", "facts": [1]}]})       # sem o fio
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=Scripted([broken]))
    assert r.outcome == Outcome.ERROR and (r.error or "").startswith("resposta fora do esquema") and len(r.options) == 1
    row = store.suggestions.get(r.suggestion_id)
    assert row["status"] == "erro" and row["thread_summary"] is None and len(store.suggestions.options_for(r.suggestion_id)) == 1


def test_an_answer_without_a_valid_option_is_an_error(store):
    mid = _meeting(store)
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=Scripted([_options(("Assunto", "fora do modo", []))]))
    assert r.outcome == Outcome.ERROR and r.error == suggest_task.NO_OPTIONS and r.options == ()


def test_cancelled_in_the_middle_records_the_estimated_cost(store):
    mid = _meeting(store)
    text = _options(("Perguntar", "Primeira.", [1]), ("Posicionar", "Segunda.", []))
    seen: list[Any] = []
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, on_option=seen.append, cancelled=lambda: bool(seen),
                    provider=Scripted([text[:text.index("}, {") + 1]], hang=True))
    assert r.outcome == Outcome.CANCELLED and r.cost_estimated and r.cost_usd is not None and len(r.options) == 1
    row = store.suggestions.get(r.suggestion_id)
    assert (row["status"], row["cost_estimated"]) == (str(SuggestStatus.CANCELLED), 1)
    call = store.llm_calls.get(r.call_id)
    assert (call["status"], call["error"], call["strategy"]) == ("erro", "cancelado", "stream_estimated")


def test_the_30_s_deadline_is_kept_by_the_task_loop(store):
    mid = _meeting(store)
    now = [0.0]

    def jump(_i: int) -> None:
        now[0] = 31.0                                                             # depois do primeiro pedaço, o relógio passa do prazo

    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, clock=lambda: now[0],
                    provider=Scripted(['{"options": ['], hang=True, after=jump))
    assert r.outcome == Outcome.ERROR and r.error == suggest_task.TIMED_OUT and r.cost_estimated


def test_the_meeting_deleted_during_the_call_records_only_the_call(store):
    mid = _meeting(store)
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, on_option=lambda _o: store.delete_meeting(mid),
                    provider=FakeProvider())
    assert r.outcome == Outcome.GONE and store.llm_calls.get(r.call_id)["meeting_id"] is None


# ---------------------------------------------------------------- o memorando
def test_the_memo_runs_only_with_10_new_ratings_and_has_no_meeting(store):
    mid = _meeting(store)
    _sid, ids = suggestion(store, mid, options=12)
    fake = FakeProvider()
    for option_id in ids[:9]:
        store.suggestions.set_rating(option_id, 1)
    assert run_memo(store, provider=fake) == suggest_task.MemoResult(Outcome.DONE, None, 9) and fake.requests == []
    store.suggestions.set_used(ids[9], True)
    r = run_memo(store, provider=fake)
    assert r.outcome == Outcome.DONE and r.memo == "Prefere opções curtas e em pergunta." and r.ratings_used == 10
    assert store.get_setting("suggest.memo") == r.memo and store.get_setting("suggest.memo_ratings") == "10"
    assert store.get_setting("suggest.memo_at") and store.llm_calls.get(r.call_id)["meeting_id"] is None
    assert "- Usei · Perguntar: opção 9" in fake.requests[0].messages[1].text
    assert run_memo(store, provider=fake).memo is None and len(fake.requests) == 1                    # nada novo desde então


# ---------------------------------------------------------------- a revisão do diff do L1 (R-02, R-05, R-06, R-08)
def test_the_lists_and_texts_over_the_schema_ceiling_are_cut_not_the_suggestion(store):
    """R-02: a OpenAI e a Anthropic não recebem os tetos; 7 fatos, 5 opções com 4 pedidas e o texto longo são cortados."""
    mid = _meeting(store)
    many = {"options": [{"kind": "Perguntar", "text": "x" * 500, "facts": [1, 2, 4, 1, 2, 4, 1]}] +
                       [{"kind": "Posicionar", "text": f"opção {i}", "facts": []} for i in range(4)], "thread_summary": "y" * 900}
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, options_n=4, provider=FakeProvider(scripts={LlmTask.SUGGEST: many}))
    assert r.outcome == Outcome.DONE and len(r.options) == 4 and len(r.options[0].text) == suggest_task.TEXT_LIMIT
    assert r.options[0].facts == ((1, 1000), (2, 5000), (4, 12_000)) and r.thread_summary == "y" * suggest_task.SUMMARY_LIMIT


def test_a_cited_caption_the_request_never_carried_is_no_evidence(store):
    """R-05: o "Ok." saiu na compactação; citado, não vale como fato (a LLM não o viu)."""
    mid = _meeting(store)
    fake = FakeProvider(scripts={LlmTask.SUGGEST: json.loads(_options(("Perguntar", "E o prazo?", [3])))})
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=fake)
    assert r.options[0].facts == () and r.options[0].evidence == Evidence.NONE


def test_never_more_options_than_asked_and_the_estimate_by_characters(store):
    mid = _meeting(store)
    four = json.loads(_options(*(("Perguntar", f"o{i}", []) for i in range(4))))
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, options_n=3, provider=FakeProvider(scripts={LlmTask.SUGGEST: four}))
    assert [o.text for o in r.options] == ["o0", "o1", "o2"]
    assert suggest_task.estimate_usd(store, LlmJobConfig.from_store(store), 3500, 350) == 0.00055   # 1.000 e 100 tokens a 0,30 e 2,50


def test_the_partial_goes_in_the_request_and_only_r_speakers_outside_the_room(store):
    from listener.model import SummaryKind as Kind

    mid = _meeting(store)
    store.captions.insert(__import__("listener.store.captions", fromlist=["CaptionInsert"]).CaptionInsert(
        mid, "L", 14_000, 16_000, "Eu concordo com o corte.", None, "Carla — CTO", 0.9, "fake:small", 6000))
    store.summaries.insert(SummaryInsert(mid, Kind.PARTIAL, json.dumps({"summary": "O corte de 8 %.", "decisions": [], "actions": [],
                                                                        "last_caption_id": 4}), "## Parcial", "fake", "m",
                                         "partial/2.0", None, None, "pt-BR"))
    fake = FakeProvider()
    run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=fake)
    text = _user_text(fake)
    assert 'Resumo parcial atual (JSON):\n{"summary":"O corte de 8 %.","decisions":[],"actions":[]}' in text
    assert "(0) Ana — CFO" in text and "Carla — CTO, " not in text.split("Falantes identificados")[1].split("\n")[0]
    store._x("UPDATE meetings SET is_in_room = 1 WHERE id = ?", (mid,))                     # R-06: na sala, o L também
    run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=40_000, provider=fake)
    assert "Carla — CTO" in _user_text(fake).split("Falantes identificados")[1].split("\n")[0]


def test_the_dossier_keeps_at_most_12_actions(store):
    ana = store.people.create_person("Ana Souza", role="CFO")
    old = store.create_meeting("Antiga", None, None, None, expected_people_json=json.dumps([ana]))
    sid = store.summaries.insert(SummaryInsert(old, SummaryKind.MINUTES, "{}", "## Ata", "fake", "m", "minutes/2.0", None, None, "pt-BR"))
    store.actions.insert_many([ActionInsert(old, sid, f"ação {i}", None, ana, None, i, None, None) for i in range(15)])
    now = store.create_meeting("Agora", None, None, None, expected_people_json=json.dumps([ana]))
    assert dossier_text(store, now).count("- Ação aberta:") == suggest_task.DOSSIER_MAX_ACTIONS == 12


def test_the_key_never_reaches_the_recorded_error(store):
    """CP10: a chave da sugestão entra na redação antes da chamada (uma chave só deste teste, fora do registro global)."""
    mid = _meeting(store)
    key = "AIzaSy-chave-so-da-sugestao-QWERTY987654"
    store.providers.update_provider("gemini", api_key=key)
    fake = FakeProvider()
    fake.fail_next(TransientError(f"falhou com {key}"), LlmTask.SUGGEST)
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=fake)
    call = store.llm_calls.get(r.call_id)
    assert r.outcome == Outcome.ERROR and key not in (call["error"] or "") and key not in (r.error or "")
    assert redact.GLOBAL.redact(key) != key


def test_the_options_come_from_the_whole_text_when_the_reader_finds_none(store, monkeypatch):
    mid = _meeting(store)
    monkeypatch.setattr(suggest_task.OptionsReader, "feed", lambda self, piece: (self._parts.append(piece), [])[1])
    r = run_suggest(store, mid, mode=SuggestMode.SPEAK, t_ms=20_000, provider=FakeProvider())
    assert r.outcome == Outcome.DONE and len(r.options) == 3


def test_the_memo_trigger_survives_a_deleted_meeting(store):
    """R-08: o memorando de antes usou 20 avaliações; a reunião excluída leva 5 delas; 10 novas bastam (pela diferença das
    contagens, 15 + 10 contra 20, faltariam 5)."""
    mid, gone = _meeting(store), store.create_meeting("Excluída", None, None, None)
    _s, old = suggestion(store, mid, options=15)
    _g, lost = suggestion(store, gone, options=5)
    for option_id in old + lost:
        store.suggestions.set_rating(option_id, 1)
    store._x("UPDATE suggestion_options SET rated_at = '2026-01-01T10:00:00.000-03:00'")
    store.set_setting("suggest.memo_at", "2026-06-01T10:00:00.000-03:00")
    store.set_setting("suggest.memo_ratings", "20")
    store.delete_meeting(gone)
    _n, new = suggestion(store, mid, options=10)
    for option_id in new[:9]:
        store.suggestions.set_rating(option_id, 0)
    assert not suggest_task.memo_due(store)
    store.suggestions.set_used(new[9], True)
    assert suggest_task.memo_due(store) and store.suggestions.count_rated() == 25


def test_the_memo_never_reaches_the_log(store, caplog):
    """R-03: o memorando é texto livre; o log da configuração leva só o tamanho."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="listener"):
        store.set_setting("suggest.memo", "PALAVRA-DO-MEMORANDO-77")
    assert "PALAVRA-DO-MEMORANDO-77" not in caplog.text and "texto com 23 caracteres" in caplog.text
