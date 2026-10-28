"""Resumo de fim de gravação (F1): duração, pedaços, tamanho, marcadores; título editável (vai para revisions);
pedaços não gravados, se houver (M-04). F5: o título salvo aqui chama `on_edited` (o índice da busca usa o título em todo
trecho; o pesado pode já ter indexado com o antigo, R5-07). F5.4 L2 (U-069): o cabeçalho com o ícone de sucesso e o título
de 20 px; a lista dos marcadores só quando há marcador; OK é o primário (Enter, U-056)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QWidget

from ..capture.clock import fmt_mmss
from .common import human_size
from .icons import set_icon
from .theme import Tone, primary, role, tone

if TYPE_CHECKING:
    from ..store import Store

HEADER_ICON_PX = 32


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
        form.addRow(self._header(stats.get("unsaved")))
        form.addRow("Título", self.title_edit)
        form.addRow("Duração", QLabel(fmt_mmss(stats.get("duration_s", 0))))
        for ch in ("L", "R"):
            c = cs.get(ch)
            gaps = fmt_mmss((c["gaps"] or 0) / (c["sr"] or 16000)) if c else ""
            txt = "sem áudio" if not c else f"{c['n']} pedaços · {human_size(c['bytes'] or 0)} · buracos preenchidos: {gaps}"
            form.addRow(f"Canal {ch}", QLabel(txt))
        markers = store.list_markers(meeting_id)
        self.markers: QListWidget | None = None
        if markers:                                    # U-069: a lista só quando há marcador
            self.markers = QListWidget()
            for mk in markers:
                self.markers.addItem(f"[{fmt_mmss(mk['t_ms'] / 1000)}] {mk['note'] or '(sem nota)'}")
            self.markers.setMaximumHeight(min(160, 8 + 36 * len(markers)))
            form.addRow(f"Marcadores ({len(markers)})", self.markers)
        unsaved = stats.get("unsaved") or []
        if unsaved:
            lab = QLabel(f"{len(unsaved)} pedaço(s) não foram gravados no banco ({', '.join(unsaved)}); a reunião ficou "
                         "marcada como interrompida. Veja o log.")
            tone(lab, Tone.DANGER)
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
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            primary(self, ok)

    @staticmethod
    def _header(unsaved: object) -> QHBoxLayout:
        """U-069: o ícone de sucesso (o de aviso quando pedaços não foram gravados) e "Gravação salva" em 20 px."""
        icon = QLabel()
        icon.setFixedSize(HEADER_ICON_PX, HEADER_ICON_PX)
        if unsaved:
            set_icon(icon, "warning", Tone.WARNING, HEADER_ICON_PX)
        else:
            set_icon(icon, "success", Tone.SUCCESS, HEADER_ICON_PX)
        title = QLabel("Gravação salva")
        role(title, "subtitle")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon)
        row.addWidget(title, 1)
        return row

    @override
    def accept(self) -> None:
        title = self.title_edit.text().strip()
        changed = bool(title) and title != str((self.store.get_meeting(self.meeting_id) or {}).get("title") or "")
        if changed:
            self.store.update_title(self.meeting_id, title, author="usuario")
            if self.on_edited is not None:
                self.on_edited(self.meeting_id, "edit")
        super().accept()
