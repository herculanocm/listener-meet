"""F5.6 L2 (U-092; A-25, A-27, A-29 a A-31; U-072; Aceite 3): a página "Prompts" das Configurações e o "Restaurar o prompt
padrão" da barra da reunião. A página lista as onze tarefas e só monta o editor da escolhida; o núcleo avisa; "Restaurar
padrão" volta ao do app e texto igual ao padrão grava vazio; o aviso do padrão que mudou; a parte fixa, só leitura, com os
marcadores; o Salvar barra vazio, variável desconhecida e texto longo abrindo a tarefa e marcando o editor; e o "falhou"
fora do esquema com instruções suas oferece restaurar, que restaura direto."""
from __future__ import annotations

from typing import Any

import pytest

from listener.config import prompts as prompt_settings
from listener.llm import prompts
from listener.llm.base import OFF_SCHEMA, BadResponseError
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask
from listener.model import JobStatus
from listener.store import Store
from tests.test_llm_steps import _ask, _run_llm
from tests.test_prompts_custom import _ready_for_minutes

MINE = "Você escreve a ata de um comitê de crédito. Destaque valores e prazos, em {idioma}."


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _dialog(qtbot: Any, store: Store, platform: Any) -> Any:
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------- A-25, A-30: a lista e o editor sob demanda
def test_the_page_lists_the_eleven_tasks_and_builds_an_editor_only_when_one_is_picked(qtbot, store, platform):
    from PySide6.QtWidgets import QListWidget, QPlainTextEdit

    from listener.ui.settings_prompts import PROMPTS_TITLE, TASKS

    dlg = _dialog(qtbot, store, platform)
    titles = list(dlg.pages)
    assert titles.index(PROMPTS_TITLE) == titles.index("LLM") + 1
    page = dlg.page(PROMPTS_TITLE)
    assert dlg._prompt_ui is None and page.findChildren(QListWidget) == []                 # nem a lista ao abrir
    dlg.nav.setCurrentRow(titles.index(PROMPTS_TITLE))                                     # a página aparece: a lista nasce
    assert dlg._prompt_ui is not None and page.findChildren(QListWidget) == [dlg.prompt_list]
    texts = [dlg.prompt_list.item(i).text() for i in range(dlg.prompt_list.count())]
    assert texts == [f"{TASKS[t][0]}\n{TASKS[t][1]}" for t in prompts.EDITABLE] and len(texts) == 12   # F5.10: sem o rag_chat
    assert "Ata e ações\na pedido" in texts and "Identificação das vozes\nautomática, ao processar" in texts
    assert dlg.prompt_panes == {} and page.findChildren(QPlainTextEdit) == []           # nada montado ao abrir
    dlg.prompt_list.setCurrentRow(prompts.EDITABLE.index(LlmTask.MINUTES))
    pane = dlg.prompt_panes[LlmTask.MINUTES]
    assert list(dlg.prompt_panes) == [LlmTask.MINUTES] and dlg.prompt_stack.currentWidget() is pane
    assert pane.edit.toPlainText() == prompts.minutes.INSTRUCTIONS and not pane.edit.isReadOnly() and pane.fixed.isReadOnly()
    assert set(page.findChildren(QPlainTextEdit)) == {pane.edit, pane.fixed}
    assert dlg.select_prompt(LlmTask.MINUTES) is pane and len(dlg.prompt_panes) == 1     # escolher de novo não remonta


def test_core_tasks_warn_that_they_change_identification_and_transcription(qtbot, store, platform):
    from listener.ui.components import InfoBar
    from listener.ui.settings_prompts import CORE, CORE_WARNING

    dlg = _dialog(qtbot, store, platform)
    assert {str(t) for t in CORE} == {"identify", "revise", "title", "glossary", "transcribe"}
    for task in prompts.EDITABLE:
        bars = [b.label.text() for b in dlg.select_prompt(task).findChildren(InfoBar)]
        assert bars == ([CORE_WARNING] if task in CORE else []), task
    assert "identificação das vozes e a transcrição" in CORE_WARNING


