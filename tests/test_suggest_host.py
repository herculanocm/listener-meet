"""F5.20 L2 (C10, C13): o host das sugestões ao vivo pelo controlador, com o ao vivo e a LLM falsos (no padrão do
`test_caption_tray.py`). Gravar com a bandeja acende o "Sugerir" no widget e na bandeja; o atalho pede, e o cartão recebe as
opções pelo sinal; avaliar grava; "Como sugerir agora" vai no pedido seguinte; `suggest.enabled` desligada em uso esconde e
religada mostra; sem a chave, apagado com o motivo; a caixa desmarcada esconde; o atalho com a bandeja fechada a reabre;
descartar a gravação cancela o pedido em voo; parar deixa o cartão sem pedir de novo."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.llm.suggest_task import Outcome, SuggestResult
from listener.model import AppState
from tests.fake_live import FakeEngines, ThreadChild, thread_spawner
from tests.test_live_partial import _ready

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

CFG = {"source": SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), "chunk_seconds": 0.5, "stop_timeout_s": 1.0,
       "retry_base_s": 0.001}


def _controller(qapp: Any, store: Any, platform: Any, monkeypatch: Any) -> Any:
    from listener.ui import controller as ctl

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.upsert_me("Eu", role="Gerente")
    store.set_setting("live.enabled", True)
    children: list[ThreadChild] = []
    monkeypatch.setattr(platform, "spawn_child", thread_spawner(store.base_dir, FakeEngines(), children))
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    return c


def _start(qtbot: Any, c: Any, platform: Any, fields: dict[str, Any] | None = None) -> Any:
    assert c.start_recording(RecordConfig(**CFG), {"title": "sugestões", "live_names": True, **(fields or {})})
    qtbot.waitUntil(lambda: c.live_status == "live", timeout=5000)
    platform.pump(10.5)                                                    # tom contínuo: a janela fecha em 10 s
    qtbot.waitUntil(lambda: c.captions.tray.chat.list.count() >= 1, timeout=5000)
    return c.captions.suggest


def test_the_shortcut_fills_the_card_rating_records_and_the_nudge_goes_in_the_next_request(qtbot, qapp, store, platform, monkeypatch):
    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    try:
        host = _start(qtbot, c, platform)
        w, tray, card = c.widget, c.captions.tray, host.card
        assert not w.btn_suggest.isHidden() and w.btn_suggest.isEnabled() and not tray.btn_suggest.isHidden()
        assert "Ctrl+Alt+Shift+D" in w.btn_suggest.toolTip() and "por pedido" in w.btn_suggest.toolTip()
        c._on_hotkey("suggest")
        assert not w.btn_suggest.isEnabled()                                # em voo: apagado
        qtbot.waitUntil(lambda: host.task is None and len(card.rows) == 3, timeout=5000)
        assert w.btn_suggest.isEnabled() and not card.isHidden() and card.rows[0].option_id is not None
        first = store.suggestions.list_for_meeting(host.meeting_id)
        assert [s["status"] for s in first] == ["ok"]
        card.rows[0].bad.click()
        card.rows[0].reason_chips["longa demais"].click()
        row = store.suggestions.get_option(card.rows[0].option_id)
        assert (row["rating"], row["reason"]) == (0, "longa demais") and "Ruim: longa demais (1)" in card.adjusted.full_text
        card.chips["mais curta"].click()
        assert host.nudge == "mais curta"
        card.btn_new.click()
        qtbot.waitUntil(lambda: host.task is None and len(store.suggestions.list_for_meeting(host.meeting_id)) == 2, timeout=5000)
        second = store.suggestions.list_for_meeting(host.meeting_id)[-1]
        req, _resp = store.llm_calls.payloads(second["call_id"])
        assert second["nudge"] == "mais curta" and "Como sugerir agora: mais curta" in req[1]["text"]
        assert "Ruim (longa demais) · " in req[1]["text"]
        store.set_setting("suggest.enabled", False)
        c.captions.settings_changed()
        assert w.btn_suggest.isHidden() and tray.btn_suggest.isHidden()
        store.set_setting("suggest.enabled", True)
        c.captions.settings_changed()
        assert not w.btn_suggest.isHidden()
        w.captions_clicked.emit()                                           # a bandeja fechada: o atalho a reabre e pede
        assert tray.isHidden()
        c.request_suggestion()
        assert tray.isVisible() and host.task is not None
        qtbot.waitUntil(lambda: host.task is None, timeout=5000)
        c.stop_recording()
        assert card.btn_new.isHidden() and not w.btn_suggest.isEnabled()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert c.captions is None
    finally:
        c.emergency_stop()


def test_without_the_key_the_button_is_dimmed_with_the_reason_and_the_box_hides_it(qtbot, qapp, store, platform, monkeypatch):
    from listener.ui.llm_ask import NO_KEY_TIP

    c = _controller(qapp, store, platform, monkeypatch)
    try:
        host = _start(qtbot, c, platform)
        assert not c.widget.btn_suggest.isHidden() and not c.widget.btn_suggest.isEnabled()
        assert c.widget.btn_suggest.toolTip() == NO_KEY_TIP
        notices: list[str] = []
        host.on_notice = notices.append
        c.request_suggestion()
        assert notices == [NO_KEY_TIP] and host.task is None
        host.set_live_state("crashed")
        _ready(store)
        host.reload()
        assert "legenda ao vivo" in c.widget.btn_suggest.toolTip() and not c.widget.btn_suggest.isEnabled()
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert c.start_recording(RecordConfig(**CFG), {"title": "sem sugestões", "suggest": False})
        assert c.widget.btn_suggest.isHidden() and c.captions.tray.btn_suggest.isHidden()
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
    finally:
        c.emergency_stop()


def test_discarding_the_recording_cancels_the_request_in_flight(qtbot, qapp, store, platform, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui import suggest_host

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    seen = threading.Event()

    def slow(store_: Any, meeting_id: int, *, cancelled: Any, **_kw: Any) -> SuggestResult:
        while not cancelled():
            threading.Event().wait(0.02)
        seen.set()
        return SuggestResult(Outcome.CANCELLED, meeting_id)

    monkeypatch.setattr(suggest_host, "run_suggest", slow)
    monkeypatch.setattr(ctl, "confirm_discard", lambda _parent: True)
    try:
        host = _start(qtbot, c, platform)
        c.request_suggestion()
        assert host.task is not None
        c.cancel_recording()
        qtbot.waitUntil(seen.is_set, timeout=5000)
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
    finally:
        c.emergency_stop()
