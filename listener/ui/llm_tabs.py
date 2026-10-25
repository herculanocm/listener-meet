"""Abas da LLM na revisão (F3 item 11, U-019/U-020): título, pré-resumo, ata, executivo e ações. Texto editável
(`text_edited` -> `revisions`), "gerar de novo" por aba como job `llm` de uma tarefa (desabilitado enquanto há job `llm` ativo
para a reunião), marca "gerados antes do reprocessamento" (P7), aviso "não gerado: estimado US$ x, acima do alerta; gerar?"
(P5) e selo de custo da reunião. Só metadados e texto: nada de SDK nem de rede aqui (chamadas vão pela fila)."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..model import JOB_STEP_LABELS, ActionStatus, JobKind, JobStatus, JobStep, SummaryKind
from ..store.jobs import ACTIVE

if TYPE_CHECKING:
    from ..store import Store

KIND_STEP: dict[str, JobStep] = {SummaryKind.MINUTES: JobStep.MINUTES, SummaryKind.EXECUTIVE: JobStep.EXECUTIVE,
                                 SummaryKind.PRE_SUMMARY: JobStep.PRE_SUMMARY}
KIND_TITLE: dict[str, str] = {SummaryKind.PRE_SUMMARY: "Pré-resumo", SummaryKind.MINUTES: "Ata", SummaryKind.EXECUTIVE: "Executivo"}
STALE_MARK = "gerados antes do reprocessamento"
ACTION_COLUMNS = ("Ação", "Dono", "Prazo", "Estado", "Quando")
STATUS_LABELS = {ActionStatus.OPEN: "aberta", ActionStatus.DONE: "feita", ActionStatus.DISCARDED: "descartada"}


def llm_job_active(store: Store, meeting_id: int) -> bool:
    return bool(store._q("SELECT 1 FROM processing_jobs WHERE meeting_id = ? AND kind = ? AND status IN (?, ?, ?) LIMIT 1",
                         (meeting_id, JobKind.LLM, *ACTIVE)))


def last_llm_info(store: Store, meeting_id: int) -> dict[str, Any]:
    """`timings_json.llm` do último job `llm` da reunião: custo, tarefas puladas (com o motivo) e feitas."""
    rows = store._q("SELECT timings_json, status, error FROM processing_jobs WHERE meeting_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                    (meeting_id, JobKind.LLM))
    if not rows:
        return {}
    try:
        data = json.loads(rows[0]["timings_json"] or "{}").get("llm") or {}
    except ValueError:
        data = {}
    data["status"], data["error"] = rows[0]["status"], rows[0]["error"]
    return data


def cost_badge(store: Store, meeting_id: int) -> str:
    total, missing = store.llm_calls.cost_for_meeting(meeting_id)
    if total is None:
        return ""
    return f"custo da LLM: {fmt_usd(total)}" + (" (parte sem preço cadastrado)" if missing else "")


def llm_done_text(job: dict[str, Any]) -> str:
    """"ata pronta · US$ 0,32" na notificação por kind (U-020)."""
    try:
        llm = json.loads(job.get("timings_json") or "{}").get("llm") or {}
    except ValueError:
        llm = {}
    done = [str(s) for s in llm.get("done") or []]
    what = "ata pronta" if "minutes" in done else ("LLM concluída" if done else "LLM sem tarefas novas")
    cost = llm.get("cost_usd")
    return f"{what} · {fmt_usd(float(cost))}" if cost else f"{what}."


class TextTab(QWidget):
    """Pré-resumo, ata ou executivo: texto editável, origem, marca de reprocessamento e "gerar de novo"."""

    def __init__(self, store: Store, meeting_id: int, kind: SummaryKind, on_regenerate: Callable[[JobStep], Any],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.kind = kind
        self.on_regenerate = on_regenerate
        self.summary_id: int | None = None
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #e67e22;")
        self.editor = QPlainTextEdit()
        self.btn_save = QPushButton("Salvar edição")
        self.btn_regen = QPushButton("Gerar de novo")
        self.btn_save.clicked.connect(self.save)
        self.btn_regen.clicked.connect(lambda: self.on_regenerate(KIND_STEP[str(self.kind)]))
        row = QHBoxLayout()
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_regen)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.notice)
        lay.addWidget(self.editor, 1)
        lay.addLayout(row)

    def reload(self, stale: set[str], skipped: dict[str, str], busy: bool) -> None:
        row = self.store.summaries.current(self.meeting_id, self.kind)
        self.summary_id = int(row["id"]) if row else None
        text = self.store.summaries.display_text(row)
        if self.editor.toPlainText() != text:
            self.editor.setPlainText(text)
        if row is None:
            self.info.setText("ainda não gerado")
        else:
            edited = " · editado por você" if row.get("text_edited") else ""
            when = str(row.get("created_at") or "")[:16].replace("T", " ")
            self.info.setText(f"{row.get('provider')} · {row.get('model')} · {when}{edited}")
        step = KIND_STEP[str(self.kind)]
        notes = []
        if str(self.kind) in stale:
            notes.append(f"{KIND_TITLE[str(self.kind)]}: {STALE_MARK}; use \"Gerar de novo\" para refazer com a transcrição atual.")
        if str(step) in skipped and row is None:
            notes.append(f"{KIND_TITLE[str(self.kind)]} não gerado: {skipped[str(step)]}; gerar?")
        self.notice.setText("\n".join(notes))
        self.btn_regen.setEnabled(not busy)
        self.btn_regen.setToolTip("há um job de LLM em andamento para esta reunião" if busy else "")
        self.btn_save.setEnabled(row is not None)

    def save(self) -> bool:
        if self.summary_id is None:
            return False
        text = self.editor.toPlainText()
        row = self.store.summaries.get(self.summary_id)
        original = str((row or {}).get("text_llm") or "")
        changed = self.store.summaries.set_text_edited(self.summary_id, None if text == original else text)
        self.reload(set(), {}, False)
        return changed


class TitleTab(QWidget):
    def __init__(self, store: Store, meeting_id: int, on_regenerate: Callable[[JobStep], Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_regenerate = on_regenerate
        self.title = QLineEdit()
        self.suggestion = QLabel("")
        self.suggestion.setWordWrap(True)
        self.btn_use = QPushButton("Usar sugestão da LLM")
        self.btn_save = QPushButton("Salvar título")
        self.btn_regen = QPushButton("Gerar de novo")
        self.btn_use.clicked.connect(self.use_suggestion)
        self.btn_save.clicked.connect(self.save)
        self.btn_regen.clicked.connect(lambda: self.on_regenerate(JobStep.TITLE))
        row = QHBoxLayout()
        for b in (self.btn_save, self.btn_use, self.btn_regen):
            row.addWidget(b)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Título da reunião (o seu; a sugestão da LLM fica guardada à parte):"))
        lay.addWidget(self.title)
        lay.addWidget(self.suggestion)
        lay.addLayout(row)
        lay.addStretch(1)

    def reload(self, busy: bool) -> None:
        m = self.store.get_meeting(self.meeting_id) or {}
        if not self.title.hasFocus():
            self.title.setText(str(m.get("title") or ""))
        llm = m.get("title_llm")
        self.suggestion.setText(f"Sugestão da LLM: {llm}" if llm else "Sugestão da LLM: ainda não gerada")
        self.btn_use.setEnabled(bool(llm))
        self.btn_regen.setEnabled(not busy)

    def use_suggestion(self) -> None:
        m = self.store.get_meeting(self.meeting_id) or {}
        if m.get("title_llm"):
            self.title.setText(str(m["title_llm"]))
            self.save()

    def save(self) -> None:
        text = self.title.text().strip()
        if text:
            self.store.update_title(self.meeting_id, text)
        self.reload(False)


class ActionsTab(QWidget):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.table = QTableWidget(0, len(ACTION_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(ACTION_COLUMNS))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.note = QLabel("")
        self._loading = False
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.note)

    def reload(self) -> None:
        rows = self.store.actions.list_for_meeting(self.meeting_id)
        people = {int(p["id"]): str(p["name"]) for p in self.store.people.list_people()}
        self._loading = True
        try:
            self.table.setRowCount(len(rows))
            for r, a in enumerate(rows):
                owner_pid = a.get("owner_person_id")
                owner = people.get(int(owner_pid)) if owner_pid is not None else (a.get("owner_text") or "")
                t = a.get("t_ms")
                when = f"{int(t) // 60000:02d}:{(int(t) // 1000) % 60:02d}" if t is not None else "sem evidência"
                for c, value in enumerate((a.get("text") or "", owner or "", a.get("due") or "", None, when)):
                    if c == 3:
                        combo = QComboBox()
                        for status, label in STATUS_LABELS.items():
                            combo.addItem(label, str(status))
                        combo.setCurrentIndex(max(0, combo.findData(str(a.get("status") or ActionStatus.OPEN))))
                        combo.currentIndexChanged.connect(lambda _i, aid=int(a["id"]), cb=combo: self._status_changed(aid, cb))
                        self.table.setCellWidget(r, c, combo)
                        continue
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, int(a["id"]))
                    if c != 0:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r, c, item)
        finally:
            self._loading = False
        self.note.setText(f"{len(rows)} ação(ões); edite o texto na própria célula e o estado na coluna Estado." if rows
                          else "nenhuma ação na ata")

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is not None:
            self.store.actions.set_text(int(aid), item.text().strip())

    def _status_changed(self, action_id: int, combo: QComboBox) -> None:
        if not self._loading:
            self.store.actions.set_status(action_id, ActionStatus(str(combo.currentData())))


def step_label(step: JobStep | str) -> str:
    return JOB_STEP_LABELS.get(str(step), str(step)).removeprefix("LLM: ")


__all__ = ["ActionsTab", "JobStatus", "TextTab", "TitleTab", "cost_badge", "last_llm_info", "llm_done_text", "llm_job_active", "step_label"]
