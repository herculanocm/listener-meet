"""Fila de processamento mínima pela bandeja (F2 item 15, U-005): reunião, etapa, porcentagem, estimativa; pausar e
retomar (pausa manual), cancelar, tentar de novo e reprocessar (só por ação do usuário, CP3). Só metadados: nada de áudio
nem de modelos aqui (R-9). F5: os jobs `index` (dezenas depois da varredura) não entram linha a linha: uma linha agregada
"Índice: N reuniões (M com erro)" fica no fim da tabela (F5A-05). F5.3 (A-05): "Reprocessar" passa pelo helper único
(`pipeline/reprocess.py`), que não põe outro processamento atrás de um ativo da mesma reunião e diz por quê. F5.3 L2
(U-047): a coluna "Enviado" (o `created_at` na hora local; início, fim e tempo de trabalho na dica, A-08), a "%" como barra
na cor do estado (`queue_bar.py`, A-08) e o botão "Ajuda" (`queue_help.py`, A-09).
F5.4 L3 (U-077, U-058): 1040 x 480, aberta dentro da área livre pelo `fit_to_screen` (A-17); o Estado em selo
(`BadgeDelegate`; o texto segue no `DisplayRole`, para o leitor de tela e os testes); a barra com o raio do token; Ajuda e
Fechar na linha dos botões; a tabela com o cabeçalho à esquerda, sem número de linha, a linha inteira selecionada e a
Reunião esticando (a Etapa e o Detalhe com largura própria; o texto inteiro dessas três na dica); o resultado da última
ação numa faixa (U-057). Os textos das colunas moram em `ui/queue_rows.py` e seguem reexportados daqui."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..config.defaults import SPECS
from ..llm.types import LLM_JOB_TASKS
from ..log import get_logger
from ..model import LIGHT_JOB_KINDS, JobKind, JobStatus
from ..store.jobs import HELD
from ..pipeline.reprocess import request_reprocess
from .common import fit_to_screen
from .components import BadgeDelegate, InfoBar
from .queue_bar import PROGRESS_ROLE, STATUS_ROLE, ProgressBarDelegate
from .queue_help import QueueHelpDialog
from .queue_rows import (TIME_FORMAT, fmt_work, index_progress, index_row_texts, job_row_texts, job_times_tip, llm_job_texts, local_time,
                         pct_text, work_seconds)
from .theme import METRICS, TONE_ROLE, Tone, note

if TYPE_CHECKING:
    from ..store import Store
    from ..store.jobs import JobRow

log = get_logger("ui.queue")

REFRESH_MS = 2000
BAND_MS = 120                   # a faixa do "rodando" anda nesse passo, só com linha rodando e a fila visível (A-08)
COLUMNS = ("Reunião", "Enviado", "Tipo", "Estado", "Etapa", "%", "Estimativa", "Detalhe")
SENT_COL, STATE_COL, PROGRESS_COL = COLUMNS.index("Enviado"), COLUMNS.index("Estado"), COLUMNS.index("%")
MEETING_COL, DETAIL_COL = COLUMNS.index("Reunião"), COLUMNS.index("Detalhe")
INDEX_KINDS: tuple[str, ...] = (str(JobKind.INDEX),)
SIZE, MIN_SIZE = (1040, 480), (720, 360)             # U-077; A-17
MARGIN = 16
PROGRESS_W, STEP_W, DETAIL_W = 96, 180, 160          # a barra; a etapa e o detalhe (o texto inteiro na dica)
TIP_COLS = frozenset({MEETING_COL, COLUMNS.index("Etapa"), DETAIL_COL})   # as colunas que podem sair com reticências
# U-077: o Estado em selo, no tom do estado; o cancelado no neutro (o selo não tem o apagado), com o texto que o distingue
STATUS_BADGES: dict[str, Tone] = {JobStatus.PENDING: Tone.NEUTRAL, JobStatus.RUNNING: Tone.INFO, JobStatus.PAUSED: Tone.WARNING,
                                  JobStatus.DONE: Tone.SUCCESS, JobStatus.ERROR: Tone.DANGER, JobStatus.CANCELLED: Tone.NEUTRAL}
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


class QueueDialog(QDialog):
    def __init__(self, store: Store, parent: QWidget | None = None, on_launch: Any = None, on_open: Any = None, on_review: Any = None):
        super().__init__(parent)
        self.store = store
        self.on_launch = on_launch                      # `on_launch(light)`: o controlador lança o worker da classe (F3)
        self.on_open = on_open                          # abre a transcrição da reunião selecionada
        self.on_review = on_review                      # abre a revisão de falantes
        self.setWindowTitle("Fila de processamento")
        self.resize(*SIZE)
        self.setMinimumSize(*MIN_SIZE)
        self.jobs: list[JobRow] = []
        self._running_rows = 0                          # linhas rodando no último refresh: ligam a faixa animada
        self._help: QueueHelpDialog | None = None
        self._message_bar: InfoBar | None = None        # o resultado da última ação, montado na primeira (U-057)
        self.table = self._make_table()
        self.status_label = QLabel("")
        note(self.status_label)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self._lay.setSpacing(METRICS.form_row)
        self._lay.addWidget(self.table, 1)
        self._lay.addWidget(self.status_label)
        self._lay.addLayout(self._buttons())
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._band_timer = QTimer(self)
        self._band_timer.setInterval(BAND_MS)
        self._band_timer.timeout.connect(self._animate)
        self.refresh()                                   # o timer da leitura só roda com a fila à vista (`showEvent`)

    def _make_table(self) -> QTableWidget:
        """U-058: o cabeçalho à esquerda, sem o número de linha, a linha inteira selecionada, as linhas de 32 px e a Reunião
        esticando; as colunas curtas no tamanho do texto, a barra e o detalhe com largura própria. U-077: o Estado em selo."""
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(list(COLUMNS))
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(METRICS.control_h)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(MEETING_COL, QHeaderView.ResizeMode.Stretch)
        for col, width in ((PROGRESS_COL, PROGRESS_W), (COLUMNS.index("Etapa"), STEP_W), (DETAIL_COL, DETAIL_W)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(col, width)
        self.bar_delegate = ProgressBarDelegate(table)
        table.setItemDelegateForColumn(PROGRESS_COL, self.bar_delegate)
        self.badge_delegate = BadgeDelegate(table)
        table.setItemDelegateForColumn(STATE_COL, self.badge_delegate)
        return table

    def _buttons(self) -> QHBoxLayout:
        """A fileira de ações da seleção e, à direita, a "Ajuda" (A-09) e o "Fechar" (U-077: na mesma linha)."""
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
        self.btn_review.clicked.connect(self._review_selected)          # R-06: métodos, não lambdas com `self` (o ciclo
        self.btn_open.clicked.connect(self._open_transcript_selected)   # pelo C++ prendia a fila até a derrubada do Qt)
        self.btn_help.clicked.connect(self.open_help)
        self.btn_close = QPushButton("Fechar")
        self.btn_close.clicked.connect(self.reject)
        row = QHBoxLayout()
        for b in (self.btn_pause, self.btn_cancel, self.btn_retry, self.btn_reprocess, self.btn_review, self.btn_open):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_help)
        row.addWidget(self.btn_close)
        return row

    @property
    def message(self) -> QLabel:
        """O texto da faixa do resultado da última ação (o rodapé acima dela é refeito a cada 2 s)."""
        return self._bar().label

    def _bar(self) -> InfoBar:
        if self._message_bar is None:
            self._message_bar = InfoBar("info")
            self._message_bar.hide()
            self._lay.insertWidget(self._lay.indexOf(self.status_label) + 1, self._message_bar)
        return self._message_bar

    def _say(self, text: str, kind: str) -> None:
        bar = self._bar()
        bar.set_kind(kind)
        bar.set_text(text)
        bar.setVisible(bool(text))

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
            elif c == STATE_COL:
                item.setData(TONE_ROLE, STATUS_BADGES.get(text, Tone.NEUTRAL).value)      # U-077: o selo no tom do estado
            elif c in TIP_COLS and text:
                item.setToolTip(text)
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
        fit_to_screen(self)                              # A-17: a janela inteira, com a moldura, na área livre
        if not self._timer.isActive():
            self.refresh()                               # o que mudou com ela fechada
            self._timer.start()
        self._sync_band()

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        """Fechada (ou minimizada), a fila não consulta o banco: antes a leitura de 2 s seguia para sempre depois do "Fechar",
        e a de uma fila esquecida num teste batia num banco já fechado."""
        super().hideEvent(event)
        self._timer.stop()
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

    def _review_selected(self) -> None:
        self._open_selected(self.on_review)

    def _open_transcript_selected(self) -> None:
        self._open_selected(self.on_open)

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
        self._say(result.message, "info" if result.job_id is not None else "warning")
        if result.job_id is not None and self.on_launch is not None:
            self.on_launch(False)
        self.refresh()
        return result.job_id


__all__ = ["BAND_MS", "BUTTON_HELP", "COLUMNS", "COLUMN_HELP", "PROGRESS_COL", "REFRESH_MS", "SENT_COL", "STATE_COL", "STATUS_BADGES",
           "TIME_FORMAT", "QueueDialog", "fmt_work", "index_progress", "index_row_texts", "job_row_texts", "job_times_tip",
           "llm_job_texts", "local_time", "pct_text", "work_seconds"]
