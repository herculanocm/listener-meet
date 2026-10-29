"""Configurações mínimas (CP8 parcial). F1: captura, atalhos (com o estado do registro, F1.1 A13), presets, log em arquivo e
nível (D-033), pasta do banco. F2: "Processamento" gerado da tabela (`pipeline.*`); o token do Hugging Face em `providers`
(Q2, CP10). F3: "LLM" gerado da tabela (`llm.*`) e as sub-abas por provedor (`ProviderTabs`); o transcritor `llm` só habilita
quando o modelo rápido do provedor ativo tem áudio. F4 e F5 (L5), F5.1: "Ao vivo" e "Busca", com os downloads e o índice.
U-037 (D-053): as chaves de acesso primeiro (`ui/keys.py`). F5.3: "Idioma e avisos" (o idioma padrão grava o código, A-03;
U-048: quanto o aviso fica na tela). Validação pela tabela `SPECS` (M-24) e pela gramática de atalhos da plataforma (M-25).

F5.4 L3: páginas com navegação à esquerda, 960 x 640 (U-071; A-17: o `fit_to_screen` encolhe numa tela menor), e um Salvar
para todas; as páginas moram em `ui/settings_general.py` e `ui/settings_search.py` (mixins deste diálogo), sobre
`ui/settings_pages.py`. Cada página diz no subtítulo quando o que está nela vale; a linha só diz quando difere (U-074). Os
editores são tipados (U-073, A-14, `ui/settings_editors.py`): sim/não em caixa, número com vírgula na faixa da `SPECS`,
opções em lista, e o que o usuário não mexeu volta para o banco com o texto salvo original. Chaves, atalhos e downloads com
o estado em selo (U-075); sem jargão interno na tela (U-076). Um erro de validação abre a página, marca o campo e explica
numa faixa, sem caixa de mensagem (U-072); a marca sai quando o valor do campo muda, também na tabela de preços (revisão da
F5.4, R-09). O valor salvo que a `SPECS` recusa, num editor tipado, dá lugar ao padrão, gravado ao salvar, com o aviso numa
faixa no topo da página (R-04). F5.6 L2 (U-092): a página "Prompts" (`ui/settings_prompts.py`), depois da LLM, com as
instruções de cada tarefa; o Salvar valida e grava as tarefas abertas nela."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QSize, Qt, SignalInstance
from PySide6.QtGui import QPalette, QShowEvent
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QSpinBox, QStackedWidget, QTableWidget,
                               QVBoxLayout, QWidget)

from ..config.defaults import HF_PROVIDER_ID, HOTKEY_KEYS, SPECS, validate_setting
from ..llm.jobconfig import audio_capable
from ..log import get_logger
from . import theme
from .common import fit_to_screen
from .components import Badge, InfoBar
from .live_prepare import ModelPreparer, preparer
from .providers import ProviderTabs
from .settings_editors import SettingEditor, make_editor
from .settings_general import SUBTITLES, TOAST_SECONDS, GeneralPagesMixin, audio_cost_text, breakable_path
from .settings_pages import SettingsPage
from .settings_prompts import PromptsMixin
from .settings_search import SearchMixin, vectors_note

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.settings")

REINDEX_TITLE = "Busca por sentido"
SIZE, MIN_SIZE = (960, 640), (720, 480)                     # U-071; A-17
NAV_WIDTH = 200


def offer_local_reindex(store: Store, parent: QWidget | None, on_launch: Callable[[bool], Any] | None) -> int:
    """A-12: terminado o preparo da busca local com o local ativo e pronto, "Reindexar agora" ou "Depois" com os minutos
    (Q3: a varredura não completa sozinha as reuniões antigas no local). Devolve quantos `index` entraram na fila."""
    from ..rag.index import embedder_status, is_local, reindex_all, reindex_estimate

    status = embedder_status(store)
    if not status.ready or not is_local(status.model_key):
        return 0
    est = reindex_estimate(store)
    if not est.chunks and not est.without_chunks:
        return 0
    text = (f"A busca local está pronta.\n\nReindexar as reuniões nela: {est.text()}.\n\n{vectors_note(est)} A busca por palavra "
            "segue funcionando enquanto isso.")
    if not ask_reindex(parent, text, "Reindexar agora", "Depois"):
        return 0
    n = reindex_all(store)
    if n and on_launch is not None:
        on_launch(True)
    return n


def change_signal(widget: QWidget) -> SignalInstance | None:
    """R-09: o sinal de "o valor mudou" de cada tipo de campo que a validação marca (None nos outros)."""
    if isinstance(widget, QLineEdit | QSpinBox | QDoubleSpinBox | QPlainTextEdit):     # F5.6 L2: as instruções de um prompt
        return widget.textChanged
    if isinstance(widget, QComboBox):
        return widget.currentTextChanged
    if isinstance(widget, QAbstractButton):
        return widget.toggled
    if isinstance(widget, QTableWidget):
        return widget.itemChanged
    return None


def ask_reindex(parent: QWidget | None, text: str, yes: str, no: str) -> bool:
    """Pergunta com dois botões nomeados ("Reindexar agora" e "Depois", ou "Reindexar" e "Cancelar"); True = o primeiro.
    Separada para os testes trocarem."""
    box = QMessageBox(QMessageBox.Icon.Question, REINDEX_TITLE, text, parent=parent)
    accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(no, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(accept)
    box.exec()
    return box.clickedButton() is accept


class SettingsDialog(GeneralPagesMixin, SearchMixin, PromptsMixin, QDialog):
    def __init__(self, store: Store, platform: Platform, hotkey_errors: dict[str, str] | None = None,
                 parent: QWidget | None = None, on_launch: Callable[[bool], Any] | None = None, *, recording: bool = False,
                 model_preparer: ModelPreparer | None = None):
        super().__init__(parent)
        self.store = store
        self.platform = platform
        self.hotkey_errors = hotkey_errors or {}
        self.recording = recording
        self.on_launch = on_launch
        self.reindexed = 0                                   # instrumentação dos testes: `index` postos na fila daqui
        self.preparer = model_preparer or preparer()
        self.audio_capable = audio_capable(store)
        self._editors: dict[str, SettingEditor] = {}
        self.badges: dict[str, Badge] = {}                   # U-075: o selo posto na linha de cada chave (os atalhos)
        self._invalid: list[QWidget] = []
        self._watched: list[QWidget] = []                    # R-09: os campos com o sinal de mudança já ligado
        self._error_bar: InfoBar | None = None
        self.setWindowTitle("Configurações")
        self.resize(*SIZE)
        self.setMinimumSize(*MIN_SIZE)
        self.provider_tabs = ProviderTabs(store, on_launch)            # a chave de cada sub-aba vai para a primeira página
        self.hf_token = QLineEdit(store.settings.provider_key(HF_PROVIDER_ID))
        built = (self._keys_page(), self._language_page(), self._capture_page(), self._hotkeys_page(), self._processing_page(),
                 self._llm_page(), self._prompts_page(), self._live_page(), self._search_page(), self._data_page())
        self.pages: dict[str, SettingsPage] = {page.page_title: page for page in built}
        self._lay_out()
        self.refresh_index_status()
        self.preparer.changed.connect(self._on_prepare_changed)
        self._preparer_connected = True
        self._on_prepare_changed(self.preparer.state)        # download em curso de uma abertura anterior: segue mostrando

    # ---------------------------------------------------------------- moldura (U-071)
    def _lay_out(self) -> None:
        """A navegação à esquerda, a página à direita (com a faixa do erro em cima, quando há) e um Salvar para todas."""
        self.nav = QListWidget()
        self.nav.setAccessibleName("Páginas das Configurações")
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setFixedWidth(NAV_WIDTH)
        self.nav.viewport().setBackgroundRole(QPalette.ColorRole.Window)   # a navegação no fundo da janela, como no Windows 11
        self.stack = QStackedWidget()
        for title, page in self.pages.items():
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, theme.METRICS.list_item_h))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._page_changed)            # F5.6 L2: a página Prompts monta a lista ao aparecer
        self.nav.setCurrentRow(0)
        self.right = QVBoxLayout()
        self.right.setContentsMargins(0, 0, 0, 0)
        self.right.addWidget(self.stack, 1)
        body = QHBoxLayout()
        body.setContentsMargins(theme.METRICS.form_row, theme.METRICS.form_row, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.nav)
        body.addLayout(self.right, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        m = theme.METRICS.margin
        footer.setContentsMargins(m, theme.METRICS.card_gap, m, m)
        footer.addWidget(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(body, 1)
        outer.addLayout(footer)
        theme.primary(self, buttons.button(QDialogButtonBox.StandardButton.Save))   # F5.4 (U-056, A-02)

    def page(self, title: str) -> SettingsPage:
        return self.pages[title]

    def _page_changed(self, index: int) -> None:
        self.prompt_page_shown(self.stack.widget(index))

    def editor(self, key: str) -> SettingEditor:
        """A-14: o editor de uma chave (`raw()` e `set_raw()`)."""
        return self._editors[key]

    def _add(self, page: SettingsPage, form: QFormLayout, key: str, editor: SettingEditor | None = None,
             badge: tuple[str, theme.Tone] | None = None, *, compact: bool = False) -> SettingEditor:
        """O editor da chave (o dado ou o do tipo dela na `SPECS`) numa linha da página, guardado para validar e salvar."""
        ed = editor if editor is not None else make_editor(key, self.store.get_setting(key))
        self._editors[key] = ed
        shown = page.add_row(form, ed, badge, compact=compact)
        if shown is not None:
            self.badges[key] = shown
        if ed.saved_error is not None:                       # R-04: o valor à vista é o padrão, que o Salvar grava
            page.warn(f"Valor salvo inválido. {ed.saved_error}. Vale o padrão ({ed.shown_text()}), gravado ao salvar.")
            log.info("configurações: o valor salvo de %s não vale; o padrão é gravado ao salvar", key)
        return ed

    def _ask(self, text: str, yes: str, no: str) -> bool:
        return ask_reindex(self, text, yes, no)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fit_to_screen(self)                                # A-17: a janela inteira, com a moldura, na área livre

    @override
    def done(self, result: int) -> None:
        """Aceitar, cancelar ou fechar: solta o preparador (o download segue; uma próxima abertura mostra o progresso)."""
        if self._preparer_connected:
            self._preparer_connected = False
            with suppress(RuntimeError, TypeError):
                self.preparer.changed.disconnect(self._on_prepare_changed)
        super().done(result)

    # ---------------------------------------------------------------- validação e gravação
    def validation_problem(self) -> tuple[str, QWidget | None] | None:
        """O primeiro erro (texto em PT-BR) e o campo dele, ou None: a `SPECS`, a gramática dos atalhos, atalhos repetidos, o
        arquivo de log e as sub-abas dos provedores."""
        hotkeys: list[tuple[str, QWidget]] = []
        for key, ed in self._editors.items():
            value = ed.raw().strip()
            if key in HOTKEY_KEYS:
                err = self.platform.hotkeys().validate(value) if value else None
                if err:
                    return f"{SPECS[key].label}: {err}", ed.widget
                if value:
                    hotkeys.append((value.lower(), ed.widget))
                continue
            err = validate_setting(key, value)
            if err:
                return err, ed.widget
        seen: set[str] = set()
        for combo, widget in hotkeys:
            if combo in seen:
                return "Dois atalhos iguais.", widget
            seen.add(combo)
        if not self.log_path.text().strip():
            return "Informe o arquivo de log.", self.log_path
        return self.prompt_problem() or self.provider_tabs.validation_problem()

    def validation_error(self) -> str | None:
        """Primeiro erro dos campos ou None; separado do `_save` para os testes."""
        problem = self.validation_problem()
        return problem[0] if problem is not None else None

    def language_code(self) -> str:
        """O código do "Idioma padrão" (`pt-BR` ou `en`), pelo `itemData`: o texto da lista é só o rótulo."""
        return str(self.language_combo.currentData())

    @property
    def error_bar(self) -> InfoBar:
        """A faixa do erro de validação (U-072), em cima da página; montada só no primeiro erro."""
        if self._error_bar is None:
            self._error_bar = InfoBar("error")
            self._error_bar.hide()
            row = QHBoxLayout()
            m = theme.METRICS.margin
            row.setContentsMargins(m, theme.METRICS.card_gap, m, 0)
            row.addWidget(self._error_bar)
            self.right.insertLayout(0, row)
        return self._error_bar

    def show_problem(self, message: str, widget: QWidget | None) -> None:
        """U-072: abre a página (e a sub-aba do provedor) do campo, marca o campo com a borda de erro, põe o foco nele e
        explica na faixa, sem caixa de mensagem. As marcas anteriores saem."""
        for old in self._invalid:
            theme.set_invalid(old, False)
        self._invalid = []
        page = next((p for p in self.pages.values() if widget is not None and p.isAncestorOf(widget)), None)
        if page is not None:
            self.nav.setCurrentRow(list(self.pages).index(page.page_title))
        if widget is not None:
            tab = self.provider_tabs.tab_of(widget)
            if tab is not None:
                self.provider_tabs.tabs.setCurrentWidget(tab)
            self.reveal_prompt(widget)                       # F5.6 L2: a tarefa do editor de instruções com o erro
            theme.set_invalid(widget, True)
            self._invalid.append(widget)
            self._watch(widget)
            if page is not None:
                page.ensureWidgetVisible(widget)
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
        self.error_bar.set_text(message)
        self.error_bar.show()
        log.info("configurações: o Salvar parou num erro de validação (página %s)", page.page_title if page else "?")

    def _watch(self, widget: QWidget) -> None:
        """R-09: o sinal de mudança do campo marcado, ligado a um método uma vez por campo (a marca sai quando o valor muda)."""
        signal = change_signal(widget)
        if signal is not None and not any(w is widget for w in self._watched):
            signal.connect(self._on_marked_changed)
            self._watched.append(widget)

    def _on_marked_changed(self, *_args: object) -> None:
        """O usuário mexeu num campo marcado: a marca de erro dele sai (a faixa fica com o motivo até o próximo Salvar)."""
        sender = self.sender()
        if isinstance(sender, QWidget) and sender in self._invalid:
            theme.set_invalid(sender, False)
            self._invalid.remove(sender)

    def _save(self) -> None:
        from ..rag.index import embedder_status

        problem = self.validation_problem()
        if problem is not None:
            self.show_problem(*problem)
            return
        before = embedder_status(self.store).model_key   # comportamento 4: trocar o modelo de embeddings oferece reindexar
        try:                                             # validado acima; a sub-aba grava primeiro e recusa sem meio-termo (R3-12)
            self.provider_tabs.save()
        except ValueError as e:
            self.show_problem(str(e), self.provider_tabs)
            return
        for key, ed in self._editors.items():
            self.store.set_setting(key, ed.raw())
        self.save_prompts()
        token = self.hf_token.text().strip()
        if token != self.store.settings.provider_key(HF_PROVIDER_ID):
            self.store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key=token or None)
        self.offer_reindex(before)
        self.accept()


__all__ = ["REINDEX_TITLE", "SUBTITLES", "TOAST_SECONDS", "SettingsDialog", "ask_reindex", "audio_cost_text", "breakable_path",
           "change_signal", "offer_local_reindex"]
