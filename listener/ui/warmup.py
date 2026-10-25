"""Warm-up (CP6): microfone e fonte de saída, medidores, teste de 3 s com detecção de eco (U-016), preset, título.
O microfone abre uma vez só (no `showEvent`); a fonte salva é escolhida na primeira lista de apps (M-12)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..capture.align import estimate_offset
from ..capture.audio import resample, rms, to_mono
from ..capture.recorder import RecordConfig
from ..config.defaults import SPECS
from ..log import get_logger
from ..model import SourceKind
from ..platform.base import pick_device
from .meters import LevelMeter
from .sources import SourceTable

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, CaptureHandle, Platform
    from ..store import Store

log = get_logger("ui.warmup")


class WarmupDialog(QDialog):
    def __init__(self, platform: Platform, store: Store, parent: QWidget | None = None):
        super().__init__(parent)
        self.platform = platform
        self.store = store
        self.setWindowTitle("Antes de gravar")
        self.setMinimumWidth(760)
        self._mic_handle: CaptureHandle | None = None
        self._r_handle: CaptureHandle | None = None
        self._mic_level = 0.0
        self._test_buffer: list[np.ndarray] = []
        self._r_buffer: list[np.ndarray] = []
        self._testing = False
        self._pending_source: tuple[str, str] | None = None
        self._devices = platform.list_input_devices()

        # --- microfone ---
        mic_box = QGroupBox("Microfone (canal L: você)")
        self.mic_combo = QComboBox()
        for d in self._devices:
            self.mic_combo.addItem(d.name, d.id)
        self.mic_meter = LevelMeter(height=10)
        self.mic_status = QLabel("ouvindo…")
        self.mic_note = QLabel("")
        self.mic_note.setStyleSheet("color: #e67e22;")
        self.mic_note.setWordWrap(True)
        self.test_btn = QPushButton("Testar 3 s")
        self.test_btn.setToolTip("Grava 3 s do microfone e da saída escolhida, toca o microfone de volta e verifica eco.")
        self.test_btn.clicked.connect(self._test_mic)
        ml = QVBoxLayout(mic_box)
        ml.addWidget(self.mic_combo)
        ml.addWidget(self.mic_meter)
        row = QHBoxLayout()
        row.addWidget(self.mic_status)
        row.addStretch(1)
        row.addWidget(self.test_btn)
        ml.addLayout(row)
        ml.addWidget(self.mic_note)

        # --- saída ---
        src_box = QGroupBox("Saída (canal R: quem você ouve). ● = app de reunião")
        self.sources = SourceTable(platform)
        self.sources.selection_changed.connect(self._update_src_status)
        self.sources.first_apps_ready.connect(self._apply_pending_source)
        self.src_status = QLabel("")
        self.echo_label = QLabel("")
        self.echo_label.setWordWrap(True)
        sl = QVBoxLayout(src_box)
        sl.addWidget(self.sources)
        sl.addWidget(self.src_status)
        sl.addWidget(self.echo_label)

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
        self._restore_last_choice()
        self.mic_combo.currentIndexChanged.connect(lambda _: self._open_mic())   # só depois de restaurar: um open só
        self._update_src_status()

    # ---------------------------------------------------------------- ciclo
    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._mic_handle is None:
            self._open_mic()
        self.sources.start()
        self._timer.start()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown()
        super().closeEvent(event)

    @override
    def accept(self) -> None:
        self._save_last_choice()
        self._shutdown()
        super().accept()

    @override
    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        self._timer.stop()
        self.sources.stop()
        self._close_mic()
        self._close_r()

    # ---------------------------------------------------------------- mic
    def _selected_device(self) -> AudioDevice | None:
        i = self.mic_combo.currentIndex()
        return self._devices[i] if 0 <= i < len(self._devices) else None

    def _open_mic(self) -> None:
        self._close_mic()
        dev = self._selected_device()
        try:
            handle = self.platform.open_mic(dev, 16000, self._on_mic)
            handle.start()
            self._mic_handle = handle
            self.mic_status.setText("ouvindo…")
        except Exception as e:
            self._mic_handle = None
            self.mic_status.setText(f"microfone indisponível: {e}")
            log.warning("warm-up: microfone indisponível: %s", e)

    def _close_mic(self) -> None:
        if self._mic_handle is not None:
            try:
                self._mic_handle.stop()
            except Exception as e:
                log.debug("fechar mic do warm-up: %s", e)
            self._mic_handle = None

    def _close_r(self) -> None:
        if self._r_handle is not None:
            try:
                self._r_handle.stop()
            except Exception as e:
                log.debug("fechar saída do teste: %s", e)
            self._r_handle = None

    def _on_mic(self, block: np.ndarray, t_first: float) -> None:
        self._mic_level = rms(block)
        if self._testing:
            self._test_buffer.append(np.asarray(block, dtype=np.float32).reshape(-1))

    def _on_r_test(self, block: np.ndarray, t_first: float) -> None:
        if self._testing:
            self._r_buffer.append(np.asarray(block, dtype=np.float32))

    def _test_mic(self) -> None:
        if self._mic_handle is None or self._testing:
            return
        self._testing = True
        self._test_buffer = []
        self._r_buffer = []
        spec = self.sources.selected_spec()
        try:
            if spec.kind == SourceKind.APP and spec.pid:
                self._r_handle = self.platform.open_app_loopback(spec.pid, self._on_r_test)
            else:
                self._r_handle = self.platform.open_system_loopback(self._on_r_test)
            self._r_handle.start()
        except Exception as e:
            log.debug("teste: saída não abriu: %s", e)
            self._r_handle = None
        self.test_btn.setText("gravando…")
        self.test_btn.setEnabled(False)
        self.echo_label.setText("")
        QTimer.singleShot(3000, self._finish_test)

    def _finish_test(self) -> None:
        self._testing = False
        r_rate = self._r_handle.sample_rate if self._r_handle is not None else None
        self._close_r()
        self.test_btn.setText("Testar 3 s")
        self.test_btn.setEnabled(True)
        if not self._test_buffer:
            self.mic_status.setText("nada gravado no teste")
            return
        from ..store.playback import play

        audio = np.concatenate(self._test_buffer)
        self._check_echo(audio, r_rate)
        self.mic_status.setText(f"tocando o teste ({len(audio) / 16000:.1f} s)…")
        try:
            play(audio, 16000)
        except Exception as e:
            self.mic_status.setText(f"não consegui tocar: {e}")

    def _check_echo(self, mic: np.ndarray, r_rate: int | None) -> None:
        """U-016: o microfone está ouvindo a saída? Correlação cruzada entre os 3 s do mic e os 3 s da saída."""
        if not self._r_buffer or not r_rate:
            self.echo_label.setStyleSheet("color: #888;")
            self.echo_label.setText("Eco: a saída não entregou áudio durante o teste (nada tocando?).")
            return
        r = resample(to_mono(np.concatenate(self._r_buffer)), r_rate, 16000)
        res = estimate_offset(mic, r, 16000)
        if res is None:
            self.echo_label.setStyleSheet("color: #888;")
            self.echo_label.setText("Eco: sinal insuficiente para testar (fale e deixe a saída tocando).")
            return
        corr, limit = res["correlation"], self.store.get(SPECS["capture.echo_corr_min"])
        log.info("teste de eco: correlação %.3f, atraso %.0f ms, limite %.2f", corr, res["offset_ms"], limit)
        if corr >= limit:
            self.echo_label.setStyleSheet("color: #e67e22;")
            self.echo_label.setText(f"Eco: seu microfone está ouvindo a reunião (correlação {corr:.2f}, atraso aproximado "
                                    f"{res['offset_ms']:.0f} ms). Use fone ou marque 'Sala de reunião'.")
        else:
            self.echo_label.setStyleSheet("color: #2ecc71;")
            self.echo_label.setText(f"Sem eco: o microfone não ouve a saída (correlação {corr:.2f}).")

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        self.mic_meter.set_rms(self._mic_level)
        if self._mic_handle is not None:
            if self._mic_level < 0.002:
                self._silent_ticks += 1
                if self._silent_ticks == 51:
                    self.mic_status.setText("sem sinal no microfone há 5 s")
            else:
                if self._silent_ticks > 50 and not self._testing:
                    self.mic_status.setText("ouvindo…")
                self._silent_ticks = 0

    def _update_src_status(self) -> None:
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
        self._select_source(self.store.get_setting("warmup.last_source_kind"), self.store.get_setting("warmup.last_source_exe"))
        self.room_check.setChecked(self.store.get_setting("warmup.last_in_room") == "1")

    def _select_mic(self, device_id: str, device_name: str) -> bool:
        """Seleciona o microfone salvo (id estável, depois nome); se sumiu, avisa e cai no padrão (F1.1 A3)."""
        if device_id or device_name:
            dev = pick_device(self._devices, device_id or None, device_name or None)
            if dev is not None:
                self.mic_combo.setCurrentIndex(self._devices.index(dev))
                self.mic_note.setText("")
                return True
            self.mic_note.setText(f"O microfone salvo ({device_name or device_id}) não está nesta máquina; usando o padrão.")
            log.warning("microfone salvo %r não encontrado; usando o padrão", device_name or device_id)
        default = self.platform.default_input_device()
        if default:
            for i, d in enumerate(self._devices):
                if d.id == default.id:
                    self.mic_combo.setCurrentIndex(i)
                    break
        return False

    def _select_source(self, kind: str, exe: str) -> None:
        """Aplica agora se a lista de apps já chegou; senão fica pendente para a primeira lista (sem chutar tempo)."""
        if kind == SourceKind.APP and exe and self.sources.select_exe(exe):
            self._pending_source = None
            return
        if kind == SourceKind.SYSTEM:
            self.sources.select_pid(-1)
            self._pending_source = None
            return
        self._pending_source = (kind, exe)

    def _apply_pending_source(self) -> None:
        if self._pending_source is not None:
            kind, exe = self._pending_source
            self._pending_source = None
            if not (kind == SourceKind.APP and exe and self.sources.select_exe(exe)):
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
        self.store.set_setting("warmup.last_source_kind", str(spec.kind))
        self.store.set_setting("warmup.last_source_exe", spec.exe or "")
        self.store.set_setting("warmup.last_in_room", "1" if self.room_check.isChecked() else "0")

    # ---------------------------------------------------------------- resultado
    def result_config(self) -> tuple[RecordConfig, dict[str, Any]]:
        cfg = RecordConfig.from_settings(self.store, self._selected_device(), self.sources.selected_spec())
        spec = cfg.source
        platform_name = (spec.exe or "sistema").removesuffix(".exe") if spec.kind == SourceKind.APP else "sistema"
        fields = {
            "title": self.title_edit.text().strip() or None,
            "participants": self.participants_edit.text().strip() or None,
            "platform": platform_name,
            "preset_id": self.preset_combo.currentData(),
            "is_in_room": self.room_check.isChecked(),
        }
        return cfg, fields
