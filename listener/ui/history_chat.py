"""A montagem da aba "Perguntar às reuniões" do Histórico (F5 item 11, U-027), fora do `ui/history.py` (F5.6 L3: o
Histórico ganhou o excluir e não podia crescer, A-44). Só os widgets e os sinais, ligados aos métodos da página (R-06: nada
de lambda com a página); a lógica da conversa fica na página."""
from __future__ import annotations

from typing import NamedTuple, Protocol

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .theme import note


class ChatOwner(Protocol):
    """O que a aba chama na página do Histórico."""

    def _thread_chosen(self, index: int) -> None: ...
    def new_thread(self) -> None: ...
    def _chat_link(self, url: QUrl) -> None: ...
    def ask(self) -> None: ...
    def stop_chat(self) -> None: ...


class ChatTab(NamedTuple):
    tab: QWidget
    threads: QComboBox
    btn_new: QPushButton
    chat_note: QLabel
    question: QLineEdit
    btn_ask: QPushButton
    btn_stop: QPushButton
    chat_status: QLabel


def _plain_note() -> QLabel:
    label = QLabel("")
    label.setTextFormat(Qt.TextFormat.PlainText)
    note(label)
    return label


def build_chat_tab(page: ChatOwner, view: QTextBrowser) -> ChatTab:
    """A aba: a conversa escolhida e "Nova conversa", a resposta (`view`, o `LinkBrowser` da página), a nota dos filtros, a
    pergunta com "Perguntar" e "Parar" e a linha de estado."""
    threads = QComboBox()
    threads.setToolTip("Conversas guardadas")
    threads.activated.connect(page._thread_chosen)
    btn_new = QPushButton("Nova conversa")
    btn_new.clicked.connect(page.new_thread)
    view.anchorClicked.connect(page._chat_link)
    chat_note = _plain_note()
    question = QLineEdit()
    question.setPlaceholderText("Pergunte sobre todas as reuniões (as citações abrem a reunião no ponto)")
    question.returnPressed.connect(page.ask)
    btn_ask = QPushButton("Perguntar")
    btn_ask.clicked.connect(page.ask)
    btn_stop = QPushButton("Parar")
    btn_stop.setEnabled(False)
    btn_stop.clicked.connect(page.stop_chat)
    chat_status = _plain_note()
    chat_status.setWordWrap(True)
    trow = QHBoxLayout()
    trow.addWidget(QLabel("Conversa"))
    trow.addWidget(threads, 1)
    trow.addWidget(btn_new)
    qrow = QHBoxLayout()
    qrow.addWidget(question, 1)
    qrow.addWidget(btn_ask)
    qrow.addWidget(btn_stop)
    tab = QWidget()
    lay = QVBoxLayout(tab)
    lay.addLayout(trow)
    lay.addWidget(view, 1)
    lay.addWidget(chat_note)
    lay.addLayout(qrow)
    lay.addWidget(chat_status)
    return ChatTab(tab, threads, btn_new, chat_note, question, btn_ask, btn_stop, chat_status)


__all__ = ["ChatOwner", "ChatTab", "build_chat_tab"]
