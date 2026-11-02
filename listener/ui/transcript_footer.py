"""O rodapé da transcrição (F3 item 8, P4; F3.1 R3-05), fora do `ui/transcript.py`, que estava no limite de 400 linhas
(F5.11, A-35: dividir antes de crescer): o texto do que a lista mostra, a caixa da transcrição revisada, "Revisar a
transcrição com IA" sob demanda e a nota com o motivo de quando a revisão foi pulada. F5.11 (U-117): o texto diz o que a
lista mostra, e a caixa só aparece com uma fala revisada. Um mixin da `TranscriptTab`: usa o estado e os métodos dela."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton

from ..fmt import plural
from ..model import JobStep
from .llm_tabs import last_llm_info, llm_job_active, request_llm_step
from .theme import note

if TYPE_CHECKING:
    from ..store import Store
    from .transcript import SegmentsModel


def status_text(falas: int, revised: int, prefer_llm: bool) -> str:
    """U-117: o que a lista mostra ("32 falas, 12 revisadas pela IA: mostrando a transcrição revisada.")."""
    if not revised:
        shown = f"{plural(falas, 'fala')} da transcrição original"
    elif prefer_llm:
        shown = f"{plural(falas, 'fala')}, {plural(revised, 'revisada')} pela IA: mostrando a transcrição revisada"
    else:
        shown = f"{plural(falas, 'fala')}: mostrando a transcrição original ({plural(revised, 'revisada')} pela IA)"
    return f"{shown}. Clique numa fala para ouvir daquele ponto."


class TranscriptFooterMixin:
    """O rodapé da `TranscriptTab` (o texto, a caixa da revisada, o pedido de revisão e a nota)."""
    store: Store
    meeting_id: int
    model: SegmentsModel
    prefer_llm: bool
    on_launch: Callable[[bool], Any] | None
    requested: list[int]

    def reload(self, force: bool = False) -> bool:
        raise NotImplementedError

    def _page(self) -> None:
        raise NotImplementedError

    def _build_footer(self) -> QHBoxLayout:
        """O texto, a caixa, o botão e a nota; devolve a linha de baixo (a caixa e o botão)."""
        self.status = QLabel("")
        self.toggle = QCheckBox("mostrar a transcrição revisada pela IA")
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self.set_prefer_llm)
        self.btn_revise = QPushButton("Revisar a transcrição com IA")
        self.btn_revise.clicked.connect(self.request_revise)
        self.llm_note = QLabel("")
        self.llm_note.setWordWrap(True)
        note(self.llm_note)
        bottom = QHBoxLayout()
        bottom.addWidget(self.toggle)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_revise)
        return bottom

    def set_prefer_llm(self, on: bool) -> None:
        self.prefer_llm = bool(on)
        self.reload(force=True)

    def _update_status(self) -> None:
        revised = sum(1 for r in self.model.rows if r.text_llm)
        self.toggle.setVisible(revised > 0)                      # U-117: a caixa só com uma fala revisada
        self._page()
        self.status.setVisible(self.model.rowCount() > 0)        # sem falas, o estado vazio já diz (o texto fica)
        self.status.setText(status_text(self.model.rowCount(), revised, self.prefer_llm))
        busy = llm_job_active(self.store, self.meeting_id)
        self.btn_revise.setEnabled(not busy and self.model.rowCount() > 0)
        self.btn_revise.setToolTip("há um job de LLM em andamento para esta reunião" if busy
                                   else "a LLM revisa os trechos ainda não revisados (pelo provedor ativo, com custo)")
        reason = (last_llm_info(self.store, self.meeting_id).get("skipped") or {}).get(str(JobStep.REVISE))
        if busy:
            self.llm_note.setText("A IA está trabalhando nesta reunião.")
        elif reason:
            self.llm_note.setText(f"Revisão pela IA não feita: {reason}; revisar?")
        else:
            self.llm_note.setText("")

    def request_revise(self) -> int | None:
        """"Revisar a transcrição com IA": job `llm` só com `revise` (incremental: só trechos sem revisão)."""
        job_id, message = request_llm_step(self.store, self.meeting_id, JobStep.REVISE)
        if job_id is not None:
            self.requested.append(job_id)
            if self.on_launch is not None:
                self.on_launch(True)
        self._update_status()
        self.llm_note.setText(message)
        return job_id


__all__ = ["TranscriptFooterMixin", "status_text"]
