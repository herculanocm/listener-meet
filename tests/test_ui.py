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


def test_controller_enqueues_job_launches_worker_and_renews_lease(qtbot, qapp, store, platform, monkeypatch):
    from datetime import datetime, timedelta

    from listener.capture.recorder import RecordConfig
    from listener.model import JobStatus
    from listener.platform.common.process import worker_command
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    t = {"now": datetime.fromisoformat("2026-10-24T10:00:00.000-03:00")}
    c.now = lambda: t["now"]
    c.lease_renew_s = 2.0                                      # 60 s no produto; aqui 2 s para não bombear um minuto de áudio
    notes: list[str] = []
    opened: list[int] = []
    monkeypatch.setattr(c, "notify", notes.append)
    monkeypatch.setattr(c, "open_review", opened.append)
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5,
                       stop_timeout_s=1.0, retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "t"})
        lease = store.get_setting("pipeline.paused_until")
        assert lease == (t["now"] + timedelta(minutes=10)).isoformat(timespec="milliseconds")           # Q7: lease ao gravar
        platform.pump(1.0)
        c._tick()
        assert store.get_setting("pipeline.paused_until") == lease                                      # ainda não renova
        platform.pump(1.1)
        t["now"] += timedelta(seconds=61)
        c._tick()
        assert store.get_setting("pipeline.paused_until") == (t["now"] + timedelta(minutes=10)).isoformat(timespec="milliseconds")
        assert platform.spawned == [] and store.jobs.active() == []
        rec = c.recorder
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert store.get_setting("pipeline.paused_until") == ""                                         # lease limpa ao parar
        jobs = store.jobs.active()
        assert len(jobs) == 1 and jobs[0]["meeting_id"] == rec.meeting_id and jobs[0]["status"] == JobStatus.PENDING
        assert len(platform.spawned) == 1 and platform.spawned[0][0] == worker_command()
        assert platform.spawned[0][1]["OMP_NUM_THREADS"] == str(store.get(ctl.SPECS["pipeline.torch_threads"]))
        assert c.launch_worker() is False                                                               # cooldown: não relança
        # o worker (simulado aqui) pega e conclui: o poll avisa e abre a revisão uma vez só
        store.jobs.claim(pid=99)
        store.jobs.finish(jobs[0]["id"], JobStatus.DONE)
        c._poll_queue()
        c._poll_queue()
        assert [n for n in notes if "pronta para revisão" in n] == ["t: pronta para revisão de falantes."] and opened == [rec.meeting_id]
        # auto_start desligado: parar não enfileira
        store.set_setting("pipeline.auto_start", False)
        assert c.start_recording(cfg, {"title": "u"})
        rec2 = c.recorder
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert store.jobs.for_meeting(rec2.meeting_id) == [] and len(platform.spawned) == 1
    finally:
        c.emergency_stop()


def test_quit_while_recording_enqueues_the_meeting_and_clears_the_lease(qtbot, qapp, store, platform, monkeypatch):
    """R2-12: "Sair" com gravação em curso para, salva, cria o job e limpa a lease (o worker solto termina a fila, P2)."""
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import RecordConfig
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(c.app, "quit", lambda: None)
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    assert c.start_recording(cfg, {"title": "q"}) and store.get_setting("pipeline.paused_until")
    rec = c.recorder
    platform.pump(0.6)
    c.quit()
    assert c.recorder is None and c.state == AppState.IDLE and store.get_meeting(rec.meeting_id)["status"] == "gravada"
    assert store.get_setting("pipeline.paused_until") == ""
    jobs = store.jobs.for_meeting(rec.meeting_id)
    assert len(jobs) == 1 and jobs[0]["status"] == "pendente" and len(platform.spawned) == 1
    c.emergency_stop()


def test_dead_worker_job_is_relaunched_by_the_app(qtbot, qapp, store, platform):
    """R2-01: job `rodando` com heartbeat vencido (worker morto) conta como ativo sem worker: o app relança."""
    from datetime import datetime, timedelta

    from listener.platform.common.process import worker_command
    from listener.ui.controller import Controller

    mid = store.create_meeting("m", None, None, None)
    store.finish_meeting(mid, 3000)
    jid = store.jobs.enqueue(mid)
    old = (datetime.now().astimezone() - timedelta(minutes=30)).isoformat(timespec="milliseconds")
    assert store.jobs.claim(pid=4242, now=old)["id"] == jid                                  # worker pegou há 30 min e morreu
    assert not store.jobs.has_pending() and store.jobs.held_fresh() == []
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        assert c.launch_worker() is True and platform.spawned[0][0] == worker_command()
        assert c.launch_worker() is False                                                   # cooldown
        store.jobs.heartbeat(jid)                                                           # worker novo vivo: não relança
        c._worker_spawned_at[False] = None
        assert c.launch_worker() is False
    finally:
        c.emergency_stop()


