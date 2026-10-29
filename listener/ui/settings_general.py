"""As páginas das Configurações fora a busca e o ao vivo (F5.4 L3, U-071: saíram do `settings_min.py`, que passava de 400
linhas): Chaves de acesso (U-037), Idioma e avisos (F5.3), Captura com os presets, Atalhos com o estado do registro em selo
(U-075), Processamento (F2, com o transcritor `llm` só com áudio, F3 item 9), LLM (as `llm.*` e as sub-abas dos provedores)
e Log e banco de dados. O subtítulo de cada página diz quando o que está nela vale (U-074) e não tem jargão interno (U-076).

`GeneralPagesMixin` é parte do `SettingsDialog`: lê `store`, `platform`, `hotkey_errors`, `audio_capable`, `provider_tabs` e
`hf_token` dele e registra os editores pelo `_add` dele. F5.7 (U-094): o cartão "Reunião sem gravação" de "Idioma e avisos"
mora em `ui/settings_detect.py`."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QPushButton, QWidget

from ..config.defaults import CAPTURE_KEYS, HOTKEY_KEYS, LLM_KEYS, PIPELINE_KEYS, SPECS
from ..config.language import LANGUAGES, SETTING, default_language, label
from ..llm.costs import AUDIO_TOKENS_PER_SECOND, fmt_usd, price_for
from ..llm.jobconfig import LlmJobConfig
from ..model import SettingScope
from .components import InfoBar
from .keys import KEYS_TITLE, STORAGE_NOTE, hf_state, keys_cards, note
from .settings_editors import ChoiceEditor, SettingEditor, TextEditor
from .settings_detect import DetectCardMixin
from .settings_pages import SettingsPage
from .theme import Tone

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store
    from .providers import ProviderTabs

TOAST_SECONDS = "ui.toast_seconds"                          # F5.3 (U-048): na página "Idioma e avisos"
NOW = SettingScope.NOW
SUBTITLES = {
    KEYS_TITLE: "A LLM escreve a ata, o resumo e o título; o token do Hugging Face identifica quem fala. Valem ao salvar.",
    "Idioma e avisos": "O idioma das reuniões novas e do chat sobre todas as reuniões, o aviso do app no canto da tela e o "
                       "aviso de reunião sem gravação. Aplica agora.",
    "Captura": "O áudio gravado, os alertas de silêncio e de eco e os presets do \"Antes de gravar\". Aplica agora; a linha "
               "diz quando vale só na próxima gravação.",
    "Atalhos": "Valem no Windows inteiro, com o app em segundo plano (ex.: ctrl+alt+shift+r; ctrl+alt sozinho é AltGr no "
               "teclado ABNT2). Aplica agora.",
    "Processamento": "A transcrição e a identificação de quem fala depois da gravação. Vale para o próximo processamento da "
                     "fila.",
    "LLM": "Ata, resumo, título, chat e busca por sentido: qual provedor faz cada um e quanto pode custar. Vale para a "
           "próxima tarefa da LLM e para o chat.",
    "Log e banco de dados": "O arquivo de log do app e a pasta do banco. Aplica agora.",
}
HOTKEY_OK, HOTKEY_FAILED = "Registrado", "Falhou"                       # U-075
HOTKEY_W = 240                                              # o campo do atalho, igual em todas as linhas


def breakable_path(path: object) -> str:
    """O caminho com um ponto de quebra invisível (U+200B) depois de cada separador: um caminho longo não tem espaço onde
    quebrar e, numa linha só, alargava o formulário além do diálogo."""
    return str(path).replace("\\", "\\\u200b").replace("/", "/\u200b")


def audio_cost_text(store: Store) -> str:
    """"≈ US$ 0,12 por hora de áudio" com o preço de áudio do modelo rápido do provedor ativo (32 tokens/s, F3A-40)."""
    cfg = LlmJobConfig.from_store(store)
    price = price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, cfg.fast_model)
    if price is None:
        return "sem preço cadastrado para estimar o custo por hora de áudio"
    per_hour = AUDIO_TOKENS_PER_SECOND * 3600 * (price.audio_usd_m or price.input_usd_m) / 1_000_000
    return f"≈ {fmt_usd(per_hour)} por hora de áudio no modelo {cfg.fast_model}"


class GeneralPagesMixin(DetectCardMixin):
    """As páginas comuns do `SettingsDialog`."""
    store: Store
    platform: Platform
    hotkey_errors: dict[str, str]
    audio_capable: bool
    provider_tabs: ProviderTabs
    hf_token: QLineEdit
    _editors: dict[str, SettingEditor]

    def _add(self, page: SettingsPage, form: QFormLayout, key: str, editor: SettingEditor | None = None,
             badge: tuple[str, Tone] | None = None, *, compact: bool = False) -> SettingEditor:
        raise NotImplementedError

    def _keys_page(self) -> SettingsPage:
        page = SettingsPage(KEYS_TITLE, SUBTITLES[KEYS_TITLE])
        cards, self.hf_badge = keys_cards(self.provider_tabs, self.hf_token)
        for card in cards:
            page.content.addWidget(card)
        page.content.addWidget(note(STORAGE_NOTE))
        page.finish()
        self.hf_token.textChanged.connect(self._hf_changed)
        return page

    def _hf_changed(self, text: str) -> None:
        badge, value = hf_state(text)
        self.hf_badge.setText(badge)
        self.hf_badge.set_tone(value)

    def _language_page(self) -> SettingsPage:
        """"Idioma e avisos" (F5.3, A-03): o idioma padrão das reuniões novas e do chat, gravado pelo código (o texto da lista
        é só o rótulo); (U-048) quanto o aviso do app fica no canto da tela, 0 volta ao balão da bandeja."""
        page = SettingsPage("Idioma e avisos", SUBTITLES["Idioma e avisos"], NOW)
        form = page.form()
        lang = ChoiceEditor(SETTING, default_language(self.store), [(label(code), code) for code in LANGUAGES])
        lang.widget.setToolTip("O idioma das reuniões novas (transcrição, legenda ao vivo e tudo o que a LLM escreve) e do chat "
                               "sobre todas as reuniões. Cada reunião guarda o seu: a gravada não muda daqui.")
        self.language_combo = lang.combo
        self._add(page, form, SETTING, lang)
        toast = self._add(page, form, TOAST_SECONDS)
        toast.widget.setToolTip("Quanto tempo o aviso do listener-meet (ata pronta, erro na fila, download...) fica no canto da "
                                "tela; com o mouse em cima ele não some. De 0 a 60; 0 desliga o aviso do app e volta ao balão "
                                "do Windows.")
        self._detect_card(page)                                  # F5.7 (U-094)
        page.finish()
        return page

    def _capture_page(self) -> SettingsPage:
        page = SettingsPage("Captura", SUBTITLES["Captura"], NOW)
        form = page.form()
        for key in CAPTURE_KEYS:
            self._add(page, form, key)
        presets = page.card("Presets")
        self.presets = QListWidget()
        for p in self.store.list_presets():
            self.presets.addItem(f"{p['id']}: {p['name']} — {p['source_kind']} {p.get('source_exe') or ''}")
        delete = QPushButton("Apagar preset selecionado")
        delete.clicked.connect(self._delete_preset)
        presets.content.addWidget(self.presets)
        presets.content.addWidget(delete, 0, Qt.AlignmentFlag.AlignLeft)
        page.finish()
        return page

    def _hotkeys_page(self) -> SettingsPage:
        """Os atalhos com o estado do registro em selo (U-075): Registrado ou Falhou, com o motivo embaixo."""
        page = SettingsPage("Atalhos", SUBTITLES["Atalhos"], NOW)
        form = page.form()
        for key in HOTKEY_KEYS:
            combo = self.store.get_setting(key)
            failed = self.hotkey_errors.get(combo) if combo else None
            badge = (HOTKEY_FAILED, Tone.DANGER) if failed else (HOTKEY_OK, Tone.SUCCESS) if combo else None
            ed = self._add(page, form, key, badge=badge, compact=True)
            ed.widget.setMinimumWidth(HOTKEY_W)
            if failed:
                page.add_note(form, f"O Windows não registrou: {failed}")
        page.finish()
        return page

    def _processing_page(self) -> SettingsPage:
        page = SettingsPage("Processamento", SUBTITLES["Processamento"], NOW)
        form = page.form()
        for key in PIPELINE_KEYS:
            self._add(page, form, key)
        self._transcriber_choice()
        page.finish()
        return page

    def _transcriber_choice(self) -> None:
        """F3 item 9: a transcrição pela LLM só com um provedor ativo com chave cujo modelo rápido aceite áudio."""
        ed = self._editors["pipeline.transcriber"]
        i = ed.combo.findData("llm") if isinstance(ed, ChoiceEditor) else -1
        if i < 0 or not isinstance(ed, ChoiceEditor):
            return
        if not self.audio_capable:
            ed.combo.model().item(i).setEnabled(False)  # type: ignore[attr-defined]
            ed.combo.setToolTip("transcrição pela LLM: precisa de um provedor ativo com chave cujo modelo rápido aceite áudio")
        else:
            ed.combo.setToolTip(f"transcrição pela LLM: {audio_cost_text(self.store)}")

    def _llm_page(self) -> SettingsPage:
        page = SettingsPage("LLM", SUBTITLES["LLM"], NOW)
        form = page.form("Geral")
        for key in LLM_KEYS:
            self._add(page, form, key)
        page.card("Provedores").content.addWidget(self.provider_tabs)
        page.finish()
        return page

    def _data_page(self) -> SettingsPage:
        """O log (aplica agora) e a pasta do banco; o caminho quebra a linha em vez de alargar a página."""
        page = SettingsPage("Log e banco de dados", SUBTITLES["Log e banco de dados"], NOW)
        form = page.form("Log")
        log_path = TextEditor("log.path", self.store.get_setting("log.path"), str(self.store.default_log_path()))
        self._editors["log.path"] = log_path
        self.log_path = log_path.edit
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse_log)
        row = QHBoxLayout()
        row.addWidget(self.log_path, 1)
        row.addWidget(browse)
        form.addRow(SPECS["log.path"].label, row)
        self._add(page, form, "log.level")
        db = page.card("Banco de dados")
        db.content.addWidget(note(f"Pasta: {breakable_path(self.store.base_dir)} (esquema v{self.store.schema_version()}; "
                                  f"{len(self.store.audio_files())} arquivo(s) de áudio, um por mês)"))
        synced = self.platform.synced_folder_name(self.store.base_dir)
        if synced:
            db.content.addWidget(InfoBar("warning", f"Esta pasta está dentro do {synced}: o banco sairá da máquina pela "
                                                    "sincronização."))
        db.content.addWidget(note("Mover o banco e fazer backup ainda não estão no app."))
        page.finish()
        return page

    def _browse_log(self) -> None:
        parent = self if isinstance(self, QWidget) else None
        f, _ = QFileDialog.getSaveFileName(parent, "Arquivo de log", self.log_path.text(), "Log (*.log);;Todos (*)")
        if f:
            self.log_path.setText(f)

    def _delete_preset(self) -> None:
        it = self.presets.currentItem()
        if not it:
            return
        pid = int(it.text().split(":", 1)[0])
        self.store.delete_preset(pid)
        self.presets.takeItem(self.presets.row(it))


__all__ = ["HOTKEY_FAILED", "HOTKEY_OK", "SUBTITLES", "TOAST_SECONDS", "GeneralPagesMixin", "audio_cost_text", "breakable_path"]
