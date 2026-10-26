"""Chaves de acesso (U-037, D-053): o primeiro grupo das Configurações tem a chave de cada provedor de LLM (com o Testar e
o estado) e o token do Hugging Face, cada um com onde conseguir; o warm-up avisa antes de gravar quando falta o token ou a
chave da LLM ativa, com o link para as chaves; as mensagens de chave e token apontam para lá. U-038 (D-054): o assistente
pede a chave da LLM escolhida (Gemini, GPT ou Claude) e o token, logo depois da pasta do banco, e o "Concluir" grava."""
from __future__ import annotations

import logging
import sys

import pytest

from listener.config.defaults import HF_PROVIDER_ID

pytest.importorskip("PySide6")
KEY = "AIza-chave-de-teste-ABCDEFGH12345678"


@pytest.fixture
def forget_models():
    """`pipeline/models.py` sai do `sys.modules` ao fim: a regra dura do `test_runner` confere que o app não o puxa."""
    yield
    sys.modules.pop("listener.pipeline.models", None)


def test_missing_keys_and_the_warmup_text(store):
    from listener.ui.keys import missing_keys, missing_keys_text

    assert missing_keys(store) == ("hf", "llm")
    text = missing_keys_text(missing_keys(store))
    assert text.startswith("Sem o token do Hugging Face") and "sem a chave da LLM" in text and text.endswith(
        '<a href="settings">Configurar as chaves</a>')
    store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key="hf_abc")
    assert missing_keys(store) == ("llm",) and missing_keys_text(("llm",)).startswith("Sem a chave da LLM")
    store.providers.update_provider("gemini", api_key=KEY)
    assert missing_keys(store) == () and missing_keys_text(()) == ""
    store.providers.set_role("llm", None)                                             # nenhum ativo: falta a LLM
    assert missing_keys(store) == ("llm",)


def test_key_and_token_messages_point_to_the_keys_group(store, tmp_path, forget_models):
    from listener.pipeline.base import PermanentError
    from listener.pipeline.models import download_pyannote
    from listener.pipeline.types import JobConfig
    from listener.rag.index import embedder_status

    assert embedder_status(store).reason.endswith("(Configurações, Chaves de acesso)")
    with pytest.raises(PermanentError, match="Configurações, Chaves de acesso"):
        download_pyannote(JobConfig(models_dir=str(tmp_path)), "", lambda *_a: None)


def test_settings_open_with_the_keys_group_first(qtbot, store, platform, monkeypatch):
    from PySide6.QtWidgets import QGroupBox, QLabel, QScrollArea

    from listener.ui.keys import KEYS_TITLE
    from listener.ui.providers import KEY_EDITED, NO_KEY
    from listener.ui.settings_min import SettingsDialog

    monkeypatch.setenv("LISTENER_LLM", "fake")
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    first = dlg.findChild(QScrollArea).widget().layout().itemAt(0).widget()
    assert isinstance(first, QGroupBox) and first.title().startswith(KEYS_TITLE)
    tabs = dlg.provider_tabs.tab_by_id
    assert set(tabs) == {"gemini", "openai", "anthropic"} and first.isAncestorOf(dlg.hf_token)
    for tab in tabs.values():                                    # a chave, o Testar e o estado: no primeiro grupo, não na aba
        assert all(first.isAncestorOf(w) and not tab.isAncestorOf(w) for w in (tab.key, tab.btn_test, tab.key_status))
    helps = [label for label in first.findChildren(QLabel) if label.openExternalLinks()]
    links = " ".join(label.text() for label in helps)
    assert all(url in links for url in ("aistudio.google.com", "huggingface.co/settings/tokens",
                                        "pyannote/speaker-diarization-community-1", "pyannote/wespeaker-voxceleb-resnet34-LM"))
    dlg.show()                                                   # a ajuda com links tem a altura do texto, sem vazio em volta
    qtbot.waitUntil(lambda: all(h.height() == h.heightForWidth(h.width()) for h in helps), timeout=3000)
    gemini = tabs["gemini"]
    assert gemini.key_status.text() == NO_KEY
    gemini.key.setText(KEY)
    assert gemini.key_status.text() == KEY_EDITED
    gemini.key.setText("")
    assert gemini.key_status.text() == NO_KEY
    gemini.key.setText(KEY)
    gemini.fast.setCurrentText("fake-fast")
    gemini.btn_test.click()                                      # testa a chave digitada, antes de salvar
    qtbot.waitUntil(lambda: gemini.last_test is not None, timeout=3000)
    assert gemini.key_status.text().startswith("ok: ") and gemini.key_status.text() == gemini.status.text()
    dlg.hf_token.setText("hf_abc")
    dlg._save()
    assert store.providers.get("gemini")["api_key"] == KEY and store.settings.provider_key(HF_PROVIDER_ID) == "hf_abc"


