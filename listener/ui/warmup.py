"""Warm-up (CP6): microfone e fonte de saída, medidores, teste de 3 s com detecção de eco (U-016), preset, título.
O microfone abre uma vez só (no `showEvent`); a fonte salva é escolhida na primeira lista de apps (M-12).
F3 (item 4, U-018): ao sair do título ou dos participantes, com 1,5 s de debounce, uma `Task` casa os participantes com
`people` e pede à LLM os termos prováveis; uma chamada em voo, resposta velha descartada; o campo "Termos prováveis" fica
editável e vai para `meetings.glossary`; sem chave, aviso discreto com link para as Configurações. Os participantes esperados
saem do casamento local mesmo sem a LLM, e uma resposta que chega depois do "Gravar" religa a chamada à reunião (F3.1 R3-11).
F4 (CP6): caixas "Legenda ao vivo" e "Nomes ao vivo" (a segunda só com a primeira); valor inicial = o do preset quando não
nulo, senão `live.enabled` e `live.names`; "Salvar como preset" grava as duas; modelos ausentes viram um aviso discreto ao
lado, com link para as Configurações, sem bloquear a gravação (o controlador grava sem ao vivo)."""
from __future__ import annotations

import functools
import json
from collections.abc import Callable
from contextlib import suppress
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
from ..llm.glossary_task import GlossaryResult, match_people_locally, merge_glossary, request_glossary, split_names
from ..log import get_logger
from ..model import SourceKind
from ..platform.base import pick_device
from .live_prepare import missing_models, preparer
from .meters import LevelMeter
from .sources import SourceTable
from .tasks import run_task

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, CaptureHandle, Platform
    from ..store import Store

log = get_logger("ui.warmup")
GLOSSARY_DEBOUNCE_MS = 1500


