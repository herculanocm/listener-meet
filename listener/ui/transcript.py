"""Leitura mínima da transcrição (F2 item 14, Q6): `QListView` com modelo (milhares de falas sem milhares de widgets),
linhas `[mm:ss] Nome — Cargo: texto`; o clique toca a partir da primeira palavra menos 150 ms (R-14), decodificando só
esse trecho numa `Task`. F3 item 8 (P4): mostra a transcrição revisada quando existe, com alternância para a original."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QListView, QPushButton, QSplitter, QVBoxLayout, QWidget

from ..log import get_logger
from ..pipeline import review
from .chat import ChatPanel
from .tasks import run_task

if TYPE_CHECKING:
    from ..store import Store

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
            return f"canal {row.channel} · {row.t_start_ms / 1000:.1f} a {row.t_end_ms / 1000:.1f} s"
        return None

    def replace(self, rows: list[review.TranscriptRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class TranscriptDialog(QDialog):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, on_review: Any = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_review = on_review
        self.played: list[tuple[int, int]] = []           # (t0, t1) pedidos: instrumentação dos testes
        m = store.get_meeting(meeting_id) or {}
        self.setWindowTitle(f"Transcrição — {m.get('title') or f'reunião {meeting_id}'}")
        self.setMinimumSize(960, 520)
        self.prefer_llm = True
        self.model = SegmentsModel(review.transcript_rows(store, meeting_id, True), self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setWordWrap(True)
        self.view.setUniformItemSizes(False)
        self.view.clicked.connect(self._clicked)
        self.status = QLabel("")
        self.toggle = QCheckBox("mostrar a transcrição revisada pela LLM")
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self.set_prefer_llm)
        self.btn_review = QPushButton("Revisar falantes…")
        self.btn_review.clicked.connect(lambda: self.on_review(self.meeting_id) if self.on_review else None)
        bottom = QHBoxLayout()
        bottom.addWidget(self.toggle)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_review)
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.addWidget(self.view, 1)
        llay.addWidget(self.status)
        llay.addLayout(bottom)
        self.chat = ChatPanel(store, meeting_id)                 # F3 item 10: chat ao lado da transcrição
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.chat)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.addWidget(self.splitter, 1)
        self._update_status()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.chat.close_panel()
        super().closeEvent(event)

    def set_prefer_llm(self, on: bool) -> None:
        self.prefer_llm = bool(on)
        self.reload()

    def reload(self) -> None:
        self.model.replace(review.transcript_rows(self.store, self.meeting_id, self.prefer_llm))
        self._update_status()
        self.chat.reload()

    def _update_status(self) -> None:
        revised = sum(1 for r in self.model.rows if r.text_llm)
        mode = "revisada" if self.prefer_llm else "original"
        self.toggle.setEnabled(revised > 0)
        self.status.setText(f"{self.model.rowCount()} fala(s), {revised} revisada(s) pela LLM; mostrando a {mode}. "
                            "Clique numa fala para ouvir.")

    def _clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self.play_row(index.row())

    def play_row(self, row: int) -> None:
        r = self.model.rows[row]
        t0, t1 = r.play_from_ms, max(r.t_end_ms, r.play_from_ms + 500)
        self.played.append((t0, t1))
        run_task(lambda: review.decode_clip(self.store, self.meeting_id, r.channel, t0, t1), self.store, self._on_clip)

    def _on_clip(self, result: Any) -> None:
        if isinstance(result, BaseException):
            self.status.setText(f"Não consegui decodificar o trecho: {result}")
            return
        samples, sr = result
        from ..store import playback

        try:
            playback.play(samples, sr)
        except Exception as e:
            log.warning("reprodução indisponível: %s", e)
            self.status.setText(f"Reprodução indisponível: {e}")