def test_the_fixed_part_is_read_only_with_markers_contract_and_schema(qtbot, store, platform):
    dlg = _dialog(qtbot, store, platform)
    fixed = dlg.select_prompt(LlmTask.MINUTES).fixed.toPlainText()
    assert fixed.startswith(f"[Sistema]\n{{suas instruções}} {prompts.minutes.CONTRACT}\n"
                            "Escreva todo texto livre em {idioma da reunião}.")
    for mark in ("{transcrição}", "{falantes identificados}", "{pessoas cadastradas}", "{marcadores com a nota}", "{data}",
                 prompts.minutes.REDUCE_SYSTEM, "[Formato da resposta]", '"segment_ids"'):
        assert mark in fixed, mark
    assert prompts.minutes.INSTRUCTIONS not in fixed and "#0 [00:00]" not in fixed
    chat = dlg.select_prompt(LlmTask.CHAT).fixed.toPlainText()
    assert "{pergunta}" in chat and "[Modelo]" in chat and chat.endswith("[Formato da resposta]\nTexto livre.")
    audio = dlg.select_prompt(LlmTask.TRANSCRIBE).fixed.toPlainText()
    assert "[+ áudio: {áudio da janela}]" in audio


# ---------------------------------------------------------------- salvar, restaurar e o padrão
def test_edit_save_reopen_restore_and_save_again(qtbot, store, platform):
    from listener.ui.settings_prompts import CUSTOM_BADGE, DEFAULT_BADGE

    dlg = _dialog(qtbot, store, platform)
    pane = dlg.select_prompt(LlmTask.MINUTES)
    assert (pane.badge.text(), pane.restore.isEnabled()) == (DEFAULT_BADGE[0], False)
    pane.edit.setPlainText(MINE)
    assert (pane.badge.text(), pane.restore.isEnabled()) == (CUSTOM_BADGE[0], True)
    assert pane.count.text() == f"{len(MINE)} de 4.000 caracteres"
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted
    assert store.get_setting("prompt.minutes") == MINE and prompt_settings.custom_prompts(store) == {"minutes": MINE}
    assert store.get_setting("prompt.minutes.base") == prompt_settings.text_hash(prompts.minutes.INSTRUCTIONS, 12)
    again = _dialog(qtbot, store, platform)
    pane2 = again.select_prompt(LlmTask.MINUTES)
    assert pane2.edit.toPlainText() == MINE and pane2.changed_bar is None
    pane2.restore.click()                                                                 # "Restaurar padrão"
    assert pane2.edit.toPlainText() == prompts.minutes.INSTRUCTIONS and pane2.badge.text() == DEFAULT_BADGE[0]
    again._save()
    assert store.get_setting("prompt.minutes") == "" and store.get_setting("prompt.minutes.base") == ""


def test_text_equal_to_the_default_saves_empty_and_unopened_tasks_are_untouched(qtbot, store, platform):
    store.set_setting("prompt.chat", "Meu chat.")
    dlg = _dialog(qtbot, store, platform)
    dlg.select_prompt(LlmTask.TITLE).edit.setPlainText(f"\n  {prompts.title.INSTRUCTIONS}  \n")
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted
    assert store.get_setting("prompt.title") == "" and store.get_setting("prompt.chat") == "Meu chat."


def test_the_changed_default_warns_until_the_text_is_saved_again(qtbot, store, platform):
    from listener.ui.settings_prompts import CHANGED_WARNING

    store.set_setting("prompt.executive", "Resumo para o conselho.")
    store.set_setting("prompt.executive.base", "000000000000")                                # o padrão de uma versão antiga
    dlg = _dialog(qtbot, store, platform)
    pane = dlg.select_prompt(LlmTask.EXECUTIVE)
    assert pane.changed_bar is not None and pane.changed_bar.label.text() == CHANGED_WARNING
    pane.edit.setPlainText("Resumo para o conselho, em três frases.")
    dlg._save()
    assert _dialog(qtbot, store, platform).select_prompt(LlmTask.EXECUTIVE).changed_bar is None


