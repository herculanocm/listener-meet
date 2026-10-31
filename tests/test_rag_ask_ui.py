"""F5.10 L4 (Interface, D6, D7, U-096, U-097; telas com pytest-qt, offscreen): "Perguntar às reuniões" roda o agente, com o
progresso ("Consultando a base… rodada 1 · 1 consulta" e "escrevendo a resposta…") e Cancelar; a resposta traz o "Como
cheguei aqui" recolhido, que abre e fecha no clique, com "Copiar SQL" e "Ver dados" (a tabela refeita na sandbox numa
`Task`, com "Copiar como tabela"); a conversa segue um parágrafo por mensagem; as sugestões do estado vazio viram a pergunta;
com filtro, a resposta começa por "Considerando os filtros: …"; as Configurações têm o cartão "Perguntar à base" e a página
Prompts as duas tarefas novas, sem o chat antigo, com o dicionário na parte fixa."""
from __future__ import annotations

from typing import Any

import pytest

from listener.llm.types import LlmTask
from listener.rag import search, semantic, trace
from tests.test_rag_agent import ANA, Script, _provider, consult
from tests.test_rag_chat import QUESTION
from tests.test_rag_ui import _indexed

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento                              # D-050: telas


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    semantic.CACHE.clear()
    yield
    search.clear_caches()
    semantic.CACHE.clear()


@pytest.fixture
def page(qtbot, store, monkeypatch):
    from listener.ui.history import HistoryPage

    monkeypatch.setenv("LISTENER_LLM", "fake")
    shown = HistoryPage(store)
    qtbot.addWidget(shown)
    shown.show()
    shown.tabs.setCurrentIndex(2)
    yield shown
    shown.shutdown()


