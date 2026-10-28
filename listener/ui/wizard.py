"""Assistente de primeira inicialização (D-026, D-029): dados do usuário, pasta do banco, chaves de acesso (U-038, D-054:
a LLM escolhida com a chave e o token do Hugging Face, opcionais; `ui/keys.py`), arquivo e nível de log (D-033), teste do
microfone; dica para fixar o ícone da bandeja (U-017)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget, QWizard, QWizardPage)

from ..capture.audio import rms
from ..config import bootstrap
from ..config.defaults import LOG_LEVELS
from ..log import attach_file, get_logger
from ..platform.base import log_id
from ..store import Store
from .devices import REFRESH_MS, refresh_mics
from .keys import KeysPage, save_wizard_keys
from .meters import LevelMeter
from .theme import PRIMARY, Tone, set_kind, tone

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, CaptureHandle, Platform

log = get_logger("ui.wizard")


class IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Bem-vindo ao listener-meet")
        lab = QLabel("Este app grava suas reuniões (qualquer plataforma) em dois canais, identifica quem fala por "
                     "perfil de voz e gera legenda, resumo e ata. Tudo fica no seu computador, num banco SQLite "
                     "que você escolhe onde guardar. Nada é apagado automaticamente.\n\n"
                     "Em cinco passos: seus dados, a pasta do banco, as chaves de acesso (opcional), o arquivo de log e "
                     "um teste do microfone.\n\n"
                     "Depois, o app vive no ícone da bandeja. No Windows 11 o ícone nasce no menu oculto (seta ao lado "
                     "do relógio): arraste-o para a barra para ver o estado da gravação.")
        lab.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(lab)


class UserPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Seus dados")
        self.setSubTitle("O canal do microfone é sempre você. Nome e cargo aparecem na transcrição e na ata.")
        self.name = QLineEdit()
        self.email = QLineEdit()
        self.role = QLineEdit()
        self.company = QLineEdit()
        form = QFormLayout(self)
        form.addRow("Nome *", self.name)
        form.addRow("E-mail", self.email)
        form.addRow("Cargo", self.role)
        form.addRow("Empresa", self.company)
        self.registerField("name*", self.name)
        self.registerField("email", self.email)
        self.registerField("role", self.role)
        self.registerField("company", self.company)


class DbPage(QWizardPage):
    def __init__(self, platform: Platform, initial: Path | None):
        super().__init__()
        self.platform = platform
        self.setTitle("Onde guardar o banco")
        self.setSubTitle("Arquivos SQLite: um com textos e configurações e um por mês com o áudio. "
                         "Evite pastas sincronizadas (OneDrive, Dropbox, Google Drive, iCloud).")
        self.path = QLineEdit(str(initial or platform.data_dir_suggestion()))
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse)
        self.warning = QLabel("")
        tone(self.warning, Tone.WARNING)
        self.warning.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.warning)
        self.registerField("db_dir*", self.path)
        self.path.textChanged.connect(self._check)
        self._check()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta do banco", self.path.text())
        if d:
            self.path.setText(d)

    def _check(self) -> None:
        synced = self.platform.synced_folder_name(Path(self.path.text()))
        self.warning.setText(f"Esta pasta está dentro do {synced}: o banco inteiro (áudio e chaves) sairia da máquina "
                             f"pela sincronização." if synced else "")

    @override
    def validatePage(self) -> bool:
        p = Path(self.path.text()).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Pasta do banco", f"Não consigo escrever em {p}: {e}")
            return False
        if self.platform.synced_folder_name(p):
            r = QMessageBox.question(self, "Pasta sincronizada", "A pasta é sincronizada com a nuvem. Usar mesmo assim?")
            return r == QMessageBox.StandardButton.Yes
        return True


class LogPage(QWizardPage):
    """Arquivo de log e nível de detalhe (D-033): escolhidos aqui, editáveis depois nas Configurações (CP8)."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Arquivo de log")
        self.setSubTitle("Registro do que o app fez e quando (diagnóstico). Escolha onde fica e o nível de detalhe; "
                         "dá para mudar nas Configurações a qualquer momento.")
        self.path = QLineEdit()
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse)
        self.level = QComboBox()
        self.level.addItems(list(LOG_LEVELS))
        self.level.setCurrentText("INFO")
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form = QFormLayout(self)
        form.addRow("Arquivo", row)
        form.addRow("Nível", self.level)
        hint = QLabel("DEBUG registra tudo (útil para investigar um problema); INFO é o padrão; WARNING e ERROR só avisos e falhas. "
                      "O arquivo gira sozinho a cada 5 MB, guardando os 5 últimos.")
        hint.setWordWrap(True)
        form.addRow(hint)

    @override
    def initializePage(self) -> None:
        if not self.path.text().strip():
            self.path.setText(str(Path(self.field("db_dir")).expanduser() / "logs" / "listener.log"))

    def _browse(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, "Arquivo de log", self.path.text(), "Log (*.log);;Todos (*)")
        if f:
            self.path.setText(f)

    @override
    def validatePage(self) -> bool:
        p = Path(self.path.text().strip()).expanduser()
        if not p.name:
            QMessageBox.warning(self, "Arquivo de log", "Informe um arquivo.")
            return False
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8"):
                pass
        except Exception as e:
            QMessageBox.warning(self, "Arquivo de log", f"Não consigo escrever em {p}: {e}")
            return False
        return True


