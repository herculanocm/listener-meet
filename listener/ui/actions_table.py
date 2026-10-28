"""As ações da ata de uma reunião (F3 item 11), saídas do `ui/llm_tabs.py`, que passava de 400 linhas: `ActionsTab`, usada
pela reunião aberta e pela revisão. F5.4 L4 (U-080): a Ação estica e quebra a linha; o prazo em dd/mm/aaaa; a ação aberta
com o prazo vencido leva o prazo num selo vermelho ("10/01/2020 (atrasada)", pelo `BadgeDelegate`: o texto fica no
`DisplayRole` para o leitor de tela); sem ações, o estado vazio (U-059). `is_overdue` e `due_text` servem também as "Ações
abertas" do Histórico. O combo de estado liga a um método (o `sender()` diz qual), sem lambda que prenda a tabela (R-06)."""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..model import ActionStatus
from ..rag.export import fmt_date
from .components import BadgeDelegate, EmptyState
from .theme import METRICS, TONE_ROLE, Tone, note

if TYPE_CHECKING:
    from ..store import Store

ACTIONS_NEWER_NOTICE = "Há ações novas ou alteradas; recarregar?"
ACTION_COLUMNS = ("Ação", "Dono", "Prazo", "Estado", "Quando")
DUE_COL, STATE_COL = 2, 3
STATUS_LABELS = {ActionStatus.OPEN: "aberta", ActionStatus.DONE: "feita", ActionStatus.DISCARDED: "descartada"}
OVERDUE_TONE = Tone.DANGER       # F5.4 (A-11, U-080): o prazo vencido em selo no tom de perigo do tema
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTION_ID = "action_id"          # a ação do combo de estado (propriedade dinâmica, lida pelo `sender()`)

type ActionView = tuple[tuple[int, str, str, str, str, str], ...]     # (id, texto, dono, prazo, estado, quando) por linha


def is_overdue(due: str | None, today: date) -> bool:
    d = str(due or "")
    return bool(DATE_RE.match(d)) and d < today.isoformat()


def due_text(due: str, overdue: bool) -> str:
    """O prazo em dd/mm/aaaa (o texto livre como veio), com "(atrasada)" quando venceu (U-080)."""
    shown = fmt_date(due) or due
    return f"{shown} (atrasada)" if overdue else shown


def local_today() -> date:
    return datetime.now().astimezone().date()


