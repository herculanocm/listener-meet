"""F4 L2, ciclo de vida no controlador (pytest-qt, offscreen): gravar com o ao vivo lança o filho e liga o desvio (o filho
real roda numa thread com motores fake), a lease pede pausa ao pesado, Parar fecha sem bloquear e as legendas chegam por
sinal; sem modelos, um aviso e segue sem ao vivo; Cancelar mata o filho antes de apagar a reunião; uma queda tem uma nova
tentativa por gravação."""
from __future__ import annotations

from typing import Any

import pytest

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import AppState
from tests.fake_live import FakeEngines, ThreadChild, thread_spawner

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")


def _cfg() -> RecordConfig:
    return RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                        retry_base_s=0.001)


def _controller(qapp: Any, platform: Any, store: Any, monkeypatch: Any) -> Any:
    from listener.ui import controller as ctl

    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    return c


def test_recording_with_live_spawns_taps_and_closes_with_captions(qtbot, qapp, store, platform, monkeypatch):
    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.set_setting("live.enabled", True)
    store.set_setting("live.threads", 3)
    store.set_setting("pipeline.pause_on_record", False)                     # mesmo assim a lease vale com o ao vivo
    children: list[ThreadChild] = []
    engines = FakeEngines()
    monkeypatch.setattr(platform, "spawn_child", thread_spawner(store.base_dir, engines, children))
    c = _controller(qapp, platform, store, monkeypatch)
    captions: list[dict] = []
    states: list[str] = []
    c.live_caption.connect(captions.append)
    c.live_state.connect(lambda s: states.append(s["state"]))
    try:
        assert c.start_recording(_cfg(), {"title": "ao vivo"})
        rec = c.recorder
        assert c.live is not None and len(children) == 1 and store.get_setting("pipeline.paused_until")
        assert children[0].argv[-4:] == ["--db", str(store.base_dir), "--meeting", str(rec.meeting_id)]
        qtbot.waitUntil(lambda: c.live_status == "live" and c.live_names_status == "ok", timeout=5000)   # nomes: fake carrega já
        assert engines.loads == [("small", 3)] and children[0].returncode is None
        platform.pump(1.5)                                                     # tom contínuo nos dois canais
        qtbot.waitUntil(lambda: rec.snapshot.blocks[0] >= 15, timeout=5000)    # a thread de trabalho já passou pelo desvio
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE and c.live_status == "stopped", timeout=10000)
        assert sorted(m["channel"] for m in captions) == ["L", "R"] and c.live_last_caption in captions
        assert states[:2] == ["starting", "live"] and states[-2:] == ["stopping", "stopped"] and c.live is None
        rows = store.captions.list_for_meeting(rec.meeting_id)
        assert len(rows) == 2 and {r["model"] for r in rows} == {"fake:small"}
        types = [e["type"] for e in store.list_events(rec.meeting_id)]
        assert "live_started" in types and "live_stopped" in types and types[-1] in ("stop", "live_stopped")
        assert store.get_setting("pipeline.paused_until") == ""
        # Q1/CP6: a caixa do warm-up desligada vence o padrão das Configurações
        assert c.start_recording(_cfg(), {"title": "sem ao vivo", "live_enabled": False})
        assert c.live is None and c.live_status == "off" and len(children) == 1
        assert store.get_setting("pipeline.paused_until") == ""                 # sem ao vivo e sem pause_on_record: sem lease
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
    finally:
        c.emergency_stop()


def test_live_without_models_warns_once_and_records_normally(qtbot, qapp, store, platform, monkeypatch, tmp_path):
    monkeypatch.delenv("LISTENER_LIVE", raising=False)
    store.set_setting("live.enabled", True)
    store.set_setting("pipeline.models_dir", str(tmp_path / "vazia"))
    c = _controller(qapp, platform, store, monkeypatch)
    notes: list[str] = []
    monkeypatch.setattr(c, "notify", notes.append)
    try:
        for title in ("a", "b"):
            assert c.start_recording(_cfg(), {"title": title})
            assert c.live is None and c.live_status == "unavailable" and platform.children == []
            platform.pump(0.6)
            c.stop_recording()
            qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert [n for n in notes if "ao vivo" in n] == ["modelos do ao vivo ausentes: prepare nas Configurações (Ao vivo)"]
    finally:
        c.emergency_stop()


def test_cancel_kills_the_child_before_deleting_and_a_crash_gets_one_retry(qtbot, qapp, store, platform, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.set_setting("live.enabled", True)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    c = _controller(qapp, platform, store, monkeypatch)
    notes: list[str] = []
    monkeypatch.setattr(c, "notify", notes.append)
    order: list[str] = []
    real_delete = store.delete_meeting

    def delete(meeting_id: int) -> None:
        order.append("delete" if all(ch.killed for ch in platform.children) else "delete-com-filho-vivo")
        real_delete(meeting_id)

    monkeypatch.setattr(store, "delete_meeting", delete)
    try:
        assert c.start_recording(_cfg(), {"title": "cancelar"})
        assert len(platform.children) == 1 and c.live is not None
        platform.pump(0.4)
        c.cancel_recording()
        assert order == ["delete"] and platform.children[0].killed and c.live is None and c.live_status == "stopped"
        assert c.recorder is None and c.state == AppState.IDLE
        # queda: uma nova tentativa por gravação; na segunda, "ao vivo parou" e a gravação segue
        assert c.start_recording(_cfg(), {"title": "queda"})
        rec = c.recorder
        first = platform.children[-1]
        first.kill()
        qtbot.waitUntil(lambda: len(platform.children) == 3 and c.live_status == "starting", timeout=5000)
        assert c.live is not None and c.live.pid == platform.children[-1].pid and rec.snapshot is not None
        platform.children[-1].kill()
        qtbot.waitUntil(lambda: c.live_status == "crashed", timeout=5000)
        assert len(platform.children) == 3 and c.live is None and any("ao vivo parou" in n for n in notes)
        assert c.recorder is rec and c.state == AppState.RECORDING                # a gravação continua intacta
        platform.pump(0.6)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert store.get_meeting(rec.meeting_id)["status"] == "gravada"
    finally:
        c.emergency_stop()
