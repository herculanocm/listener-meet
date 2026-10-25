"""Configurações mínimas da F1 (CP8 parcial): captura, atalhos (com estado do registro, F1.1 A13), presets,
log em arquivo e nível (D-033), pasta do banco; F2: grupo "Processamento" gerado da tabela (`pipeline.*`) e o token do
Hugging Face em `providers` (Q2, CP10); F3: grupo "LLM" gerado da tabela (`llm.*`, idioma das saídas como opção, P1) e as
sub-abas por provedor (`ProviderTabs`); o transcritor `llm` só habilita quando o modelo rápido do provedor ativo tem áudio.
F4: grupo "Ao vivo" gerado da tabela (`live.*`) e o botão "Preparar modelos do ao vivo" (`ModelPreparer`, um download por
vez no app inteiro, com progresso e o estado da pasta), desabilitado durante a gravação; o diálogo se desconecta do
preparador ao fechar, e o download segue.
Cada item mostra se aplica agora ou só na próxima gravação. Validação pela tabela `SPECS` (M-24) e pela gramática de
atalhos da plataforma (M-25)."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..config.defaults import (CAPTURE_KEYS, HF_PROVIDER_ID, HOTKEY_KEYS, LIVE_KEYS, LLM_KEYS, LOG_LEVELS, OUTPUT_LANGUAGES,
                               PIPELINE_KEYS, SPECS, validate_setting)
from ..llm.costs import AUDIO_TOKENS_PER_SECOND, fmt_usd, price_for
from ..llm.jobconfig import LlmJobConfig, audio_capable
from ..model import SettingScope
from .live_prepare import ModelPreparer, models_status_text, preparer
from .providers import ProviderTabs

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

SCOPE_TEXT = {SettingScope.NOW: "aplica agora", SettingScope.NEXT_RECORDING: "na próxima gravação"}


def audio_cost_text(store: Store) -> str:
    """"≈ US$ 0,12 por hora de áudio" com o preço de áudio do modelo rápido do provedor ativo (32 tokens/s, F3A-40)."""
    cfg = LlmJobConfig.from_store(store)
    price = price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, cfg.fast_model)
    if price is None:
        return "sem preço cadastrado para estimar o custo por hora de áudio"
    per_hour = AUDIO_TOKENS_PER_SECOND * 3600 * (price.audio_usd_m or price.input_usd_m) / 1_000_000
    return f"≈ {fmt_usd(per_hour)} por hora de áudio no modelo {cfg.fast_model}"


class SettingsDialog(QDialog):
    def __init__(self, store: Store, platform: Platform, hotkey_errors: dict[str, str] | None = None,
                 parent: QWidget | None = None, on_launch: Callable[[bool], Any] | None = None, *, recording: bool = False,
                 model_preparer: ModelPreparer | None = None):
        super().__init__(parent)
        self.store = store
        self.platform = platform
        self.hotkey_errors = hotkey_errors or {}
        self.recording = recording
        self.preparer = model_preparer or preparer()
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(680)
        self.setMinimumHeight(560)
        self._edits: dict[str, QLineEdit] = {}
        self._combos: dict[str, QComboBox] = {}
        self.audio_capable = audio_capable(store)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

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

        llm = QGroupBox("LLM (F3: vale para o próximo job de LLM e para o chat)")
        llmf = QFormLayout(llm)
        for key in LLM_KEYS:
            llmf.addRow(SPECS[key].label, self._row(key))
        self.provider_tabs = ProviderTabs(store, on_launch)
        llmf.addRow(self.provider_tabs)
        lay.addWidget(llm)

        live = QGroupBox("Ao vivo (F4: vale na próxima gravação; o intervalo do resumo parcial vale já)")
        livef = QFormLayout(live)
        for key in LIVE_KEYS:
            livef.addRow(SPECS[key].label, self._row(key))
        self.prepare_btn = QPushButton("Preparar modelos do ao vivo")
        self.prepare_btn.setToolTip("Baixa os modelos small e base da legenda ao vivo para a pasta de modelos (fora da gravação).")
        self.prepare_btn.clicked.connect(self.prepare_models)
        self.prepare_bar = QProgressBar()
        self.prepare_bar.setRange(0, 100)
        self.prepare_bar.setTextVisible(True)
        self.prepare_status = QLabel("")
        self.prepare_status.setStyleSheet("color: #888; font-size: 11px;")
        self.prepare_status.setWordWrap(True)
        prow = QHBoxLayout()
        prow.addWidget(self.prepare_btn)
        prow.addWidget(self.prepare_bar, 1)
        livef.addRow(prow)
        livef.addRow("Modelos", self.prepare_status)
        lay.addWidget(live)
        self.preparer.changed.connect(self._on_prepare_changed)
        self._preparer_connected = True
        self._on_prepare_changed(self.preparer.state)        # download em curso de uma abertura anterior: segue mostrando

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
        outer.addWidget(buttons)

    # ---------------------------------------------------------------- ao vivo (F4)
    def prepare_models(self) -> None:
        if self.recording or self.preparer.running:
            return
        self.preparer.start(self.store, self.platform.spawn_child)

    def _on_prepare_changed(self, state: Any) -> None:
        st = dict(state or {})
        running = st.get("state") == "running"
        self.prepare_btn.setEnabled(not running and not self.recording)
        self.prepare_bar.setVisible(running or st.get("state") == "done")
        self.prepare_bar.setValue(round(float(st.get("fraction") or 0.0) * 100))
        if running:
            model, status = str(st.get("model") or ""), str(st.get("status") or "iniciando…")
            text = f"{model}: {status}…" if model and status == "baixando" else (f"{model}: {status}" if model else status)
        elif st.get("state") == "error":
            text = f"falhou: {st.get('message') or 'erro'} · {models_status_text(self.store)}"
        else:
            text = models_status_text(self.store)
        if self.recording and not running:
            text += " · prepare depois de parar a gravação"
            self.prepare_btn.setToolTip("Desabilitado durante a gravação: o download disputaria a CPU e a rede com a reunião.")
        self.prepare_status.setText(text)

    @override
    def done(self, result: int) -> None:
        """Aceitar, cancelar ou fechar: solta o preparador (o download segue; uma próxima abertura mostra o progresso)."""
        if self._preparer_connected:
            self._preparer_connected = False
            with suppress(RuntimeError, TypeError):
                self.preparer.changed.disconnect(self._on_prepare_changed)
        super().done(result)

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
        if key == "llm.output_language":                                         # F3 P1: opções mais um código livre
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(OUTPUT_LANGUAGES))
            combo.setCurrentText(self.store.get_setting(key))
            combo.setToolTip("pt-BR, auto (segue o idioma detectado da reunião), en, es ou outro código, como fr")
            self._combos[key] = combo
            field = combo
        elif spec.choices is not None and all(isinstance(c, str) for c in spec.choices):
            combo = QComboBox()
            for choice in sorted(spec.choices):
                combo.addItem(choice)
                if key == "pipeline.transcriber" and choice == "llm":                              # F3 item 9: só com áudio nativo
                    if not self.audio_capable:
                        combo.model().item(combo.count() - 1).setEnabled(False)  # type: ignore[attr-defined]
                        combo.setToolTip("transcrição pela LLM: precisa de um provedor ativo com chave cujo modelo rápido aceite áudio")
                    else:
                        combo.setToolTip(f"transcrição pela LLM: {audio_cost_text(self.store)}")
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
        for key, combo in self._combos.items():
            err = validate_setting(key, combo.currentText().strip())
            if err:
                return err
        return self.provider_tabs.validation_error()

    def _save(self) -> None:
        err = self.validation_error()
        if err:
            QMessageBox.warning(self, "Configurações", err)
            return
        try:                                             # validado acima; a sub-aba grava primeiro e recusa sem meio-termo (R3-12)
            self.provider_tabs.save()
        except ValueError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        for key, edit in self._edits.items():
            self.store.set_setting(key, edit.text().strip())
        for key, combo in self._combos.items():
            self.store.set_setting(key, combo.currentText().strip())
        self.store.set_setting("log.path", self.log_path.text().strip())
        self.store.set_setting("log.level", self.log_level.currentText())
        token = self.hf_token.text().strip()
        if token != self.store.settings.provider_key(HF_PROVIDER_ID):
            self.store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key=token or None)
        self.accept()
