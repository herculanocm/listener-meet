"""Revisão mínima de falantes (F2 item 13, U-006): um card por voz com nome sugerido, similaridade, margem e minutos de fala;
Enter confirma, setas navegam, O = outra pessoa, N = nova pessoa, M = mesclar com outra voz, I = ignorar, Espaço ouve 5 s
(decodificados só no clique, numa `Task`). "Concluir" marca a reunião como revisada. Só metadados no construtor (R-9)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from ..log import get_logger
from ..pipeline import review
from .tasks import run_task

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.review")


class ReviewDialog(QDialog):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, on_transcript: Any = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_transcript = on_transcript
        self.items: list[review.ReviewItem] = []
        self.played: list[tuple[str, int, int]] = []       # instrumentação dos testes
        m = store.get_meeting(meeting_id) or {}
        self.setWindowTitle(f"Quem falou — {m.get('title') or f'reunião {meeting_id}'}")
        self.setMinimumSize(640, 380)
        self.header = QLabel("")
        self.header.setWordWrap(True)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list.installEventFilter(self)                 # Enter, Espaço e letras valem com o foco na lista
        self.btn_confirm = QPushButton("Confirmar (Enter)")
        self.btn_other = QPushButton("Outra pessoa (O)")
        self.btn_new = QPushButton("Nova pessoa (N)")
        self.btn_merge = QPushButton("É a mesma que… (M)")
        self.btn_ignore = QPushButton("Ignorar (I)")
        self.btn_play = QPushButton("Ouvir 5 s (Espaço)")
        self.btn_transcript = QPushButton("Ver transcrição")
        self.btn_finish = QPushButton("Concluir")
        self.btn_confirm.clicked.connect(self._confirm_clicked)
        self.btn_other.clicked.connect(self.ask_other_person)
        self.btn_new.clicked.connect(self.ask_new_person)
        self.btn_merge.clicked.connect(self.ask_merge)
        self.btn_ignore.clicked.connect(self.ignore_current)
        self.btn_play.clicked.connect(self.play_current)
        self.btn_transcript.clicked.connect(self._open_transcript)
        self.btn_finish.clicked.connect(self.finish)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (self.btn_confirm, self.btn_other, self.btn_new, self.btn_merge, self.btn_ignore):
            row1.addWidget(b)
        row2.addWidget(self.btn_play)
        row2.addStretch(1)
        row2.addWidget(self.btn_transcript)
        row2.addWidget(self.btn_finish)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.list, 1)
        lay.addLayout(row1)
        lay.addLayout(row2)
        self.list.itemDoubleClicked.connect(lambda _item: self.play_current())
        self.reload()

    # ---------------------------------------------------------------- estado
    def reload(self, keep: int | None = None) -> None:
        current = keep if keep is not None else self.current_cluster_id()
        self.items = review.review_items(self.store, self.meeting_id)
        self.list.clear()
        for it in self.items:
            item = QListWidgetItem(f"{it.title} · {it.minutes_text} · {it.summary()}")
            item.setData(Qt.ItemDataRole.UserRole, it.cluster_id)
            if it.pending and it.merged_into is None:
                item.setForeground(Qt.GlobalColor.darkRed)
            self.list.addItem(item)
        waiting = review.pending(self.items)
        self.header.setText("Tudo confirmado: nenhuma voz pendente." if not waiting else
                            f"{len(waiting)} voz(es) para confirmar. Enter aceita a sugestão; setas navegam.")
        self.btn_transcript.setEnabled(self.store.segments.count_segments(self.meeting_id) > 0)
        target = current if current is not None else (waiting[0].cluster_id if waiting else None)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self.list.setCurrentRow(i)
                break
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.list.setFocus()

    def current_cluster_id(self) -> int | None:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def current_item(self) -> review.ReviewItem | None:
        cid = self.current_cluster_id()
        return next((i for i in self.items if i.cluster_id == cid), None)

    def _next_pending(self) -> None:
        waiting = review.pending(review.review_items(self.store, self.meeting_id))
        self.reload(waiting[0].cluster_id if waiting else self.current_cluster_id())

    # ---------------------------------------------------------------- ações
    def _confirm_clicked(self) -> None:
        self.confirm_current()

    def confirm_current(self, person_id: int | None = None) -> bool:
        it = self.current_item()
        if it is None:
            return False
        if person_id is None and it.person_id is None:
            self.ask_other_person()
            return False
        try:
            review.confirm(self.store, it.cluster_id, person_id)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._next_pending()
        return True

    def ask_other_person(self) -> None:
        people = self.store.people.list_people()
        if not people:
            self.ask_new_person()
            return
        names = [str(p["name"]) for p in people]
        name, ok = QInputDialog.getItem(self, "Outra pessoa", "Quem é esta voz?", names, 0, False)
        if ok and name in names:
            self.confirm_current(int(people[names.index(name)]["id"]))

    def ask_new_person(self) -> None:
        name, ok = QInputDialog.getText(self, "Nova pessoa", "Nome:")
        if ok and name.strip():
            self.create_person(name.strip())

    def create_person(self, name: str, role: str = "") -> bool:
        it = self.current_item()
        if it is None:
            return False
        try:
            review.new_person(self.store, it.cluster_id, name, role)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._next_pending()
        return True

    def ask_merge(self) -> None:
        it = self.current_item()
        others = [o for o in self.items if o.cluster_id != (it.cluster_id if it else None) and o.merged_into is None]
        if it is None or not others:
            return
        labels = [f"{o.title} · {o.summary()}" for o in others]
        choice, ok = QInputDialog.getItem(self, "É a mesma pessoa que…", "Voz:", labels, 0, False)
        if ok and choice in labels:
            self.merge_into(others[labels.index(choice)].cluster_id)

    def merge_into(self, cluster_id: int) -> bool:
        it = self.current_item()
        if it is None:
            return False
        try:
            review.merge(self.store, it.cluster_id, cluster_id)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._next_pending()
        return True

    def ignore_current(self) -> None:
        it = self.current_item()
        if it is not None:
            review.ignore(self.store, it.cluster_id)
            self._next_pending()

    def play_current(self) -> None:
        it = self.current_item()
        if it is None:
            return
        rng = review.clip_range(self.store, it.cluster_id)
        if rng is None:
            self.header.setText("Esta voz ainda não tem trecho para ouvir.")
            return
        channel, t0, t1 = rng
        self.played.append(rng)
        run_task(lambda: review.decode_clip(self.store, self.meeting_id, channel, t0, t1), self.store, self._on_clip)

    def _on_clip(self, result: Any) -> None:
        if isinstance(result, BaseException):
            self.header.setText(f"Não consegui decodificar o trecho: {result}")
            return
        samples, sr = result
        from ..store import playback

        try:
            playback.play(samples, sr)
        except Exception as e:
            log.warning("reprodução indisponível: %s", e)
            self.header.setText(f"Reprodução indisponível: {e}")

    def _open_transcript(self) -> None:
        if self.on_transcript is not None:
            self.on_transcript(self.meeting_id)

    def finish(self) -> None:
        review.finish(self.store, self.meeting_id)
        self.accept()

    # ---------------------------------------------------------------- teclado (U-006)
    def handle_key(self, key: int) -> bool:
        """True quando a tecla é um atalho da revisão (setas ficam com a lista)."""
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm_current()
        elif key == Qt.Key.Key_O:
            self.ask_other_person()
        elif key == Qt.Key.Key_N:
            self.ask_new_person()
        elif key == Qt.Key.Key_M:
            self.ask_merge()
        elif key == Qt.Key.Key_I:
            self.ignore_current()
        elif key == Qt.Key.Key_Space:
            self.play_current()
        elif key == Qt.Key.Key_Escape:
            self.reject()
        else:
            return False
        return True

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self.handle_key(event.key()):
            super().keyPressEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.list and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and self.handle_key(event.key()):
            return True
        return super().eventFilter(watched, event)
