"""Configurações mínimas da F1 (CP8 parcial): captura, atalhos (com estado do registro, F1.1 A13), presets,
log em arquivo e nível (D-033), pasta do banco; F2: grupo "Processamento" gerado da tabela (`pipeline.*`) e o token do
Hugging Face em `providers` (Q2, CP10). Cada item mostra se aplica agora ou só na próxima gravação.
Validação pela tabela `SPECS` (M-24) e pela gramática de atalhos da plataforma (M-25)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..config.defaults import CAPTURE_KEYS, HF_PROVIDER_ID, HOTKEY_KEYS, LOG_LEVELS, PIPELINE_KEYS, SPECS, validate_setting
from ..model import SettingScope

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

SCOPE_TEXT = {SettingScope.NOW: "aplica agora", SettingScope.NEXT_RECORDING: "na próxima gravação"}


class SettingsDialog(QDialog):
    def __init__(self, store: Store, platform: Platform, hotkey_errors: dict[str, str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.platform = platform
        self.hotkey_errors = hotkey_errors or {}
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(620)
        self._edits: dict[str, QLineEdit] = {}
        self._combos: dict[str, QComboBox] = {}
        lay = QVBoxLayout(self)

        cap = QGroupBox("Captura")
        capf = QFormLayout(cap)
        for key in CAPTURE_KEYS:
            capf.addRow(SPECS[key].label, self._row(key))
        lay.addWidget(cap)

        hk = QGroupBox("Atalhos globais (ex.: ctrl+alt+shift+r; ctrl+alt sozinho é AltGr no teclado ABNT2)")
        hkf = QFormLayout(hk)
        for key in HOTKEY_KEYS:
            hkf.addRow(SPECS[key].label, self._row(key, status=self._hotkey_status(key)))
        lay.addWidget(hk)

        proc = QGroupBox("Processamento (F2: vale para o próximo job da fila)")
        procf = QFormLayout(proc)
        for key in PIPELINE_KEYS:
            procf.addRow(SPECS[key].label, self._row(key))
        self.hf_token = QLineEdit(store.settings.provider_key(HF_PROVIDER_ID))
        self.hf_token.setPlaceholderText("hf_… (necessário para baixar os modelos do pyannote)")
        procf.addRow("Token do Hugging Face", self.hf_token)
        lay.addWidget(proc)

        lg = QGroupBox("Log (aplica agora)")
        lgf = QFormLayout(lg)
        self.log_path = QLineEdit(store.get_setting("log.path") or str(store.default_log_path()))
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse_log)
        prow = QHBoxLayout()
        prow.addWidget(self.log_path, 1)
        prow.addWidget(browse)
        self.log_level = QComboBox()
        self.log_level.addItems(list(LOG_LEVELS))
        self.log_level.setCurrentText(store.get_setting("log.level").upper() or "INFO")
        lgf.addRow(SPECS["log.path"].label, prow)
        lgf.addRow(SPECS["log.level"].label, self.log_level)
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
        dbl.addWidget(QLabel(f"Pasta: {store.base_dir} (esquema v{store.schema_version()}; "
                             f"{len(store.audio_files())} arquivo(s) de áudio, um por mês)"))
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
        spec = SPECS[key]
        field: QWidget
        if spec.choices is not None and all(isinstance(c, str) for c in spec.choices):
            combo = QComboBox()
            for choice in sorted(spec.choices):
                combo.addItem(choice)
                if key == "pipeline.transcriber" and choice == "llm":            # F3: ainda não existe
                    combo.model().item(combo.count() - 1).setEnabled(False)  # type: ignore[attr-defined]
            combo.setCurrentText(self.store.get_setting(key))
            self._combos[key] = combo
            field = combo
        else:
            edit = QLineEdit(self.store.get_setting(key))
            self._edits[key] = edit
            field = edit
        scope = QLabel(SCOPE_TEXT.get(spec.scope, ""))
        scope.setStyleSheet("color: #888; font-size: 11px;")
        box.addWidget(field, 1)
        box.addWidget(scope)
        if status:
            st = QLabel(status)
            st.setStyleSheet("color: {}; font-size: 11px;".format("#e74c3c" if status.startswith("falhou") else "#2ecc71"))
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

    def validation_error(self) -> str | None:
        """Primeiro erro dos campos (texto em PT-BR) ou None; separado do `_save` para os testes."""
        for key, edit in self._edits.items():
            value = edit.text().strip()
            if key in HOTKEY_KEYS:
                if value:
                    err = self.platform.hotkeys().validate(value)
                    if err:
                        return f"{SPECS[key].label}: {err}"
                continue
            err = validate_setting(key, value)
            if err:
                return err
        combos = [e.text().strip().lower() for k, e in self._edits.items() if k in HOTKEY_KEYS and e.text().strip()]
        if len(set(combos)) != len(combos):
            return "Dois atalhos iguais."
        if not self.log_path.text().strip():
            return "Informe o arquivo de log."
        return None

    def _save(self) -> None:
        err = self.validation_error()
        if err:
            QMessageBox.warning(self, "Configurações", err)
            return
        for key, edit in self._edits.items():
            self.store.set_setting(key, edit.text().strip())
        for key, combo in self._combos.items():
            self.store.set_setting(key, combo.currentText())
        self.store.set_setting("log.path", self.log_path.text().strip())
        self.store.set_setting("log.level", self.log_level.currentText())
        token = self.hf_token.text().strip()
        if token != self.store.settings.provider_key(HF_PROVIDER_ID):
            self.store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key=token or None)
        self.accept()
