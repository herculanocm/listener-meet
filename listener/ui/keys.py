"""Chaves de acesso (U-037, D-053): a chave de cada provedor de LLM e o token do Hugging Face num grupo só, o primeiro das
Configurações, cada um com onde conseguir e o estado; as LLMs com "Testar" (o teste da sub-aba, com a chave digitada, antes
de salvar). E o aviso do warm-up quando falta alguma antes de gravar: sem o token, o processamento não sai (ele identifica
quem fala); sem a chave da LLM ativa, sai a transcrição sem ata, resumo nem título. As chaves ficam no banco do app, em
texto aberto (CP10); os links são fixos (nenhum texto do usuário vira HTML, R7-07)."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit

from ..config.defaults import HF_PROVIDER_ID
from .providers import TITLES

if TYPE_CHECKING:
    from ..store import Store
    from .providers import ProviderTabs

KEYS_TITLE = "Chaves de acesso"
FIRST = "gemini"                 # o provedor padrão vem antes do token; os outros, opcionais, depois
HF_PLACEHOLDER = "hf_… (token de leitura)"
HF_HELP = ('Token de leitura em <a href="https://huggingface.co/settings/tokens">huggingface.co/settings/tokens</a>, '
           'depois de aceitar os termos de <a href="https://huggingface.co/pyannote/speaker-diarization-community-1">'
           'speaker-diarization-community-1</a> e de <a href="https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM">'
           'wespeaker-voxceleb-resnet34-LM</a> com a mesma conta. Sem ele, o processamento não sai: é ele que identifica quem '
           'fala.')
LLM_HELP = {"gemini": ('Chave em <a href="https://aistudio.google.com/app/apikey">aistudio.google.com</a> (plano pago): ata, '
                       'resumo, título, chat e busca por sentido.'),
            "openai": 'Opcional. Chave em <a href="https://platform.openai.com/api-keys">platform.openai.com</a>.',
            "anthropic": 'Opcional. Chave em <a href="https://console.anthropic.com/settings/keys">console.anthropic.com</a>.'}
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


__all__ = ["KEYS_TITLE", "keys_group", "missing_keys", "missing_keys_text", "note"]
