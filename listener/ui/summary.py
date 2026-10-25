"""Resumo de fim de gravação (F1): duração, pedaços, tamanho, marcadores; título editável (vai para revisions);
pedaços não gravados, se houver (M-04). F5: o título salvo aqui chama `on_edited` (o índice da busca usa o título em todo
trecho; o pesado pode já ter indexado com o antigo, R5-07)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget, QWidget

from ..capture.clock import fmt_mmss
from .common import human_size

if TYPE_CHECKING:
    from ..store import Store


class SummaryDialog(QDialog):
    def __init__(self, store: Store, meeting_id: int, stats: dict[str, Any], parent: QWidget | None = None,
                 on_edited: Callable[..., Any] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_edited = on_edited                     # F5: `on_edited(meeting_id, motivo)` reindexa a reunião
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
        unsaved = stats.get("unsaved") or []
        if unsaved:
            lab = QLabel(f"{len(unsaved)} pedaço(s) não foram gravados no banco ({', '.join(unsaved)}); a reunião ficou "
                         "marcada como interrompida. Veja o log.")
            lab.setStyleSheet("color: #e74c3c;")
            lab.setWordWrap(True)
            form.addRow("Atenção", lab)
        warns = stats.get("warnings") or []
        if warns:
            w = QLabel("\n".join(warns))
            w.setWordWrap(True)
            form.addRow("Avisos", w)
        sizes = store.db_sizes()
        form.addRow("Banco", QLabel(f"texto {human_size(sizes['main'])} · áudio {human_size(sizes['audio'])} "
                                    f"em {len(sizes.get('audio_files', {}))} arquivo(s)"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        form.addRow(buttons)

    @override
    def accept(self) -> None:
        title = self.title_edit.text().strip()
        changed = bool(title) and title != str((self.store.get_meeting(self.meeting_id) or {}).get("title") or "")
        if changed:
            self.store.update_title(self.meeting_id, title, author="usuario")
            if self.on_edited is not None:
                self.on_edited(self.meeting_id, "edit")
        super().accept()
