"""Chaves de acesso (U-037, D-053): a chave de cada provedor de LLM e o token do Hugging Face num grupo só, o primeiro das
Configurações, cada um com onde conseguir e o estado; as LLMs com "Testar" (o teste da sub-aba, com a chave digitada, antes
de salvar). E o aviso do warm-up quando falta alguma antes de gravar: sem o token, o processamento não sai (ele identifica
quem fala); sem a chave da LLM ativa, sai a transcrição sem ata, resumo nem título. As chaves ficam no banco do app, em
texto aberto (CP10); os links são fixos (nenhum texto do usuário vira HTML, R7-07).
U-038 (D-054): a página "Chaves de acesso" do assistente da primeira inicialização (`KeysPage`): a LLM escolhida (Gemini,
GPT ou Claude) com a chave e o "Testar conexão", e o token do Hugging Face, tudo opcional; o assistente grava no fim
(`save_wizard_keys`), e a escolhida vira a LLM ativa."""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWizardPage

from ..config.defaults import HF_PROVIDER_ID
from ..llm.adapters_common import friendly_error
from ..llm.models_table import DEFAULT_MODELS, PROVIDER_CAPABILITIES
from ..llm.registry import NetworkConfig
from ..llm.types import Capability, ProviderKind
from ..log import get_logger
from ..store.core import _now
from ..store.providers import LOCAL_ID
from .providers import TITLES, test_connection
from .tasks import run_task

if TYPE_CHECKING:
    from ..llm.types import ConnectionTest
    from ..store import Store
    from .providers import ProviderTabs

log = get_logger("ui.keys")

KEYS_TITLE = "Chaves de acesso"
FIRST = "gemini"                 # o provedor padrão vem antes do token; os outros, opcionais, depois
HF_PLACEHOLDER = "hf_… (token de leitura)"
HF_HELP = ('Token de leitura em <a href="https://huggingface.co/settings/tokens">huggingface.co/settings/tokens</a>, '
           'depois de aceitar os termos de <a href="https://huggingface.co/pyannote/speaker-diarization-community-1">'
           'speaker-diarization-community-1</a> e de <a href="https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM">'
           'wespeaker-voxceleb-resnet34-LM</a> com a mesma conta. Sem ele, o processamento não sai: é ele que identifica quem '
           'fala.')
KEY_WHERE = {"gemini": 'Chave em <a href="https://aistudio.google.com/app/apikey">aistudio.google.com</a> (plano pago)',
             "openai": 'Chave em <a href="https://platform.openai.com/api-keys">platform.openai.com</a>',
             "anthropic": 'Chave em <a href="https://console.anthropic.com/settings/keys">console.anthropic.com</a>'}
LLM_HELP = {"gemini": f"{KEY_WHERE['gemini']}: ata, resumo, título, chat e busca por sentido.",
            "openai": f"Opcional. {KEY_WHERE['openai']}.",
            "anthropic": f"Opcional. {KEY_WHERE['anthropic']}."}
WIZARD_LLMS = ("gemini", "openai", "anthropic")
WIZARD_HELP = {"gemini": LLM_HELP["gemini"],
               "openai": f"{KEY_WHERE['openai']}: ata, resumo, título, chat e busca por sentido.",
               "anthropic": (f"{KEY_WHERE['anthropic']}: ata, resumo, título e chat. O Claude não faz a busca por sentido: ela "
                             "fica na busca local, sem custo (prepare em Configurações, Busca, 135 MB).")}
WIZARD_NOTE = ("Tudo opcional: pule e cole depois em Configurações, Chaves de acesso. Ficam no banco do app, em texto aberto "
               "(CP10).")
STORAGE_NOTE = ("Valem ao salvar. Ficam no banco do app, em texto aberto (CP10): mantenha a pasta do banco fora do OneDrive "
                "e do git. Qual LLM faz o quê fica no grupo LLM, mais abaixo.")
NOTE_STYLE = "color: #888; font-size: 11px;"


