"""O que vem antes do "Salvar vídeo com legendas…" (F5.26, D-112): a guarda das vozes pendentes (U-178), a do
reprocessamento em curso e o diálogo "Vídeo com legendas" (U-179), que escolhe o texto da legenda.

Pendente é o que `review.pending` diz (`ambíguo` ou `sem_nome`, fora das mescladas); ignorada, mesclada e confirmada contam
como decididas. Só segura o vídeo a pendente com pelo menos uma fala com texto na leitura vigente, contando as falas das
vozes mescladas nela (no vídeo, elas saem com o "Voz N" do alvo). O aviso não tem "salvar mesmo assim": "Revisar
falantes…" abre a revisão que já existe (CP11: a IA não decide nada novo).

O U-179 vem antes do diálogo de salvar: "Revisado pela IA" ou "Original (como foi transcrito)", com o padrão no que a
Transcrição mostra. Sem nenhuma fala revisada, a primeira opção fica apagada e "Gerar a revisão" faz o pedido de "Revisar a
transcrição com IA" e fecha; nada fica encadeado. O custo é o da conta do job sobre a transcrição vigente
(`llm_steps.estimate_meeting_usd`, a mesma da pergunta do reprocessamento); sem chave, sem modelo ou sem preço, o botão vai
sem valor."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QVBoxLayout, QWidget

from ..llm.costs import fmt_usd
from ..model import JobStep
from ..pipeline import review
from ..pipeline.reprocess import active_jobs

if TYPE_CHECKING:
    from ..store import Store

REPROCESSING = "A reunião está sendo reprocessada."
REVIEW = "Revisar falantes…"
CLOSE = "Fechar"
TEXT_TITLE = "Vídeo com legendas"
TEXT_LABEL = "Texto da legenda"
LLM_TEXT = "Revisado pela IA"
ORIGINAL_TEXT = "Original (como foi transcrito)"
NOT_REVISED = "A revisão pela IA não foi gerada."
CHOOSE = "Escolher onde salvar…"
CANCEL = "Cancelar"


def _root(cluster_id: int, merged: dict[int, int | None]) -> int:
    """A voz do fim da cadeia de mesclas, com guarda contra ciclo."""
    seen = {cluster_id}
    while (nxt := merged.get(cluster_id)) is not None and nxt in merged and nxt not in seen:
        cluster_id = nxt
        seen.add(nxt)
    return cluster_id


def blocking_voices(store: Store, meeting_id: int) -> int:
    """Quantas vozes pendentes do processamento vigente falam na leitura (uma fala com texto, dela ou de uma voz mesclada
    nela); 0 deixa o vídeo sair."""
    items = review.review_items(store, meeting_id)
    waiting = {i.cluster_id for i in review.pending(items)}
    if not waiting:
        return 0
    merged = {i.cluster_id: i.merged_into for i in items}
    speaking: set[int] = set()
    for s in store.segments.list_segments_light(meeting_id, store.segments.current_job_id(meeting_id)):
        cid = s.get("cluster_id")
        if cid is not None and str(s.get("text") or s.get("text_llm") or "").strip():
            root = _root(int(cid), merged)
            if root in waiting:
                speaking.add(root)
    return len(speaking)


def blocked_text(n: int) -> str:
    """U-178: "2 vozes sem nome falam no vídeo. Decida quem é cada uma para salvar." (no singular, a forma que concorda)."""
    if n == 1:
        return "1 voz sem nome fala no vídeo. Decida quem é ela para salvar."
    return f"{n} vozes sem nome falam no vídeo. Decida quem é cada uma para salvar."


def reprocessing(store: Store, meeting_id: int) -> bool:
    """Um processamento da reunião na fila ou rodando: a revisão ainda mostra o job anterior (a dívida do backlog)."""
    return bool(active_jobs(store, meeting_id))


def review_box(parent: QWidget, n: int, can_review: bool) -> tuple[QMessageBox, QPushButton]:
    """O aviso do U-178 e o "Revisar falantes…" (padrão e Enter; apagado sem a revisão); "Fechar" é o Esc."""
    box = QMessageBox(QMessageBox.Icon.Information, "Exportar", blocked_text(n), parent=parent)
    go = box.addButton(REVIEW, QMessageBox.ButtonRole.AcceptRole)
    close = box.addButton(CLOSE, QMessageBox.ButtonRole.RejectRole)
    go.setEnabled(can_review)
    box.setDefaultButton(go if can_review else close)
    box.setEscapeButton(close)
    return box, go


def ask_review(parent: QWidget, n: int, can_review: bool) -> bool:
    """Mostra o aviso; True quando o usuário escolhe "Revisar falantes…"."""
    box, go = review_box(parent, n, can_review)
    try:
        box.exec()
        return box.clickedButton() is go
    finally:
        box.deleteLater()


def revise_cost(store: Store, meeting_id: int) -> float | None:
    """O custo estimado de revisar a transcrição vigente inteira; None sem chave da IA (o pedido não sairia), sem modelo ou
    sem preço."""
    from ..pipeline.llm_steps import estimate_meeting_usd

    if store.providers.active_with_key("llm") is None:
        return None
    return estimate_meeting_usd(store, meeting_id, (JobStep.REVISE,))


def generate_label(usd: float | None) -> str:
    """"Gerar a revisão (~US$ 0,04)"; sem estimativa, só "Gerar a revisão"."""
    return "Gerar a revisão" if usd is None else f"Gerar a revisão (~{fmt_usd(usd)})"


class VideoTextDialog(QDialog):
    """U-179: o texto da legenda (`use_llm`) e, sem a revisada, o pedido dela (`on_generate`, que fecha o diálogo)."""

    def __init__(self, parent: QWidget | None, *, revised: bool, prefer_llm: bool, cost: float | None = None,
                 on_generate: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle(TEXT_TITLE)
        self.on_generate = on_generate
        box = QGroupBox(TEXT_LABEL)
        self.llm = QRadioButton(LLM_TEXT)
        self.original = QRadioButton(ORIGINAL_TEXT)
        self.llm.setEnabled(revised)
        (self.llm if revised and prefer_llm else self.original).setChecked(True)
        options = QVBoxLayout(box)
        options.addWidget(self.llm)
        options.addWidget(self.original)
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        self.missing: QLabel | None = None
        self.btn_generate: QPushButton | None = None
        if not revised:
            self.missing = QLabel(NOT_REVISED)
            self.btn_generate = QPushButton(generate_label(cost))
            self.btn_generate.setEnabled(on_generate is not None)
            self.btn_generate.setAutoDefault(False)
            self.btn_generate.clicked.connect(self._generate)
            row = QHBoxLayout()
            row.addWidget(self.missing, 1)
            row.addWidget(self.btn_generate)
            layout.addLayout(row)
        self.btn_choose = QPushButton(CHOOSE)
        self.btn_cancel = QPushButton(CANCEL)
        self.btn_choose.setDefault(True)
        self.btn_cancel.setAutoDefault(False)
        self.btn_choose.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_choose)
        buttons.addWidget(self.btn_cancel)
        layout.addLayout(buttons)

    @property
    def use_llm(self) -> bool:
        return self.llm.isEnabled() and self.llm.isChecked()

    def _generate(self) -> None:
        if self.on_generate is not None:
            self.on_generate()
        self.reject()


def ask_text(parent: QWidget, *, revised: bool, prefer_llm: bool, cost: float | None,
             on_generate: Callable[[], None] | None) -> bool | None:
    """Mostra o U-179: True usa a revisada, False a original; None cancelou ou pediu a revisão."""
    dlg = VideoTextDialog(parent, revised=revised, prefer_llm=prefer_llm, cost=cost, on_generate=on_generate)
    try:
        return dlg.use_llm if dlg.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dlg.deleteLater()


__all__ = [
    "CANCEL",
    "CHOOSE",
    "CLOSE",
    "LLM_TEXT",
    "NOT_REVISED",
    "ORIGINAL_TEXT",
    "REPROCESSING",
    "REVIEW",
    "TEXT_LABEL",
    "TEXT_TITLE",
    "VideoTextDialog",
    "ask_review",
    "ask_text",
    "blocked_text",
    "blocking_voices",
    "generate_label",
    "reprocessing",
    "review_box",
    "revise_cost",
]
