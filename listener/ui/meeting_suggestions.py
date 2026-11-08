"""F5.20 (C14, U-162): a aba "Sugestões" da reunião aberta, só leitura. Uma linha por pedido ("[mm:ss] · Falar · 3 opções ·
US$ 0,0004", ou cancelada e falhou), as opções embaixo com a avaliação ("Boa", "Ruim (longa demais)", "Usei", "Boa e usei")
e, embaixo de cada opção, os fatos citados: o duplo clique ou o Enter num fato toca daquele ponto. Sem sugestão, o estado
vazio (U-059). Relê o banco no `reload` (a janela da reunião chama quando um job dela termina)."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..model import SuggestMode, SuggestStatus
from .components import EmptyState

if TYPE_CHECKING:
    from ..store import Store

EMPTY = "Nenhuma sugestão pedida nesta reunião: durante a gravação, o botão Sugerir da bandeja de legendas propõe o que falar."
MODES = {str(SuggestMode.SPEAK): "Falar", str(SuggestMode.ORIENT): "Me situar"}
STATUS = {str(SuggestStatus.CANCELLED): "cancelada", str(SuggestStatus.ERROR): "falhou"}
T_ROLE = Qt.ItemDataRole.UserRole


def verdict(rating: Any, reason: Any, used: Any) -> str:
    """"Boa", "Ruim (longa demais)", "Usei", "Boa e usei"; vazio sem avaliação."""
    text = "Boa" if rating == 1 else (f"Ruim ({reason})" if reason else "Ruim") if rating == 0 else ""
    if used:
        text = f"{text} e usei" if text else "Usei"
    return text


class SuggestionsTab(QWidget):
    def __init__(self, store: Store, meeting_id: int, *, play: Callable[[int], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store, self.meeting_id, self.play = store, meeting_id, play
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Quando e o quê", "Avaliação"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setWordWrap(True)
        self.tree.itemActivated.connect(self._activated)
        self.empty = EmptyState("lightbulb", EMPTY)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack)
        self.reload()

    def reload(self) -> None:
        suggestions = self.store.suggestions.list_for_meeting(self.meeting_id)
        options = self.store.suggestions.options_for_meeting(self.meeting_id)
        self.tree.clear()
        for s in suggestions:
            opts = options.get(int(s["id"]), [])
            cost = fmt_usd(s.get("cost_usd")) + (" estimado" if s.get("cost_estimated") else "")
            state = STATUS.get(str(s.get("status")), "")
            count = f"{len(opts)} {'opção' if len(opts) == 1 else 'opções'}"
            head = f"[{stamp(int(s['t_ms']))}] · {MODES.get(str(s['mode']), str(s['mode']))} · {count} · {cost}"
            head += f" · {state}" if state else ""
            top = QTreeWidgetItem([head, ""])
            for o in opts:
                child = QTreeWidgetItem([f"{o['kind']}: {o['text']}", verdict(o.get("rating"), o.get("reason"), o.get("used"))])
                try:
                    facts = json.loads(str(o.get("facts_json") or "[]"))
                except ValueError:
                    facts = []
                for fact in facts:
                    t = int(fact.get("t_ms") or 0)
                    leaf = QTreeWidgetItem([f"[{stamp(t)}]", ""])
                    leaf.setData(0, T_ROLE, t)
                    leaf.setToolTip(0, "duplo clique ou Enter: ouvir deste ponto")
                    child.addChild(leaf)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.stack.setCurrentWidget(self.tree if suggestions else self.empty)

    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        t = item.data(0, T_ROLE)
        if self.play is not None and isinstance(t, int):
            self.play(t)


__all__ = ["EMPTY", "SuggestionsTab", "verdict"]