class WrapLabel(QLabel):
    """Rótulo com quebra de linha cuja altura acompanha a largura que o layout deu. Com texto rico, o `QFormLayout` dentro
    do `QScrollArea` usava o `sizeHint`, calculado para uma largura estreita: 60 px para um texto de 15 (medido em 26/10),
    com espaço em branco em volta."""

    def _fit(self, hint: QSize) -> QSize:
        if self.wordWrap() and self.width() > 0:
            hint.setHeight(self.heightForWidth(self.width()))
        return hint

    @override
    def sizeHint(self) -> QSize:
        return self._fit(super().sizeHint())

    @override
    def minimumSizeHint(self) -> QSize:
        return self._fit(super().minimumSizeHint())

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()                         # a largura mudou: a altura certa muda junto


def note(text: str, *, links: bool = False) -> QLabel:
    """Linha de ajuda pequena; com `links`, o texto fixo vira HTML e os links abrem no navegador."""
    label = WrapLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(NOTE_STYLE)
    label.setTextFormat(Qt.TextFormat.RichText if links else Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(links)
    return label


def _llm_rows(form: QFormLayout, tabs: ProviderTabs, pid: str) -> None:
    tab = tabs.tab_by_id[pid]
    row = QHBoxLayout()
    row.addWidget(tab.key, 1)
    row.addWidget(tab.btn_test)
    form.addRow(TITLES.get(pid, pid), row)
    form.addRow("", tab.key_status)
    if pid in LLM_HELP:
        form.addRow("", note(LLM_HELP[pid], links=True))


def keys_group(tabs: ProviderTabs, hf_token: QLineEdit) -> QGroupBox:
    """O grupo: Gemini (o padrão), o token do Hugging Face e os outros provedores. Os campos são os das sub-abas e o do
    token, só postos aqui: quem grava continua sendo `ProviderTabs.save` e o `_save` do diálogo."""
    box = QGroupBox(f"{KEYS_TITLE} (a LLM faz a ata e o resumo; o Hugging Face, saber quem fala)")
    form = QFormLayout(box)
    if FIRST in tabs.tab_by_id:
        _llm_rows(form, tabs, FIRST)
    hf_token.setPlaceholderText(HF_PLACEHOLDER)
    form.addRow("Hugging Face", hf_token)
    form.addRow("", note(HF_HELP, links=True))
    for pid in tabs.tab_by_id:
        if pid != FIRST:
            _llm_rows(form, tabs, pid)
    form.addRow(note(STORAGE_NOTE))
    return box


def missing_keys(store: Store) -> tuple[str, ...]:
    """O que falta para uma gravação virar transcrição com ata: "hf" (o token do Hugging Face) e "llm" (nenhum provedor de
    LLM ativo ou o ativo sem chave). Só leitura."""
    out: list[str] = []
    if not str(store.settings.provider_key(HF_PROVIDER_ID) or "").strip():
        out.append("hf")
    row = store.providers.active_provider("llm")
    if row is None or not str(row.get("api_key") or "").strip():
        out.append("llm")
    return tuple(out)


def missing_keys_text(missing: tuple[str, ...]) -> str:
    """O aviso do warm-up, vazio quando nada falta; só o link é HTML (o resto é fixo)."""
    parts = []
    if "hf" in missing:
        parts.append("sem o token do Hugging Face, o processamento desta gravação não sai")
    if "llm" in missing:
        parts.append("sem a chave da LLM, não sai ata, resumo nem título")
    if not parts:
        return ""
    text = "; ".join(parts)
    return f'{text[:1].upper()}{text[1:]}. <a href="settings">Configurar as chaves</a>'


# ---------------------------------------------------------------- assistente (U-038)
class KeysPage(QWizardPage):
    """A página das chaves no assistente: a LLM escolhida com a chave e o "Testar conexão" (a chave digitada, na rede
    padrão, antes de o banco existir) e o token do Hugging Face. Nada é gravado aqui: o "Concluir" grava tudo."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(KEYS_TITLE)
        self.setSubTitle("A LLM escreve a ata, o resumo e o título; o token do Hugging Face identifica quem fala (sem ele, "
                         "o processamento não sai).")
        self.provider = QComboBox()
        for pid in WIZARD_LLMS:
            self.provider.addItem(f"{TITLES[pid]}, recomendado" if pid == FIRST else TITLES[pid], pid)
        self.key = QLineEdit()
        self.key.setPlaceholderText("chave de API da LLM escolhida")
        self.test_btn = QPushButton("Testar conexão")
        self.status = note("")
        self.llm_help = note("", links=True)
        self.hf_token = QLineEdit()
        self.hf_token.setPlaceholderText(HF_PLACEHOLDER)
        self.last_test: ConnectionTest | None = None
        row = QHBoxLayout()
        row.addWidget(self.key, 1)
        row.addWidget(self.test_btn)
        form = QFormLayout(self)
        form.addRow("LLM", self.provider)
        form.addRow("Chave", row)
        form.addRow("", self.status)
        form.addRow("", self.llm_help)
        form.addRow("Hugging Face", self.hf_token)
        form.addRow("", note(HF_HELP, links=True))
        form.addRow(note(WIZARD_NOTE))
        self.provider.currentIndexChanged.connect(lambda _i: self._changed())
        self.key.textChanged.connect(lambda _t: self._changed(help_text=False))
        self.test_btn.clicked.connect(self.run_test)
        self._changed()

    def chosen(self) -> str:
        return str(self.provider.currentData())

    def _changed(self, *, help_text: bool = True) -> None:
        """Outra LLM ou outra chave: o teste anterior não vale mais."""
        if help_text:
            self.llm_help.setText(WIZARD_HELP[self.chosen()])
        self.status.setText("")
        self.last_test = None

    def run_test(self) -> None:
        key = self.key.text().strip()
        if not key:
            self.status.setText("cole a chave antes de testar")
            return
        pid = self.chosen()
        self.test_btn.setEnabled(False)
        self.status.setText("testando…")
        model = DEFAULT_MODELS[ProviderKind(pid)][0]
        run_task(functools.partial(test_connection, pid, key, model, NetworkConfig()), None,
                 functools.partial(self._on_test, pid, key))

    def _on_test(self, pid: str, key: str, result: Any) -> None:
        self.test_btn.setEnabled(True)
        if (pid, key) != (self.chosen(), self.key.text().strip()):
            return                                        # o usuário trocou a LLM ou a chave durante o teste
        if isinstance(result, BaseException):
            self.status.setText(f"falhou: {friendly_error(str(result))}")
            return
        self.last_test = result
        self.status.setText(f"ok: {result.model} respondeu em {result.latency_ms} ms" if result.ok
                            else f"falhou: {friendly_error(result.error)}")


def save_wizard_keys(store: Store, provider_id: str, key: str, hf_token: str, tested: ConnectionTest | None = None) -> None:
    """O que o "Concluir" grava da página: o token e a chave, se informados; a LLM escolhida vira a ativa para texto e, se
    faz embeddings (Gemini, GPT), também para a busca por sentido; com o Claude, que não faz, a busca por sentido fica na
    local, sem custo (A-01: a linha `local` nasce aqui, no caminho de escrita). Nada de chave no log."""
    token, key = hf_token.strip(), key.strip()
    if token:
        store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key=token)
    if key:
        store.providers.update_provider(provider_id, api_key=key)
        if tested is not None and tested.ok:
            store.providers.update_provider(provider_id, tested_at=_now(), latency_ms=int(tested.latency_ms))
    store.providers.set_role("llm", provider_id)
    if Capability.EMBEDDINGS in PROVIDER_CAPABILITIES[ProviderKind(provider_id)]:
        store.providers.set_role("embed", provider_id)
    else:
        store.providers.ensure_local()
        store.providers.set_role("embed", LOCAL_ID)
    log.info("assistente: LLM ativa %s (%s), token do Hugging Face %s", provider_id, "com chave" if key else "sem chave",
             "informado" if token else "ausente")


__all__ = ["KEYS_TITLE", "KeysPage", "keys_group", "missing_keys", "missing_keys_text", "note", "save_wizard_keys"]