class MicPage(QWizardPage):
    def __init__(self, platform: Platform):
        super().__init__()
        self.platform = platform
        self.setTitle("Teste do microfone")
        self.setSubTitle("Fale alguma coisa: o medidor precisa mexer. Você pode trocar o microfone depois, no warm-up.")
        self.combo = QComboBox()
        self._devices: list[AudioDevice] = []
        try:
            self._devices = platform.list_input_devices()
        except Exception as e:
            log.warning("listar microfones: %s", e)
            self._devices = []
        for d in self._devices:
            self.combo.addItem(d.name, d.id)
        self.meter = LevelMeter(height=12)
        self.status = QLabel("")
        lay = QVBoxLayout(self)
        lay.addWidget(self.combo)
        lay.addWidget(self.meter)
        lay.addWidget(self.status)
        self._handle: CaptureHandle | None = None
        self._level = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(lambda: self.meter.set_rms(self._level))
        self._devices_timer = QTimer(self)                  # U-043: fone conectado com o assistente aberto
        self._devices_timer.setInterval(REFRESH_MS)
        self._devices_timer.timeout.connect(self._refresh_devices)
        self.combo.currentIndexChanged.connect(lambda _: self._open())

    @override
    def initializePage(self) -> None:
        default = None
        try:
            default = self.platform.default_input_device()
        except Exception as e:
            log.debug("microfone padrão: %s", e)
        if default:
            i = self.combo.findData(default.id)
            if i >= 0:
                self.combo.setCurrentIndex(i)
        self._open()
        self._timer.start()
        self._devices_timer.start()

    @override
    def cleanupPage(self) -> None:
        self._close()
        self._timer.stop()
        self._devices_timer.stop()

    @override
    def validatePage(self) -> bool:
        self._close()
        self._timer.stop()
        self._devices_timer.stop()
        return True

    def _refresh_devices(self) -> None:
        """U-043: o fone conectado ou removido com a página aberta entra ou sai do combo, sem reiniciar o app."""
        result = refresh_mics(self.platform, self.combo, self._devices, meter_open=self._handle is not None,
                              close_meter=self._close)
        if result is None:
            return
        self._devices = result.devices
        if result.reopen:
            self._open()
        if result.note:
            self.status.setText(f"{self.status.text()} ({result.note})")

    def selected_device(self) -> AudioDevice | None:
        i = self.combo.currentIndex()
        return self._devices[i] if 0 <= i < len(self._devices) else None

    def _open(self) -> None:
        self._close()
        try:
            self._handle = self.platform.open_mic(self.selected_device(), 16000, self._on_audio)
            self._handle.start()
            self.status.setText("ouvindo…")
        except Exception as e:
            self._handle = None
            self.status.setText(f"microfone indisponível: {e}")
            log.warning("assistente: microfone indisponível: %s", e)

    def _close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.stop()
            except Exception as e:
                log.debug("fechar mic do assistente: %s", e)
            self._handle = None

    def _on_audio(self, block: np.ndarray, t: float) -> None:
        self._level = rms(block)


class FirstRunWizard(QWizard):
    def __init__(self, platform: Platform, initial_dir: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.platform = platform
        self.store: Store | None = None
        self.setWindowTitle("listener-meet — primeira inicialização")
        self.setMinimumSize(640, 440)
        self.addPage(IntroPage())
        self.user_page = UserPage()
        self.addPage(self.user_page)
        self.db_page = DbPage(platform, initial_dir)
        self.addPage(self.db_page)
        self.keys_page = KeysPage()
        self.addPage(self.keys_page)
        self.log_page = LogPage()
        self.addPage(self.log_page)
        self.mic_page = MicPage(platform)
        self.addPage(self.mic_page)
        for which in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):   # F5.4 (U-056): o primário
            set_kind(self.button(which), PRIMARY)

    @override
    def accept(self) -> None:
        db_dir = Path(self.field("db_dir")).expanduser()
        try:
            store = Store(db_dir)
        except Exception as e:
            log.exception("não consegui criar o banco em %s", db_dir)
            QMessageBox.critical(self, "Banco de dados", f"Não consegui criar o banco em {db_dir}:\n{e}")
            return                                        # o assistente continua aberto para escolher outra pasta
        store.upsert_me(self.field("name").strip(), self.field("email").strip(), self.field("role").strip(),
                        self.field("company").strip())
        store.seed_default_presets()
        keys = self.keys_page
        save_wizard_keys(store, keys.chosen(), keys.key.text(), keys.hf_token.text(), keys.last_test)
        dev = self.mic_page.selected_device()
        if dev:
            store.set_setting("warmup.last_mic_device_id", dev.id)
            store.set_setting("warmup.last_mic_device_name", dev.name)
        log_path = self.log_page.path.text().strip() or str(store.default_log_path())
        store.set_setting("log.path", log_path)
        store.set_setting("log.level", self.log_page.level.currentText())
        try:
            attach_file(log_path, self.log_page.level.currentText())
        except Exception as e:
            log.warning("log em arquivo indisponível (%s): %s", log_path, e)
        bootstrap.write_db_dir(self.platform.config_dir(), db_dir)
        log.info("primeira inicialização concluída: banco em %s, log em %s (nível %s); microfone %s; ficha do usuário com "
                 "cargo %s, empresa %s, e-mail %s", db_dir, log_path, self.log_page.level.currentText(),
                 log_id(dev.id) if dev else "o padrão", *("sim" if self.field(f).strip() else "não" for f in ("role", "company",
                                                                                                              "email")))
        self.store = store
        super().accept()
