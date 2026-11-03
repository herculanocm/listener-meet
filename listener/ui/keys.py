"""Chaves de acesso (U-037, D-053): a chave de cada provedor de LLM e o token do Hugging Face num grupo só, o primeiro das
Configurações, cada um com onde conseguir e o estado; as LLMs com "Testar" (o teste da sub-aba, com a chave digitada, antes
de salvar). E o aviso do warm-up quando falta alguma antes de gravar: sem o token, o processamento não sai (ele identifica
quem fala); sem a chave da LLM ativa, sai a transcrição sem ata, resumo nem título. As chaves ficam no banco do app, em
texto aberto (CP10); os links são fixos (nenhum texto do usuário vira HTML, R7-07).
U-038 (D-054): a página "Chaves de acesso" do assistente da primeira inicialização (`KeysPage`): a LLM escolhida (Gemini,
GPT ou Claude) com a chave e o "Testar conexão", e o token do Hugging Face, tudo opcional; o assistente grava no fim
(`save_wizard_keys`), e a escolhida vira a LLM ativa.
F5.4 L3 (U-071, U-075): nas Configurações, a página "Chaves de acesso" tem um cartão por provedor (`keys_cards`), cada um
com a chave, o "Testar conexão" e o estado em selo (Falta a chave, Conectado, Falhou...); o do Hugging Face diz se falta o
token (`hf_state`). Sem jargão interno na tela (U-076).
F5.11 (U-113): o token do Hugging Face ganha "Testar" (`ui/hf_test.py`), com o token digitado, antes de salvar, no cartão e
no assistente; o aviso do "Antes de gravar" passou para a faixa "Falta configurar" (`ui/warmup_notice.py`).
F5.14 (U-134): o assistente diz numa linha própria que o texto das reuniões vai para a IA escolhida."""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, NamedTuple, override

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QWizardPage

from ..config.defaults import HF_PROVIDER_ID
from ..llm.adapters_common import friendly_error
from ..llm.models_table import DEFAULT_MODELS, PROVIDER_CAPABILITIES
from ..llm.registry import NetworkConfig
from ..llm.types import Capability, ProviderKind
from ..log import get_logger
from ..store.core import _now
from ..store.providers import LOCAL_ID
from .components import Badge, Card
from .hf_test import HfTestControl, Spawn
from .providers import TITLES, test_connection
from . import theme
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
WIZARD_NOTE = ("Tudo opcional: pule e cole depois em Configurações, Chaves de acesso. Ficam no banco do app, em texto "
               "aberto.")                     # F5.4 L4: sem o jargão interno (U-076) também no assistente
WIZARD_SEND = "O texto das reuniões vai para a IA escolhida."                     # F5.14 (U-134)
STORAGE_NOTE = ("Ficam no banco do app, em texto aberto: mantenha a pasta do banco fora do OneDrive e do git. Qual LLM faz o "
                "quê fica na página LLM.")