class ActionsTab(QWidget):
    """Ações da ata, uma por linha: texto editável na célula e estado no combo. A recarga compara o que a tabela mostra com
    o banco e só a reconstrói quando algo mudou; com a tabela em edição ou com o foco, acende "recarregar?" (R3-01)."""

    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, on_edited: Callable[[], Any] | None = None,
                 today: Callable[[], date] = local_today):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_edited = on_edited                               # F5: ações vigentes entram na busca
        self.today = today
        self.table = self._make_table()
        self.table.itemChanged.connect(self._item_changed)
        self._empty: EmptyState | None = None                                           # U-059 (sob demanda)
        self._deferred = False
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.note = QLabel("")
        note(self.note)
        self.btn_reload = QPushButton("Recarregar ações")
        self.btn_reload.setVisible(False)
        self.btn_reload.clicked.connect(self._force_reload)
        self._loading = False
        self._shown: ActionView | None = None
        self.newer = False
        row = QHBoxLayout()
        row.addWidget(self.note, 1)
        row.addWidget(self.btn_reload)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addLayout(row)

    @property
    def empty(self) -> EmptyState:
        if self._empty is None:
            self._empty = EmptyState("check", "Nenhuma ação nesta ata.")
            self.stack.addWidget(self._empty)
        return self._empty

    def _page(self, has: bool) -> None:
        """A tabela ou o estado vazio; o vazio de uma aba escondida nasce quando ela aparece."""
        self._deferred = not has and self._empty is None and not self.isVisible()
        if not self._deferred:
            self.stack.setCurrentWidget(self.table if has else self.empty)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._deferred:
            self._page(self.table.rowCount() > 0)

    @staticmethod
    def _make_table() -> QTableWidget:
        """U-058 e U-080: o cabeçalho à esquerda, sem o número de linha, a linha inteira selecionada, a Ação esticando e
        quebrando a linha, o prazo no selo."""
        table = QTableWidget(0, len(ACTION_COLUMNS))
        table.setHorizontalHeaderLabels(list(ACTION_COLUMNS))
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setMinimumSectionSize(METRICS.control_h)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)   # a altura segue a largura da Ação
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setItemDelegateForColumn(DUE_COL, BadgeDelegate(table))
        return table

    def _view(self) -> ActionView:
        people = {int(p["id"]): str(p["name"]) for p in self.store.people.list_people()}
        out: list[tuple[int, str, str, str, str, str]] = []
        for a in self.store.actions.list_for_meeting(self.meeting_id):
            owner_pid = a.get("owner_person_id")
            owner = people.get(int(owner_pid)) if owner_pid is not None else (a.get("owner_text") or "")
            t = a.get("t_ms")
            when = f"{int(t) // 60000:02d}:{(int(t) // 1000) % 60:02d}" if t is not None else "sem evidência"
            out.append((int(a["id"]), str(a.get("text") or ""), str(owner or ""), str(a.get("due") or ""),
                        str(a.get("status") or ActionStatus.OPEN), when))
        return tuple(out)

    def editing(self) -> bool:
        """Célula em edição ou foco na tabela (inclusive num combo de estado)."""
        if self.table.state() == QAbstractItemView.State.EditingState:
            return True
        focus = QApplication.focusWidget()
        return focus is not None and (focus is self.table or self.table.isAncestorOf(focus))

    def _force_reload(self) -> None:
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        view = self._view()
        if view == self._shown and not force:
            return
        if not force and self._shown is not None and self.editing():
            self.newer = True
            self.note.setText(ACTIONS_NEWER_NOTICE)
            self.btn_reload.setVisible(True)
            return
        self._fill(view)

    def _fill(self, view: ActionView) -> None:
        self._loading = True
        today = self.today()
        try:
            self.table.setRowCount(len(view))
            for r, (aid, text, owner, due, status, when) in enumerate(view):
                overdue = status == ActionStatus.OPEN and is_overdue(due, today)
                for c, value in enumerate((text, owner, due_text(due, overdue), None, when)):
                    if c == STATE_COL:
                        self.table.setCellWidget(r, c, self._status_combo(aid, status))
                        continue
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, aid)
                    if c == DUE_COL and overdue:
                        item.setData(TONE_ROLE, OVERDUE_TONE.value)        # o selo vermelho (U-080)
                    if c != 0:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r, c, item)
        finally:
            self._loading = False
        self._shown = view
        self.newer = False
        self.btn_reload.setVisible(False)
        self._page(bool(view))
        self.note.setText(f"{len(view)} ação(ões); edite o texto na própria célula e o estado na coluna Estado." if view
                          else "nenhuma ação na ata")

    def _status_combo(self, action_id: int, status: str) -> QComboBox:
        combo = QComboBox()
        for st, label in STATUS_LABELS.items():
            combo.addItem(label, str(st))
        combo.setCurrentIndex(max(0, combo.findData(status)))
        combo.setProperty(ACTION_ID, action_id)
        combo.currentIndexChanged.connect(self._status_changed)        # R-06: um método; o combo vem do `sender()`
        return combo

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is not None:
            self._after_write(int(aid), self.store.actions.set_text(int(aid), item.text().strip()))

    def _status_changed(self, _index: int) -> None:
        combo = self.sender()
        if self._loading or not isinstance(combo, QComboBox) or combo.property(ACTION_ID) is None:
            return
        action_id, status = int(combo.property(ACTION_ID)), ActionStatus(str(combo.currentData()))
        written = self.store.actions.set_status(action_id, status)
        self._after_write(action_id, written)
        if written:
            self._mark_due(action_id, status)

    def _mark_due(self, action_id: int, status: str) -> None:
        """O selo do prazo segue o estado na hora (feita deixa de estar atrasada), sem refazer a tabela: o combo que mudou
        é o `sender()` deste sinal."""
        entry = next((v for v in self._shown or () if v[0] == action_id), None)
        if entry is None:
            return
        overdue = status == ActionStatus.OPEN and is_overdue(entry[3], self.today())
        for r in range(self.table.rowCount()):
            item = self.table.item(r, DUE_COL)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == action_id:
                item.setText(due_text(entry[3], overdue))
                item.setData(TONE_ROLE, OVERDUE_TONE.value if overdue else None)

    def _after_write(self, action_id: int, written: bool) -> None:
        """A própria edição não conta como versão nova; ação já superada (a ata mudou) recusa a escrita e pede recarga."""
        if written and self.on_edited is not None:
            self.on_edited()
        if written and not self.newer:
            self._shown = self._view()
        elif not written and str((self.store.actions.get(action_id) or {}).get("status")) == ActionStatus.SUPERSEDED:
            self.newer = True
            self.note.setText("Esta ação é de uma ata anterior e não foi alterada; recarregue as ações.")
            self.btn_reload.setVisible(True)


__all__ = ["ACTION_COLUMNS", "ActionsTab", "due_text", "is_overdue", "local_today"]
