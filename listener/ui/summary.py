"""Resumo de fim de gravação (F1): duração, pedaços, tamanho, marcadores; título editável (vai para revisions)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget

from ..capture.clock import fmt_mmss
from .common import human_size


class SummaryDialog(QDialog):
    def __init__(self, store, meeting_id: int, stats: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.setWindowTitle("Gravação salva")
        self.setMinimumWidth(480)
        m = store.get_meeting(meeting_id) or {}
        cs = store.chunk_stats(meeting_id)
        self.title_edit = QLineEdit(m.get("title") or "")
        self.title_edit.setPlaceholderText("título da reunião")
        form = QFormLayout(self)
        form.addRow("Título", self.title_edit)
        form.addRow("Duração", QLabel(fmt_mmss(stats.get("duration_s", 0))))
        for ch in ("L", "R"):
            c = cs.get(ch)
            gaps = fmt_mmss((c["gaps"] or 0) / (c["sr"] or 16000)) if c else ""
            txt = "sem áudio" if not c else f"{c['n']} pedaços · {human_size(c['bytes'] or 0)} · buracos preenchidos: {gaps}"
            form.addRow(f"Canal {ch}", QLabel(txt))
        markers = store.list_markers(meeting_id)
        lst = QListWidget()
        for mk in markers:
            lst.addItem(f"[{fmt_mmss(mk['t_ms'] / 1000)}] {mk['note'] or '(sem nota)'}")
        form.addRow(f"Marcadores ({len(markers)})", lst)
        warns = stats.get("warnings") or []
        if warns:
            form.addRow("Avisos", QLabel("\n".join(warns)))
        sizes = store.db_sizes()
        form.addRow("Banco", QLabel(f"texto {human_size(sizes['main'])} · áudio {human_size(sizes['audio'])}"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        form.addRow(buttons)

    def accept(self) -> None:
        title = self.title_edit.text().strip()
        if title:
            self.store.update_title(self.meeting_id, title, author="usuario")
        super().accept()
