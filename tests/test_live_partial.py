"""F4 L5, resumo parcial ao vivo: prompt e esquema no subconjunto estrito, `run_partial` com o fake (legendas novas por
`last_caption_id`, parcial anterior superado, `t_until_ms`, `t_ms` das legendas citadas, limite de entrada, falha redigida,
orçamento do Q6, cancelamento, reunião apagada durante a chamada), o timer do controlador (intervalo relido em uso, 0
desliga, "atualizar agora", um aviso por gravação sem chave, parada única no orçamento, Cancelar com chamada em voo) e o
alerta da ata somando o que a reunião já gastou (Q6)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from listener.llm import partial_task, prompts
from listener.llm.base import TransientError
from listener.llm.fake import FakeProvider
from listener.llm.partial_task import Outcome, run_partial, select_captions
from listener.llm.schema import check_strict
from listener.llm.types import TASK_CLASS, LlmTask, ModelClass
from listener.model import LlmCallStatus, SummaryKind
from listener.store.captions import CaptionInsert
from listener.store.llm_calls import LlmCallInsert

KEY = "AIzaSy-chave-do-parcial-ABCDEFGH12345678"


def _ready(store: Any) -> None:
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast", strong_model="fake-strong")
    store.providers.set_price("gemini", "fake-fast", 0.30, 2.50, 0.075)
    store.providers.set_price("gemini", "fake-strong", 1.25, 10.0, 0.31)


def _caption(store: Any, mid: int, t0: int, text: str, label: str | None = "Ana — CFO", channel: str = "R") -> int:
    return store.captions.insert(CaptionInsert(mid, channel, t0, t0 + 4000, text, None, label, 0.8, "fake:small", 6000))


def test_partial_prompt_is_strict_fast_and_versioned():
    assert check_strict(prompts.partial.SCHEMA) == [] and TASK_CLASS[LlmTask.PARTIAL] == ModelClass.FAST
    assert prompts.prompt_version(LlmTask.PARTIAL) == "partial/1.0"
    rows: list[Any] = [{"text": "x" * 500}] * 10
    used, left = select_captions(rows, max_chars=1200)
    assert len(used) == 2 and left == 8 and select_captions(rows[:1], max_chars=10) == (rows[:1], 0)   # ao menos uma


def test_run_partial_accumulates_supersedes_and_records_the_call(store, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.upsert_me("Eu", role="Gerente")
    mid = store.create_meeting("Orçamento", None, None, "Ana")
    assert run_partial(store, mid).outcome == Outcome.NOTHING                                       # sem legenda: nada
    c1 = _caption(store, mid, 1000, "vamos cortar oito por cento")
    _caption(store, mid, 6000, "eu mando a planilha amanhã", None, "L")
    assert run_partial(store, mid).outcome == Outcome.NO_PROVIDER                                   # sem chave: sem chamada
    assert store.llm_calls.list_for_meeting(mid) == []
    _ready(store)
    r = run_partial(store, mid)
    assert r.outcome == Outcome.DONE and r.captions == 2 and r.t_until_ms == 10000 and r.cost_usd is not None
    row = store.summaries.get(r.summary_id)
    data = json.loads(row["data_json"])
    assert row["kind"] == "partial" and row["t_until_ms"] == 10000 and row["call_id"] == r.call_id and row["status"] == "atual"
    assert data["decisions"] == [{"text": "Decidido: vamos cortar oito por cento", "caption_id": c1, "t_ms": 1000}]
    assert data["actions"][0]["owner"] == "Eu" and data["actions"][0]["t_ms"] == 6000 and data["last_caption_id"] == r.last_caption_id
    assert "## Possíveis decisões" in row["text_llm"] and "[00:01]" in row["text_llm"]
    call = store.llm_calls.get(r.call_id)
    assert call["task"] == "partial" and call["meeting_id"] == mid and call["status"] == "ok" and call["prompt_version"] == "partial/1.0"
    req, _resp = store.llm_calls.payloads(r.call_id)
    assert f"#{c1} [00:01] Ana — CFO: vamos cortar" in req[-1]["text"] and "Eu: eu mando" in req[-1]["text"]
    assert run_partial(store, mid).outcome == Outcome.NOTHING                                       # nada novo: sem chamada
    c3 = _caption(store, mid, 12000, "fechado então")
    r2 = run_partial(store, mid)
    data2 = json.loads(store.summaries.get(r2.summary_id)["data_json"])
    assert r2.outcome == Outcome.DONE and r2.captions == 1 and store.summaries.get(r.summary_id)["status"] == "superada"
    assert [d["caption_id"] for d in data2["decisions"]] == [c1, c3] and data2["decisions"][0]["t_ms"] == 1000   # o anterior fica
    req2, _ = store.llm_calls.payloads(r2.call_id)
    assert "Decidido: vamos cortar" in req2[-1]["text"] and "vamos cortar oito por cento\n" not in req2[-1]["text"]
    # limite de entrada: o que sobra fica para a próxima chamada, sem buraco
    for i in range(3):
        _caption(store, mid, 20000 + i * 5000, f"fala {i} " + "palavra " * 40)
    monkeypatch.setattr(partial_task, "MAX_INPUT_CHARS", 800)
    r3 = run_partial(store, mid)
    assert r3.captions == 2 and r3.left == 1 and run_partial(store, mid).captions == 1


def test_run_partial_failure_budget_cancel_and_deleted_meeting(store, monkeypatch):
    _ready(store)
    mid = store.create_meeting("Q6", None, None, None)
    _caption(store, mid, 0, "primeira")
    fake = FakeProvider()
    fake.fail_next(TransientError(f"rede caiu com a chave {KEY}"), LlmTask.PARTIAL)
    r = run_partial(store, mid, provider=fake)
    assert r.outcome == Outcome.ERROR and KEY not in (r.error or "") and store.summaries.current(mid, SummaryKind.PARTIAL) is None
    assert store.llm_calls.get(r.call_id)["status"] == LlmCallStatus.ERROR
    # Q6: com o gasto da reunião na metade do alerta, nem chama
    store.set_setting("llm.cost_alert_usd", 1.0)
    store.llm_calls.insert(LlmCallInsert(mid, None, "glossary", "gemini", "fake-fast", None, None, 1, 1, 0, 0, 0.5, 1, LlmCallStatus.OK))
    n = len(fake.requests)
    b = run_partial(store, mid, provider=fake)
    assert b.outcome == Outcome.BUDGET and len(fake.requests) == n and b.spent_usd == 0.5 and b.limit_usd == 0.5
    assert "metade do alerta de US$ 1,00" in (b.error or "")
    store.set_setting("llm.cost_alert_usd", 100.0)
    slow = FakeProvider(delay_s=5.0)
    c = run_partial(store, mid, provider=slow, cancelled=lambda: True)
    assert c.outcome == Outcome.CANCELLED and store.llm_calls.get(c.call_id)["error"] == "cancelado"
    # Cancelar durante a chamada: a reunião some; a chamada fica sem reunião e nada mais é gravado
    gone = FakeProvider()

    def answer_after_delete(req: Any) -> dict[str, Any]:
        store.delete_meeting(mid)
        return {"summary": "x", "decisions": [], "actions": []}

    gone.script(LlmTask.PARTIAL, answer_after_delete)
    g = run_partial(store, mid, provider=gone)
    assert g.outcome == Outcome.GONE and store.llm_calls.get(g.call_id)["meeting_id"] is None
    assert store._q("SELECT COUNT(*) FROM summaries WHERE kind = 'partial'")[0][0] == 0


def test_minutes_alert_counts_what_the_meeting_already_spent(platform, store):
    """Q6: a mesma conta da ata soma o parcial ao vivo; as opcionais do job automático saem com "já gastos"."""
    from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm
    from listener.pipeline import llm_steps

    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    store.set_setting("llm.cost_alert_usd", 1.0)
    _pipeline(store, mid)
    runner, _status, _ = _run_llm(store)
    assert json.loads(store.jobs.get(runner.job_id)["timings_json"])["llm"]["skipped"] == {}       # estimativa barata: tudo roda
    store.llm_calls.insert(LlmCallInsert(mid, None, "partial", "gemini", "fake-fast", None, None, 1, 1, 0, 0, 0.999, 1, LlmCallStatus.OK))
    assert llm_steps.enqueue_llm_job(store, mid, ["executive"], auto=True)
    runner2, _status, _ = _run_llm(store)
    skipped = json.loads(store.jobs.get(runner2.job_id)["timings_json"])["llm"]["skipped"]
    assert set(skipped) == {"executive"} and "já gastos US$ 1,00" in skipped["executive"]


pytest.importorskip("PySide6")


def test_controller_partial_timer_interval_manual_budget_and_cancel(qtbot, qapp, store, platform, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.model import AppState
    from listener.ui import controller as ctl

    pytest.importorskip("pytestqt")
    monkeypatch.setenv("LISTENER_LIVE", "fake")                      # o filho silencioso da plataforma simulada basta aqui
    monkeypatch.setenv("LISTENER_LLM", "fake")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    store.set_setting("live.enabled", True)
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    notes: list[str] = []
    monkeypatch.setattr(c, "notify", notes.append)
    now = [1000.0]
    c.monotonic = lambda: now[0]
    states: list[dict] = []
    c.partial_state.connect(states.append)
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)

    def tick(minutes: float) -> None:
        now[0] += minutes * 60
        c._partial_tick()

    try:
        assert c.start_recording(cfg, {"title": "parcial"})
        mid = c.recorder.meeting_id
        assert c.live is not None and c._partial_timer.isActive()
        tick(4.9)
        assert not c._partial_tasks
        tick(0.2)                                                     # venceu, mas sem legenda nova: nada, nem aviso
        assert not c._partial_tasks and notes == []
        _caption(store, mid, 1000, "primeira")
        tick(5)                                                       # sem chave: um aviso por gravação
        tick(5)
        assert [n for n in notes if "sem provedor" in n.lower()] == [notes[0]] and len(notes) == 1 and not c._partial_tasks
        _ready(store)
        tick(5)
        qtbot.waitUntil(lambda: c.partial_status["state"] == "done", timeout=5000)
        assert store.summaries.current(mid, SummaryKind.PARTIAL)["t_until_ms"] == 5000
        store.set_setting("live.summary_interval_min", 1)            # CP8: vale em uso
        _caption(store, mid, 7000, "segunda")
        tick(1.01)
        qtbot.waitUntil(lambda: c.partial_status["state"] == "done" and not c._partial_tasks, timeout=5000)
        assert store._q("SELECT COUNT(*) FROM summaries WHERE meeting_id = ? AND kind = 'partial'", (mid,))[0][0] == 2
        store.set_setting("live.summary_interval_min", 0)            # desligado: o timer não chama
        _caption(store, mid, 9000, "terceira")
        tick(120)
        assert not c._partial_tasks
        assert c.request_partial_now() == "atualizando o resumo parcial…"   # "atualizar agora" vale com o automático desligado
        assert c.request_partial_now() == "o resumo parcial já está sendo atualizado"
        qtbot.waitUntil(lambda: not c._partial_tasks, timeout=5000)
        assert c.request_partial_now() == "nada novo desde o último resumo parcial"
        win = c.open_live_window()
        assert win is not None and win.refresh_btn.isVisibleTo(win) and "Parcial simulado" in win.partial_view.toPlainText()
        win._refresh()
        assert win.status.text() == "nada novo desde o último resumo parcial"
        # Q6: no teto, um aviso único e o parcial para nesta reunião
        store.set_setting("live.summary_interval_min", 5)
        store.set_setting("llm.cost_alert_usd", 0.0000001)
        _caption(store, mid, 11000, "quarta")
        tick(5)
        qtbot.waitUntil(lambda: c.partial_status["state"] == "stopped", timeout=5000)
        _caption(store, mid, 13000, "quinta")
        tick(5)
        assert not c._partial_tasks and len([n for n in notes if "metade do alerta" in n]) == 1 and not c._partial_timer.isActive()
        assert "metade do alerta" in c.request_partial_now()
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        # Cancelar com a chamada em voo: cancela, nada é gravado e nada cai
        store.set_setting("llm.cost_alert_usd", 100.0)
        slow = FakeProvider(delay_s=5.0)
        real = partial_task.run_partial
        monkeypatch.setattr(partial_task, "run_partial", lambda s, m, **kw: real(s, m, provider=slow, **kw))
        assert c.start_recording(cfg, {"title": "cancelar"})
        mid2 = c.recorder.meeting_id
        _caption(store, mid2, 500, "vai sumir")
        assert c.request_partial_now() == "atualizando o resumo parcial…"
        qtbot.waitUntil(lambda: len(slow.requests) == 1, timeout=5000)
        c.cancel_recording()
        qtbot.waitUntil(lambda: not c._partial_tasks, timeout=5000)
        assert store.get_meeting(mid2) is None and not c._partial_timer.isActive() and c.state == AppState.IDLE
        assert store._q("SELECT COUNT(*) FROM summaries WHERE meeting_id = ?", (mid2,))[0][0] == 0
        assert any(s.get("state") == "running" and s.get("meeting_id") == mid2 for s in states)
    finally:
        c.emergency_stop()
