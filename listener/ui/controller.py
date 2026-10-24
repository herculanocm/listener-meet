"""Controlador: estado (ocioso, gravando, pausado), warm-up, gravador, widget, bandeja, atalhos, mudo global."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QSystemTrayIcon

from ..capture.recorder import RecordConfig, Recorder, SourceSpec
from .settings_min import SettingsDialog
from .sources import SourceTable
from .summary import SummaryDialog
from .tray import TrayIcon
from .warmup import WarmupDialog
from .widget import MarkerPopup, RecordingWidget

HOTKEY_ACTIONS = ("start", "pause", "stop", "mark", "mute")


class Controller(QObject):
    hotkey_pressed = Signal(str)

    def __init__(self, app, platform, store):
        super().__init__()
        self.app = app
        self.platform = platform
        self.store = store
        self.state = "idle"
        self.recorder: Recorder | None = None
        self.widget: RecordingWidget | None = None
        self.tray: TrayIcon | None = None
        self._last_os_mute_check = 0.0
        self._warmup_open = False
        self.hotkey_pressed.connect(self._on_hotkey)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- início
    def start(self) -> None:
        interrupted = self.store.mark_interrupted()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = TrayIcon(self)
            self.tray.show()
            if interrupted:
                self.tray.showMessage("listener-meet", f"{interrupted} gravação(ões) interrompida(s) na última execução; os pedaços foram preservados.")
        self.register_hotkeys()

    def register_hotkeys(self) -> None:
        hk = self.platform.hotkeys()
        hk.unregister_all()
        for name in HOTKEY_ACTIONS:
            combo = self.store.get_setting(f"hotkeys.{name}")
            if combo:
                hk.register(combo, lambda n=name: self.hotkey_pressed.emit(n))

    def _on_hotkey(self, name: str) -> None:
        {"start": self.open_warmup, "pause": self.toggle_pause, "stop": self.stop_recording,
         "mark": self.mark, "mute": self.toggle_mute}[name]()

    # ---------------------------------------------------------------- gravação
    def open_warmup(self) -> None:
        if self.state != "idle" or self._warmup_open:
            self.show_widget()
            return
        self._warmup_open = True
        try:
            dlg = WarmupDialog(self.platform, self.store)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                cfg, fields = dlg.result_config()
                self.start_recording(cfg, fields)
        finally:
            self._warmup_open = False

    def start_recording(self, cfg: RecordConfig, fields: dict) -> None:
        meeting_id = self.store.create_meeting(fields.get("title"), fields.get("platform"), fields.get("preset_id"),
                                               fields.get("participants"), fields.get("is_in_room", False))
        self.recorder = Recorder(self.platform, self.store, meeting_id, cfg)
        self.recorder.start()
        self.state = "recording"
        pos = self._saved_widget_pos()
        size = self._saved_widget_size()
        self.widget = RecordingWidget(size=size, pos=pos)
        w = self.widget
        w.pause_clicked.connect(self.toggle_pause)
        w.mark_clicked.connect(self.mark)
        w.mute_clicked.connect(self.toggle_mute)
        w.stop_clicked.connect(self.stop_recording)
        w.cancel_clicked.connect(self.cancel_recording)
        w.switch_source_clicked.connect(self.switch_source)
        w.settings_requested.connect(self.open_settings)
        w.moved.connect(lambda p: self.store.set_setting("ui.widget_pos", f"{p.x()},{p.y()}"))
        w.show()
        if self.tray:
            self.tray.set_state("recording")
        self._timer.start()
        if self.recorder.warnings and self.tray:
            self.tray.showMessage("listener-meet", "\n".join(self.recorder.warnings))

    def show_widget(self) -> None:
        if self.widget:
            self.widget.show()
            self.widget.raise_()

    def toggle_pause(self) -> None:
        if not self.recorder:
            return
        if self.recorder.paused:
            self.recorder.resume()
            self.state = "recording"
        else:
            self.recorder.pause()
            self.state = "paused"
        if self.tray:
            self.tray.set_state(self.state)

    def mark(self) -> None:
        if not self.recorder:
            return
        t_rel = self.recorder.elapsed()
        popup = MarkerPopup(t_rel, self.widget)
        note = popup.note() if popup.exec() == QDialog.DialogCode.Accepted else None
        if note is not None:
            self.recorder.mark(note)

    def toggle_mute(self) -> None:
        if not self.recorder:
            return
        muted = not self.recorder.muted
        ok = self.platform.mic_mute().set(muted)
        self.recorder.set_muted(muted, source="app")
        if not ok and self.tray:
            self.tray.showMessage("listener-meet", "Não consegui mutar o microfone no sistema; gravando silêncio mesmo assim.")

    def switch_source(self) -> None:
        if not self.recorder:
            return
        dlg = QDialog(self.widget)
        dlg.setWindowTitle("Trocar a fonte do canal R")
        from PySide6.QtWidgets import QDialogButtonBox, QVBoxLayout

        table = SourceTable(self.platform, dlg)
        table.start()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(table)
        lay.addWidget(buttons)
        dlg.resize(560, 320)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.recorder.switch_source(table.selected_spec())
        table.stop()

    def stop_recording(self) -> None:
        if not self.recorder:
            return
        rec = self.recorder
        self._timer.stop()
        stats = rec.stop()
        self.platform.mic_mute().set(False) if rec.muted else None
        self._teardown_widget()
        self.recorder = None
        self.state = "idle"
        if self.tray:
            self.tray.set_state("idle")
        SummaryDialog(self.store, rec.meeting_id, stats).exec()

    def cancel_recording(self) -> None:
        if not self.recorder:
            return
        r = QMessageBox.question(self.widget, "Cancelar gravação", "Descartar esta gravação? Isso apaga o áudio gravado até agora.")
        if r != QMessageBox.StandardButton.Yes:
            return
        rec = self.recorder
        self._timer.stop()
        rec.cancel()
        if rec.muted:
            self.platform.mic_mute().set(False)
        self._teardown_widget()
        self.recorder = None
        self.state = "idle"
        if self.tray:
            self.tray.set_state("idle")

    def _teardown_widget(self) -> None:
        if self.widget:
            self.widget.close()
            self.widget.deleteLater()
            self.widget = None

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        rec, w = self.recorder, self.widget
        if not rec or not w:
            return
        health = rec.health()
        w.update_state(rec.elapsed(), rec.levels(), health, rec.paused, rec.muted, rec.markers)
        if health.get("R") == "alert":
            w.show_alert("saída sem sinal: a reunião está tocando em outro app?", can_switch=True)
        elif health.get("L") == "alert":
            w.show_alert("microfone sem sinal há mais de %ds" % int(rec.cfg.l_alert_seconds))
        else:
            w.show_alert(None)
        now = time.monotonic()
        if now - self._last_os_mute_check > 2.0:
            self._last_os_mute_check = now
            os_muted = self.platform.mic_mute().get()
            if os_muted is not None and os_muted != rec.muted:
                rec.set_muted(os_muted, source="dispositivo")

    # ---------------------------------------------------------------- outros
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.store, self.platform, self.widget)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.register_hotkeys()
            if self.recorder:
                cfg = self.recorder.cfg
                cfg.silence_rms = self.store.get_float("capture.silence_rms")
                cfg.l_alert_seconds = self.store.get_float("capture.l_alert_seconds")
                cfg.r_alert_seconds = self.store.get_float("capture.r_alert_seconds")

    def quit(self) -> None:
        if self.recorder:
            r = QMessageBox.question(self.widget, "Sair", "Há uma gravação em andamento. Parar e salvar antes de sair?")
            if r == QMessageBox.StandardButton.Yes:
                self.stop_recording()
            else:
                return
        try:
            self.platform.hotkeys().stop()
        except Exception:
            pass
        self.store.close()
        self.app.quit()

    def _saved_widget_pos(self) -> QPoint | None:
        raw = self.store.get_setting("ui.widget_pos")
        try:
            x, y = (int(v) for v in raw.split(","))
            return QPoint(x, y)
        except Exception:
            return None

    def _saved_widget_size(self) -> tuple[int, int]:
        raw = self.store.get_setting("ui.widget_size") or "320x96"
        try:
            w, h = (int(v) for v in raw.lower().split("x"))
            return (w, h)
        except Exception:
            return (320, 96)
