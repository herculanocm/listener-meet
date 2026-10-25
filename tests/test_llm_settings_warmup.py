"""Configurações e warm-up da F3 (L4): grupo LLM gerado da tabela (idioma como opção, P1), sub-abas por provedor (chave,
modelos listados, testar conexão numa Task, ativo para texto e embeddings, preços), transcritor `llm` por capacidade;
glossário no warm-up com debounce, uma chamada em voo e resposta velha descartada; casamento local de participantes;
`create_meeting` com glossário e religação da chamada; `build_prompt` com o glossário da LLM primeiro; desempate por ids."""
from __future__ import annotations

import json

import pytest

from listener.llm import glossary_task
from listener.llm.adapters_common import friendly_error
from listener.pipeline.runner import build_prompt
from listener.pipeline.types import Word

pytest.importorskip("PySide6")
KEY = "sk-chave-de-teste-ABCDEFGH12345678"


def _item_enabled(combo, text: str) -> bool:
    from PySide6.QtCore import Qt

    row = [combo.itemText(i) for i in range(combo.count())].index(text)
    return bool(combo.model().flags(combo.model().index(row, 0)) & Qt.ItemFlag.ItemIsEnabled)


def _people(store):
    return [store.people.create_person("Ana Souza", role="CFO"), store.people.create_person("Rodrigo Lima"),
            store.people.create_person("Ana Paula")]


def test_match_people_locally_merge_and_friendly_errors(store):
    ana, rodrigo, _paula = _people(store)
    people = [dict(p) for p in store.people.list_people()]
    resolved, unresolved = glossary_task.match_people_locally(["rodrigo", "Ana Souza", "ana", "Rodrigo Lmia", "Carlos"], people)
    assert dict(resolved) == {"rodrigo": rodrigo, "Ana Souza": ana, "Rodrigo Lmia": rodrigo} or dict(resolved) == {"rodrigo": rodrigo,
                                                                                                                  "Ana Souza": ana}
    assert "ana" in unresolved and "Carlos" in unresolved                                             # "ana" é ambíguo: duas Anas
    assert glossary_task.split_names("Ana, Bia; Carlos\nDani") == ["Ana", "Bia", "Carlos", "Dani"] and glossary_task.split_names(None) == []
    assert glossary_task.merge_glossary(["SRM", "FIDC"], "fidc, Cetip") == "SRM, FIDC, Cetip"
    assert friendly_error("Errno 11001 getaddrinfo failed").startswith("DNS:")
    assert friendly_error("407 Proxy Authentication Required").startswith("Proxy pediu")
    assert friendly_error("CERTIFICATE_VERIFY_FAILED").startswith("TLS:")
    assert friendly_error("401 invalid api key").startswith("Chave recusada") and friendly_error("").startswith("falha")
    assert friendly_error("qualquer outra coisa") == "qualquer outra coisa"


def test_request_glossary_with_fake_and_without_key(store, monkeypatch):
    ana, rodrigo, _paula = _people(store)
    r = glossary_task.request_glossary(store, "Comitê FIDC Alfa", "Rodrigo, Carlos")
    assert r.terms == () and r.error and r.error.startswith("sem chave") and dict(r.people) == {"Rodrigo": rodrigo}
    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast")
    store.providers.set_price("gemini", "fake-fast", 0.3, 2.5)
    r = glossary_task.request_glossary(store, "Comitê FIDC Alfa", "Rodrigo, Carlos")
    assert r.error is None and set(r.terms) >= {"Comitê", "FIDC", "Alfa"} and dict(r.people) == {"Rodrigo": rodrigo}
    assert r.call_id and r.expected_people_json == json.dumps([rodrigo]) and r.cost_usd is not None
    call = store.llm_calls.get(r.call_id)
    assert call["meeting_id"] is None and call["task"] == "glossary" and call["status"] == "ok" and call["prompt_version"] == "glossary/1.0"
    store.set_setting("llm.glossary_enabled", False)
    r2 = glossary_task.request_glossary(store, "x", "Ana Souza")
    assert r2.terms == () and r2.error is None and dict(r2.people) == {"Ana Souza": ana}


