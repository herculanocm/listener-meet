"""Configurações mínimas da F1 (CP8 parcial): captura, atalhos, presets, pasta do banco.
Cada item mostra se aplica agora ou só na próxima gravação."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton, QVBoxLayout)

from ..config.defaults import DEFAULTS, LABELS

SCOPE_TEXT = {"now": "aplica agora", "next_recording": "na próxima gravação"}


class SettingsDialog(QDialog):
    def __init__(self, store, platform, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(560)
        self._edits: dict[str, QLineEdit] = {}
        lay = QVBoxLayout(self)

        cap = QGroupBox("Captura")
        capf = QFormLayout(cap)
        for key in ("capture.chunk_seconds", "capture.target_rate", "capture.mic_rate", "capture.silence_rms",
                    "capture.l_alert_seconds", "capture.r_alert_seconds"):
            capf.addRow(LABELS[key], self._row(key))
        lay.addWidget(cap)

        hk = QGroupBox("Atalhos globais (ex.: ctrl+shift+r)")
        hkf = QFormLayout(hk)
        for key in ("hotkeys.start", "hotkeys.pause", "hotkeys.stop", "hotkeys.mark", "hotkeys.mute"):
            hkf.addRow(LABELS[key], self._row(key))
        lay.addWidget(hk)

        pr = QGroupBox("Presets")
        prl = QVBoxLayout(pr)
        self.presets = QListWidget()
        for p in store.list_presets():
            self.presets.addItem(f"{p['id']}: {p['name']} — {p['source_kind']} {p.get('source_exe') or ''}")
        del_btn = QPushButton("Apagar preset selecionado")
        del_btn.clicked.connect(self._delete_preset)
        prl.addWidget(self.presets)
        prl.addWidget(del_btn)
        lay.addWidget(pr)

        db = QGroupBox("Banco de dados")
        dbl = QVBoxLayout(db)
        dbl.addWidget(QLabel(f"Pasta: {store.base_dir}"))
        synced = platform.synced_folder_name(store.base_dir)
        if synced:
            w = QLabel(f"Atenção: esta pasta está dentro do {synced}; o banco sairá da máquina pela sincronização.")
            w.setStyleSheet("color: #e67e22;")
            dbl.addWidget(w)
        dbl.addWidget(QLabel("Mover o banco e o backup entram na fase das Configurações completas (F6)."))
        lay.addWidget(db)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _row(self, key: str):
        box = QHBoxLayout()
        edit = QLineEdit(self.store.get_setting(key))
        self._edits[key] = edit
        scope = QLabel(SCOPE_TEXT.get(DEFAULTS[key][1], ""))
        scope.setStyleSheet("color: #888; font-size: 11px;")
        box.addWidget(edit, 1)
        box.addWidget(scope)
        holder = QLabel()
        holder.setLayout(box)
        return holder

    def _delete_preset(self) -> None:
        it = self.presets.currentItem()
        if not it:
            return
        pid = int(it.text().split(":", 1)[0])
        self.store.delete_preset(pid)
        self.presets.takeItem(self.presets.row(it))

    def _save(self) -> None:
        for key, edit in self._edits.items():
            value = edit.text().strip()
            if key.startswith("capture."):
                try:
                    float(value)
                except ValueError:
                    QMessageBox.warning(self, "Configurações", f"{LABELS[key]}: valor inválido ({value}).")
                    return
            self.store.set_setting(key, value)
        self.accept()
