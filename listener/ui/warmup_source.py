"""O "Antes de gravar" (`ui/warmup.py`), a parte da escolha: os presets, a última escolha (microfone, fonte e sala) e, F5.7
(L3, U-094), a fonte vinda do aviso de reunião sem gravação. Saiu do `warmup.py` (659 linhas) para ele não crescer;
`SourceChoiceMixin` é parte do `WarmupDialog` e lê os campos dele.

F5.7: aberto pelo aviso (`source_hint`, o executável), a linha do app vira a fonte na primeira lista de apps tocando
(`select_exe`), uma faixa no topo diz "Fonte: Teams, pelo aviso de reunião. Confira os medidores e clique em Gravar." e, ao
aparecer, o foco vai para o Gravar e a janela vem à frente; o app que não está tocando troca a faixa por um aviso e a fonte
fica no sistema inteiro. Sempre, com ou sem o aviso (D-065, pergunta 6), a janela sai da captura de tela ao aparecer; se o
sistema recusa, segue normal e o log diz o tipo. O Esc e o Cancelar continuam fechando sem gravar: a cola do aviso
(`ui/call_watch.py`) vê que o app não está gravando e dispensa a chamada ("Agora não")."""
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QInputDialog, QMessageBox, QPushButton, QWidget

from ..log import get_logger
from ..model import SourceKind
from ..platform.base import log_id, pick_device
from .call_config import label_for
from .components import InfoBar
from .sources import SYSTEM_FOLLOW, SourceKey, SourceTable

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, Platform
    from ..store import Store
    from .warmup_live import LiveBoxes

log = get_logger("ui.warmup")
SOURCE_HINT = "Fonte: {label}, pelo aviso de reunião. Confira os medidores e clique em Gravar."
HINT_MISSING = "O {label} não está tocando agora: escolha a fonte na lista e clique em Gravar."


class SourceChoiceMixin:
    """Presets, última escolha e a fonte pelo aviso de reunião, no `WarmupDialog`."""
    platform: Platform
    store: Store
    preset_combo: QComboBox
    mic_combo: QComboBox
    sources: SourceTable
    room_check: QCheckBox
    live: LiveBoxes
    live_check: QCheckBox
    names_check: QCheckBox
    record_btn: QPushButton
    _devices: list[AudioDevice]
    _pending_source: tuple[str, str] | None
    source_hint: str | None = None                   # F5.7: o executável do app pelo aviso de reunião
    hint_bar: InfoBar | None = None
    _capture_checked = False

    def _set_mic_note(self, text: str, kind: str = "warning") -> None:
        raise NotImplementedError

    def _selected_device(self) -> AudioDevice | None:
        raise NotImplementedError

    def _window(self) -> QWidget:
        return cast("QWidget", self)

    # ---------------------------------------------------------------- F5.7: a fonte pelo aviso de reunião
    def _hint_label(self) -> str:
        return label_for(self.platform.watched_apps(), self.source_hint or "")

    def _apply_hint(self) -> None:
        """A linha do app como fonte (já ou na primeira lista) e a faixa no topo, por cima da última escolha."""
        if not self.source_hint:
            return
        self.hint_bar = InfoBar("info", SOURCE_HINT.format(label=self._hint_label()))
        layout: Any = self.layout()                  # type: ignore[attr-defined]
        layout.insertWidget(0, self.hint_bar)
        self._select_source(SourceKind.APP, self.source_hint)

    def _hint_missing(self, exe: str) -> None:
        if self.hint_bar is not None and self.source_hint and exe.lower() == self.source_hint.lower():
            self.hint_bar.set_kind("warning")
            self.hint_bar.set_text(HINT_MISSING.format(label=self._hint_label()))
            log.info("\"Antes de gravar\" pelo aviso de reunião: o app não está na lista; a fonte fica no sistema inteiro")

    def _on_shown(self) -> None:
        """Na primeira vez que aparece: fora da captura de tela (sempre); pelo aviso, o foco no Gravar e a janela à frente."""
        if self._capture_checked:
            return
        self._capture_checked = True
        win_id = int(self.winId())                   # type: ignore[attr-defined]
        try:
            reason = "" if self.platform.exclude_from_capture(win_id) else "o sistema recusou"
        except Exception as e:
            reason = type(e).__name__
        if reason:
            log.info("\"Antes de gravar\" segue na captura de tela (%s)", reason)
        if self.source_hint:
            self.record_btn.setFocus(Qt.FocusReason.OtherFocusReason)
            QTimer.singleShot(0, self.record_btn, self._to_front)

    def _to_front(self) -> None:
        with suppress(Exception):
            self.platform.bring_to_front(int(self.winId()))     # type: ignore[attr-defined]

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
                self._hint_missing(exe)

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
