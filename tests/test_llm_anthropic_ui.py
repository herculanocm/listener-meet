"""F5.14 L2: as telas do Claude. U-134: o aviso do envio nomeia o provedor em cada sub-aba e o assistente diz que o texto
das reuniões vai para a IA escolhida; U-135: com o Claude ativo para texto (e só então), a nota sob o "Ativo para
embeddings" diz que a busca por sentido segue com o escolhido ali, e o "Modelo de embeddings" do Claude diz o porquê na
dica; os Parâmetros de cada provedor mostram um exemplo que o adaptador dele lê."""
from __future__ import annotations

import json

import pytest

from listener.llm.types import LlmTask, Message, Request, Role

pytest.importorskip("PySide6")


def _tabs(qtbot, store):
    from listener.ui.providers import ProviderTabs

    tabs = ProviderTabs(store)
    qtbot.addWidget(tabs)
    return tabs


def test_each_tab_names_its_provider_and_shows_a_parameter_it_reads(qtbot, store):
    from listener.llm.anthropic_request import ClaudeParams
    from listener.llm.gemini import GeminiProvider
    from listener.llm.openai import OpenAIProvider
    from listener.ui.components import InfoBar
    from listener.ui.providers import NO_EMBED_TIP, PARAMS_EXAMPLE, SEND_WARNING

    tabs = _tabs(qtbot, store)
    names = {"gemini": "o Google (Gemini)", "openai": "a OpenAI (GPT)", "anthropic": "a Anthropic (Claude)"}
    for pid, tab in tabs.tab_by_id.items():
        bars = [b.label.text() for b in tab.findChildren(InfoBar)]
        assert bars == [SEND_WARNING[pid]] and names[pid] in bars[0] and bars[0].startswith("O texto da reunião")
        assert tab.params.placeholderText() == f"JSON opcional, ex.: {PARAMS_EXAMPLE[pid]}"
    assert SEND_WARNING["anthropic"].endswith("O Claude não recebe áudio.") and "Áudio só sai" in SEND_WARNING["gemini"]
    claude, gemini = tabs.tab_by_id["anthropic"], tabs.tab_by_id["gemini"]
    assert not claude.embed_model.isEnabled() and claude.embed_model.toolTip() == NO_EMBED_TIP
    assert gemini.embed_model.isEnabled() and gemini.embed_model.toolTip() == ""
    req = Request(LlmTask.CHAT, "m", (Message(Role.USER, "oi"),))
    assert ClaudeParams.parse(json.loads(PARAMS_EXAMPLE["anthropic"]), "anthropic").effort_strong == "high"
    assert OpenAIProvider("k", params=json.loads(PARAMS_EXAMPLE["openai"]))._kwargs(req)["service_tier"] == "flex"
    assert GeminiProvider("k", params=json.loads(PARAMS_EXAMPLE["gemini"]))._config(req, None)["top_p"] == 0.9


def test_the_search_note_shows_only_while_claude_writes_the_text(qtbot, store):
    from listener.ui.providers import CLAUDE_SEARCH

    tabs = _tabs(qtbot, store)
    tabs.show()
    qtbot.waitExposed(tabs)
    assert tabs.search_note.text() == CLAUDE_SEARCH and not tabs.search_note.isVisible()
    assert tabs.form.labelForField(tabs.search_note) is None                       # na coluna do campo, sob o combo
    for pid, shown in (("anthropic", True), ("openai", False), ("anthropic", True), ("", False)):
        tabs.active_llm.setCurrentIndex(tabs.active_llm.findData(pid))
        assert tabs.search_note.isVisible() is shown and tabs.form.isRowVisible(tabs.search_note) is shown, pid
    assert tabs.form.rowCount() == 3 and tabs.form.labelForField(tabs.active_embed).text() == "Ativo para embeddings"
    store.providers.set_role("llm", "anthropic")                                     # aberta já com o Claude ativo
    claude = _tabs(qtbot, store)
    claude.show()
    qtbot.waitExposed(claude)
    assert claude.search_note.isVisible() and claude.active_embed.currentData() == "gemini"   # a busca segue onde estava


def test_the_wizard_says_the_text_goes_to_the_chosen_ai(qtbot):
    from PySide6.QtWidgets import QLabel

    from listener.ui.keys import WIZARD_SEND, KeysPage

    page = KeysPage()
    qtbot.addWidget(page)
    assert WIZARD_SEND == "O texto das reuniões vai para a IA escolhida."
    assert WIZARD_SEND in [label.text() for label in page.findChildren(QLabel)]
