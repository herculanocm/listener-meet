"""Fila de processamento mínima pela bandeja (F2 item 15, U-005): reunião, etapa, porcentagem, estimativa; pausar e
retomar (pausa manual), cancelar, tentar de novo e reprocessar (só por ação do usuário, CP3). Só metadados: nada de áudio
nem de modelos aqui (R-9). F5: os jobs `index` (dezenas depois da varredura) não entram linha a linha: uma linha agregada
"Índice: N reuniões (M com erro)" fica no fim da tabela (F5A-05). F5.3 (A-05): "Reprocessar" passa pelo helper único
(`pipeline/reprocess.py`), que não põe outro processamento atrás de um ativo da mesma reunião e diz por quê. F5.3 L2
(U-047): a coluna "Enviado" (o `created_at` na hora local; início, fim e tempo de trabalho na dica, A-08), a "%" como barra
na cor do estado (`queue_bar.py`, A-08) e o botão "Ajuda" (`queue_help.py`, A-09)."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..config.defaults import SPECS
from ..llm.costs import fmt_usd
from ..llm.types import LLM_JOB_TASKS
from ..log import get_logger
from ..model import JOB_KIND_LABELS, JOB_STEP_LABELS, LIGHT_JOB_KINDS, JobKind, JobStatus
from ..store.jobs import HELD
from ..pipeline.estimate import fmt_remaining, remaining_seconds
from ..pipeline.reprocess import request_reprocess
from .queue_bar import PROGRESS_ROLE, STATUS_ROLE, ProgressBarDelegate
from .queue_help import QueueHelpDialog

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow

log = get_logger("ui.queue")

REFRESH_MS = 2000
BAND_MS = 120                   # a faixa do "rodando" anda nesse passo, só com linha rodando e a fila visível (A-08)
COLUMNS = ("Reunião", "Enviado", "Tipo", "Estado", "Etapa", "%", "Estimativa", "Detalhe")
SENT_COL, STATE_COL, PROGRESS_COL = COLUMNS.index("Enviado"), COLUMNS.index("Estado"), COLUMNS.index("%")
INDEX_KINDS: tuple[str, ...] = (str(JobKind.INDEX),)
TIME_FORMAT = "%d/%m %H:%M:%S"
TIMING_BLOCKS = frozenset({"llm", "index"})         # os resumos do job `llm` (custo, tarefas) e do `index` (trechos, nota): sem tempo
BUTTON_HELP: dict[str, str] = {                     # a Ajuda (A-09), com o nome que cada botão tem na tela
    "Pausar fila": "nada novo começa, e o que está rodando para no próximo ponto seguro e fica pausado. O botão vira "
                   "\"Retomar fila\", que continua de onde parou.",
    "Cancelar": "cancela o item selecionado: na fila, sai na hora; rodando, para no próximo ponto seguro. A gravação e o que a "
                "reunião já tinha pronto ficam.",
    "Tentar de novo": "põe de volta na fila um item com erro ou cancelado, aproveitando o que já tinha ficado pronto quando dá. "
                      "Num item da LLM vale a configuração atual (provedor e modelo).",
    "Reprocessar": "processa de novo, do começo, a reunião selecionada. A versão atual vale até a nova ficar pronta, e os nomes "
                   "confirmados passam para ela; se a reunião já está sendo processada, nada é duplicado.",
    "Revisar falantes": "abre a revisão de quem fala na reunião selecionada.",
    "Transcrição": "abre a transcrição da reunião selecionada.",
}
COLUMN_HELP: dict[str, str] = {
    "Enviado": "quando o item entrou na fila. Com o mouse em cima, também o início, o fim e o tempo de trabalho (a soma das "
               "etapas, contando as tentativas).",
    "%": "o andamento, na cor do estado.",
}


def local_time(iso: str | None) -> str:
    """Data ISO do banco (com fuso, `store.core._now`) na hora local deste computador: "27/10 09:05:07"; vazio sem data."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime(TIME_FORMAT)
    except (ValueError, OverflowError, OSError):
        return ""


