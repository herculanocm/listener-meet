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
from listener.llm.suggest_task import Outcome, SuggestOption, SuggestResult
from listener.model import AppState, Evidence, SuggestMode
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


# ---------------------------------------------------------------- L3: o dossiê, o teto, o nome e o memorando
def test_the_name_lights_the_button_for_a_while_only_on_r_and_only_with_the_hint_on(qtbot, qapp, store, platform, monkeypatch):
    c = _controller(qapp, store, platform, monkeypatch)
    store.upsert_me("Carla Dias", role="CTO")
    _ready(store)
    try:
        host = _start(qtbot, c, platform)
        host.hint_timer.setInterval(80)
        c._on_caption_ui({"type": "caption", "id": 900, "channel": "L", "text": "Carla aqui.", "t_end_ms": 30_000})
        assert not host.hint
        c._on_caption_ui({"type": "caption", "id": 901, "channel": "R", "text": "Valeu, Carla! E o prazo?", "t_end_ms": 31_000})
        assert host.hint and c.widget.btn_suggest.property("theme_icon") == "lightbulb:accent:20"
        qtbot.waitUntil(lambda: not host.hint, timeout=3000)
        assert c.widget.btn_suggest.property("theme_icon") == "lightbulb:text:20"
        store.set_setting("suggest.name_hint", False)
        c.captions.settings_changed()
        c._on_caption_ui({"type": "caption", "id": 902, "channel": "R", "text": "Carla, e você?", "t_end_ms": 32_000})
        assert not host.hint
    finally:
        c.emergency_stop()


def test_the_budget_is_shared_with_the_partial_both_ways(qtbot, qapp, store, platform, monkeypatch):
    from listener.llm import partial_task
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    store.set_setting("llm.cost_alert_usd", 1.0)
    try:
        host = _start(qtbot, c, platform, {"live_partial": True})
        mid = host.meeting_id
        notices: list[str] = []
        host.on_notice = notices.append
        store.llm_calls.insert(LlmCallInsert(mid, None, "partial", "gemini", "fake-fast", "partial/2.0", None, 1, 1, 0, 0, 0.6, 5,
                                             LlmCallStatus.OK))
        assert c._partial_timer.isActive()
        c.request_suggestion()
        qtbot.waitUntil(lambda: host.task is None, timeout=5000)
        assert host.budget_stopped and "metade do alerta" in host.budget_stopped and not c.widget.btn_suggest.isEnabled()
        assert c._partial_stopped == host.budget_stopped and c.partial_status["state"] == "stopped" and not c._partial_timer.isActive()
        assert host.card.state.text().startswith("sugestões paradas") and host.card.btn_retry.isHidden()     # R-04 do L3
        assert notices == [f"{host.budget_stopped[:1].upper()}{host.budget_stopped[1:]}."]               # um aviso só
        c._on_partial_done(mid, [], partial_task.PartialResult(partial_task.Outcome.DONE, mid, t_until_ms=60_000))
        assert c.partial_status["state"] == "stopped"                     # R-05 do L3: o parcial em voo não desfaz o teto
        host.budget_stopped = None                                        # o outro sentido: o parcial bate no teto
        c._partial_stopped = None
        c._on_partial_done(mid, [], partial_task.PartialResult(partial_task.Outcome.BUDGET, mid, spent_usd=0.6, limit_usd=0.5,
                                                               error="resumo parcial parado"))
        assert host.budget_stopped == "resumo parcial parado" and not c.widget.btn_suggest.isEnabled()
    finally:
        c.emergency_stop()


