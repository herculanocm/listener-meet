"""Tabela "apps tocando agora" (U-001) + "Sistema inteiro", com medidor de pico por app."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..capture.recorder import SourceSpec
from ..platform.base import PlayingApp

SYSTEM_ROW_PID = -1


class SourceTable(QWidget):
    selection_changed = Signal()

    def __init__(self, platform, parent=None, refresh_ms: int = 500):
        super().__init__(parent)
        self.platform = platform
        self._apps: list[PlayingApp] = []
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Fonte", "Janela", "Nível"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 110)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ---- dados ----
    def refresh(self) -> None:
        try:
            apps = self.platform.list_playing_apps()
        except Exception:
            apps = []
        selected = self.selected_pid()
        self._apps = apps
        self.table.setRowCount(1 + len(apps))
        self._set_row(0, SYSTEM_ROW_PID, "Sistema inteiro", "tudo que toca no computador", 0.0, False)
        for i, app in enumerate(apps, start=1):
            self._set_row(i, app.pid, app.exe.removesuffix(".exe"), app.title, app.peak, app.is_meeting_app)
        if selected is not None:
            self.select_pid(selected)
        elif self.table.rowCount() > 0 and not self.table.selectedItems():
            self.table.selectRow(1 if apps and apps[0].is_meeting_app else 0)

    def _set_row(self, row: int, pid: int, name: str, title: str, peak: float, meeting: bool) -> None:
        it = self.table.item(row, 0)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(row, 0, it)
        it.setText(("● " if meeting else "") + name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        t = self.table.item(row, 1)
        if t is None:
            t = QTableWidgetItem()
            self.table.setItem(row, 1, t)
        t.setText(title or "")
        bar = self.table.cellWidget(row, 2)
        if not isinstance(bar, QProgressBar):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            self.table.setCellWidget(row, 2, bar)
        bar.setValue(int(min(1.0, peak) * 100))

    def selected_pid(self) -> int | None:
        items = self.table.selectedItems()
        if not items:
            return None
        return int(self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole))

    def select_pid(self, pid: int) -> bool:
        for row in range(self.table.rowCount()):
            if int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == pid:
                self.table.selectRow(row)
                return True
        return False

    def select_exe(self, exe: str) -> bool:
        want = (exe or "").lower()
        for app in self._apps:
            if app.exe.lower() == want:
                return self.select_pid(app.pid)
        return False

    def selected_spec(self) -> SourceSpec:
        pid = self.selected_pid()
        if pid is None or pid == SYSTEM_ROW_PID:
            return SourceSpec(kind="system", label="Sistema inteiro")
        for app in self._apps:
            if app.pid == pid:
                return SourceSpec(kind="app", pid=app.pid, exe=app.exe, label=app.label)
        return SourceSpec(kind="system", label="Sistema inteiro")
