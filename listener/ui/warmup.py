"""Warm-up (CP6): escolher microfone e fonte de saída, ver medidores, testar, preset, título e participantes."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from ..capture.audio import rms
from ..capture.recorder import RecordConfig
from .meters import LevelMeter
from .sources import SourceTable


class WarmupDialog(QDialog):
    def __init__(self, platform, store, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.store = store
        self.setWindowTitle("Antes de gravar")
        self.setMinimumWidth(760)
        self._mic_handle = None
        self._mic_level = 0.0
        self._test_buffer: list[np.ndarray] = []
        self._testing = False
        self._devices = platform.list_input_devices()

        # --- microfone ---
        mic_box = QGroupBox("Microfone (canal L: você)")
        self.mic_combo = QComboBox()
        for d in self._devices:
            self.mic_combo.addItem(d.name, d.id)
        self.mic_meter = LevelMeter(height=10)
        self.mic_status = QLabel("ouvindo…")
        self.test_btn = QPushButton("Testar 3 s")
        self.test_btn.clicked.connect(self._test_mic)
        ml = QVBoxLayout(mic_box)
        ml.addWidget(self.mic_combo)
        ml.addWidget(self.mic_meter)
        row = QHBoxLayout()
        row.addWidget(self.mic_status)
        row.addStretch(1)
        row.addWidget(self.test_btn)
        ml.addLayout(row)

        # --- saída ---
        src_box = QGroupBox("Saída (canal R: quem você ouve). ● = app de reunião")
        self.sources = SourceTable(platform)
        self.src_status = QLabel("")
        sl = QVBoxLayout(src_box)
        sl.addWidget(self.sources)
        sl.addWidget(self.src_status)

        # --- reunião ---
        meet_box = QGroupBox("Reunião (opcional)")
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(último usado)", None)
        for p in store.list_presets():
            self.preset_combo.addItem(p["name"], p["id"])
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.save_preset_btn = QPushButton("Salvar como preset…")
        self.save_preset_btn.clicked.connect(self._save_preset)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("título da reunião")
        self.participants_edit = QLineEdit()
        self.participants_edit.setPlaceholderText("participantes esperados, separados por vírgula")
        self.room_check = QCheckBox("Sala de reunião presencial (o microfone ouve outras pessoas)")
        fl = QFormLayout(meet_box)
        prow = QHBoxLayout()
        prow.addWidget(self.preset_combo, 1)
        prow.addWidget(self.save_preset_btn)
        fl.addRow("Preset", prow)
        fl.addRow("Título", self.title_edit)
        fl.addRow("Participantes", self.participants_edit)
        fl.addRow("", self.room_check)

        # --- botões ---
        self.buttons = QDialogButtonBox()
        self.record_btn = self.buttons.addButton("Gravar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.record_btn.setDefault(True)
        self.record_btn.setMinimumHeight(40)
        self.buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(mic_box, 1)
        top.addWidget(src_box, 2)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(meet_box)
        lay.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._silent_ticks = 0
        self.mic_combo.currentIndexChanged.connect(lambda _: self._open_mic())
        self._restore_last_choice()

    # ---------------------------------------------------------------- ciclo
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._open_mic()
        self.sources.start()
        self._timer.start()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)

    def accept(self) -> None:
        self._save_last_choice()
        self._shutdown()
        super().accept()

    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        self._timer.stop()
        self.sources.stop()
        self._close_mic()

    # ---------------------------------------------------------------- mic
    def _selected_device(self):
        i = self.mic_combo.currentIndex()
        return self._devices[i] if 0 <= i < len(self._devices) else None

    def _open_mic(self) -> None:
        self._close_mic()
        dev = self._selected_device()
        try:
            self._mic_handle = self.platform.open_mic(dev, 16000, self._on_mic)
            self.mic_status.setText("ouvindo…")
        except Exception as e:
            self._mic_handle = None
            self.mic_status.setText(f"microfone indisponível: {e}")

    def _close_mic(self) -> None:
        if self._mic_handle is not None:
            try:
                self._mic_handle.stop()
            except Exception:
                pass
            self._mic_handle = None

    def _on_mic(self, block, t_first) -> None:
        self._mic_level = rms(block)
        if self._testing:
            self._test_buffer.append(np.asarray(block, dtype=np.float32).reshape(-1))

    def _test_mic(self) -> None:
        if self._mic_handle is None or self._testing:
            return
        self._testing = True
        self._test_buffer = []
        self.test_btn.setText("gravando…")
        self.test_btn.setEnabled(False)
        QTimer.singleShot(3000, self._finish_test)

    def _finish_test(self) -> None:
        self._testing = False
        self.test_btn.setText("Testar 3 s")
        self.test_btn.setEnabled(True)
        if not self._test_buffer:
            self.mic_status.setText("nada gravado no teste")
            return
        from ..store.playback import play

        audio = np.concatenate(self._test_buffer)
        self.mic_status.setText(f"tocando o teste ({len(audio) / 16000:.1f} s)…")
        try:
            play(audio, 16000)
        except Exception as e:
            self.mic_status.setText(f"não consegui tocar: {e}")

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        self.mic_meter.set_rms(self._mic_level)
        if self._mic_handle is not None:
            if self._mic_level < 0.002:
                self._silent_ticks += 1
                if self._silent_ticks > 50:
                    self.mic_status.setText("sem sinal no microfone há 5 s")
            else:
                self._silent_ticks = 0
                if not self._testing:
                    self.mic_status.setText("ouvindo…")
        spec = self.sources.selected_spec()
        self.src_status.setText(f"selecionado: {spec.label or spec.kind}")

    # ---------------------------------------------------------------- presets e última escolha
    def _restore_last_choice(self) -> None:
        last_preset = self.store.get_setting("warmup.last_preset_id")
        if last_preset:
            i = self.preset_combo.findData(int(last_preset))
            if i >= 0:
                self.preset_combo.setCurrentIndex(i)
                return
        self._select_mic(self.store.get_setting("warmup.last_mic_device_id"), self.store.get_setting("warmup.last_mic_device_name"))
        kind = self.store.get_setting("warmup.last_source_kind")
        exe = self.store.get_setting("warmup.last_source_exe")
        QTimer.singleShot(600, lambda: self._select_source(kind, exe))
        self.room_check.setChecked(self.store.get_setting("warmup.last_in_room") == "1")

    def _select_mic(self, device_id: str, device_name: str) -> None:
        for i, d in enumerate(self._devices):
            if device_id and d.id == device_id:
                self.mic_combo.setCurrentIndex(i)
                return
        for i, d in enumerate(self._devices):
            if device_name and d.name == device_name:
                self.mic_combo.setCurrentIndex(i)
                return
        default = self.platform.default_input_device()
        if default:
            for i, d in enumerate(self._devices):
                if d.id == default.id:
                    self.mic_combo.setCurrentIndex(i)
                    return

    def _select_source(self, kind: str, exe: str) -> None:
        if kind == "app" and exe and self.sources.select_exe(exe):
            return
        if kind == "system":
            self.sources.select_pid(-1)

    def _apply_preset(self, index: int) -> None:
        pid = self.preset_combo.itemData(index)
        if pid is None:
            return
        p = self.store.get_preset(int(pid))
        if not p:
            return
        self._select_mic(p.get("mic_device_id") or "", p.get("mic_device_name") or "")
        self._select_source(p["source_kind"], p.get("source_exe") or "")
        self.room_check.setChecked(bool(p.get("is_in_room")))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Salvar preset", "Nome do preset:")
        if not ok or not name.strip():
            return
        dev = self._selected_device()
        spec = self.sources.selected_spec()
        pid = self.store.upsert_preset(name.strip(), dev.id if dev else None, dev.name if dev else None,
                                       spec.kind, spec.exe or None, is_in_room=self.room_check.isChecked())
        if self.preset_combo.findData(pid) < 0:
            self.preset_combo.addItem(name.strip(), pid)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(pid))
        QMessageBox.information(self, "Preset", f"Preset “{name.strip()}” salvo.")

    def _save_last_choice(self) -> None:
        dev = self._selected_device()
        spec = self.sources.selected_spec()
        pid = self.preset_combo.currentData()
        self.store.set_setting("warmup.last_preset_id", pid if pid is not None else "")
        self.store.set_setting("warmup.last_mic_device_id", dev.id if dev else "")
        self.store.set_setting("warmup.last_mic_device_name", dev.name if dev else "")
        self.store.set_setting("warmup.last_source_kind", spec.kind)
        self.store.set_setting("warmup.last_source_exe", spec.exe or "")
        self.store.set_setting("warmup.last_in_room", "1" if self.room_check.isChecked() else "0")

    # ---------------------------------------------------------------- resultado
    def result_config(self) -> tuple[RecordConfig, dict]:
        s = self.store
        cfg = RecordConfig(
            mic_device=self._selected_device(),
            source=self.sources.selected_spec(),
            mic_rate=s.get_int("capture.mic_rate"),
            target_rate=s.get_int("capture.target_rate"),
            chunk_seconds=s.get_float("capture.chunk_seconds"),
            silence_rms=s.get_float("capture.silence_rms"),
            l_alert_seconds=s.get_float("capture.l_alert_seconds"),
            r_alert_seconds=s.get_float("capture.r_alert_seconds"),
        )
        spec = cfg.source
        platform_name = (spec.exe or "sistema").removesuffix(".exe") if spec.kind == "app" else "sistema"
        fields = {
            "title": self.title_edit.text().strip() or None,
            "participants": self.participants_edit.text().strip() or None,
            "platform": platform_name,
            "preset_id": self.preset_combo.currentData(),
            "is_in_room": self.room_check.isChecked(),
        }
        return cfg, fields
