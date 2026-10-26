"""Chaves de acesso (U-037, D-053): o primeiro grupo das Configurações tem a chave de cada provedor de LLM (com o Testar e
o estado) e o token do Hugging Face, cada um com onde conseguir; o warm-up avisa antes de gravar quando falta o token ou a
chave da LLM ativa, com o link para as chaves; as mensagens de chave e token apontam para lá."""
from __future__ import annotations

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
