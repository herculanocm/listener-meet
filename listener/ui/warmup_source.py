"""O "Antes de gravar" (`ui/warmup.py`), a parte da escolha: os presets e a última escolha (microfone, fonte e sala). Saiu do
`warmup.py` (659 linhas) para ele não crescer; `SourceChoiceMixin` é parte do `WarmupDialog` e lê os campos dele."""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtWidgets import QCheckBox, QComboBox, QInputDialog, QMessageBox, QWidget

from ..log import get_logger
from ..model import SourceKind
from ..platform.base import log_id, pick_device
from .sources import SYSTEM_FOLLOW, SourceKey, SourceTable

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, Platform
    from ..store import Store
    from .warmup_live import LiveBoxes

log = get_logger("ui.warmup")


class SourceChoiceMixin:
    """Presets e última escolha, no `WarmupDialog`."""
    platform: Platform
    store: Store
    preset_combo: QComboBox
    mic_combo: QComboBox
    sources: SourceTable
    room_check: QCheckBox
    live: LiveBoxes
    live_check: QCheckBox
    names_check: QCheckBox
    _devices: list[AudioDevice]
    _pending_source: tuple[str, str] | None

    def _set_mic_note(self, text: str, kind: str = "warning") -> None:
        raise NotImplementedError

    def _selected_device(self) -> AudioDevice | None:
        raise NotImplementedError

    def _window(self) -> QWidget:
        return cast("QWidget", self)

    # ---------------------------------------------------------------- presets e última escolha
    def _restore_last_choice(self) -> None:
        last_preset = self.store.get_setting("warmup.last_preset_id")
        if last_preset:
            i = self.preset_combo.findData(int(last_preset))
            if i >= 0:
                self.preset_combo.setCurrentIndex(i)
                return
        self._select_mic(self.store.get_setting("warmup.last_mic_device_id"), self.store.get_setting("warmup.last_mic_device_name"))
        self._select_source(self.store.get_setting("warmup.last_source_kind"), self.store.get_setting("warmup.last_source_exe"),
                            self.store.get_setting("warmup.last_output_id"))
        self.room_check.setChecked(self.store.get_setting("warmup.last_in_room") == "1")

    def _select_mic(self, device_id: str, device_name: str) -> bool:
        """Seleciona o microfone salvo (id estável, depois nome); sem nenhum salvo, o "Padrão do Windows (acompanha)" (F5.3,
        pergunta 11); o salvo que sumiu avisa e cai nele (F1.1 A3). Log com o id, nunca o nome do aparelho (A-22)."""
        if device_id or device_name:
            dev = pick_device(self._devices, device_id or None, device_name or None)
            if dev is not None:
                self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(dev.id)))
                self._set_mic_note("")
                return True
            self._set_mic_note(f"O microfone salvo ({device_name or device_id}) não está nesta máquina; usando o padrão do "
                               "Windows.")
            log.warning("microfone salvo %s não encontrado; usando o padrão", log_id(device_id))
        self.mic_combo.setCurrentIndex(0)
        return False

    def _select_source(self, kind: str, exe: str, output_id: str = "") -> None:
        """Aplica agora se a lista de apps já chegou; senão fica pendente para a primeira lista (sem chutar tempo). O sistema
        inteiro vai na saída salva (U-051) ou, sem ela, na padrão."""
        if kind == SourceKind.APP and exe and self.sources.select_exe(exe):
            self._pending_source = None
            return
        if kind == SourceKind.SYSTEM:
            if not (output_id and self.sources.select_key(SourceKey(SourceKind.SYSTEM, output_id=output_id))):
                self.sources.select_key(SYSTEM_FOLLOW)
            self._pending_source = None
            return
        self._pending_source = (kind, exe)

    def _apply_pending_source(self) -> None:
        if self._pending_source is not None:
            kind, exe = self._pending_source
            self._pending_source = None
            if not (kind == SourceKind.APP and exe and self.sources.select_exe(exe)):
                self.sources.select_key(SYSTEM_FOLLOW)

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
        self.live.set_values(p.get("live_enabled"), p.get("live_names"))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self._window(), "Salvar preset", "Nome do preset:")
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
        QMessageBox.information(self._window(), "Preset", f"Preset “{name.strip()}” salvo.")

    def _save_last_choice(self) -> None:
        dev = self._selected_device()
        spec = self.sources.selected_spec()
        pid = self.preset_combo.currentData()
        self.store.set_setting("warmup.last_preset_id", pid if pid is not None else "")
        self.store.set_setting("warmup.last_mic_device_id", dev.id if dev else "")
        self.store.set_setting("warmup.last_mic_device_name", dev.name if dev else "")
        self.store.set_setting("warmup.last_source_kind", str(spec.kind))
        self.store.set_setting("warmup.last_source_exe", spec.exe or "")
        self.store.set_setting("warmup.last_output_id", spec.output_id)              # F5.3 (U-051): vazio = acompanha
        self.store.set_setting("warmup.last_in_room", "1" if self.room_check.isChecked() else "0")