def test_the_dossier_goes_in_the_request_and_the_memo_runs_after_stop(qtbot, qapp, store, platform, monkeypatch):
    import json

    from listener.model import SummaryKind
    from listener.store.actions import ActionInsert
    from listener.store.summaries import SummaryInsert

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    ana = store.people.create_person("Ana Souza", role="CFO")
    old = store.create_meeting("Orçamento antigo", None, None, None, expected_people_json=json.dumps([ana]))
    sid = store.summaries.insert(SummaryInsert(old, SummaryKind.MINUTES, "{}", "## Ata", "fake", "m", "minutes/2.0", None, None, "pt-BR"))
    store.actions.insert_many([ActionInsert(old, sid, "mandar a planilha", None, ana, None, 1000, None, None)])
    try:
        host = _start(qtbot, c, platform, {"expected_people_json": json.dumps([ana])})
        qtbot.waitUntil(lambda: host.dossier_task is None, timeout=3000)
        assert host.dossier and "mandar a planilha" in host.dossier
        c.request_suggestion()
        qtbot.waitUntil(lambda: host.task is None and len(host.card.rows) == 3, timeout=5000)
        call = store.suggestions.list_for_meeting(host.meeting_id)[-1]["call_id"]
        assert "Dossiê dos participantes:\n- Ação aberta: mandar a planilha" in store.llm_calls.payloads(call)[0][1]["text"]
        for _ in range(3):                                                # 10 avaliações: o memorando roda ao parar
            for row in host.card.rows:
                row.good.click()
            host.card.btn_new.click()
            qtbot.waitUntil(lambda: host.task is None, timeout=5000)
        for row in host.card.rows:
            row.used.click()
        assert store.suggestions.count_rated() >= 10 and store.get_setting("suggest.memo") == ""
        c.stop_recording()
        qtbot.waitUntil(lambda: store.get_setting("suggest.memo") != "", timeout=8000)
        assert store.get_setting("suggest.memo") == "Prefere opções curtas e em pergunta."
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
    finally:
        c.emergency_stop()


def test_no_memo_without_learning_and_none_on_discard(qtbot, qapp, store, platform, monkeypatch):
    from listener.llm import suggest_task
    from listener.ui import controller as ctl
    from listener.ui import suggest_host

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    asked: list[int] = []
    monkeypatch.setattr(suggest_host, "run_memo", lambda *a, **k: asked.append(1))
    monkeypatch.setattr(suggest_host, "memo_due", lambda _store: True)
    monkeypatch.setattr(ctl, "confirm_discard", lambda _parent: True)
    try:
        _start(qtbot, c, platform)
        store.set_setting("suggest.learn", False)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        store.set_setting("suggest.learn", True)
        _start(qtbot, c, platform)
        c.cancel_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert asked == [] and suggest_task.MEMO_MIN_RATINGS == 10
    finally:
        c.emergency_stop()


# ---------------------------------------------------------------- a revisão do diff do L2 e do L3: os fios sem teste
def _bare(qtbot: Any, store: Any, platform: Any, monkeypatch: Any) -> tuple[Any, list[str]]:
    """O host sem o controlador nem o ao vivo: o widget e a bandeja de verdade, a legenda "de pé" e a LLM falsa."""
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.suggest_host import SuggestHost
    from listener.ui.widget import RecordingWidget

    monkeypatch.setenv("LISTENER_LLM", "fake")
    _ready(store)
    mid = store.create_meeting("sugestões", "meeting", None, None)
    widget = RecordingWidget(captions_button=True)
    qtbot.addWidget(widget)
    tray = CaptionTray(store, mid, platform=platform)
    qtbot.addWidget(tray)
    tray.show()
    notices: list[str] = []
    host = SuggestHost(store, platform, widget, tray, mid, active=True, live_state="live", elapsed=lambda: 60.0,
                       on_notice=notices.append)
    return host, notices


def test_switched_off_the_shortcut_is_mute_and_the_cost_tip_has_four_places(qtbot, store, platform, monkeypatch):
    """R-05 e R-06 da revisão do L2: desligadas nas Configurações, o atalho não avisa nada (Escopo 12); abaixo de um centavo,
    a dica leva 4 casas, como o resto do app (com o preço do flash-lite, saía "US$ 0,000")."""
    import re

    host, notices = _bare(qtbot, store, platform, monkeypatch)
    store.providers.set_price("gemini", "fake-fast", 0.10, 0.40, 0.025)
    host.reload()
    assert re.search(r"· ~US\$ 0,\d{4} por pedido", host.cost_tip), host.cost_tip
    store.set_setting("suggest.enabled", False)
    host.reload()
    host.request_from_outside()
    assert notices == [] and host.task is None and host.widget.btn_suggest.isHidden()