def _statuses(page: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = page._chat_status

    def spy(text: str, *a: Any) -> None:
        seen.append(text)
        real(text, *a)

    monkeypatch.setattr(page, "_chat_status", spy)
    return seen


def _ask(qtbot: Any, page: Any, question: str = QUESTION) -> Any:
    count = len(page.answers)
    page.question.setText(question)
    page.ask()
    qtbot.waitUntil(lambda: len(page.answers) > count and page.chat_task is None, timeout=15000)
    return page.answers[-1]


def test_the_ask_with_progress_the_collapsed_trace_copy_sql_and_view_data(qtbot, store, monkeypatch, page):
    from PySide6.QtCore import QPoint, QUrl

    a, _b, _who = _indexed(store)
    _provider(monkeypatch, Script([consult(ANA)], f"A Ana falou do orçamento [R{a} 00:00]."))
    seen = _statuses(page, monkeypatch)
    ans = _ask(qtbot, page)
    assert ans.error is None and "Consultando a base…" in seen and "Consultando a base… rodada 1 · 1 consulta" in seen
    assert "escrevendo a resposta…" in seen and page.chat_status.text() == (
        f"1 citação(ões) com link · 1 consulta(s) em 1 rodada(s) · {page.chat_status.text().split(' · ')[-1]}")
    assert page.btn_stop.text() == "Cancelar" and not page.btn_stop.isEnabled()
    shown = page.chat_view.toPlainText()
    assert "▸ Como cheguei aqui (1 consulta · " in shown and "SELECT" not in shown
    page._chat_link(QUrl(trace.link("trace", ans.message_id)))                       # abre
    shown = page.chat_view.toPlainText()
    assert "▾ Como cheguei aqui" in shown and "#1.1 as falas da Ana · 3 linhas" in shown and "Copiar SQL · Ver dados" in shown
    assert "FROM falas" in shown and 'params: ["Ana%"]' in shown
    page.chat_view.verticalScrollBar().setValue(0)                                  # o ponto em coordenadas da janela
    block = page.chat_view.document().findBlockByNumber(1)                          # ainda um parágrafo por mensagem
    rect = page.chat_view.document().documentLayout().blockBoundingRect(block)
    assert [label for label, _fn in page._answer_actions(QPoint(int(rect.x()) + 4, int(rect.y()) + 4), "")] == ["Copiar com citação"]
    page._chat_link(QUrl(trace.link("copysql", ans.message_id, "1.1")))
    assert page.copied[-1].startswith("SELECT reuniao_id") and "\nFROM falas" in page.copied[-1]
    assert page.copied[-1].endswith("-- params: Ana%") and page.chat_status.text() == "SQL copiado."
    page._chat_link(QUrl(trace.link("data", ans.message_id, "1.1")))                 # U-097
    dialog = page.data_dialogs[-1]
    qtbot.waitUntil(lambda: dialog.data is not None, timeout=10000)
    assert dialog.table.rowCount() == 3 and dialog.table.horizontalHeaderItem(4).text() == "falante"
    assert dialog.status.text().startswith("3 linhas · ") and dialog.btn_copy.isEnabled()
    copied = dialog.copy_table()
    assert copied.split("\n")[0] == "reuniao_id\tinicio\tinicio_ms\tfala_id\tfalante\ttexto" and len(copied.split("\n")) == 4
    page._chat_link(QUrl(trace.link("trace", ans.message_id)))                       # fecha
    assert "▸ Como cheguei aqui" in page.chat_view.toPlainText() and "FROM falas" not in page.chat_view.toPlainText()
    dialog.close()


def test_suggestions_in_the_empty_state_become_the_question_and_the_filters_open_the_answer(qtbot, store, monkeypatch, page):
    from PySide6.QtCore import QUrl

    from listener.ui import history_ask

    _a, _b, who = _indexed(store)
    page.refresh()                                                                  # as pessoas no filtro
    page.new_thread()
    shown = page.chat_view.toPlainText()
    assert shown.startswith("Pergunte sobre todas") and "Experimente:" in shown
    wanted = history_ask.suggestions(store)
    assert wanted[0] == "Quantas reuniões tive com Ana este mês?" and "Quais ações estão atrasadas, por dono?" in wanted
    assert history_ask.parse_ask_link(history_ask.ask_link(wanted[0])) == wanted[0] and history_ask.parse_ask_link("listener://x") is None
    count = len(page.answers)
    page._chat_link(QUrl(history_ask.ask_link(wanted[1])))                           # a sugestão vira a pergunta (U-096)
    qtbot.waitUntil(lambda: len(page.answers) > count and page.chat_task is None, timeout=15000)
    asked = [m["text"] for m in store.chat.list_for_thread(page.answers[-1].thread_id) if m["role"] == "user"]
    assert asked == [wanted[1]] and page.answers[-1].text == "resposta simulada para rag_answer"
    page.new_thread()
    page.person.setCurrentIndex(page.person.findData(who["bia"]))
    ans = _ask(qtbot, page)
    assert ans.text.startswith("Considerando os filtros: pessoa: Bia.\n\n") and ans.trace["filtros"]["pessoas"] == [who["bia"]]
    assert "Considerando os filtros: pessoa: Bia." in page.chat_view.toPlainText()


def test_cancel_during_the_rounds_keeps_the_question(qtbot, store, monkeypatch, page):
    import threading

    _indexed(store)
    gate = threading.Event()

    def hold(_n: int) -> None:
        gate.wait(5)

    _provider(monkeypatch, Script([consult(ANA)], on_round=hold))
    page.question.setText(QUESTION)
    page.ask()
    qtbot.waitUntil(page.btn_stop.isEnabled, timeout=5000)
    page.stop_chat()
    gate.set()
    qtbot.waitUntil(lambda: page.chat_task is None, timeout=15000)
    ans = page.answers[-1]
    assert ans.cancelled and ans.message_id is None and page.chat_status.text() == "Pergunta cancelada."


def test_the_settings_card_and_the_prompts_page_of_the_ask(qtbot, store, platform):
    from listener.config.defaults import RAG_SQL_KEYS
    from listener.llm import prompts
    from listener.ui.settings_min import SettingsDialog
    from listener.ui.settings_prompts import PROMPTS_TITLE, TASKS
    from listener.ui.settings_search import ASK_CARD

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    search_page = dlg.page("Busca")
    assert ASK_CARD in search_page.cards and all(key in dlg._editors for key in RAG_SQL_KEYS)
    titles = list(dlg.pages)
    dlg.nav.setCurrentRow(titles.index(PROMPTS_TITLE))
    names = [dlg.prompt_list.item(i).text().split("\n")[0] for i in range(dlg.prompt_list.count())]
    assert "Perguntar à base: consultas" in names and "Perguntar à base: resposta" in names
    assert "Chat de todas as reuniões" not in names and LlmTask.RAG_CHAT not in TASKS
    fixed = dlg.select_prompt(LlmTask.RAG_SQL).fixed.toPlainText()
    assert "Dicionário de dados:" in fixed and "reunioes: uma linha por reunião" in fixed and '"enum": ["responder", "consultar"]' in fixed
    assert dlg.select_prompt(LlmTask.RAG_SQL).edit.toPlainText() == prompts.rag_sql.INSTRUCTIONS
    answer = dlg.select_prompt(LlmTask.RAG_ANSWER).fixed.toPlainText()
    assert "{reuniões citáveis}" in answer and answer.endswith("[Formato da resposta]\nTexto livre.")