def test_text_saved_over_the_english_default_sees_the_warning_and_restore_brings_pt_br(qtbot, store, platform):
    """D-071: quem editou quando o padrão do app era em inglês vê o aviso de padrão mudado, e "Restaurar padrão" traz o
    pt-BR; salvo de novo, o aviso some."""
    from listener.ui.settings_prompts import CHANGED_WARNING

    old_default = "You write meeting minutes from a transcript. Report only what was actually said."     # a ata até a 1.2
    assert prompt_settings.save(store, "minutes", "You write minutes for the board.", old_default)
    dlg = _dialog(qtbot, store, platform)
    pane = dlg.select_prompt(LlmTask.MINUTES)
    assert pane.changed_bar is not None and pane.changed_bar.label.text() == CHANGED_WARNING
    pane.restore.click()
    assert pane.edit.toPlainText() == prompts.minutes.INSTRUCTIONS
    assert prompts.minutes.INSTRUCTIONS == "Escreva a ata de uma reunião a partir da transcrição. Relate só o que foi dito de fato."
    dlg._save()
    assert store.get_setting("prompt.minutes") == "" and store.get_setting("prompt.minutes.base") == ""
    assert _dialog(qtbot, store, platform).select_prompt(LlmTask.MINUTES).changed_bar is None


# ---------------------------------------------------------------- A-27, U-072: o Salvar barra
@pytest.mark.parametrize(("text", "says"), [("  \n ", "as instruções estão vazias"),
                                            ("Ata para {cliente} em {idioma}.", "{cliente} não é uma variável conhecida"),
                                            ("Ata. " * 820, "as instruções têm 4.100 caracteres; o limite é 4.000")],
                         ids=["vazio", "variavel", "longo"])
def test_the_save_refuses_empty_unknown_variable_and_long_text(qtbot, store, platform, monkeypatch, text, says):
    from PySide6.QtWidgets import QMessageBox

    from listener.ui.settings_min import change_signal
    from listener.ui.settings_prompts import PROMPTS_TITLE

    boxes: list[Any] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(a))
    dlg = _dialog(qtbot, store, platform)
    pane = dlg.select_prompt(LlmTask.MINUTES)
    pane.edit.setPlainText(text)
    dlg.select_prompt(LlmTask.IDENTIFY)                                                     # o erro reabre a tarefa dele
    dlg.nav.setCurrentRow(0)
    dlg._save()
    assert dlg.result() != dlg.DialogCode.Accepted and boxes == [] and store.get_setting("prompt.minutes") == ""
    assert dlg.stack.currentWidget() is dlg.page(PROMPTS_TITLE) and dlg.prompt_stack.currentWidget() is pane
    assert pane.edit.property("invalid") and dlg.error_bar.label.text().startswith(f"Prompts, Ata e ações: {says}")
    assert change_signal(pane.edit) is not None
    pane.edit.setPlainText("Ata em {idioma}.")                                             # mexeu: a marca sai (R-09)
    assert not pane.edit.property("invalid")
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted and store.get_setting("prompt.minutes") == "Ata em {idioma}."


# ---------------------------------------------------------------- A-29: "falhou" com instruções suas, na reunião
@pytest.mark.usefixtures("no_summary_gate")
def test_the_bar_offers_to_restore_the_default_prompt_after_an_off_schema_failure(qtbot, platform, store):
    from listener.pipeline.llm_requests import Species
    from listener.ui.llm_request_bar import RESTORE, RESTORED, RETRY, custom_text
    from listener.ui.meeting import MeetingWindow

    mid = _ready_for_minutes(platform, store)
    store.set_setting("prompt.minutes", "Ata em versos.")
    _ask(store, mid, ("minutes",))
    fake = FakeProvider()
    fake.fail_next(BadResponseError(f"{OFF_SCHEMA} após reenvio: $.decisions: esperava lista"), LlmTask.MINUTES)
    assert _run_llm(store, fake)[1] == JobStatus.ERROR
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    messages: list[str] = []
    win.llm.on_message = messages.append
    bar = win.llm.bar
    assert bar is not None and bar.buttons[Species.MINUTES].text() == RETRY and bar.custom == ("minutes",)
    assert bar.restore is not None and not bar.restore.isHidden() and bar.restore.text() == RESTORE
    assert bar.note.text() == custom_text(("minutes",)) == "A resposta saiu fora do formato com as suas instruções de ata."
    bar.restore.click()
    assert store.get_setting("prompt.minutes") == "" and messages == [RESTORED]
    assert bar.custom == () and bar.restore.isHidden() and bar.note.isHidden()
    assert bar.buttons[Species.MINUTES].text() == RETRY                     # a falha fica; "Gerar de novo" pede com o padrão