def test_a_failed_or_cancelled_request_keeps_the_options_that_came_ratable(qtbot, store, platform, monkeypatch):
    """R-07 da revisão do L2: o L1 grava as opções que chegaram antes do erro ou do cancelamento; o cartão liga os ids."""
    host, _notices = _bare(qtbot, store, platform, monkeypatch)
    for outcome, oid in ((Outcome.ERROR, 41), (Outcome.CANCELLED, 42)):
        host.card.begin(SuggestMode.SPEAK, "14:05", 60_000)
        host._option(SuggestOption(0, "Perguntar", "E o prazo?", (), Evidence.NONE))
        host._done(SuggestResult(outcome, host.meeting_id, option_ids=(oid,), error="prazo de 30 s esgotado", cost_usd=0.0002,
                                 cost_estimated=True))
        assert host.card.rows[0].option_id == oid and host.card.rows[0].good.isEnabled(), outcome
        assert host.card.cost.text() == "custo estimado US$ 0,0002"


def test_the_goal_the_nudge_and_the_card_wires(qtbot, store, platform, monkeypatch):
    """R-11 da revisão do L2: o "Objetivo…" grava e apaga; "Limpar" leva os chips e o texto livre; o pedido mostra o cartão pela
    bandeja; o atalho com a bandeja fechada a traz e acende o botão "Legendas"; o popup apagado no meio não derruba nada (R-10)."""
    import shiboken6

    from listener.ui.suggest_popup import SuggestTextPopup

    host, _notices = _bare(qtbot, store, platform, monkeypatch)
    mid = host.meeting_id
    monkeypatch.setattr(host, "_ask", lambda *_a: "fechar o corte")
    host.edit_goal()
    assert store.get_meeting(mid)["goal"] == "fechar o corte"
    monkeypatch.setattr(host, "_ask", lambda *_a: "")
    host.edit_goal()
    assert store.get_meeting(mid)["goal"] is None
    host.chips, host.free = ["mais curta"], "sem jargão"
    host.clear_nudge()
    assert host.nudge == "" and host.free == "" and host.card.nudge.full_text.endswith("como o assistente achar melhor")
    host.tray.close_by_user()
    host.widget.set_captions_shown(False)
    host.request_from_outside()
    assert host.tray.isVisible() and host.tray.card_wanted and host.widget.btn_captions.accessibleDescription() == "à vista"
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    monkeypatch.undo()
    monkeypatch.setenv("LISTENER_LLM", "fake")

    def gone(popup: Any) -> int:                                          # a bandeja (a mãe) saiu no meio do `exec()`
        shiboken6.delete(popup)
        return 0

    monkeypatch.setattr(SuggestTextPopup, "exec", gone)
    host.edit_goal()
    assert store.get_meeting(mid)["goal"] is None


def test_a_result_after_the_teardown_touches_nothing(qtbot, store, platform, monkeypatch):
    """R-11 da revisão do L2: a resposta que chega com o host fechado e o cartão já apagado com a bandeja (o widget, a mãe
    dela, sai antes do host) não mexe em nada; sem a guarda, `RuntimeError` no cartão apagado."""
    import shiboken6

    host, _notices = _bare(qtbot, store, platform, monkeypatch)
    card = host.card
    host.teardown()
    shiboken6.delete(card)
    host._done(SuggestResult(Outcome.ERROR, host.meeting_id, error="falha"))
    host._done(SuggestResult(Outcome.DONE, host.meeting_id))
    host._option(SuggestOption(0, "Perguntar", "E o prazo?", (), Evidence.NONE))


def test_the_goal_of_the_warmup_reaches_the_meeting_and_settings_bring_the_button_back(qtbot, qapp, store, platform, monkeypatch):
    """R-11 da revisão do L2: o objetivo dos `fields` chega a `meetings.goal`; o Salvar das Configurações relê o host (o botão
    volta) pelo `open_settings`, não só pelo `settings_changed` chamado à mão."""
    from listener.ui import controller as ctl

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)

    class Accepting:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def exec(self) -> Any:
            return ctl.QDialog.DialogCode.Accepted

        def deleteLater(self) -> None:  # noqa: N802 (o nome do QObject)
            pass

    try:
        host = _start(qtbot, c, platform, {"goal": "fechar o corte"})
        assert store.get_meeting(host.meeting_id)["goal"] == "fechar o corte"
        store.set_setting("suggest.enabled", False)
        monkeypatch.setattr(ctl, "SettingsDialog", Accepting)
        c.open_settings()
        assert c.widget.btn_suggest.isHidden()
        store.set_setting("suggest.enabled", True)
        c.open_settings()
        assert not c.widget.btn_suggest.isHidden()
    finally:
        c.emergency_stop()


