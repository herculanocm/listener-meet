"""Configurações mínimas da F1 (CP8 parcial): captura, atalhos (com estado do registro, F1.1 A13), presets,
log em arquivo e nível (D-033), pasta do banco. Cada item mostra se aplica agora ou só na próxima gravação."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..config.defaults import DEFAULTS, LABELS
from ..log import LEVELS
from ..platform.win32.hotkeys import parse_combo   # função pura: valida a sintaxe em qualquer sistema

SCOPE_TEXT = {"now": "aplica agora", "next_recording": "na próxima gravação"}


class SettingsDialog(QDialog):
    def __init__(self, store, platform, hotkey_errors: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.hotkey_errors = hotkey_errors or {}
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(620)
        self._edits: dict[str, QLineEdit] = {}
        lay = QVBoxLayout(self)

        cap = QGroupBox("Captura")
        capf = QFormLayout(cap)
        for key in ("capture.chunk_seconds", "capture.target_rate", "capture.mic_rate", "capture.silence_rms",
                    "capture.l_alert_seconds", "capture.r_alert_seconds", "capture.echo_corr_min"):
            capf.addRow(LABELS[key], self._row(key))
        lay.addWidget(cap)

        hk = QGroupBox("Atalhos globais (ex.: ctrl+alt+shift+r; ctrl+alt sozinho é AltGr no teclado ABNT2)")
        hkf = QFormLayout(hk)
        for key in ("hotkeys.start", "hotkeys.pause", "hotkeys.stop", "hotkeys.mark", "hotkeys.mute"):
            hkf.addRow(LABELS[key], self._row(key, status=self._hotkey_status(key)))
        lay.addWidget(hk)

        lg = QGroupBox("Log (aplica agora)")
        lgf = QFormLayout(lg)
        self.log_path = QLineEdit(store.get_setting("log.path") or str(store.default_log_path()))
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse_log)
        prow = QHBoxLayout()
        prow.addWidget(self.log_path, 1)
        prow.addWidget(browse)
        self.log_level = QComboBox()
        self.log_level.addItems(list(LEVELS))
        self.log_level.setCurrentText(store.get_setting("log.level").upper() or "INFO")
        lgf.addRow(LABELS["log.path"], prow)
        lgf.addRow(LABELS["log.level"], self.log_level)
        lay.addWidget(lg)

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
        dbl.addWidget(QLabel(f"Pasta: {store.base_dir} (esquema v{store.schema_version()})"))
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

    def _hotkey_status(self, key: str) -> str:
        combo = self.store.get_setting(key)
        if combo in self.hotkey_errors:
            return f"falhou: {self.hotkey_errors[combo]}"
        return "registrado" if combo else ""

    def _row(self, key: str, status: str = "") -> QWidget:
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(self.store.get_setting(key))
        self._edits[key] = edit
        scope = QLabel(SCOPE_TEXT.get(DEFAULTS[key][1], ""))
        scope.setStyleSheet("color: #888; font-size: 11px;")
        box.addWidget(edit, 1)
        box.addWidget(scope)
        if status:
            st = QLabel(status)
            st.setStyleSheet("color: %s; font-size: 11px;" % ("#e74c3c" if status.startswith("falhou") else "#2ecc71"))
            box.addWidget(st)
        holder = QWidget()
        holder.setLayout(box)
        return holder

    def _browse_log(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, "Arquivo de log", self.log_path.text(), "Log (*.log);;Todos (*)")
        if f:
            self.log_path.setText(f)

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
            if key.startswith("hotkeys.") and value:
                try:
                    parse_combo(value)
                except ValueError as e:
                    QMessageBox.warning(self, "Configurações", f"{LABELS[key]}: {e}")
                    return
        combos = [e.text().strip() for k, e in self._edits.items() if k.startswith("hotkeys.") and e.text().strip()]
        if len(set(c.lower() for c in combos)) != len(combos):
            QMessageBox.warning(self, "Configurações", "Dois atalhos iguais.")
            return
        if not self.log_path.text().strip():
            QMessageBox.warning(self, "Configurações", "Informe o arquivo de log.")
            return
        for key, edit in self._edits.items():
            self.store.set_setting(key, edit.text().strip())
        self.store.set_setting("log.path", self.log_path.text().strip())
        self.store.set_setting("log.level", self.log_level.currentText())
        self.accept()