def test_the_bar_without_a_custom_failure_never_builds_the_restore_button(qtbot):
    from listener.pipeline.llm_requests import RequestState, Species, State
    from listener.ui.llm_request_bar import RequestBar

    restored: list[tuple[str, ...]] = []
    bar = RequestBar(_ignore, on_restore=restored.append)
    qtbot.addWidget(bar)
    plain = RequestState(State.FAILED, error=f"Ata falhou: {OFF_SCHEMA}: x")
    bar.show_states({Species.MINUTES: plain, Species.SUMMARY: RequestState(State.NONE)})
    assert bar.restore is None and bar.note.isHidden()
    custom = RequestState(State.FAILED, error=f"Ata falhou: {OFF_SCHEMA}: x", custom=("minutes",))
    bar.show_states({Species.MINUTES: custom, Species.SUMMARY: RequestState(State.NONE)}, off="Sem chave de LLM.")
    assert bar.restore is None and bar.note.text() == "Sem chave de LLM."                   # sem chave: o motivo, sem restaurar
    bar.show_states({Species.MINUTES: custom, Species.SUMMARY: RequestState(State.NONE)})
    assert bar.restore is not None and not bar.restore.isHidden()
    bar.restore.click()
    assert restored == [("minutes",)]


def _ignore(*_args: object) -> None:
    """O `on_ask` da barra no teste: nada pedido."""


# ---------------------------------------------------------------- revisão do L2: teclado (R-04, R-05)
def test_tab_in_the_instructions_and_in_the_fixed_part_moves_focus(qtbot, store, platform):
    """R-04: Tab vai ao próximo campo, sem escrever uma tabulação nas instruções (e sai da prévia só leitura)."""
    from PySide6.QtCore import Qt

    dlg = _dialog(qtbot, store, platform)
    dlg.show()
    qtbot.waitExposed(dlg)
    dlg.nav.setCurrentRow(list(dlg.pages).index("Prompts"))
    pane = dlg.select_prompt(LlmTask.MINUTES)
    pane.edit.setFocus()
    qtbot.wait(10)
    before = pane.edit.toPlainText()
    qtbot.keyClick(pane.edit, Qt.Key.Key_Tab)
    assert pane.edit.toPlainText() == before and "\t" not in pane.edit.toPlainText() and not pane.edit.hasFocus()
    assert pane.edit.tabChangesFocus() and pane.fixed.tabChangesFocus()


def test_the_restore_button_follows_the_bar_buttons_in_tab_order(qtbot):
    """R-05: montado quando a falha chega, com a janela aberta, o "Restaurar o prompt padrão" vem logo depois de "Gerar
    resumo" no Tab, não no fim da janela."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

    from listener.pipeline.llm_requests import RequestState, Species, State
    from listener.ui.llm_request_bar import RequestBar

    win = QWidget()
    qtbot.addWidget(win)
    lay = QVBoxLayout(win)
    bar = RequestBar(_ignore, on_restore=_ignore)
    lay.addWidget(bar)
    lay.addWidget(QLineEdit("um campo depois da barra (as abas da reunião)"))
    win.show()
    custom = RequestState(State.FAILED, error=f"Ata falhou: {OFF_SCHEMA}: x", custom=("minutes",))
    bar.show_states({Species.MINUTES: custom, Species.SUMMARY: RequestState(State.NONE)})
    start = bar.buttons[Species.SUMMARY]
    w = start.nextInFocusChain()
    while w is not start and not (w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisibleTo(win)):
        w = w.nextInFocusChain()
    assert w is bar.restore
