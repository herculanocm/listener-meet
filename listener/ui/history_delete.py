"""Excluir reuniões no Histórico (F5.6 L3, U-093, A-40 a A-42, P-04), o lado da página: a lista em seleção múltipla, o
"Excluir…" no menu de contexto e num botão sob a lista (sem ícone por linha), a tecla Delete só na tabela (na busca ela
apaga o texto, como sempre) e a confirmação única (`DeleteConfirm`): título, data e duração de até 10 reuniões e "e mais N",
o que sai e o que fica e "Cancelar" como botão padrão. Quem exclui é o controlador (`ui/meeting_delete.py`), pelo
`on_delete(linhas, vista)`: a vista (`MeetingsDelete`) mostra "Excluindo 2 de 5…" e, no fim, recarrega a lista, as ações
abertas, a busca e a conversa aberta (a citação da reunião excluída vira "reunião excluída", sem link, P-02); o resultado vai
no aviso do app. Sinais ligados a métodos (R-06)."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, override

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6 import QtGui
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QTableView, QVBoxLayout, QWidget

from ..rag import export
from .history_data import MeetingLine
from .theme import DANGER, Tone, note, role, set_kind, tone

if TYPE_CHECKING:
    from .history_data import MeetingsModel

SHOWN_MAX = 10                       # A-41: as reuniões listadas na confirmação; o resto vira "e mais N"
TITLE_SHOWN = 90                     # caracteres do título na confirmação (o inteiro fica na dica)
GOES = ("Sai de vez, sem lixeira: o áudio, as falas, as vozes, a ata, os resumos, as ações, o histórico de edições, os trechos "
        "da busca, as conversas da reunião e os eventos. O custo das chamadas à LLM continua no total do mês, sem a reunião.")
STAYS = ("Ficam os perfis de voz e as fichas das pessoas. Nas conversas de “Perguntar às reuniões”, a citação vira "
         "“reunião excluída”.")
BUTTON_TIP = "Exclui as reuniões selecionadas, com o áudio e tudo o que saiu delas (pede confirmação)"
EMPTY_TIP = "Selecione uma ou mais reuniões na lista para excluir"


class DeleteView(Protocol):
    """O que o controlador usa da página durante a exclusão."""

    def parent_widget(self) -> QWidget: ...
    def show_progress(self, text: str) -> None: ...
    def finished(self) -> None: ...


type OnDelete = Callable[[list[MeetingLine], DeleteView], Any]


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def line_text(line: MeetingLine) -> str:
    """"28/10/2026 14:03 · Comitê de investimentos · 1 h 02 min" (o título longo cortado; a data e a duração sempre)."""
    title = line.title if len(line.title) <= TITLE_SHOWN else line.title[:TITLE_SHOWN - 1].rstrip() + "…"
    parts = [export.fmt_datetime(line.started_at), title, export.fmt_duration(line.duration_ms)]
    return " · ".join(p for p in parts if p)


def summary_lines(lines: Sequence[MeetingLine]) -> list[str]:
    shown = [f"• {line_text(line)}" for line in lines[:SHOWN_MAX]]
    rest = len(lines) - SHOWN_MAX
    if rest > 0:
        shown.append(f"e mais {plural(rest, 'reunião', 'reuniões')}")
    return shown


def confirm_head(n: int, total: int) -> str:
    """O título da confirmação pelas que de fato saem (R-11)."""
    if not n:
        return "Esta reunião não pode ser excluída agora." if total == 1 else "Nenhuma das reuniões selecionadas pode ser excluída agora."
    return "Excluir esta reunião?" if total == 1 else f"Excluir {plural(n, 'reunião', 'reuniões')}?"


class DeleteConfirm(QDialog):
    """A confirmação única (A-41): as que saem, o que sai e o que fica e, à parte, as que ficam de fora (gravando), com o
    motivo. R-11: o título, a lista e o botão contam só as que saem; com todas de fora, o "Excluir" fica desabilitado.
    "Cancelar" é o padrão (Enter e Esc cancelam; o "Excluir" é o vermelho do tema, U-087)."""

    def __init__(self, lines: Sequence[MeetingLine], refused: Mapping[int, str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        refused = refused or {}
        go = [line for line in lines if line.meeting_id not in refused]
        out = [line for line in lines if line.meeting_id in refused]
        self.setWindowTitle("Excluir reuniões" if len(lines) > 1 else "Excluir reunião")
        self.setMinimumWidth(560)
        self.head = QLabel(confirm_head(len(go), len(lines)))
        role(self.head, "body_strong")
        self.items = self._label("\n".join(summary_lines(go)))
        titles = "\n".join(line.title for line in go[:SHOWN_MAX])                  # o título inteiro, em texto puro (R7-07)
        self.items.setToolTip(QtGui.Qt.convertFromPlainText(titles, Qt.WhiteSpaceMode.WhiteSpacePre))
        goes, stays = self._label(GOES), self._label(STAYS)
        note(stays)
        lay = QVBoxLayout(self)
        for w in (self.head, *((self.items, goes, stays) if go else ())):
            lay.addWidget(w)
        self.refused_label: QLabel | None = None
        if out:
            reasons = " ".join(sorted({refused[line.meeting_id] for line in out}))
            self.refused_label = self._label(f"{plural(len(out), 'reunião fica', 'reuniões ficam')} de fora:" + "\n"
                                             + "\n".join(summary_lines(out)) + "\n" + reasons)
            tone(self.refused_label, Tone.WARNING)
            lay.addWidget(self.refused_label)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_delete = QPushButton(f"Excluir {plural(len(go), 'reunião', 'reuniões')}" if len(go) > 1 else "Excluir")
        self.btn_delete.setEnabled(bool(go))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_delete.clicked.connect(self.accept)
        set_kind(self.btn_delete, DANGER)
        self.btn_cancel.setDefault(True)                 # A-41: Enter e Esc cancelam; excluir pede o clique ou o foco nele
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.btn_cancel.setFocus()

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)        # um título com "<b>" fica texto (R7-07)
        label.setWordWrap(True)
        return label


def confirm_delete(parent: QWidget, lines: Sequence[MeetingLine], refused: Mapping[int, str]) -> bool:
    """A pergunta; separada para os testes trocarem (como `meeting_language.confirm_change`)."""
    dlg = DeleteConfirm(lines, refused, parent)
    try:
        return dlg.exec() == QDialog.DialogCode.Accepted
    finally:
        dlg.deleteLater()


class MeetingsDelete(QObject):
    """O excluir na lista do Histórico (a tabela já em seleção múltipla, A-40): menu de contexto, Delete na tabela e o botão
    sob a lista."""

    def __init__(self, page: Any, table: QTableView, model: MeetingsModel, on_delete: OnDelete | None):
        super().__init__(page)
        self.page = page
        self.table = table
        self.model = model
        self.on_delete = on_delete
        self.busy = False
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._menu_requested)
        table.installEventFilter(self)
        self.button = QPushButton("Excluir…")
        self.button.clicked.connect(self.request)
        table.selectionModel().selectionChanged.connect(self._refresh_button)
        model.modelReset.connect(self._refresh_button)
        if on_delete is None:                            # sem quem exclua (uma página solta, a sonda): sem o botão
            self.button.hide()
        self._refresh_button()

    def row(self, list_note: QLabel) -> QHBoxLayout:
        """A linha sob a lista: a nota (contagem e filtros) e o "Excluir…" à direita."""
        row = QHBoxLayout()
        row.addWidget(list_note, 1)
        row.addWidget(self.button)
        return row

    def selected(self) -> list[MeetingLine]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.lines[r] for r in rows if 0 <= r < len(self.model.lines)]

    def _refresh_button(self, *_args: object) -> None:
        n = len(self.selected()) if self.on_delete is not None else 0
        self.button.setEnabled(bool(n) and not self.busy)
        self.button.setToolTip(BUTTON_TIP if n else EMPTY_TIP)

    def request(self) -> bool:
        """"Excluir…" (botão, menu ou Delete): as selecionadas vão ao controlador; False quando não há o que pedir."""
        lines = self.selected()
        if self.on_delete is None or self.busy or not lines:
            return False
        self.on_delete(lines, self)
        return True

    def menu_for(self, pos: QPoint) -> QMenu | None:
        """O menu de contexto no ponto da tabela (a linha debaixo do cursor entra na seleção, se ainda não estava)."""
        if self.on_delete is None:
            return None
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), index.parent()):
            self.table.selectRow(index.row())
        n = len(self.selected())
        if not n:
            return None
        menu = QMenu(self.table)
        act = QAction(f"Excluir {plural(n, 'reunião', 'reuniões')}…" if n > 1 else "Excluir…", menu)
        act.setEnabled(not self.busy)
        act.triggered.connect(self.request)
        menu.addAction(act)
        return menu

    def _menu_requested(self, pos: QPoint) -> None:
        menu = self.menu_for(pos)
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))
            menu.deleteLater()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """A-40: Delete é atalho só da tabela (o filtro está nela; a busca apaga o texto, como sempre)."""
        if (watched is self.table and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent)
                and event.key() == Qt.Key.Key_Delete and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.request()
            return True
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- `DeleteView`
    def parent_widget(self) -> QWidget:
        return self.table.window()

    def show_progress(self, text: str) -> None:
        self.busy = True
        self._refresh_button()
        self.page.list_note.setText(text)

    def finished(self) -> None:
        """Fim: a lista, as ações abertas, a busca (o vetor vem do cache) e a conversa aberta de novo (A-42)."""
        self.busy = False
        self.page.reload_meetings()
        self.page.reload_actions()
        if self.page.search.text().strip():
            self.page.run_search(with_vector=True)
        if self.page.chat_task is None:
            self.page._render_thread(keep_scroll=True)
        self._refresh_button()


__all__ = ["GOES", "SHOWN_MAX", "STAYS", "DeleteConfirm", "DeleteView", "MeetingsDelete", "OnDelete", "confirm_delete", "confirm_head",
           "line_text", "summary_lines"]