def test_stop_with_a_request_in_flight_and_a_busy_database_on_stop(qtbot, qapp, store, platform, monkeypatch):
    """R-11 da revisão do L2 (a resposta que chega depois do Parar não mexe no cartão apagado) e R-02 da do L3 (o banco ocupado
    na decisão do memorando não trava o Parar)."""
    import sqlite3

    from listener.ui import suggest_host

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    gate, done = threading.Event(), threading.Event()

    def slow(store_: Any, meeting_id: int, **_kw: Any) -> SuggestResult:
        gate.wait(5)
        done.set()
        return SuggestResult(Outcome.ERROR, meeting_id, error="falha")

    def busy(_store: Any) -> bool:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(suggest_host, "run_suggest", slow)
    monkeypatch.setattr(suggest_host, "memo_due", busy)
    try:
        host = _start(qtbot, c, platform)
        c.request_suggestion()
        assert host.task is not None
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert c.captions is None and host.closed                         # o host já saiu com a bandeja
        gate.set()
        qtbot.waitUntil(done.is_set, timeout=5000)
        qtbot.wait(200)                                                   # o `done` chega ao host desmontado: nada levanta
    finally:
        gate.set()
        c.emergency_stop()


def test_cancel_takes_the_suggestions_and_quitting_never_asks_the_memo(qtbot, qapp, store, platform, monkeypatch):
    """R-03 da revisão do L3: o Cancelar apaga as sugestões com a reunião; sair do app com a gravação e uma chamada em voo
    cancela a chamada e espera, e o memorando nunca roda na saída (só no Parar)."""
    from listener.ui import controller as ctl
    from listener.ui import suggest_host

    c = _controller(qapp, store, platform, monkeypatch)
    _ready(store)
    asked: list[int] = []
    monkeypatch.setattr(suggest_host, "run_memo", lambda *a, **k: asked.append(1))
    monkeypatch.setattr(suggest_host, "memo_due", lambda _store: True)
    monkeypatch.setattr(ctl, "confirm_discard", lambda _parent: True)
    monkeypatch.setattr(ctl.QMessageBox, "question", lambda *a, **k: ctl.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(qapp, "quit", lambda: None)
    seen = threading.Event()
    try:
        host = _start(qtbot, c, platform)
        mid = host.meeting_id
        c.request_suggestion()
        qtbot.waitUntil(lambda: host.task is None and len(host.card.rows) == 3, timeout=5000)
        assert store.suggestions.list_for_meeting(mid)
        c.cancel_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert store.suggestions.list_for_meeting(mid) == [] and store.suggestions.options_for_meeting(mid) == {}

        def slow(store_: Any, meeting_id: int, *, cancelled: Any, **_kw: Any) -> SuggestResult:
            while not cancelled():
                threading.Event().wait(0.02)
            seen.set()
            return SuggestResult(Outcome.CANCELLED, meeting_id)

        monkeypatch.setattr(suggest_host, "run_suggest", slow)
        host = _start(qtbot, c, platform)
        c.request_suggestion()
        assert host.task is not None
        c.quit()
        c.emergency_stop()                                                # o `aboutToQuit` do app: espera as threads
        assert seen.is_set() and asked == []
    finally:
        c.emergency_stop()


def test_the_dossier_follows_its_setting(qtbot, store, platform, monkeypatch):
    """R-03 da revisão do L3: sem `suggest.dossier`, nenhuma leitura; com ele, uma `Task` ao começar."""
    store.set_setting("suggest.dossier", False)
    host, _notices = _bare(qtbot, store, platform, monkeypatch)
    assert host.dossier_task is None and host.dossier is None
    store.set_setting("suggest.dossier", True)
    other, _n = _bare(qtbot, store, platform, monkeypatch)
    assert other.dossier_task is not None
    qtbot.waitUntil(lambda: other.dossier_task is None, timeout=3000)


def test_the_f520_modules_never_log_the_text():
    """R-03 da revisão do L3 (D-053, D-059): nenhum módulo da F5.20 passa a sugestão, o fio, o objetivo, o memorando, o dossiê
    ou o primeiro nome do usuário ao log."""
    from tests.log_privacy import F510_ALLOWED_CALLS, F520_FORBIDDEN, F520_MODULES, leaks

    assert {m.name: leaks(m, F520_FORBIDDEN, F510_ALLOWED_CALLS) for m in F520_MODULES} == {m.name: [] for m in F520_MODULES}


# ---------------------------------------------------------------- L3: a aba da reunião e o --check
def test_the_meeting_tab_lists_requests_options_and_ratings_and_plays_a_fact(qtbot, store):
    from listener.ui.meeting_suggestions import EMPTY, SuggestionsTab, verdict
    from tests.test_suggest_store import suggestion

    mid = store.create_meeting("Orçamento", None, None, None)
    played: list[int] = []
    tab = SuggestionsTab(store, mid, play=played.append)
    qtbot.addWidget(tab)
    assert tab.stack.currentWidget() is tab.empty and EMPTY.startswith("Nenhuma sugestão")
    sid, (a, b) = suggestion(store, mid, 65_000)
    store._x("UPDATE suggestion_options SET facts_json = ? WHERE id = ?", ('[{"caption_id": 4, "t_ms": 61000}]', a))
    store.suggestions.set_rating(a, 0, "genérica")
    store.suggestions.set_used(b, True)
    tab.reload()
    top = tab.tree.topLevelItem(0)
    assert tab.stack.currentWidget() is tab.tree and top.text(0).startswith("[01:05] · Falar · 2 opções · US$ 0,0010")
    assert [top.child(i).text(1) for i in range(top.childCount())] == ["Ruim (genérica)", "Usei"]
    fact = top.child(0).child(0)
    assert fact.text(0) == "[01:01]"
    tab.tree.itemActivated.emit(fact, 0)
    assert played == [61_000] and sid
    assert verdict(1, None, True) == "Boa e usei" and verdict(None, None, False) == ""


def test_the_meeting_tab_wraps_the_whole_option_and_keeps_what_was_open_on_reload(qtbot, store):
    """R-01 e R-06 da revisão do L3: a primeira coluna estica e a fala longa quebra em linhas, com a fala inteira na dica; o
    pedido que falhou sem custo não diz "sem preço cadastrado"; o `reload` repõe o que estava aberto e o item atual."""
    from listener.model import SuggestStatus
    from listener.ui.meeting_suggestions import SuggestionsTab
    from tests.test_suggest_store import suggestion

    mid = store.create_meeting("Orçamento", None, None, None)
    _sid, (a, _b) = suggestion(store, mid, 65_000)
    long = "Antes de fecharmos, " + "o impacto do corte de oito por cento no projeto de dados precisa entrar na planilha, " * 3
    store._x("UPDATE suggestion_options SET text = ?, facts_json = ? WHERE id = ?", (long, '[{"caption_id": 4, "t_ms": 61000}]', a))
    failed, _ = suggestion(store, mid, 90_000, status=SuggestStatus.ERROR)
    store._x("UPDATE suggestions SET cost_usd = NULL WHERE id = ?", (failed,))
    tab = SuggestionsTab(store, mid)
    qtbot.addWidget(tab)
    tab.resize(700, 600)
    tab.show()
    qtbot.waitUntil(lambda: tab.tree.header().sectionSize(0) > tab.tree.header().sectionSize(1) * 3, timeout=2000)
    top = tab.tree.topLevelItem(0)
    option = top.child(0)
    assert option.toolTip(0) == long and tab.tree.visualItemRect(option).height() > 2 * tab.tree.visualItemRect(top.child(1)).height()
    assert tab.tree.topLevelItem(1).text(0) == "[01:30] · Falar · 2 opções · falhou"
    option.setExpanded(True)
    tab.tree.setCurrentItem(option.child(0))
    top.setExpanded(False)
    tab.reload()
    top = tab.tree.topLevelItem(0)
    assert not top.isExpanded() and top.child(0).isExpanded() and tab.tree.currentItem() is top.child(0).child(0)


def test_the_arrows_in_the_suggestions_tree_stay_with_the_tree(qtbot):
    """R-07 da revisão do L3: ← e → abrem e fecham a árvore da aba Sugestões; não avançam o player."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QTreeWidget, QWidget

    from listener.ui.meeting_keys import player_key

    seen: list[str] = []
    bar = type("Bar", (), {"toggle": lambda self: seen.append("toggle"), "back": lambda self: seen.append("back"),
                           "forward": lambda self: seen.append("forward")})()
    win = QWidget()
    qtbot.addWidget(win)
    tree, other = QTreeWidget(win), QWidget(win)
    other.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    win.show()
    right = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    tree.setFocus()
    assert win.focusWidget() is tree and player_key(win, bar, right) is False and seen == []
    other.setFocus()
    assert player_key(win, bar, right) is True and seen == ["forward"]


def test_the_check_reports_the_suggestions_and_the_gates(store):
    from listener.__main__ import meeting_report
    from listener.live.report import suggestions_report
    from listener.model import SuggestStatus
    from tests.test_suggest_store import suggestion

    mid = store.create_meeting("Orçamento", None, None, None)
    assert suggestions_report(store, mid) == {"requests": 0}
    _s1, (a, _b) = suggestion(store, mid, 60_000)
    suggestion(store, mid, 120_000, status=SuggestStatus.CANCELLED)
    store._x("UPDATE suggestions SET cost_estimated = 1, cost_usd = 0.002 WHERE status = 'cancelada'")
    store.suggestions.set_rating(a, 1)
    out = suggestions_report(store, mid)
    assert (out["requests"], out["by_status"], out["options"], out["good"]) == (2, {"ok": 1, "cancelada": 1}, 4, 1)
    assert out["first_ms"]["p50"] == 900 and out["useful_share"] == 1.0 and out["evidence_share"] == 0.0 and out["compaction"] == 0.2
    assert out["cost_usd"] == 0.003 and out["cost_estimated_usd"] == 0.002
    assert out["gates"]["first_p50"]["ok"] and out["gates"]["useful"]["ok"] and out["gates"]["evidence"]["ok"] is False
    assert out["gates"]["compaction"]["ok"] is False                     # 20 % abaixo dos 25 %
    assert meeting_report(store, mid)["suggestions"]["requests"] == 2


def test_the_check_takes_the_times_from_the_good_ones_counts_used_and_gates_the_cost(store):
    """R-03 e R-08 da revisão do L3: pedidos variados. Os tempos só dos que terminaram bem, a mediana com contagem par, o
    "Usei" sozinho como útil, a compactação de todos os pedidos, os p95 contra os alvos e o custo por pedido (US$ 0,01)."""
    from listener.live.report import suggestions_report
    from listener.model import SuggestStatus
    from tests.test_suggest_store import suggestion

    mid = store.create_meeting("Orçamento", None, None, None)
    a, _ = suggestion(store, mid, 60_000)
    b, (b1, _b2) = suggestion(store, mid, 120_000)
    cx, _ = suggestion(store, mid, 180_000, status=SuggestStatus.CANCELLED)
    for sid, first, total, raw, sent, cost in ((a, 1000, 4000, 1000, 600, 0.004), (b, 7000, 11_000, 3000, 2400, 0.004),
                                               (cx, 50_000, 60_000, 2000, 2000, 0.034)):
        store._x("UPDATE suggestions SET first_ms = ?, total_ms = ?, raw_chars = ?, input_chars = ?, cost_usd = ? WHERE id = ?",
                 (first, total, raw, sent, cost, sid))
    store.suggestions.set_used(b1, True)
    out = suggestions_report(store, mid)
    assert out["first_ms"]["n"] == 2 and out["first_ms"]["p50"] == 4000 and out["first_ms"]["p95"] == 7000
    assert out["useful_share"] == 0.5 and out["compaction"] == round(1 - 5000 / 6000, 3)
    assert out["gates"]["first_p95"]["ok"] is False and out["gates"]["total_p95"]["ok"] is False      # 7 s e 11 s
    assert out["cost_per_request_usd"] == 0.014 and out["gates"]["cost"] == {"target": "<= 0.01", "value": 0.014, "ok": False}