def test_settings_dialog_llm_group_provider_tabs_and_capability(qtbot, store, platform, monkeypatch):
    from listener.ui.settings_min import SettingsDialog

    monkeypatch.setenv("LISTENER_LLM", "fake")
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    assert "llm.output_language" in dlg._combos and "llm.cost_alert_usd" in dlg._edits and dlg.provider_tabs.tabs.count() == 3
    assert not _item_enabled(dlg._combos["pipeline.transcriber"], "llm")                                # sem provedor com chave
    tab = dlg.provider_tabs.tab_by_id["gemini"]
    assert tab.key.text() == "" and tab.fast.currentText() == "gemini-2.5-flash" and tab.strong.currentText() == "gemini-2.5-pro"
    assert tab.status.text() == "ainda não testado" and tab.prices.rowCount() >= 4
    tab.run_test()
    assert "informe a chave" in tab.status.text()
    tab.key.setText(KEY)
    tab.fast.setCurrentText("fake-fast")
    tab.run_test()
    qtbot.waitUntil(lambda: tab.last_test is not None, timeout=3000)
    assert tab.last_test.ok and "respondeu em" in tab.status.text() and store.providers.get("gemini")["tested_at"]
    tab.refresh_models()
    qtbot.waitUntil(lambda: bool(tab.last_models), timeout=3000)
    assert [m.id for m in tab.last_models] == ["fake-fast", "fake-strong"] and [tab.fast.itemText(i) for i in range(tab.fast.count())] == [
        "fake-fast", "fake-strong"]
    assert store.providers.models("gemini")[0]["id"] == "fake-fast" and "audio_in" in store.providers.models("gemini")[0]["capabilities"]
    tab.params.setText("{nao json")
    assert "parâmetros não são JSON" in (dlg.validation_error() or "")
    tab.params.setText('{"top_p": 0.9}')
    dlg._combos["llm.output_language"].setCurrentText("fr")
    dlg._edits["llm.cost_alert_usd"].setText("5")
    dlg.provider_tabs.active_llm.setCurrentIndex(dlg.provider_tabs.active_llm.findData("openai"))
    dlg.provider_tabs.active_embed.setCurrentIndex(dlg.provider_tabs.active_embed.findData("gemini"))
    assert dlg.provider_tabs.active_embed.findData("anthropic") < 0                                     # sem embeddings na Anthropic
    tab.prices.item(0, 2).setText("9,5")
    tab.prices.item(tab.prices.rowCount() - 1, 0).setText("modelo-novo")
    tab.prices.item(tab.prices.rowCount() - 1, 1).setText("1")
    assert dlg.validation_error() is None
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted
    g = store.providers.get("gemini")
    assert g["api_key"] == KEY and g["fast_model"] == "fake-fast" and g["params"] == '{"top_p": 0.9}'
    assert g["model"] == "gemini-embedding-001"
    assert store.providers.active_provider("llm")["id"] == "openai" and store.providers.active_provider("embed")["id"] == "gemini"
    assert store.get_setting("llm.output_language") == "fr" and store.get_setting("llm.cost_alert_usd") == "5"
    prices = {p["model"]: p for p in store.providers.list_prices("gemini")}
    assert prices[tab.prices.item(0, 0).text()]["output_usd_m"] == 9.5 and prices["modelo-novo"]["input_usd_m"] == 1.0
    # com o gemini ativo e chave, o modelo rápido do cache tem áudio: o transcritor `llm` habilita
    store.providers.set_active("gemini", llm=True)
    dlg2 = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg2)
    assert _item_enabled(dlg2._combos["pipeline.transcriber"], "llm")
    dlg2._combos["llm.output_language"].setCurrentText("12")
    assert "idioma" in (dlg2.validation_error() or "").lower()