def _wizard(qtbot, tmp_path):
    from listener.ui.wizard import FirstRunWizard
    from tests.fake_platform import FakePlatform

    wiz = FirstRunWizard(FakePlatform(tmp_path), tmp_path / "db")
    qtbot.addWidget(wiz)
    wiz.setField("name", "Ana Souza")
    return wiz


def test_wizard_asks_for_the_keys_right_after_the_database(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    wiz = _wizard(qtbot, tmp_path)
    pages = [wiz.page(i) for i in wiz.pageIds()]
    assert pages.index(wiz.keys_page) == pages.index(wiz.db_page) + 1
    page = wiz.keys_page
    assert [page.provider.itemData(i) for i in range(page.provider.count())] == ["gemini", "openai", "anthropic"]
    assert page.chosen() == "gemini" and "aistudio.google.com" in page.llm_help.text()
    page.provider.setCurrentIndex(page.provider.findData("anthropic"))           # o link e o aviso seguem a escolha
    assert "console.anthropic.com" in page.llm_help.text() and "busca local" in page.llm_help.text()
    page.provider.setCurrentIndex(page.provider.findData("gemini"))
    page.run_test()
    assert page.status.text() == "cole a chave antes de testar"
    page.key.setText(KEY)
    page.test_btn.click()                                        # antes de o banco existir: a rede padrão, sem gravar nada
    qtbot.waitUntil(lambda: page.last_test is not None, timeout=3000)
    assert page.status.text().startswith("ok: ")
    page.key.setText(KEY + "x")                                  # outra chave: o teste anterior não vale mais
    assert page.last_test is None and page.status.text() == ""


def test_wizard_saves_the_chosen_llm_key_and_the_token(qtbot, tmp_path, monkeypatch, caplog):
    from listener.ui.keys import missing_keys

    monkeypatch.setenv("LISTENER_LLM", "fake")
    wiz = _wizard(qtbot, tmp_path)
    page = wiz.keys_page
    page.provider.setCurrentIndex(page.provider.findData("openai"))
    page.key.setText(KEY)
    page.test_btn.click()
    qtbot.waitUntil(lambda: page.last_test is not None, timeout=3000)
    page.hf_token.setText("hf_abc")
    with caplog.at_level(logging.INFO, logger="listener.ui.keys"):
        wiz.accept()
    store = wiz.store
    assert store is not None
    try:
        row = store.providers.get("openai")
        assert row["api_key"] == KEY and row["tested_at"] and store.settings.provider_key(HF_PROVIDER_ID) == "hf_abc"
        assert store.providers.active_provider("llm")["id"] == "openai" and store.providers.active_provider("embed")["id"] == "openai"
        assert missing_keys(store) == () and "com chave" in caplog.text and KEY not in caplog.text and "hf_abc" not in caplog.text
    finally:
        store.close()


def test_wizard_with_claude_puts_the_search_on_the_local_and_skipping_saves_no_key(qtbot, tmp_path):
    from listener.ui.keys import missing_keys

    wiz = _wizard(qtbot, tmp_path / "claude")
    wiz.keys_page.provider.setCurrentIndex(wiz.keys_page.provider.findData("anthropic"))
    wiz.keys_page.key.setText(KEY)
    wiz.accept()
    store = wiz.store
    assert store is not None
    try:                                                         # o Claude não faz embeddings: a busca local, sem custo
        assert store.providers.active_provider("llm")["id"] == "anthropic" and store.providers.active_provider("embed")["id"] == "local"
        assert missing_keys(store) == ("hf",)
    finally:
        store.close()
    skip = _wizard(qtbot, tmp_path / "pulou")
    skip.accept()                                                # tudo opcional: nada gravado, o Gemini segue o ativo
    store = skip.store
    assert store is not None
    try:
        assert store.providers.get("gemini")["api_key"] in (None, "") and not store.settings.provider_key(HF_PROVIDER_ID)
        assert store.providers.active_provider("llm")["id"] == "gemini" and missing_keys(store) == ("hf", "llm")
    finally:
        store.close()


def test_warmup_warns_before_recording_when_keys_are_missing(qtbot, store, platform):
    from listener.ui import warmup as wu

    def configure() -> None:                                     # o que o dono faz nas Configurações abertas pelo link
        store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key="hf_abc")
        store.providers.update_provider("gemini", api_key=KEY)

    dlg = wu.WarmupDialog(platform, store, on_settings=configure)
    qtbot.addWidget(dlg)
    assert not dlg.keys_note.isHidden() and "Hugging Face" in dlg.keys_note.text() and "LLM" in dlg.keys_note.text()
    dlg.keys_note.linkActivated.emit("settings")
    assert dlg.keys_note.isHidden() and dlg.keys_note.text() == ""                       # nada falta: o aviso some
    dlg.reject()