def test_queue_dialog_lists_jobs_and_acts_on_selection(qtbot, store):
    from listener.model import JobKind, JobStep
    from listener.ui.queue import QueueDialog, job_row_texts

    mid = store.create_meeting("Diretoria", "teams", None, None)
    store.finish_meeting(mid, 1_800_000)
    j1, j2 = store.jobs.enqueue(mid), store.jobs.enqueue(mid)
    store.jobs.claim(pid=1)
    store.jobs.set_progress(j1, JobStep.DIARIZE, 0.2)
    launched: list[int] = []
    dlg = QueueDialog(store, on_launch=lambda light=False: launched.append(1 + int(light)))
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 2
    row0 = [dlg.table.item(0, c).text() for c in range(7)]
    assert row0[0].startswith("Diretoria (") and row0[1:5] == ["processar", "rodando", "separando vozes", "20"] and row0[5].startswith("≈ ")
    assert job_row_texts(store.jobs.get(j2), None, "{}")[0] == f"reunião {mid}" and "sem estimativa" not in row0[5]
    dlg.table.selectRow(1)
    assert dlg.selected_job_id() == j2
    dlg.cancel_selected()
    assert store.jobs.get(j2)["status"] == "cancelado"
    dlg.toggle_pause()
    assert store.get(ctl_specs()["pipeline.paused_manual"]) is True and dlg.btn_pause.text() == "Retomar fila" and launched == []
    dlg.toggle_pause()
    assert store.get(ctl_specs()["pipeline.paused_manual"]) is False and launched == [1, 2]       # as duas classes (F3)
    dlg.table.selectRow(1)
    dlg.retry_selected()
    assert store.jobs.get(j2)["status"] == "pendente" and launched == [1, 2, 1]
    dlg.table.selectRow(0)
    dlg.reprocess_selected()
    assert store.jobs.for_meeting(mid)[0]["kind"] == JobKind.REPROCESS and dlg.table.rowCount() == 3
    assert "pendente(s)" in dlg.status_label.text()


def ctl_specs():
    from listener.config.defaults import SPECS

    return SPECS


def test_start_enqueues_recorded_meetings_without_a_job(qtbot, qapp, store, platform):
    """R2-24: ao abrir, reuniões `gravada` ou `interrompida` sem job entram na fila (D-014); as que já têm job, não."""
    from listener.model import MeetingStatus
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    done = store.create_meeting("com job", None, None, None)
    store.finish_meeting(done, 1000)
    store.jobs.enqueue(done)
    a = store.create_meeting("sem job", None, None, None)
    store.finish_meeting(a, 1000)
    b = store.create_meeting("interrompida", None, None, None)
    store.finish_meeting(b, 1000, MeetingStatus.INTERRUPTED)
    c_ = store.create_meeting("cancelada", None, None, None)
    store.finish_meeting(c_, 1000, MeetingStatus.CANCELLED)
    store.set_setting("pipeline.paused_until", "2099-01-01T00:00:00.000-03:00")
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        c.start()
        assert sorted(j["meeting_id"] for j in store.jobs.active()) == sorted([done, a, b])
        assert store.get_setting("pipeline.paused_until") == "" and len(platform.spawned) == 1
        c.start()
        assert len(store.jobs.active()) == 3                                                    # idempotente
    finally:
        c.emergency_stop()


def test_light_worker_is_launched_during_recording_and_heavy_is_not(qtbot, qapp, store, platform, monkeypatch):
    """F3 item 2: o worker leve (jobs `llm`) sobe mesmo com gravação em curso; o pesado espera o fim da gravação."""
    from listener.capture.recorder import RecordConfig
    from listener.model import JobKind
    from listener.platform.common.process import worker_command
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    old = store.create_meeting("antiga", None, None, None)
    store.finish_meeting(old, 3000)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "nova"}) and c.recorder is not None
        store.jobs.enqueue(old)                                    # pesado: espera
        store.jobs.enqueue(old, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": 1})
        c._poll_queue()
        assert [argv for argv, _env in platform.spawned] == [worker_command(light=True)]
        assert platform.spawned[0][1]["OMP_NUM_THREADS"] == "1"
        assert c.launch_worker(light=True) is False and c.launch_worker() is False       # cooldown do leve; pesado: gravando
        c._poll_queue()
        assert len(platform.spawned) == 1
        monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert [argv for argv, _env in platform.spawned][1] == worker_command()           # parou: o pesado sobe
        # "ata pronta" por kind: um job llm concluído não abre a revisão de falantes nem a oferece
        notes: list[str] = []
        opened: list[int] = []
        monkeypatch.setattr(c, "notify", notes.append)
        monkeypatch.setattr(c, "open_review", opened.append)
        llm_job = store.jobs.for_meeting(old)[0]
        assert llm_job["kind"] == "llm"
        store.jobs.claim(pid=5, kinds=("llm",))
        store.jobs.finish(int(llm_job["id"]), __import__("listener.model", fromlist=["JobStatus"]).JobStatus.DONE)
        c._poll_queue()
        assert notes == ["antiga: ata pronta."] and opened == []
    finally:
        c.emergency_stop()
