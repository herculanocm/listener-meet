"""Tabela "apps tocando agora" (U-001) + "Sistema inteiro", com medidor de pico por app.
A consulta roda numa thread própria (F1.1 A4): a interface nunca espera pelo sistema de áudio. O poller não tem
parent, acorda por `Event` e se destrói ao terminar (M-12); `first_apps_ready` dispara uma vez, na primeira lista."""
from __future__ import annotations

import functools
import threading
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..capture.recorder import SourceSpec
from ..log import get_logger
from ..model import SourceKind
from ..platform.base import PlayingApp

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.sources")
SYSTEM_ROW_PID = -1
_live_pollers: set[QThread] = set()      # referências até `finished` (sem parent, M-12)


def _release(thread: QThread) -> None:
    _live_pollers.discard(thread)
    thread.deleteLater()


class _Poller(QThread):
    apps_ready = Signal(object)

    def __init__(self, platform: Platform, interval_ms: int):
        super().__init__(None)
        self.platform = platform
        self.interval_s = int(interval_ms) / 1000.0
        self._wake = threading.Event()

    def interrupt(self) -> None:
        self.requestInterruption()
        self._wake.set()

    @override
    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                apps = list(self.platform.list_playing_apps())
            except Exception as e:
                log.debug("list_playing_apps: %s", e)
                apps = []
            self.apps_ready.emit(apps)
            self._wake.wait(self.interval_s)


class SourceTable(QWidget):
    selection_changed = Signal()
    first_apps_ready = Signal()

    def __init__(self, platform: Platform, parent: QWidget | None = None, refresh_ms: int = 500):
        super().__init__(parent)
        self.platform = platform
        self.refresh_ms = refresh_ms
        self._apps: list[PlayingApp] = []
        self._poller: _Poller | None = None
        self._got_first = False
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
        self.render_apps([])

    def start(self) -> None:
        if self._poller is None:
            poller = _Poller(self.platform, self.refresh_ms)
            poller.apps_ready.connect(self._on_apps)
            poller.finished.connect(functools.partial(_release, poller))
            _live_pollers.add(poller)
            self._poller = poller
            poller.start()

    def stop(self) -> None:
        if self._poller is not None:
            poller, self._poller = self._poller, None
            poller.apps_ready.disconnect(self._on_apps)
            poller.interrupt()                            # termina sozinho; `finished` faz o deleteLater

    # ---- dados ----
    def _on_apps(self, apps: list[PlayingApp]) -> None:
        self.render_apps(apps)
        if not self._got_first:
            self._got_first = True
            self.first_apps_ready.emit()

    def render_apps(self, apps: list[PlayingApp]) -> None:
        selected = self.selected_pid()
        self._apps = list(apps)
        self.table.setRowCount(1 + len(self._apps))
        self._set_row(0, SYSTEM_ROW_PID, "Sistema inteiro", "tudo que toca no computador", 0.0, False)
        for i, app in enumerate(self._apps, start=1):
            self._set_row(i, app.pid, app.exe.removesuffix(".exe"), app.title, app.peak, app.is_meeting_app)
        if selected is not None and self.select_pid(selected):
            return
        if self.table.rowCount() > 0 and not self.table.selectedItems():
            self.table.selectRow(1 if self._apps and self._apps[0].is_meeting_app else 0)

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
        it = self.table.item(items[0].row(), 0)
        return int(it.data(Qt.ItemDataRole.UserRole)) if it is not None else None

    def select_pid(self, pid: int) -> bool:
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and int(it.data(Qt.ItemDataRole.UserRole)) == pid:
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
            return SourceSpec(kind=SourceKind.SYSTEM, label="Sistema inteiro")
        for app in self._apps:
            if app.pid == pid:
                return SourceSpec(kind=SourceKind.APP, pid=app.pid, exe=app.exe, label=app.label)
        return SourceSpec(kind=SourceKind.SYSTEM, label="Sistema inteiro")
