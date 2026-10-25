"""Leitura da transcrição (F2 item 14, Q6): `QListView` com modelo (milhares de falas sem milhares de widgets), linhas
`[mm:ss] Nome — Cargo: texto`. F3 item 8 (P4): mostra a transcrição revisada quando existe, com alternância para a original, e
"Revisar transcrição pela LLM" sob demanda com o motivo de quando foi pulada (F3.1 R3-05).

F5 L4 (Q6): a janela "Transcrição" vira a aba "Transcrição" da reunião aberta (`TranscriptTab`). O clique numa fala (ou
Enter) toca pelo player único do app, a partir da primeira palavra menos 150 ms (R-14), com L e R misturados até parar;
`select_time` acha a fala de início mais perto de um tempo, seleciona e rola até ela ("abrir neste ponto"); a recarga só
refaz a lista quando as falas mudaram e, refazendo, mantém a fala selecionada e a rolagem (o fim de um job não leva a
leitura para o topo); o menu de contexto tem "Tocar daqui" e "Copiar com citação" (U-029: texto e HTML)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QListView, QMenu, QPushButton, QVBoxLayout, QWidget

from ..log import get_logger
from ..model import JobStep
from ..pipeline import review
from ..rag import export
from .common import copy_rich
from .llm_tabs import last_llm_info, llm_job_active, request_llm_step

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.transcript")


class SegmentsModel(QAbstractListModel):
    def __init__(self, rows: list[review.TranscriptRow], parent: Any = None):
        super().__init__(parent)
        self.rows = rows

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.line
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"canal {row.channel} · {row.t_start_ms / 1000:.1f} a {row.t_end_ms / 1000:.1f} s · clique para ouvir daqui"
        return None

    def replace(self, rows: list[review.TranscriptRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


def nearest_row(rows: list[review.TranscriptRow], t_ms: int) -> int | None:
    """A fala de `t_start_ms` mais perto de `t_ms` (a primeira, no empate); None sem falas."""
    best: tuple[int, int] | None = None
    for i, r in enumerate(rows):
        d = abs(r.t_start_ms - int(t_ms))
        if best is None or d < best[0]:
            best = (d, i)
    return best[1] if best is not None else None


class TranscriptTab(QWidget):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, *, player: Player | None = None,
                 on_launch: Callable[[bool], Any] | None = None, title: Callable[[], str] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.player = player
        self.on_launch = on_launch
        self.title_fn = title
        self.requested: list[int] = []                    # jobs `llm` de revisão pedidos aqui: instrumentação dos testes
        self.played: list[tuple[int, int]] = []           # (t0, fim da fala) pedidos: instrumentação dos testes
        self.prefer_llm = True
        self.model = SegmentsModel(review.transcript_rows(store, meeting_id, True), self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setWordWrap(True)
        self.view.setUniformItemSizes(False)
        self.view.setAlternatingRowColors(True)
        self.view.setSpacing(1)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.clicked.connect(self._clicked)
        self.view.installEventFilter(self)                # Enter e Espaço tocam a fala selecionada (teclado)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.status = QLabel("")
        self.toggle = QCheckBox("mostrar a transcrição revisada pela LLM")
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self.set_prefer_llm)
        self.btn_revise = QPushButton("Revisar transcrição pela LLM")
        self.btn_revise.clicked.connect(self.request_revise)
        self.llm_note = QLabel("")
        self.llm_note.setWordWrap(True)
        self.llm_note.setStyleSheet("color: #888; font-size: 11px;")
        bottom = QHBoxLayout()
        bottom.addWidget(self.toggle)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_revise)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.llm_note)
        lay.addLayout(bottom)
        self._update_status()

    # ---------------------------------------------------------------- leitura
    def set_prefer_llm(self, on: bool) -> None:
        self.prefer_llm = bool(on)
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        """Relê as falas; sem mudança, a lista fica como está (seleção e rolagem); com mudança, refaz e devolve as duas."""
        rows = review.transcript_rows(self.store, self.meeting_id, self.prefer_llm)
        if force or rows != self.model.rows:
            current = self.current_row()
            bar = self.view.verticalScrollBar()
            scroll = bar.value()
            self.model.replace(rows)
            if current is not None:
                i = next((k for k, r in enumerate(rows) if r.segment_id == current.segment_id), None)
                if i is not None:
                    self.view.setCurrentIndex(self.model.index(i))
            if not force:
                QTimer.singleShot(0, self, lambda: bar.setValue(min(scroll, bar.maximum())))   # depois do layout das linhas
        self._update_status()

    def _update_status(self) -> None:
        revised = sum(1 for r in self.model.rows if r.text_llm)
        mode = "revisada" if self.prefer_llm else "original"
        self.toggle.setEnabled(revised > 0)
        self.status.setText(f"{self.model.rowCount()} fala(s), {revised} revisada(s) pela LLM; mostrando a {mode}. "
                            "Clique numa fala para ouvir daquele ponto.")
        busy = llm_job_active(self.store, self.meeting_id)
        self.btn_revise.setEnabled(not busy and self.model.rowCount() > 0)
        self.btn_revise.setToolTip("há um job de LLM em andamento para esta reunião" if busy
                                   else "a LLM revisa os trechos ainda não revisados (pelo provedor ativo, com custo)")
        reason = (last_llm_info(self.store, self.meeting_id).get("skipped") or {}).get(str(JobStep.REVISE))
        if busy:
            self.llm_note.setText("LLM em andamento para esta reunião.")
        elif reason:
            self.llm_note.setText(f"Revisão pela LLM não feita: {reason}; revisar?")
        else:
            self.llm_note.setText("")

    def request_revise(self) -> int | None:
        """"Revisar transcrição pela LLM": job `llm` só com `revise` (incremental: só trechos sem revisão)."""
        job_id, message = request_llm_step(self.store, self.meeting_id, JobStep.REVISE)
        if job_id is not None:
            self.requested.append(job_id)
            if self.on_launch is not None:
                self.on_launch(True)
        self._update_status()
        self.llm_note.setText(message)
        return job_id

    # ---------------------------------------------------------------- navegação e som
    def current_row(self) -> review.TranscriptRow | None:
        index = self.view.currentIndex()
        return self.model.rows[index.row()] if index.isValid() and index.row() < len(self.model.rows) else None

    def current_play_from(self) -> int:
        """Onde "Tocar" da barra começa: a fala selecionada, senão o começo."""
        row = self.current_row()
        return row.play_from_ms if row is not None else 0

    def select_time(self, t_ms: int) -> review.TranscriptRow | None:
        """Seleciona a fala de início mais perto de `t_ms` e rola até ela (no meio da lista); None sem falas."""
        i = nearest_row(self.model.rows, t_ms)
        if i is None:
            return None
        index = self.model.index(i)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        QTimer.singleShot(0, self, lambda: self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter))  # depois do layout
        return self.model.rows[i]

    def _clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.play_row(index.row())

    def play_row(self, row: int) -> None:
        r = self.model.rows[row]
        self.played.append((r.play_from_ms, r.t_end_ms))
        if self.player is None:
            self.status.setText("Reprodução indisponível nesta janela.")
            return
        self.player.play_from(self.meeting_id, r.play_from_ms, title=self._title())

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (watched is self.view and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent)
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)):
            index = self.view.currentIndex()
            if index.isValid():
                self.play_row(index.row())
                return True
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- copiar com citação (U-029)
    def _title(self) -> str:
        if self.title_fn is not None:
            return self.title_fn()
        m = self.store.get_meeting(self.meeting_id) or {}
        return str(m.get("title") or m.get("title_llm") or f"Reunião {self.meeting_id}")

    def quote_row(self, row: review.TranscriptRow) -> tuple[str, str]:
        """A fala "copiada com citação": `"<fala>" — <Nome — Cargo>, <título> (dd/mm/aaaa) [mm:ss]` em texto e HTML."""
        m = self.store.get_meeting(self.meeting_id) or {}
        speaker = f"{row.name} — {row.role}" if row.role else row.name
        return export.quote(row.shown_text, speaker, self._title(), str(m.get("started_at") or ""), row.t_start_ms)

    def copy_quote(self, row: review.TranscriptRow) -> str:
        plain, rich = self.quote_row(row)
        copy_rich(plain, rich)
        self.status.setText("Fala copiada com a citação.")
        return plain

    def _context_menu(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        row = self.model.rows[index.row()]
        menu = QMenu(self)
        menu.addAction("Tocar daqui", lambda: self.play_row(index.row()))
        menu.addAction("Copiar com citação", lambda: self.copy_quote(row))
        menu.exec(self.view.viewport().mapToGlobal(pos))


__all__ = ["SegmentsModel", "TranscriptTab", "nearest_row"]
