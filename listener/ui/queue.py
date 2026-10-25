"""Fila de processamento mínima pela bandeja (F2 item 15, U-005): reunião, etapa, porcentagem, estimativa; pausar e
retomar (pausa manual), cancelar, tentar de novo e reprocessar (só por ação do usuário, CP3). Só metadados: nada de áudio
nem de modelos aqui (R-9)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..config.defaults import SPECS
from ..llm.costs import fmt_usd
from ..model import JOB_STEP_LABELS, JobKind, JobStatus
from ..pipeline.estimate import fmt_remaining, remaining_seconds

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow

REFRESH_MS = 2000
COLUMNS = ("Reunião", "Tipo", "Estado", "Etapa", "%", "Estimativa", "Detalhe")
KIND_TEXT: dict[str, str] = {JobKind.MEETING: "processar", JobKind.REPROCESS: "reprocessar", JobKind.EMBED_RANGE: "perfil de trecho",
                             JobKind.LLM: "LLM"}


def job_row_texts(job: JobRow, meeting: dict[str, Any] | None, rtf_json: str) -> tuple[str, ...]:
    """Texto das colunas de um job (separado do widget para o teste e para o tooltip da bandeja)."""
    title = (meeting or {}).get("title") or f"reunião {job['meeting_id']}"
    started = str((meeting or {}).get("started_at") or "")[:16].replace("T", " ")
    status = str(job["status"])
    step = str(job.get("step") or "")
    duration_ms = (meeting or {}).get("duration_ms") or 0
    eta = ""
    if status in (JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.PENDING):
        eta = fmt_remaining(remaining_seconds(step or None, float(job.get("progress") or 0.0), duration_ms / 1000 if duration_ms else None,
                                              rtf_json, job.get("timings_json"), kind=str(job.get("kind") or "")))
    detail = str(job.get("error") or "")
    label = JOB_STEP_LABELS.get(step, step)
    if str(job.get("kind")) == JobKind.LLM:                              # F3: "LLM: ata (2 de 4)" e custo acumulado (U-020)
        label, cost = llm_job_texts(job, label)
        detail = " · ".join(x for x in (cost, detail) if x)
    return (f"{title} ({started})" if started else title, KIND_TEXT.get(str(job.get("kind")), str(job.get("kind"))), status,
            label, f"{float(job.get('progress') or 0.0) * 100:.0f}", eta, detail)


def llm_job_texts(job: JobRow, label: str) -> tuple[str, str]:
    """Rótulo com a contagem de tarefas e o custo acumulado do job `llm` (`params_json.tasks`, `timings_json.llm`)."""
    try:
        tasks = json.loads(job.get("params_json") or "{}").get("tasks") or []
    except ValueError:
        tasks = []
    try:
        llm = json.loads(job.get("timings_json") or "{}").get("llm") or {}
    except ValueError:
        llm = {}
    done = len(llm.get("done") or [])
    if tasks and label:
        label = f"{label} ({min(done + 1, len(tasks))} de {len(tasks)})" if str(job["status"]) in (JobStatus.RUNNING, JobStatus.PAUSED) \
            else f"{label} ({done} de {len(tasks)})"
    cost = llm.get("cost_usd")
    return label, (f"custo {fmt_usd(float(cost))}" if cost else "")


class QueueDialog(QDialog):
    def __init__(self, store: Store, parent: QWidget | None = None, on_launch: Any = None, on_open: Any = None, on_review: Any = None):
        super().__init__(parent)
        self.store = store
        self.on_launch = on_launch                      # `on_launch(light)`: o controlador lança o worker da classe (F3)
        self.on_open = on_open                          # abre a transcrição da reunião selecionada
        self.on_review = on_review                      # abre a revisão de falantes
        self.setWindowTitle("Fila de processamento")
        self.setMinimumSize(760, 360)
        self.jobs: list[JobRow] = []
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.status_label = QLabel("")
        self.btn_pause = QPushButton("Pausar fila")
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_retry = QPushButton("Tentar de novo")
        self.btn_reprocess = QPushButton("Reprocessar")
        self.btn_review = QPushButton("Revisar falantes")
        self.btn_open = QPushButton("Transcrição")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_retry.clicked.connect(self.retry_selected)
        self.btn_reprocess.clicked.connect(self.reprocess_selected)
        self.btn_review.clicked.connect(lambda: self._open_selected(self.on_review))
        self.btn_open.clicked.connect(lambda: self._open_selected(self.on_open))
        row = QHBoxLayout()
        for b in (self.btn_pause, self.btn_cancel, self.btn_retry, self.btn_reprocess, self.btn_review, self.btn_open):
            row.addWidget(b)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status_label)
        lay.addLayout(row)
        lay.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ---------------------------------------------------------------- leitura
    def refresh(self) -> None:
        selected = self.selected_job_id()
        self.jobs = self.store.jobs.list_jobs()
        rtf = self.store.get_setting("pipeline.rtf_json")
        meetings = {m["id"]: dict(m) for m in self.store.list_meetings()}
        self.table.setRowCount(len(self.jobs))
        for r, job in enumerate(self.jobs):
            for c, text in enumerate(job_row_texts(job, meetings.get(job["meeting_id"]), rtf)):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, job["id"])
                self.table.setItem(r, c, item)
            if selected == job["id"]:
                self.table.selectRow(r)
        paused = self.store.get(SPECS["pipeline.paused_manual"])
        self.btn_pause.setText("Retomar fila" if paused else "Pausar fila")
        summary = self.store.jobs.summary()
        self.status_label.setText(("fila pausada pelo usuário · " if paused else "") + f"{summary['active']} ativo(s), "
                                  f"{summary['pending']} pendente(s), {summary['errors']} com erro")

    def selected_job_id(self) -> int | None:
        items = self.table.selectedItems()
        return int(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def selected_job(self) -> JobRow | None:
        jid = self.selected_job_id()
        return next((j for j in self.jobs if j["id"] == jid), None)

    # ---------------------------------------------------------------- ações
    def toggle_pause(self) -> None:
        paused = self.store.get(SPECS["pipeline.paused_manual"])
        self.store.set_setting("pipeline.paused_manual", not paused)
        if paused and self.on_launch is not None:
            self.on_launch(False)
            self.on_launch(True)
        self.refresh()

    def cancel_selected(self) -> None:
        job = self.selected_job()
        if job is not None and str(job["status"]) in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.PAUSED):
            self.store.jobs.request_cancel(int(job["id"]))
            self.refresh()

    def retry_selected(self) -> None:
        """"Tentar de novo"; num job `llm` o snapshot do provedor é descartado e o ativo passa a valer (F3 item 15)."""
        job = self.selected_job()
        if job is not None and self.store.jobs.retry(int(job["id"]), reset_config=str(job.get("kind")) == JobKind.LLM):
            if self.on_launch is not None:
                self.on_launch(str(job.get("kind")) == JobKind.LLM)
            self.refresh()

    def _open_selected(self, fn: Any) -> None:
        job = self.selected_job()
        if job is not None and fn is not None:
            fn(int(job["meeting_id"]))

    def reprocess_selected(self) -> None:
        job = self.selected_job()
        if job is None:
            return
        self.store.jobs.enqueue(int(job["meeting_id"]), JobKind.REPROCESS)
        if self.on_launch is not None:
            self.on_launch(False)
        self.refresh()
