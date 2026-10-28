"""Partes da reunião aberta (F5.4 L4), saídas do `ui/meeting.py`, que estava no limite de 400 linhas:

- `MarkersTab`: os marcadores da gravação ("[mm:ss] nota", com a bandeira; o clique ou Enter toca daquele ponto) e, sem
  nenhum, o estado vazio (U-059).
- `SummaryTab`: a aba "Resumo", com o executivo e o pré-resumo em dois cartões lado a lado (cada um a `TextTab` dele,
  formatada, U-079; lado a lado, a reunião cabe na altura do notebook, A-17).
- `menu_button`: o botão com menu do cabeçalho ("Exportar"), um `QPushButton` com a seta inteira e o espaço dela (U-078:
  o `QToolButton` desenhava a seta por cima do canto do texto)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from ..llm.prompts.common import stamp
from ..model import SummaryKind
from .components import Card, EmptyState
from .icons import tone_icon
from .theme import Tone, note, surface_tokens

if TYPE_CHECKING:
    from ..store import Store
    from .minutes_view import TextTab

MARKERS_EMPTY = "Nenhum marcador nesta reunião: durante a gravação, o atalho do marcador guarda o instante com uma nota."
SUMMARY_CARDS = ((SummaryKind.EXECUTIVE, "Resumo executivo"), (SummaryKind.PRE_SUMMARY, "Pré-resumo"))


class MarkersTab(QWidget):
    """Os marcadores da gravação ("[mm:ss] nota"); o clique ou Enter toca daquele ponto."""

    def __init__(self, store: Store, meeting_id: int, *, play: Callable[[int], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.play = play
        self.list = QListWidget()
        self.list.itemClicked.connect(self._activated)
        self.list.itemActivated.connect(self._activated)
        self._empty: EmptyState | None = None                         # U-059 (sob demanda)
        self._deferred = False
        self.stack = QStackedWidget()
        self.stack.addWidget(self.list)
        self.note = QLabel("")
        note(self.note)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.note)
        self.reload()

    @property
    def empty(self) -> EmptyState:
        if self._empty is None:
            self._empty = EmptyState("flag", MARKERS_EMPTY)
            self.stack.addWidget(self._empty)
        return self._empty

    def reload(self) -> None:
        markers = self.store.list_markers(self.meeting_id)
        current = self.list.currentRow()
        self.list.clear()
        flag = tone_icon("flag", Tone.TEXT, surface_tokens(self))
        for mk in markers:
            item = QListWidgetItem(flag, f"[{stamp(int(mk['t_ms']))}] {mk.get('note') or '(sem nota)'}")
            item.setData(Qt.ItemDataRole.UserRole, int(mk["t_ms"]))
            item.setToolTip("clique para ouvir deste ponto")
            self.list.addItem(item)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self._page(bool(markers))
        self.note.setText(f"{len(markers)} marcador(es); clique num para ouvir deste ponto." if markers
                          else "Nenhum marcador nesta reunião.")
        self.note.setVisible(bool(markers))                  # sem marcador, o estado vazio já diz (como na linha do tempo, U-034)

    def _page(self, has: bool) -> None:
        """A lista ou o estado vazio; o vazio de uma aba escondida nasce quando ela aparece."""
        self._deferred = not has and self._empty is None and not self.isVisible()
        if not self._deferred:
            self.stack.setCurrentWidget(self.list if has else self.empty)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._deferred:
            self._page(self.list.count() > 0)

    def retheme(self) -> None:
        """A troca de tema: a bandeira dos itens nos tokens novos (o ícone de um item não é varrido pelo tema). Sem ler o
        banco: a troca pode chegar com ele já fechado."""
        flag = tone_icon("flag", Tone.TEXT, surface_tokens(self))
        for i in range(self.list.count()):
            self.list.item(i).setIcon(flag)

    def _activated(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if t is not None and self.play is not None:
            self.play(int(t))


class SummaryTab(QSplitter):
    """A aba "Resumo": o executivo à esquerda e o pré-resumo à direita, cada um num cartão."""

    def __init__(self, tabs: dict[str, TextTab], parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)
        for kind, label in SUMMARY_CARDS:
            card, tab = Card(label), tabs[str(kind)]
            inner = tab.layout()
            if inner is not None:
                inner.setContentsMargins(0, 0, 0, 0)          # o cartão já dá a margem de dentro
            card.content.addWidget(tab)
            self.addWidget(card)


def menu_button(text: str, tip: str, menu: QMenu) -> QPushButton:
    """U-078: o botão que abre um menu, com a seta inteira ao lado do texto."""
    button = QPushButton(text)
    button.setToolTip(tip)
    button.setMenu(menu)
    return button


__all__ = ["MARKERS_EMPTY", "MarkersTab", "SummaryTab", "menu_button"]