def fmt_work(seconds: float | None) -> str:
    """"menos de 1 s", "45 s", "3 min 07 s", "1 h 05 min"; vazio sem medida."""
    if seconds is None:
        return ""
    s = round(seconds)
    if s < 1:
        return "menos de 1 s"
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def work_seconds(timings_json: str | None) -> float | None:
    """Tempo de trabalho de um job pelo `timings_json` (A-08): a soma do `wall_s` das etapas, de todas as tentativas (o
    `started_at` é o do primeiro claim e o "Tentar de novo" não o zera: fim menos início contaria a espera). O pipeline grava
    `{etapa: {audio_s, speech_s, wall_s, model}}` (`StepTiming`), uma etapa depois da outra; o job `llm` grava o mesmo por
    tarefa mais o bloco `llm`, e o `index` o mesmo por etapa mais o bloco `index`, os dois sem tempo. Executivo, pré-resumo e
    título rodam juntos no job `llm` (`llm_steps.PARALLEL`): contam pelo maior, não pela soma. None quando nada foi medido."""
    from ..pipeline.llm_steps import PARALLEL

    try:
        data = json.loads(timings_json) if timings_json else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    walls = {str(k): w for k, v in data.items() if k not in TIMING_BLOCKS and (w := _wall(v)) is not None}
    if not walls:
        return None
    together = [w for k, w in walls.items() if k in PARALLEL]
    return sum(w for k, w in walls.items() if k not in PARALLEL) + max(together, default=0.0)


def _wall(block: object) -> float | None:
    """O `wall_s` de um bloco de etapa; None num bloco sem tempo ou com lixo (o JSON aceita NaN)."""
    if not isinstance(block, dict):
        return None
    wall = block.get("wall_s")
    return float(wall) if isinstance(wall, int | float) and math.isfinite(wall) else None


def job_times_tip(job: JobRow) -> str:
    """A dica de "Enviado" (F5.3 item 7): enviado, início, fim e tempo de trabalho; "—" no que ainda não houve."""
    rows = (("Enviado", local_time(job.get("created_at"))), ("Início", local_time(job.get("started_at"))),
            ("Fim", local_time(job.get("finished_at"))), ("Tempo de trabalho", fmt_work(work_seconds(job.get("timings_json")))))
    return "\n".join(f"{name}: {value or '—'}" for name, value in rows)


def pct_text(progress: float | None) -> str:
    return f"{float(progress or 0.0) * 100:.0f} %"


def job_row_texts(job: JobRow, meeting: dict[str, Any] | None, rtf_json: str, llm_latency_s: float | None = None) -> tuple[str, ...]:
    """Texto das colunas de um job, na ordem de `COLUMNS` (separado do widget para o teste e para o tooltip da bandeja).
    `llm_latency_s`: latência média das chamadas (`llm_calls`) para estimar o que falta num job `llm` (F3 item 16)."""
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
        label, cost, left = llm_job_texts(job, label)
        detail = " · ".join(x for x in (cost, detail) if x)
        if status in (JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.PENDING) and llm_latency_s and left:
            eta = fmt_remaining(llm_latency_s * left)
    return (f"{title} ({started})" if started else title, local_time(job.get("created_at")),
            JOB_KIND_LABELS.get(str(job.get("kind")), str(job.get("kind"))), status, label, pct_text(job.get("progress")), eta, detail)


def index_row_texts(summary: dict[str, int], running: JobRow | None) -> tuple[str, ...] | None:
    """Linha agregada dos jobs `index` (F5A-05): "Índice: 3 reuniões (1 com erro)"; None quando não há nada a mostrar.
    "Enviado" é o do job que roda; sem ele, vazio (F5.3)."""
    meetings, errors = int(summary.get("meetings") or 0), int(summary.get("errors") or 0)
    if not meetings and not errors:
        return None
    title = f"Índice: {meetings} {'reunião' if meetings == 1 else 'reuniões'}" + (f" ({errors} com erro)" if errors else "")
    sent = ""
    if running is not None:
        state, step, sent = str(running["status"]), str(running.get("step") or ""), local_time(running.get("created_at"))
        label, pct = JOB_STEP_LABELS.get(step, step), pct_text(running.get("progress"))
    elif summary.get("pending"):
        state, label, pct = str(JobStatus.PENDING), "", pct_text(0.0)
    else:
        state, label, pct = str(JobStatus.ERROR), "", ""
    detail = "a busca por palavra já vale; os vetores vêm em seguida" if meetings else ""
    return (title, sent, JOB_KIND_LABELS[JobKind.INDEX], state, label, pct, "", detail)


