"""Tabela "apps tocando agora" (U-001) + "Sistema inteiro", com medidor de pico por app.
A consulta roda numa thread própria (F1.1 A4): a interface nunca espera pelo sistema de áudio. O poller não tem
parent, acorda por `Event` e se destrói ao terminar (M-12); `first_apps_ready` dispara uma vez, na primeira lista.
F5.3 L4 (U-051, A-21): cada linha tem uma chave tipada (`SourceKey`) no lugar do pid -1 do sistema inteiro; "Sistema
inteiro" na saída padrão (acompanha) e uma linha "Sistema inteiro" por saída ativa, com o nome dela na segunda coluna
("Janela ou saída", larga: o nome longo de uma saída não empurra a coluna das janelas para fora) e "(comunicação)" na
primeira, sempre à vista, quando o padrão de comunicação difere (A-20). As saídas vêm do Core Audio na thread da
interface, relidas a cada 2 s (só a assinatura, A-19); a escolhida que sai volta a seleção para a padrão.
F5.4 L2 (U-067): cada linha com o medidor do app (o `LevelMeter`, com as zonas, no lugar da barra de progresso) e o app de
reunião com o selo "Reunião" na coluna da fonte (no lugar do "●"), pintado pelo `MeetingBadgeDelegate`.
F5.11 (U-110, A-30): a tabela virou uma lista no molde do painel do widget: o nome primeiro (cortado no meio) e o tipo na
linha de baixo, o ✓ na escolhida, "Novo" na saída que chegou com o diálogo aberto e "Reunião" no app de reunião, pelo
`SourceRowDelegate` (derivado do `DeviceRowDelegate`), com o medidor de cada linha pintado à direita (`MeterPainter`). A API
fica (`selected_key`, `select_key`, `select_exe`, `selected_spec`, `render_apps`, `refresh_outputs`, os dois sinais); o
texto inteiro da linha segue no `DisplayRole` (leitor de tela) e na dica."""
from __future__ import annotations

import functools
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyleOptionViewItem, QVBoxLayout, QWidget

from ..capture.recorder import SourceSpec
from ..log import get_logger
from ..model import SourceKind
from ..platform.base import Endpoints, PlayingApp
from .common import level_fraction
from .devices_panel import CURRENT_ROLE, DETAIL_ROLE, NAME_ROLE, NEW_ROLE, DeviceRowDelegate
from .meters import MeterPainter

if TYPE_CHECKING:
    from ..platform.base import Platform
    from .theme import Tokens

log = get_logger("ui.sources")
OUTPUTS_MS = 2000
SYSTEM_TEXT = "Sistema inteiro"
FOLLOW_NAME = "Saída padrão (acompanha)"
APP_TEXT = "App"
MEETING_BADGE = "Reunião"                # U-067: o app de reunião
MEETING_ROLE, LEVEL_ROLE = Qt.ItemDataRole.UserRole + 5, Qt.ItemDataRole.UserRole + 6    # depois dos papéis do painel
LEVEL_W, METER_H, BADGE_GAP = 80, 6, 8
_live_pollers: set[QThread] = set()      # referências até `finished` (sem parent, M-12)


@dataclass(frozen=True, slots=True)
class SourceKey:
    """A chave de uma linha (A-21): um app pelo pid, ou o sistema inteiro numa saída (vazio = a padrão, que acompanha)."""
    kind: str
    pid: int = 0
    output_id: str = ""


SYSTEM_FOLLOW = SourceKey(SourceKind.SYSTEM)


@dataclass(frozen=True, slots=True)
class SourceRow:
    """Uma linha da lista (U-110): o nome primeiro, o tipo embaixo, o pico (o medidor) e as marcas."""
    key: SourceKey
    name: str
    detail: str
    level: float
    meeting: bool = False                # o app de reunião: o selo "Reunião"
    new: bool = False                    # a saída que chegou com o diálogo aberto: o selo "Novo"

    @property
    def text(self) -> str:
        """A linha inteira (o leitor de tela e a dica)."""
        return f"{self.name} · {self.detail}" if self.detail else self.name


def levels_by_output(apps: Iterable[PlayingApp]) -> dict[str, float]:
    """F5.9 (item 9): o maior pico de cada saída entre os apps que tocam nela (a linha de cada saída mostra o nível dela:
    a chamada no fone de comunicação com o alto-falante como padrão aparece onde toca)."""
    levels: dict[str, float] = {}
    for app in apps:
        for eid, peak in app.outputs:
            levels[eid] = max(levels.get(eid, 0.0), peak)
    return levels


def plays_on(app: PlayingApp, reading: Endpoints | None) -> str:
    """F5.9 (item 9): a segunda coluna da linha do app: o título e "toca em: <saídas>" (o nome do aparelho só na tela,
    A-22; a saída que a leitura não nomeia fica de fora)."""
    names: list[str] = []
    for eid, _peak in app.outputs:
        ep = reading.output(eid) if reading is not None else None
        if ep is not None and ep.name:
            names.append(ep.name)
    where = f"toca em: {', '.join(names)}" if names else ""
    return " · ".join(part for part in (app.title, where) if part)


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