HF_MISSING, HF_SET = "Falta o token", "Informado"   # U-075: o token não tem teste aqui; o selo diz só se foi informado


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
    theme.note(label)
    label.setTextFormat(Qt.TextFormat.RichText if links else Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(links)
    return label


def process_spawn(argv: list[str], env: Any = None) -> Any:
    """O `spawn_child` da plataforma, para o assistente (que nasce antes do banco e não recebe a plataforma na página)."""
    from ..platform import get_platform

    return get_platform().spawn_child(argv, env)


def _with_button(edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(button)
    return row


def _state_row(badge: Badge, detail: QLabel | None = None) -> QHBoxLayout:
    """O selo do estado no tamanho do texto e, ao lado, o detalhe (que estica)."""
    badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout()
    row.setSpacing(theme.METRICS.form_row)
    row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
    if detail is not None:
        row.addWidget(detail, 1)
    else:
        row.addStretch(1)
    return row


def _llm_card(tabs: ProviderTabs, pid: str) -> Card:
    """O cartão de um provedor de LLM: a chave com o "Testar conexão", o estado em selo e onde conseguir."""
    tab = tabs.tab_by_id[pid]
    card = Card(TITLES.get(pid, pid))
    row = QHBoxLayout()
    row.addWidget(tab.key, 1)
    row.addWidget(tab.btn_test)
    card.content.addLayout(row)
    card.content.addLayout(_state_row(tab.key_badge, tab.key_status))
    if pid in LLM_HELP:
        card.content.addWidget(note(LLM_HELP[pid], links=True))
    return card


def hf_state(token: str) -> tuple[str, theme.Tone]:
    """O selo do token do Hugging Face (U-075): falta (aviso) ou informado."""
    return (HF_SET, theme.Tone.NEUTRAL) if token.strip() else (HF_MISSING, theme.Tone.WARNING)


class HfWidgets(NamedTuple):
    """F5.11 (U-113): o selo do token, o "Testar" e a mensagem do resultado ao lado do selo."""
    badge: Badge
    button: QPushButton
    detail: QLabel


def keys_cards(tabs: ProviderTabs, hf_token: QLineEdit) -> tuple[list[Card], HfWidgets]:
    """Os cartões da página: Gemini (o padrão), o Hugging Face e os outros provedores, e o selo do token com o "Testar" (quem
    chama liga o teste e atualiza o selo quando o token muda). Os campos são os das sub-abas e o do token, só postos aqui:
    quem grava continua sendo `ProviderTabs.save` e o `_save` do diálogo."""
    hf_token.setPlaceholderText(HF_PLACEHOLDER)
    hf_token.setAccessibleName("Token do Hugging Face")
    widgets = HfWidgets(Badge(*hf_state(hf_token.text())), QPushButton("Testar"), note(""))
    hf = Card("Hugging Face")
    row = QHBoxLayout()
    row.addWidget(hf_token, 1)
    row.addWidget(widgets.button)
    hf.content.addLayout(row)
    hf.content.addLayout(_state_row(widgets.badge, widgets.detail))
    hf.content.addWidget(note(HF_HELP, links=True))
    cards = [_llm_card(tabs, FIRST)] if FIRST in tabs.tab_by_id else []
    cards.append(hf)
    cards += [_llm_card(tabs, pid) for pid in tabs.tab_by_id if pid != FIRST]
    return cards, widgets


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


# ---------------------------------------------------------------- assistente (U-038)
class KeysPage(QWizardPage):
    """A página das chaves no assistente: a IA escolhida com a chave e o "Testar conexão" (a chave digitada, na rede
    padrão, antes de o banco existir) e o token do Hugging Face com o "Testar" dele (F5.11, U-113: no filho, sem banco, na
    pasta de modelos padrão). Nada é gravado aqui: o "Concluir" grava tudo."""

    def __init__(self, spawn: Spawn | None = None) -> None:
        super().__init__()
        self.setTitle(KEYS_TITLE)
        self.setSubTitle("A IA escreve a ata, o resumo e o título; o token do Hugging Face identifica quem fala (sem ele, "
                         "o processamento não sai).")                     # F5.11 (U-106): fora das Configurações, "IA"
        self.provider = QComboBox()
        for pid in WIZARD_LLMS:
            self.provider.addItem(f"{TITLES[pid]}, recomendado" if pid == FIRST else TITLES[pid], pid)
        self.key = QLineEdit()
        self.key.setPlaceholderText("chave de API da IA escolhida")
        self.test_btn = QPushButton("Testar conexão")
        self.status = note("")
        self.llm_help = note("", links=True)
        self.hf_token = QLineEdit()
        self.hf_token.setPlaceholderText(HF_PLACEHOLDER)
        self.hf_test_btn = QPushButton("Testar")
        self.hf_status = note("")
        self.last_test: ConnectionTest | None = None
        form = QFormLayout(self)
        form.addRow("IA", self.provider)
        form.addRow("Chave", _with_button(self.key, self.test_btn))
        form.addRow("", self.status)
        form.addRow("", self.llm_help)
        form.addRow("", note(WIZARD_SEND))
        form.addRow("Hugging Face", _with_button(self.hf_token, self.hf_test_btn))
        form.addRow("", self.hf_status)
        form.addRow("", note(HF_HELP, links=True))
        form.addRow(note(WIZARD_NOTE))
        self.provider.currentIndexChanged.connect(self._on_provider_changed)      # métodos, nunca lambda com `self`
        self.key.textChanged.connect(self._on_key_edited)
        self.test_btn.clicked.connect(self.run_test)
        self.hf_control = HfTestControl(self.hf_token, self.hf_test_btn, spawn or process_spawn, None, self)
        self.hf_control.shown.connect(self._hf_show)
        self._changed()

    def _hf_show(self, badge: str, _value: theme.Tone, detail: str) -> None:
        """U-113: o resultado do teste do token numa linha ("Conectado: token aceito…"); sem selo, só a mensagem."""
        self.hf_status.setText(f"{badge}: {detail}" if badge and detail else badge or detail)

    def chosen(self) -> str:
        return str(self.provider.currentData())

    def _on_provider_changed(self, _index: int) -> None:
        self._changed()

    def _on_key_edited(self, _text: str) -> None:
        self._changed(help_text=False)

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


__all__ = ["HF_MISSING", "HF_SET", "KEYS_TITLE", "STORAGE_NOTE", "HfWidgets", "KeysPage", "hf_state", "keys_cards", "missing_keys",
           "note", "process_spawn", "save_wizard_keys"]
