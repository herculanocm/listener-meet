"""Tabela "apps tocando agora" (U-001) + "Sistema inteiro", com medidor de pico por app.
A consulta roda numa thread própria (F1.1 A4): a interface nunca espera pelo sistema de áudio. O poller não tem
parent, acorda por `Event` e se destrói ao terminar (M-12); `first_apps_ready` dispara uma vez, na primeira lista.
F5.3 L4 (U-051, A-21): cada linha tem uma chave tipada (`SourceKey`) no lugar do pid -1 do sistema inteiro; "Sistema
inteiro" na saída padrão (acompanha) e uma linha "Sistema inteiro" por saída ativa, com o nome dela na segunda coluna
("Janela ou saída", larga: o nome longo de uma saída não empurra a coluna das janelas para fora) e "(comunicação)" na
primeira, sempre à vista, quando o padrão de comunicação difere (A-20). As saídas vêm do Core Audio na thread da
interface, relidas a cada 2 s (só a assinatura, A-19); a escolhida que sai volta a seleção para a padrão."""
from __future__ import annotations

import functools
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..capture.recorder import SourceSpec
from ..log import get_logger
from ..model import SourceKind
from ..platform.base import Endpoints, PlayingApp

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.sources")
OUTPUTS_MS = 2000
SYSTEM_TEXT = "Sistema inteiro"
FOLLOW_TEXT = "saída padrão (acompanha)"
_live_pollers: set[QThread] = set()      # referências até `finished` (sem parent, M-12)


@dataclass(frozen=True, slots=True)
class SourceKey:
    """A chave de uma linha (A-21): um app pelo pid, ou o sistema inteiro numa saída (vazio = a padrão, que acompanha)."""
    kind: str
    pid: int = 0
    output_id: str = ""


SYSTEM_FOLLOW = SourceKey(SourceKind.SYSTEM)
type _Row = tuple[SourceKey, str, str, float, bool]       # chave, fonte, janela, pico, app de reunião


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
        self._endpoints: Endpoints | None = None
        self._poller: _Poller | None = None
        self._got_first = False
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Fonte", "Janela ou saída", "Nível"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)     # F5.3: o fim do nome ("(comunicação)") à vista
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 110)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self._outputs_timer = QTimer(self)                # F5.3 (U-051): saída conectada com a tabela aberta
        self._outputs_timer.setInterval(OUTPUTS_MS)
        self._outputs_timer.timeout.connect(self.refresh_outputs)
        self._endpoints = self._read_outputs()
        self.render_apps([])

    def start(self) -> None:
        if self._poller is None:
            poller = _Poller(self.platform, self.refresh_ms)
            poller.apps_ready.connect(self._on_apps)
            poller.finished.connect(functools.partial(_release, poller))
            _live_pollers.add(poller)
            self._poller = poller
            poller.start()
        self._outputs_timer.start()

    def stop(self) -> None:
        self._outputs_timer.stop()
        if self._poller is not None:
            poller, self._poller = self._poller, None
            poller.apps_ready.disconnect(self._on_apps)
            poller.interrupt()                            # termina sozinho; `finished` faz o deleteLater

    def _read_outputs(self) -> Endpoints | None:
        try:
            return self.platform.audio_endpoints()
        except Exception as e:  # sem o Core Audio: só a saída padrão
            log.debug("saídas de áudio: %s", e)
            return None

    def refresh_outputs(self) -> bool:
        """As saídas ativas de novo (só a assinatura quando nada mudou, A-19); True quando a lista mudou."""
        reading = self._read_outputs()
        if reading == self._endpoints:
            return False
        self._endpoints = reading
        self.render_apps(self._apps)
        return True

    # ---- dados ----
    def _on_apps(self, apps: list[PlayingApp]) -> None:
        self.render_apps(apps)
        if not self._got_first:
            self._got_first = True
            self.first_apps_ready.emit()

    def _rows(self) -> list[_Row]:
        reading = self._endpoints
        default = reading.output(reading.render_default) if reading is not None else None
        now = f" (agora: {default.name})" if default is not None and default.name else ""
        rows: list[_Row] = [(SYSTEM_FOLLOW, SYSTEM_TEXT, f"{FOLLOW_TEXT}{now}", 0.0, False)]
        for e in reading.render if reading is not None else ():
            comm = reading is not None and e.id == reading.render_comm and reading.render_comm != reading.render_default
            rows.append((SourceKey(SourceKind.SYSTEM, output_id=e.id), SYSTEM_TEXT + (" (comunicação)" if comm else ""),
                         e.name or e.id, 0.0, False))
        rows += [(SourceKey(SourceKind.APP, pid=app.pid), app.exe.removesuffix(".exe"), app.title, app.peak, app.is_meeting_app)
                 for app in self._apps]
        return rows

    def render_apps(self, apps: list[PlayingApp]) -> None:
        selected = self.selected_key()
        self._apps = list(apps)
        rows = self._rows()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._set_row(i, *row)
        if selected is not None and self.select_key(selected):
            return
        if selected is not None and selected.kind == SourceKind.SYSTEM:
            self.select_key(SYSTEM_FOLLOW)                # a saída escolhida saiu: a padrão
            return
        if self.table.rowCount() > 0 and not self.table.selectedItems():
            first_app = len(rows) - len(self._apps)
            self.table.selectRow(first_app if self._apps and self._apps[0].is_meeting_app else 0)

    def _set_row(self, row: int, key: SourceKey, name: str, title: str, peak: float, meeting: bool) -> None:
        it = self.table.item(row, 0)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(row, 0, it)
        it.setText(("● " if meeting else "") + name)
        it.setData(Qt.ItemDataRole.UserRole, key)
        t = self.table.item(row, 1)
        if t is None:
            t = QTableWidgetItem()
            self.table.setItem(row, 1, t)
        t.setText(title or "")
        t.setToolTip(title or "")                         # o nome inteiro, cortado ou não
        bar = self.table.cellWidget(row, 2)
        if not isinstance(bar, QProgressBar):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            self.table.setCellWidget(row, 2, bar)
        bar.setValue(int(min(1.0, peak) * 100))

    def selected_key(self) -> SourceKey | None:
        items = self.table.selectedItems()
        if not items:
            return None
        it = self.table.item(items[0].row(), 0)
        key = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        return key if isinstance(key, SourceKey) else None

    def select_key(self, key: SourceKey) -> bool:
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == key:
                self.table.selectRow(row)
                return True
        return False

    def select_exe(self, exe: str) -> bool:
        want = (exe or "").lower()
        for app in self._apps:
            if app.exe.lower() == want:
                return self.select_key(SourceKey(SourceKind.APP, pid=app.pid))
        return False

    def selected_spec(self) -> SourceSpec:
        key = self.selected_key()
        if key is None or key.kind == SourceKind.SYSTEM:
            ep = self._endpoints.output(key.output_id) if key is not None and key.output_id and self._endpoints else None
            if ep is None:
                return SourceSpec(kind=SourceKind.SYSTEM, label="Sistema inteiro")
            return SourceSpec(kind=SourceKind.SYSTEM, label=f"Sistema inteiro: {ep.name or ep.id}", output_id=ep.id)
        for app in self._apps:
            if app.pid == key.pid:
                return SourceSpec(kind=SourceKind.APP, pid=app.pid, exe=app.exe, label=app.label)
        return SourceSpec(kind=SourceKind.SYSTEM, label="Sistema inteiro")