class SourceRowDelegate(DeviceRowDelegate):
    """A-30: a linha do painel do widget com o medidor dela pintado à direita e o selo "Reunião" no app de reunião."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.meter = MeterPainter()

    @override
    def content_rect(self, painter: QPainter, opt: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex,
                     t: Tokens) -> QRect:
        r = super().content_rect(painter, opt, index, t)
        bar = QRect(r.right() - LEVEL_W + 1, r.center().y() - METER_H // 2, LEVEL_W, METER_H)
        self.meter.paint(painter, bar, level_fraction(min(1.0, float(index.data(LEVEL_ROLE) or 0.0))), 0.0, t)
        return r.adjusted(0, 0, -(LEVEL_W + BADGE_GAP), 0)

    @override
    def badge_text(self, index: QModelIndex | QPersistentModelIndex) -> str:
        return MEETING_BADGE if index.data(MEETING_ROLE) else super().badge_text(index)


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
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)      # F5.3: o fim do nome ("(comunicação)") à vista
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(SourceRowDelegate(self.list))
        self.list.setAccessibleName("Saídas e apps que tocam agora")
        self.list.itemSelectionChanged.connect(self._on_selection)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list)
        self._outputs_timer = QTimer(self)                # F5.3 (U-051): saída conectada com a lista aberta
        self._outputs_timer.setInterval(OUTPUTS_MS)
        self._outputs_timer.timeout.connect(self.refresh_outputs)
        self._endpoints = self._read_outputs()
        self._first_outputs = frozenset(e.id for e in self._endpoints.render) if self._endpoints is not None else frozenset()
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

    def _rows(self) -> list[SourceRow]:
        """U-110: a padrão que acompanha, cada saída ativa (o nome dela primeiro) e cada app que toca."""
        reading = self._endpoints
        default = reading.output(reading.render_default) if reading is not None else None
        now = f" · agora {default.name}" if default is not None and default.name else ""
        levels = levels_by_output(self._apps)              # F5.9 (item 9): o nível de cada saída, pelos apps que tocam nela
        rows = [SourceRow(SYSTEM_FOLLOW, FOLLOW_NAME, f"{SYSTEM_TEXT}{now}", levels.get(default.id, 0.0) if default else 0.0)]
        for e in reading.render if reading is not None else ():
            comm = reading is not None and e.id == reading.render_comm and reading.render_comm != reading.render_default
            rows.append(SourceRow(SourceKey(SourceKind.SYSTEM, output_id=e.id), e.name or e.id,
                                  SYSTEM_TEXT + (" (comunicação)" if comm else ""), levels.get(e.id, 0.0),
                                  new=e.id not in self._first_outputs))
        rows += [SourceRow(SourceKey(SourceKind.APP, pid=app.pid), app.exe.removesuffix(".exe"),
                           " · ".join(p for p in (APP_TEXT, plays_on(app, reading)) if p), app.peak, app.is_meeting_app)
                 for app in self._apps]
        return rows

    def render_apps(self, apps: list[PlayingApp]) -> None:
        selected = self.selected_key()
        self._apps = list(apps)
        rows = self._rows()
        while self.list.count() > len(rows):
            self.list.takeItem(self.list.count() - 1)
        for i, row in enumerate(rows):
            self._set_row(i, row)
        if selected is not None and self.select_key(selected):
            return
        if selected is not None and selected.kind == SourceKind.SYSTEM:
            self.select_key(SYSTEM_FOLLOW)                # a saída escolhida saiu: a padrão
            return
        if self.list.count() > 0 and not self.list.selectedItems():
            first_app = len(rows) - len(self._apps)
            self.list.setCurrentRow(first_app if self._apps and self._apps[0].is_meeting_app else 0)

    def _set_row(self, i: int, row: SourceRow) -> None:
        item = self.list.item(i)
        if item is None:
            item = QListWidgetItem()
            self.list.addItem(item)
        item.setText(row.text)
        item.setToolTip(f"{row.text}\napp de reunião" if row.meeting else row.text)   # o nome inteiro, cortado ou não
        for role, value in ((Qt.ItemDataRole.UserRole, row.key), (NAME_ROLE, row.name), (DETAIL_ROLE, row.detail),
                            (NEW_ROLE, row.new), (MEETING_ROLE, row.meeting), (LEVEL_ROLE, row.level),
                            (CURRENT_ROLE, item.isSelected())):
            if item.data(role) != value:
                item.setData(role, value)

    def _on_selection(self) -> None:
        """O ✓ e o nome em semibold na escolhida (o `CURRENT_ROLE` do painel), e o aviso de quem segue a escolha."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            if bool(item.data(CURRENT_ROLE)) != item.isSelected():
                item.setData(CURRENT_ROLE, item.isSelected())
        self.selection_changed.emit()

    def selected_key(self) -> SourceKey | None:
        items = self.list.selectedItems()
        key = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        return key if isinstance(key, SourceKey) else None

    def select_key(self, key: SourceKey) -> bool:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(i)
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