class WarmupDialog(QDialog):
    def __init__(self, platform: Platform, store: Store, parent: QWidget | None = None, on_settings: Any = None,
                 on_late_call: Callable[[int], Any] | None = None):
        super().__init__(parent)
        self.platform = platform
        self.store = store
        self.on_settings = on_settings
        self.on_late_call = on_late_call                 # chamada do glossário que volta depois do "Gravar" (F3.1 R3-11)
        self.glossary_result: GlossaryResult | None = None
        self.glossary_call_ids: list[int] = []           # toda chamada feita aqui, velha ou não, é da reunião que nascer
        self._accepted = False
        self._glossary_seq = 0
        self._glossary_inflight = False
        self._glossary_pending = False
        self.glossary_requests = 0                       # instrumentação dos testes
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
        self.live_check = QCheckBox("Legenda ao vivo")
        self.live_check.setToolTip("Legenda de quem fala durante a gravação, com 5 a 10 s de atraso (Configurações, grupo Ao vivo).")
        self.names_check = QCheckBox("Nomes ao vivo")
        self.names_check.setToolTip("Nome e cargo de quem tem perfil de voz na legenda (carrega o modelo de voz: mais memória).")
        self.live_note = QLabel("")
        self.live_note.setStyleSheet("color: #e67e22; font-size: 11px;")
        self.live_note.linkActivated.connect(lambda _link: self._open_settings_for_live())
        self.live_check.toggled.connect(self._live_toggled)
        self._preparer = preparer()                       # download pelo link das Configurações: o aviso some quando terminar
        self._preparer.changed.connect(self._on_models_changed)
        self._preparer_connected = True
        self.glossary_edit = QLineEdit()
        self.glossary_edit.setPlaceholderText("termos prováveis para a transcrição (a LLM sugere; edite à vontade)")
        self.glossary_status = QLabel("")
        self.glossary_status.setStyleSheet("color: #888; font-size: 11px;")
        self.glossary_status.setOpenExternalLinks(False)
        self.glossary_status.linkActivated.connect(lambda _link: self.on_settings() if self.on_settings else None)
        fl = QFormLayout(meet_box)
        prow = QHBoxLayout()
        prow.addWidget(self.preset_combo, 1)
        prow.addWidget(self.save_preset_btn)
        fl.addRow("Preset", prow)
        fl.addRow("Título", self.title_edit)
        fl.addRow("Participantes", self.participants_edit)
        fl.addRow("Termos prováveis", self.glossary_edit)
        fl.addRow("", self.glossary_status)
        fl.addRow("", self.room_check)
        lrow = QHBoxLayout()
        lrow.addWidget(self.live_check)
        lrow.addWidget(self.names_check)
        lrow.addWidget(self.live_note, 1)
        fl.addRow("Ao vivo", lrow)
        self._glossary_timer = QTimer(self)
        self._glossary_timer.setSingleShot(True)
        self._glossary_timer.setInterval(GLOSSARY_DEBOUNCE_MS)
        self._glossary_timer.timeout.connect(self.request_glossary)
        self.title_edit.editingFinished.connect(self._glossary_timer.start)
        self.participants_edit.editingFinished.connect(self._glossary_timer.start)

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
        self._set_live(None, None)                          # padrão das Configurações; um preset salvo sobrescreve
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
        self._accepted = True
        self._save_last_choice()
        self._shutdown()
        super().accept()

    @override
    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        self._timer.stop()
        self._glossary_timer.stop()
        if self._preparer_connected:                     # `_shutdown` roda mais de uma vez (aceitar e depois fechar)
            self._preparer_connected = False
            with suppress(RuntimeError, TypeError):
                self._preparer.changed.disconnect(self._on_models_changed)
        self._glossary_seq += 1                            # resposta que chegar depois é descartada
        self.sources.stop()
        self._close_mic()
        self._close_r()

    # ---------------------------------------------------------------- glossário (F3 item 4)
    def request_glossary(self) -> None:
        """Uma chamada em voo; mudança durante a chamada agenda outra; nada sem título nem participantes."""
        title, participants = self.title_edit.text().strip(), self.participants_edit.text().strip()
        if not title and not participants:
            return
        if self._glossary_inflight:
            self._glossary_pending = True
            return
        self._glossary_seq += 1
        self._glossary_inflight = True
        self.glossary_requests += 1
        self.glossary_status.setText("pedindo termos prováveis à LLM…")
        run_task(functools.partial(request_glossary, self.store, title or None, participants or None), self.store,
                 functools.partial(self._on_glossary, self._glossary_seq))

    def _on_glossary(self, seq: int, result: Any) -> None:
        self._glossary_inflight = False
        call_id = result.call_id if isinstance(result, GlossaryResult) else None
        if call_id is not None:
            if self._accepted:                             # "Gravar" já criou a reunião: o controlador religa a chamada
                if self.on_late_call is not None:
                    self.on_late_call(call_id)
            elif call_id not in self.glossary_call_ids:
                self.glossary_call_ids.append(call_id)
        if seq != self._glossary_seq:                      # velha: o usuário mudou o texto ou fechou
            return
        if self._glossary_pending:
            self._glossary_pending = False
            self._glossary_timer.start()
        if isinstance(result, BaseException):
            log.warning("glossário indisponível: %s", result)
            self.glossary_status.setText(f"glossário indisponível: {result}")
            return
        self.glossary_result = result
        if result.error:
            text = result.error
            if text.startswith("sem chave"):
                text = 'sem chave de LLM: <a href="settings">configurar provedor</a>'
            self.glossary_status.setText(text)
            return
        self.glossary_edit.setText(merge_glossary(result.terms, self.glossary_edit.text()))
        people = f"; {len(result.people)} participante(s) com ficha" if result.people else ""
        self.glossary_status.setText(f"{len(result.terms)} termo(s) sugeridos pela LLM{people}")

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

    # ---------------------------------------------------------------- ao vivo (F4)
    def _set_live(self, enabled: int | bool | None, names: int | bool | None) -> None:
        """Caixas do ao vivo: o valor do preset quando não nulo, senão o padrão das Configurações."""
        self.live_check.setChecked(bool(self.store.get(SPECS["live.enabled"]) if enabled is None else enabled))
        self.names_check.setChecked(bool(self.store.get(SPECS["live.names"]) if names is None else names))
        self._live_toggled(self.live_check.isChecked())

    def _live_toggled(self, on: bool) -> None:
        self.names_check.setEnabled(on)
        self._update_live_note()

    def _update_live_note(self) -> None:
        """Aviso discreto quando faltam os modelos do ao vivo (a gravação segue sem ele, com um aviso do controlador)."""
        missing = missing_models(self.store) if self.live_check.isChecked() else []
        self.live_note.setText(f'modelos ausentes ({", ".join(missing)}): <a href="settings">prepare nas Configurações, Ao vivo</a>'
                               if missing else "")

    def _on_models_changed(self, state: Any) -> None:
        if isinstance(state, dict) and state.get("state") in ("done", "error"):
            self._update_live_note()

    def _open_settings_for_live(self) -> None:
        if self.on_settings is not None:
            self.on_settings()
        self._update_live_note()

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
        self._set_live(p.get("live_enabled"), p.get("live_names"))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Salvar preset", "Nome do preset:")
        if not ok or not name.strip():
            return
        dev = self._selected_device()
        spec = self.sources.selected_spec()
        pid = self.store.upsert_preset(name.strip(), dev.id if dev else None, dev.name if dev else None,
                                       spec.kind, spec.exe or None, is_in_room=self.room_check.isChecked(),
                                       live_enabled=self.live_check.isChecked(), live_names=self.names_check.isChecked())
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
        result = self.glossary_result
        participants = self.participants_edit.text().strip()
        # casamento local sempre (R3-11): gravar com o glossário em voo não perde os participantes esperados
        ids = {pid for _name, pid in match_people_locally(split_names(participants), [dict(p) for p in self.store.people.list_people()])[0]}
        ids |= {pid for _name, pid in (result.people if result else ())}
        fields = {
            "title": self.title_edit.text().strip() or None,
            "participants": participants or None,
            "platform": platform_name,
            "preset_id": self.preset_combo.currentData(),
            "is_in_room": self.room_check.isChecked(),
            "glossary": self.glossary_edit.text().strip() or None,
            "expected_people_json": json.dumps(sorted(ids)) if ids else None,
            "glossary_call_ids": list(self.glossary_call_ids),
            "live_enabled": self.live_check.isChecked(),                  # F4: o controlador lê as duas (CP6)
            "live_names": self.names_check.isChecked(),
        }
        return cfg, fields
