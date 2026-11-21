"""O que vem antes do "Salvar vídeo com legendas…" (F5.26, D-112): a guarda das vozes pendentes (U-178) e a do
reprocessamento em curso.

Pendente é o que `review.pending` diz (`ambíguo` ou `sem_nome`, fora das mescladas); ignorada, mesclada e confirmada contam
como decididas. Só segura o vídeo a pendente com pelo menos uma fala com texto na leitura vigente, contando as falas das
vozes mescladas nela (no vídeo, elas saem com o "Voz N" do alvo). O aviso não tem "salvar mesmo assim": "Revisar
falantes…" abre a revisão que já existe (CP11: a IA não decide nada novo)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from ..pipeline import review
from ..pipeline.reprocess import active_jobs

if TYPE_CHECKING:
    from ..store import Store

REPROCESSING = "A reunião está sendo reprocessada."
REVIEW = "Revisar falantes…"
CLOSE = "Fechar"


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


__all__ = ["CLOSE", "REPROCESSING", "REVIEW", "ask_review", "blocked_text", "blocking_voices", "reprocessing", "review_box"]