def test_warmup_glossary_debounce_stale_and_no_key(qtbot, store, platform, monkeypatch):
    from listener.ui import warmup as wu

    rodrigo = store.people.create_person("Rodrigo Lima")
    opened: list[int] = []
    dlg = wu.WarmupDialog(platform, store, on_settings=lambda: opened.append(1))
    qtbot.addWidget(dlg)
    dlg._glossary_timer.setInterval(1)
    dlg.title_edit.setText("Comitê FIDC")
    dlg.title_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: "sem chave" in dlg.glossary_status.text(), timeout=3000)
    assert dlg.glossary_requests == 1 and dlg.glossary_edit.text() == ""
    dlg.glossary_status.linkActivated.emit("settings")
    assert opened == [1]
    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast")
    dlg.participants_edit.setText("Rodrigo, Carlos")
    dlg.participants_edit.editingFinished.emit()
    dlg.participants_edit.editingFinished.emit()                                                           # dois eventos: um pedido
    qtbot.waitUntil(lambda: dlg.glossary_edit.text() != "", timeout=3000)
    assert dlg.glossary_requests == 2 and "FIDC" in dlg.glossary_edit.text() and "termo(s) sugeridos" in dlg.glossary_status.text()
    dlg.glossary_edit.setText(dlg.glossary_edit.text() + ", Cetip")                                        # edição do usuário
    _cfg, fields = dlg.result_config()
    assert fields["glossary"].endswith("Cetip") and fields["expected_people_json"] == json.dumps([rodrigo]) and fields["glossary_call_id"]
    # resposta velha: a sequência anda antes de a Task voltar e o texto do usuário fica
    dlg._glossary_inflight = True
    dlg._glossary_seq += 1
    dlg._on_glossary(dlg._glossary_seq - 1, glossary_task.GlossaryResult(("Velho",), ()))
    assert "Velho" not in dlg.glossary_edit.text() and not dlg._glossary_inflight
    # mudança durante a chamada em voo agenda outra ao terminar
    dlg._glossary_inflight = True
    dlg.request_glossary()
    assert dlg._glossary_pending and dlg.glossary_requests == 2
    dlg._on_glossary(dlg._glossary_seq, glossary_task.GlossaryResult(("Novo",), ()))
    assert dlg._glossary_timer.isActive() and "Novo" in dlg.glossary_edit.text()
    dlg.reject()


def test_controller_creates_meeting_with_glossary_and_relinks_the_call(qtbot, qapp, store, platform, monkeypatch):
    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    call = store.llm_calls.insert(LlmCallInsert(None, None, "glossary", "gemini", "fake-fast", None, None, 1, 1, 0, 0, 0.0, 5,
                                                LlmCallStatus.OK))
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "t", "glossary": "SRM, FIDC", "expected_people_json": "[3]", "glossary_call_id": call})
        rec = c.recorder
        m = store.get_meeting(rec.meeting_id)
        assert m["glossary"] == "SRM, FIDC" and m["expected_people_json"] == "[3]"
        assert store.llm_calls.get(call)["meeting_id"] == rec.meeting_id
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state.value == "idle", timeout=5000)
    finally:
        c.emergency_stop()


def test_build_prompt_puts_llm_glossary_first_and_matcher_breaks_ties_by_expected_ids():
    import numpy as np

    from listener.model import ClusterStatus
    from listener.pipeline.profiles import ProfileMatcher, ProfileRef, expected_person_ids
    from listener.pipeline.types import ClusterData

    p = build_prompt("Cetip, FIDC", "Ana", "Título", (), llm_glossary="FIDC, SRM; Alfa")
    assert p == "FIDC, SRM, Alfa, Cetip Ana Título"
    assert build_prompt("", None, None, (Word(0, 1, "w"),), None) == "w" and build_prompt("a", None, None, (), "a") == "a"
    assert expected_person_ids('[1, 2, "3"]') == {1, 2, 3} and expected_person_ids("lixo") == frozenset()
    assert expected_person_ids(None) == frozenset()
    v = np.zeros(4, np.float32)
    v[0] = 1.0
    w = np.array([0.98, 0.2, 0, 0], np.float32)
    w /= np.linalg.norm(w)
    cluster = ClusterData("SPK_0", 20_000, v)
    profiles = [ProfileRef(1, 10, v * 0.99 + w * 0.01), ProfileRef(2, 20, w)]
    amb = ProfileMatcher(0.6, 0.1).match([cluster], profiles)[0]
    assert amb.status == ClusterStatus.AMBIGUOUS
    by_id = ProfileMatcher(0.6, 0.1, expected_ids=frozenset({20})).match([cluster], profiles)[0]
    assert by_id.status == ClusterStatus.AUTO and by_id.person_id == 20                                  # id esperado desempata
    by_name = ProfileMatcher(0.6, 0.1, expected=("x",), expected_ids=frozenset()).match([cluster], profiles)[0]
    assert by_name.status == ClusterStatus.AMBIGUOUS
