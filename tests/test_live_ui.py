"""F4 L4, interface do ao vivo (pytest-qt, offscreen): selo e linha da legenda no widget, caixas do warm-up com o preset,
grupo "Ao vivo" das Configurações com o preparo dos modelos (um download por vez, fechar no meio não derruba nada), janela
"Ao vivo" (carga, leitura por incremento, marcar legenda e item do parcial, só leitura depois de parar) e o controlador
marcando pelo `Recorder.mark` no instante da legenda, com a ata da F3 lendo os marcadores."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import AppState, SummaryKind
from listener.store.captions import CaptionInsert
from listener.store.summaries import SummaryInsert
from tests.fake_live import FakeEngines, ThreadChild, thread_spawner

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")


def _caption(store: Any, mid: int, t0: int, text: str, label: str | None = "Ana — CFO", channel: str = "R") -> int:
    return store.captions.insert(CaptionInsert(mid, channel, t0, t0 + 3000, text, None, label, 0.8, "fake:small", 6000))


def test_widget_live_badge_caption_line_and_height(qtbot):
    from listener.ui.widget import LIVE_MIN_HEIGHT, RecordingWidget

    w = RecordingWidget(size=(320, 96), live_line=True)
    qtbot.addWidget(w)
    w.show()
    assert w.height() >= LIVE_MIN_HEIGHT and w.caption_line.isVisibleTo(w) and not w.live_badge.isVisibleTo(w)
    long_text = "vamos fechar o orçamento de 2027 hoje com corte de oito por cento em todas as áreas " * 3
    w.set_caption("Ana — CFO", long_text)
    full = "Ana — CFO: " + " ".join(long_text.split())
    assert w.caption_line.full_text == full and full in w.caption_line.toolTip()
    assert w.caption_line.text() != full and w.caption_line.text().endswith("…")                 # cortada na largura
    n = w.style_changes
    w.set_live_state("live", "legenda ao vivo")
    w.set_live_state("live", "legenda ao vivo")                                                   # igual: nada muda
    assert w.live_badge.isVisibleTo(w) and "ao vivo" in w.live_badge.text() and w.style_changes == n + 1
    w.set_live_state("skipping")
    assert "legenda atrasada" in w.live_badge.text()
    w.set_live_state("crashed")
    assert "ao vivo parou" in w.live_badge.text()
    w.set_live_state("stopped")
    assert not w.live_badge.isVisibleTo(w)
    with qtbot.waitSignal(w.live_clicked, timeout=1000):
        qtbot.mouseClick(w.caption_line, pytestqt.qt_compat.qt_api.QtCore.Qt.MouseButton.LeftButton)
    # sem `live.in_widget`: sem linha, mas o selo aparece; tamanho salvo maior é respeitado
    w2 = RecordingWidget(size=(400, 150))
    qtbot.addWidget(w2)
    w2.set_caption("Ana", "oi")
    w2.set_live_state("degraded")
    assert w2.height() == 150 and not w2.caption_line.isVisibleTo(w2) and "legenda atrasada" in w2.live_badge.text()
    assert RecordingWidget(size=(320, 150), live_line=True).height() == 150


def test_upsert_preset_keeps_live_flags(store):
    pid = store.upsert_preset("Teams", None, None, "app", "ms-teams.exe")
    assert store.get_preset(pid)["live_enabled"] is None and store.get_preset(pid)["live_names"] is None   # nulo = padrão
    assert store.upsert_preset("Teams", None, None, "app", "ms-teams.exe", live_enabled=False, live_names=True) == pid
    assert (store.get_preset(pid)["live_enabled"], store.get_preset(pid)["live_names"]) == (0, 1)
    store.upsert_preset("Teams", "1", "Mic", "app", "ms-teams.exe")                            # quem não sabe do ao vivo não apaga
    assert (store.get_preset(pid)["live_enabled"], store.get_preset(pid)["live_names"]) == (0, 1)


def test_warmup_live_boxes_follow_preset_settings_and_models(qtbot, store, platform, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from listener.ui.warmup import WarmupDialog

    monkeypatch.delenv("LISTENER_LIVE", raising=False)
    store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))
    store.set_setting("live.enabled", True)
    store.set_setting("live.names", False)
    with_flags = store.upsert_preset("Com ao vivo", None, None, "system", None, live_enabled=False, live_names=True)
    plain = store.upsert_preset("Sem marca", None, None, "system", None)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    assert dlg.live_check.isChecked() and not dlg.names_check.isChecked() and dlg.names_check.isEnabled()
    assert "modelos ausentes (small, base)" in dlg.live_note.text()                               # aviso, sem bloquear
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData(with_flags))
    assert not dlg.live_check.isChecked() and dlg.names_check.isChecked() and not dlg.names_check.isEnabled()
    assert dlg.live_note.text() == ""                                                            # desligado: nada a avisar
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData(plain))
    assert dlg.live_check.isChecked() and not dlg.names_check.isChecked()                        # nulo = Configurações
    dlg.names_check.setChecked(True)
    _cfg, fields = dlg.result_config()
    assert fields["live_enabled"] is True and fields["live_names"] is True
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Novo", True)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dlg._save_preset()
    novo = next(p for p in store.list_presets() if p["name"] == "Novo")
    assert (novo["live_enabled"], novo["live_names"]) == (1, 1)
    opened: list[int] = []
    dlg.on_settings = lambda: opened.append(1)
    dlg._open_settings_for_live()                                                                # fechou sem preparar: o aviso fica
    assert opened == [1] and "modelos ausentes" in dlg.live_note.text()
    from listener.ui.live_prepare import preparer

    monkeypatch.setenv("LISTENER_LIVE", "fake")                                                  # o download terminou depois
    preparer().changed.emit({"state": "done"})
    assert dlg.live_note.text() == ""
    dlg.reject()
    dlg.reject()                                                                                 # fechar duas vezes não reclama


def _prepare_target(db: Path, gate: threading.Event | None = None, models: Path | None = None):
    """Alvo do `ThreadChild` para o `--prepare`: o de verdade (modo fake) ou um roteiro que espera `gate` no meio."""
    from listener.live import proc
    from listener.live.protocol import encode_line

    def run(_stdin: BinaryIO, stdout: BinaryIO) -> int:
        if gate is None:
            return proc.prepare(db, stdout)
        stdout.write(encode_line({"type": "progress", "model": "small", "status": "baixando", "fraction": 0.0}))
        stdout.flush()
        gate.wait(5.0)
        for m in ("small", "base"):
            if models is not None:
                (models / f"faster-whisper-{m}").mkdir(parents=True, exist_ok=True)
                (models / f"faster-whisper-{m}" / "model.bin").write_bytes(b"x")
        stdout.write(encode_line({"type": "done", "models_dir": str(models)}))
        stdout.flush()
        return 0
    return run


def test_settings_live_group_prepares_models_once_and_survives_closing(qtbot, store, platform, monkeypatch, tmp_path):
    from listener.ui.live_prepare import ModelPreparer
    from listener.ui.settings_min import SettingsDialog

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    prep = ModelPreparer()
    spawned: list[ThreadChild] = []

    def spawn_with(target: Any):
        def spawn(argv: list[str], env: Any = None) -> ThreadChild:
            assert argv[-3:] == ["--prepare", "--db", str(store.base_dir)]
            child = ThreadChild(target, pid=7000 + len(spawned), argv=argv)
            spawned.append(child)
            return child
        return spawn

    dlg = SettingsDialog(store, platform, {}, model_preparer=prep)
    qtbot.addWidget(dlg)
    assert "live.model" in dlg._combos and {"live.enabled", "live.summary_interval_min", "live.in_widget"} <= set(dlg._edits)
    assert dlg.prepare_btn.isEnabled() and dlg.prepare_status.text() == "prontos (small e base)"
    monkeypatch.setattr(platform, "spawn_child", spawn_with(_prepare_target(store.base_dir)))   # o `--prepare` real, modo fake
    dlg.prepare_models()
    qtbot.waitUntil(lambda: prep.state["state"] == "done", timeout=5000)
    assert prep.starts == 1 and not prep.running and dlg.prepare_btn.isEnabled() and dlg.prepare_bar.value() == 100
    # sem os modelos: o estado diz o que falta; um download por vez; fechar no meio não derruba nada
    monkeypatch.delenv("LISTENER_LIVE")
    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    gate = threading.Event()
    monkeypatch.setattr(platform, "spawn_child", spawn_with(_prepare_target(store.base_dir, gate, models)))
    dlg.reject()
    dlg2 = SettingsDialog(store, platform, {}, model_preparer=prep)
    qtbot.addWidget(dlg2)
    assert dlg2.prepare_status.text() == "faltam: small, base"
    dlg2.prepare_models()
    qtbot.waitUntil(lambda: prep.state.get("model") == "small", timeout=5000)
    assert not dlg2.prepare_btn.isEnabled() and "small: baixando" in dlg2.prepare_status.text()
    dlg2.prepare_models()
    assert prep.start(store, platform.spawn_child) is False and prep.starts == 2 and len(spawned) == 2
    dlg2.reject()                                                                                 # fecha com o download no meio
    dlg2.deleteLater()
    gate.set()
    qtbot.waitUntil(lambda: prep.state["state"] == "done", timeout=5000)
    dlg3 = SettingsDialog(store, platform, {}, model_preparer=prep, recording=True)
    qtbot.addWidget(dlg3)
    assert dlg3.prepare_status.text().startswith("prontos (small e base)") and not dlg3.prepare_btn.isEnabled()
    dlg3.prepare_models()                                                                         # gravando: nada
    assert prep.starts == 2


def test_live_window_loads_reads_by_increment_and_marks(qtbot, store):
    from PySide6.QtCore import QUrl

    from listener.ui.live import LiveWindow

    mid = store.create_meeting("Diretoria", None, None, None)
    _caption(store, mid, 9000, "segunda fala", "Bia")
    _caption(store, mid, 2000, "primeira fala")                                                    # gravada depois, tempo antes
    marks: list[Any] = []
    items: list[Any] = []

    def mark_caption(row: Any) -> tuple[bool, str]:
        marks.append(dict(row))
        return True, "marcador criado"

    def mark_item(kind: str, item: Any, until: int | None) -> tuple[bool, str]:
        items.append((kind, dict(item), until))
        return True, "marcador criado"

    marked: set[Any] = set()
    win = LiveWindow(store, mid, on_mark_caption=mark_caption, on_mark_item=mark_item, on_refresh=lambda: "pedido",
                     marked_captions=marked)
    qtbot.addWidget(win)
    assert [r["text"] for r in win.captions] == ["primeira fala", "segunda fala"]
    assert win.list.item(0).text() == "[00:02] Ana — CFO: primeira fala"
    _caption(store, mid, 5000, "fala do meio", None, "L")
    win.on_caption({"type": "caption"})
    assert [r["text"] for r in win.captions] == ["primeira fala", "fala do meio", "segunda fala"]
    assert win.list.item(1).text() == "[00:05] fala do meio"
    win._caption_clicked(win.list.item(0))
    assert len(marks) == 1 and marks[0]["t_start_ms"] == 2000
    from listener.ui.live import caption_key

    marked.add(caption_key(marks[0]))                                                             # o controlador guarda a chave
    win._paint(win.list.item(0), marks[0])
    win._caption_clicked(win.list.item(0))
    assert len(marks) == 1 and win.status.text() == "esta legenda já virou marcador" and win.list.item(0).text().startswith("⚑")
    # parcial: hora do último, lista com "marcar"; o item sem `t_ms` cai no fim do parcial
    assert "Ainda sem resumo parcial" in win.partial_view.toPlainText()
    data = {"summary": "Discutiram o orçamento.", "decisions": [{"text": "Cortar 8%", "caption_id": 1, "t_ms": 9000}],
            "actions": [{"text": "Enviar a planilha", "caption_id": None, "t_ms": None, "owner": "Bia"}]}
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.PARTIAL, json.dumps(data), "md", "fake", "fake-fast", "partial/1.0",
                                               None, None, "pt-BR"))
    store._x("UPDATE summaries SET t_until_ms = 12000 WHERE id = ?", (sid,))
    win.on_partial_state({"state": "done", "meeting_id": mid})
    html = win.partial_view.toHtml()
    assert "mark:decisions:0" in html and "mark:actions:0" in html and "[00:12]" in win.partial_when.text()
    win._anchor(QUrl("mark:actions:0"))
    assert items == [("actions", data["actions"][0], 12000)]
    win.on_partial_state({"state": "running"})
    assert not win.refresh_btn.isEnabled() and "atualizando" in win.status.text()
    win._refresh()
    # só leitura depois de parar: sem marcar nem atualizar
    win.set_finished("gravação parada: só leitura")
    win._caption_clicked(win.list.item(2))
    win._anchor(QUrl("mark:decisions:0"))
    assert len(marks) == 1 and len(items) == 1 and "mark:" not in win.partial_view.toHtml() and not win.refresh_btn.isEnabled()
    # posição, tamanho e "sempre visível" lembrados
    win.resize(500, 640)
    win.on_top.setChecked(False)
    win.save_geometry()
    assert store.get_setting("ui.live_geometry").endswith(",500,640") and store.get_setting("ui.live_on_top") == "0"
    win2 = LiveWindow(store, mid)
    qtbot.addWidget(win2)
    assert (win2.width(), win2.height()) == (500, 640) and not win2.on_top.isChecked() and not win2.refresh_btn.isVisibleTo(win2)


def test_controller_live_window_marks_caption_and_partial_item_and_minutes_read_them(qtbot, qapp, store, platform, monkeypatch):
    from listener.pipeline.llm_steps import build_context
    from listener.llm.prompts.common import header
    from listener.ui import controller as ctl

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.upsert_me("Eu", role="Gerente")
    store.set_setting("live.enabled", True)
    children: list[ThreadChild] = []
    monkeypatch.setattr(platform, "spawn_child", thread_spawner(store.base_dir, FakeEngines(), children))
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "ao vivo", "live_names": True})
        rec, w = c.recorder, c.widget
        assert w is not None and w.live_line and w.height() >= 118
        qtbot.waitUntil(lambda: c.live_status == "live", timeout=5000)
        assert "ao vivo" in w.live_badge.text()
        platform.pump(10.5)                                                    # tom contínuo: a janela fecha em 10 s
        qtbot.waitUntil(lambda: c.live_last_caption is not None, timeout=5000)
        win = c.open_live_window()
        assert win is not None and c.open_live_window() is win                                     # uma janela por gravação
        qtbot.waitUntil(lambda: len(win.captions) >= 1, timeout=5000)
        assert w.caption_line.full_text.endswith(c.live_last_caption["text"])
        row = win.captions[0]
        ok, msg = c.mark_caption(row)
        assert ok and msg.startswith("marcador criado em [")
        assert c.mark_caption(row) == (False, "esta legenda já virou marcador")
        ok, _ = c.mark_partial_item("decisions", {"text": "Cortar 8%", "caption_id": None, "t_ms": None}, 1234)
        assert ok and c.mark_partial_item("decisions", {"text": "cortar  8%"}, 1234)[0] is False     # mesmo item, outra caixa
        assert c.mark_partial_item("actions", {"text": "Enviar", "t_ms": 800, "owner": "Bia"})[0]
        markers = {m["note"]: m["t_ms"] for m in store.list_markers(rec.meeting_id)}
        label = f"{row['label']}: " if row.get("label") else ""
        assert markers == {f"{label}{row['text']}": row["t_start_ms"], "Decisão: Cortar 8%": 1234, "Ação: Enviar (Bia)": 800}
        origins = [e["data"].get("origin") for e in store.list_events(rec.meeting_id) if e["type"] == "marker"]
        assert origins == ["legenda", "parcial", "parcial"] and rec.markers == 3
        c.stop_recording()
        assert win.finished and c.mark_caption(row)[0] is False
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        ctx = build_context(store, rec.meeting_id, 0, "pt-BR")                                     # a ata da F3 lê os marcadores
        assert {m.note for m in ctx.meeting_ctx.markers} == set(markers) and "Decisão: Cortar 8%" in header(ctx.meeting_ctx)
        win.close()
        assert c.live_window is None
    finally:
        c.emergency_stop()


def test_recording_interrupts_a_model_download_in_progress(qtbot, qapp, store, platform, monkeypatch, tmp_path):
    """Revisão do L4 a L6: o preparo é fora da gravação (item 10); gravar interrompe o download, que disputaria a rede com a
    reunião, e avisa uma vez; o fim do filho morto não mexe mais no estado."""
    from listener.ui import controller as ctl
    from listener.ui.live_prepare import ModelPreparer

    prep = ModelPreparer()
    gate = threading.Event()
    child = ThreadChild(_prepare_target(store.base_dir, gate, tmp_path / "modelos"), pid=7100)
    assert prep.start(store, lambda argv, env=None: child)
    qtbot.waitUntil(lambda: prep.state.get("model") == "small", timeout=5000)
    monkeypatch.setattr(ctl, "preparer", lambda: prep)
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    notes: list[str] = []
    monkeypatch.setattr(c, "notify", notes.append)
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "com download", "live_enabled": False})
        assert not prep.running and prep.state["state"] == "error" and "interrompido pela gravação" in prep.state["message"]
        assert child.killed and [n for n in notes if "Download dos modelos" in n] == [notes[0]]
        gate.set()
        qtbot.wait(200)                                                          # o `exit` do filho morto chega e é ignorado
        assert prep.state["state"] == "error" and prep.starts == 1
        platform.pump(0.6)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
    finally:
        gate.set()
        c.emergency_stop()