def index_progress(summary: dict[str, int], running: JobRow | None) -> float | None:
    """A fração da barra da linha agregada: a do job que roda; 0 com pendentes; None (sem barra) quando só há erros."""
    if running is not None:
        return float(running.get("progress") or 0.0)
    return 0.0 if summary.get("pending") else None


def llm_job_texts(job: JobRow, label: str) -> tuple[str, str, int]:
    """Rótulo com a contagem de tarefas, custo acumulado e tarefas que faltam do job `llm` (`params_json.tasks`, `timings_json.llm`)."""
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
    return label, (f"custo {fmt_usd(float(cost))}" if cost else ""), max(0, len(tasks) - done)


class QueueDialog(QDialog):
    def __init__(self, store: Store, parent: QWidget | None = None, on_launch: Any = None, on_open: Any = None, on_review: Any = None):
        super().__init__(parent)
        self.store = store
        self.on_launch = on_launch                      # `on_launch(light)`: o controlador lança o worker da classe (F3)
        self.on_open = on_open                          # abre a transcrição da reunião selecionada
        self.on_review = on_review                      # abre a revisão de falantes
        self.setWindowTitle("Fila de processamento")
        self.setMinimumSize(860, 360)
        self.jobs: list[JobRow] = []
        self._running_rows = 0                          # linhas rodando no último refresh: ligam a faixa animada
        self._help: QueueHelpDialog | None = None
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.bar_delegate = ProgressBarDelegate(self.table)
        self.table.setItemDelegateForColumn(PROGRESS_COL, self.bar_delegate)
        self.status_label = QLabel("")
        self.message = QLabel("")                       # o resultado da última ação (o rodapé acima é refeito a cada 2 s)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #e67e22;")
        closing = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        closing.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status_label)
        lay.addWidget(self.message)
        lay.addLayout(self._buttons())
        lay.addWidget(closing)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._band_timer = QTimer(self)
        self._band_timer.setInterval(BAND_MS)
        self._band_timer.timeout.connect(self._animate)
        self._timer.start()
        self.refresh()

    def _buttons(self) -> QHBoxLayout:
        """A fileira de ações da seleção e, à direita, a "Ajuda" (A-09)."""
        self.btn_pause = QPushButton("Pausar fila")
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_retry = QPushButton("Tentar de novo")
        self.btn_reprocess = QPushButton("Reprocessar")
        self.btn_review = QPushButton("Revisar falantes")
        self.btn_open = QPushButton("Transcrição")
        self.btn_help = QPushButton("Ajuda")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_retry.clicked.connect(self.retry_selected)
        self.btn_reprocess.clicked.connect(self.reprocess_selected)
        self.btn_review.clicked.connect(lambda: self._open_selected(self.on_review))
        self.btn_open.clicked.connect(lambda: self._open_selected(self.on_open))
        self.btn_help.clicked.connect(self.open_help)
        row = QHBoxLayout()
        for b in (self.btn_pause, self.btn_cancel, self.btn_retry, self.btn_reprocess, self.btn_review, self.btn_open):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_help)
        return row

    # ---------------------------------------------------------------- leitura
    def refresh(self) -> None:
        selected = self.selected_job_id()
        self.jobs = self.store.jobs.list_jobs(exclude_kinds=INDEX_KINDS)
        rtf = self.store.get_setting("pipeline.rtf_json")
        meetings = {m["id"]: dict(m) for m in self.store.list_meetings()}
        latency_ms = (self.store.llm_calls.avg_latency_ms(tasks=LLM_JOB_TASKS) if any(str(j.get("kind")) == JobKind.LLM for j in self.jobs)
                      else None)
        latency_s = latency_ms / 1000 if latency_ms else None
        held = self.store.jobs.list_jobs(1, kinds=INDEX_KINDS, statuses=HELD)
        running = held[0] if held else None
        summary = self.store.jobs.index_summary()
        aggregated = index_row_texts(summary, running)
        self.table.setRowCount(len(self.jobs) + (1 if aggregated else 0))
        states: list[str] = []
        for r, job in enumerate(self.jobs):
            texts = job_row_texts(job, meetings.get(job["meeting_id"]), rtf, latency_s)
            self._fill_row(r, texts, int(job["id"]), float(job.get("progress") or 0.0), job_times_tip(job))
            states.append(texts[STATE_COL])
            if selected == job["id"]:
                self.table.selectRow(r)
        if aggregated:                                       # sem id: as ações da seleção não valem para a linha agregada
            self._fill_row(len(self.jobs), aggregated, None, index_progress(summary, running),
                           job_times_tip(running) if running is not None else "")
            states.append(aggregated[STATE_COL])
        self._running_rows = states.count(JobStatus.RUNNING)
        self._sync_band()
        paused = self.store.get(SPECS["pipeline.paused_manual"])
        self.btn_pause.setText("Retomar fila" if paused else "Pausar fila")
        summary_jobs = self.store.jobs.summary(exclude_kinds=INDEX_KINDS)
        self.status_label.setText(("fila pausada pelo usuário · " if paused else "") + f"{summary_jobs['active']} ativo(s), "
                                  f"{summary_jobs['pending']} pendente(s), {summary_jobs['errors']} com erro")

    def _fill_row(self, row: int, texts: tuple[str, ...], job_id: int | None, progress: float | None, times_tip: str) -> None:
        """Uma linha: o id do job em toda célula (a seleção age por ele), a dica de "Enviado" e, na "%", a fração e o estado
        que o delegate pinta (A-08: o dado anda com o item quando o refresh reordena as linhas)."""
        for c, text in enumerate(texts):
            item = QTableWidgetItem(text)
            if job_id is not None:
                item.setData(Qt.ItemDataRole.UserRole, job_id)
            if c == SENT_COL and times_tip:
                item.setToolTip(times_tip)
            elif c == PROGRESS_COL:
                item.setData(PROGRESS_ROLE, progress)
                item.setData(STATUS_ROLE, texts[STATE_COL])
            self.table.setItem(row, c, item)

    def selected_job_id(self) -> int | None:
        items = self.table.selectedItems()
        data = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        return int(data) if data is not None else None

    def selected_job(self) -> JobRow | None:
        jid = self.selected_job_id()
        return next((j for j in self.jobs if j["id"] == jid), None)

    # ---------------------------------------------------------------- a faixa do "rodando" (A-08)
    def _sync_band(self) -> None:
        """Liga o timer da faixa só com alguma linha rodando e a fila à vista; sem isso ele nem acorda."""
        if self._running_rows and self.isVisible() and not self.isMinimized():
            if not self._band_timer.isActive():
                self._band_timer.start()
        elif self._band_timer.isActive():
            self._band_timer.stop()

    def _animate(self) -> None:
        """Um passo da faixa e o repinte só da coluna "%"."""
        self.bar_delegate.advance()
        viewport = self.table.viewport()
        viewport.update(QRect(self.table.columnViewportPosition(PROGRESS_COL), 0, self.table.columnWidth(PROGRESS_COL),
                              viewport.height()))

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_band()

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._band_timer.stop()

    # ---------------------------------------------------------------- ações
    def open_help(self) -> QueueHelpDialog:
        """"Ajuda" (A-09): não modal, para ler com a fila ao lado; abrir de novo traz a mesma janela para a frente."""
        if self._help is None:
            self._help = QueueHelpDialog(BUTTON_HELP, COLUMN_HELP, self)
        log.info("fila: ajuda aberta")
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()
        return self._help

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
        """"Tentar de novo"; num job `llm` o snapshot do provedor é descartado e o ativo passa a valer (F3 item 15); o worker
        lançado é o da classe do job (F5: `llm` e `index` no leve)."""
        job = self.selected_job()
        if job is not None and self.store.jobs.retry(int(job["id"]), reset_config=str(job.get("kind")) == JobKind.LLM):
            if self.on_launch is not None:
                self.on_launch(str(job.get("kind")) in LIGHT_JOB_KINDS)
            self.refresh()

    def _open_selected(self, fn: Any) -> None:
        job = self.selected_job()
        if job is not None and fn is not None:
            fn(int(job["meeting_id"]))

    def reprocess_selected(self) -> int | None:
        """"Reprocessar" a reunião da linha selecionada (só por ação do usuário, CP3); devolve o job criado."""
        job = self.selected_job()
        if job is None:
            return None
        result = request_reprocess(self.store, int(job["meeting_id"]))
        self.message.setText(result.message)
        if result.job_id is not None and self.on_launch is not None:
            self.on_launch(False)
        self.refresh()
        return result.job_id
