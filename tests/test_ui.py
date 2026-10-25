"""Telas com pytest-qt em modo offscreen (F1.2 M-27/M-28): widget sem trabalho inútil, validação das Configurações,
warm-up abrindo o microfone uma vez, controlador gravando com a plataforma simulada e parada assíncrona."""
from __future__ import annotations

import pytest

from listener.capture.recorder import SourceSpec
from listener.model import AppState

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")


def test_recording_widget_only_restyles_on_change(qtbot):
    from listener.ui.widget import RecordingWidget

    w = RecordingWidget()
    qtbot.addWidget(w)
    base = {"L": "ok", "R": "ok", "db": "ok"}
    w.update_state(1.0, {"L": 0.1, "R": 0.1}, base, False, False, 0)
    n = w.style_changes
    for i in range(20):
        w.update_state(1.0 + i * 0.2, {"L": 0.1, "R": 0.1}, base, False, False, 0)
    assert w.style_changes == n                                   # 20 ticks iguais: nenhum setStyleSheet
    w.update_state(6.0, {"L": 0.0, "R": 0.0}, {**base, "R": "alert"}, True, False, 1)
    assert w.style_changes == n + 2 and w.status_label.text() == "pausado" and w.markers_label.text() == "⚑ 1"
    w.show_alert("x", True)
    w.show_alert("x", True)
    assert w.alert_btn.isVisibleTo(w) and w.alert_label.text() == "x"
    w.update_state(7.0, {"L": 0.0, "R": 0.0}, base, False, False, 1, saving=True)
    assert w.status_label.text() == "salvando…" and not w.btn_stop.isEnabled()


def test_level_meter_skips_repaint_when_silent(qtbot):
    from listener.ui.meters import LevelMeter

    m = LevelMeter()
    qtbot.addWidget(m)
    m.set_rms(0.0)
    assert m._fraction == 0.0 and m._peak == 0.0
    m.set_rms(0.2)
    assert m._fraction > 0
    for _ in range(200):
        m.set_rms(0.0)
    assert m._peak == 0.0                                          # o pico decai até zero e para de repintar


def test_settings_dialog_validates_with_platform_grammar(qtbot, store, platform):
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    assert dlg.validation_error() is None
    dlg._edits["capture.target_rate"].setText("0")
    assert "Taxa de amostragem" in (dlg.validation_error() or "")
    dlg._edits["capture.target_rate"].setText("16000")
    dlg._edits["hotkeys.mark"].setText("ctrl+ç")
    assert "tecla desconhecida" in (dlg.validation_error() or "")
    dlg._edits["hotkeys.mark"].setText("ctrl+alt+shift+r")      # igual ao de iniciar
    assert dlg.validation_error() == "Dois atalhos iguais."
    dlg._edits["hotkeys.mark"].setText("ctrl+alt+shift+m")
    dlg._edits["pipeline.threshold"].setText("0.9")                 # F2: grupo Processamento gerado da tabela
    assert "Limiar" in (dlg.validation_error() or "")
    dlg._edits["pipeline.threshold"].setText("0.7")
    dlg._combos["pipeline.whisper_model"].setCurrentText("small")
    dlg.hf_token.setText("hf_abc")
    dlg._save()
    assert store.get_setting("capture.target_rate") == "16000" and dlg.result() == dlg.DialogCode.Accepted
    assert store.get_setting("pipeline.threshold") == "0.7" and store.get_setting("pipeline.whisper_model") == "small"
    assert store.settings.provider_key("huggingface") == "hf_abc"


def test_warmup_opens_mic_once_and_builds_config(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    store.set_setting("warmup.last_source_kind", "app")
    store.set_setting("warmup.last_source_exe", "meeting.exe")
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    assert platform.streams == []                                  # o construtor não abre o microfone
    dlg.show()
    qtbot.waitUntil(lambda: dlg.sources.selected_spec().kind == "app", timeout=3000)   # fonte salva na 1ª lista de apps
    assert len(platform.live_streams()) == 1 and platform.live_streams()[0].channels == 1
    cfg, fields = dlg.result_config()
    assert cfg.source.pid == 4242 and fields["platform"] == "meeting" and cfg.validate() == []
    dlg.accept()
    assert platform.live_streams() == [] and store.get_setting("warmup.last_source_exe") == "meeting.exe"


def test_controller_records_and_stops_async(qtbot, qapp, store, platform, monkeypatch):
    from listener.capture.recorder import RecordConfig
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller

    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5,
                       stop_timeout_s=1.0, retry_base_s=0.001)
    shown: list[int] = []
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: shown.append(a[1])})())
    try:
        assert c.start_recording(cfg, {"title": "t"}) and c.state == AppState.RECORDING and c.widget is not None
        rec = c.recorder
        assert rec is not None and platform.awake is True
        platform.pump(1.2)
        c._tick()
        assert c.widget.time_label.text() == "00:01"
        c.stop_recording()
        assert c.state == AppState.SAVING
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert shown == [rec.meeting_id] and store.get_meeting(rec.meeting_id)["status"] == "gravada"
        assert store.chunk_stats(rec.meeting_id)["L"]["n"] == 3 and platform.awake is False and c.recorder is None
    finally:
        c.emergency_stop()                                         # garante que a thread de trabalho não fica viva
    with pytest.raises(RuntimeError):
        store.list_meetings()                                      # o banco foi fechado pelo emergency_stop
