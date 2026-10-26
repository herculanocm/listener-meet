"""Abas da LLM na revisão (F3 item 11, U-019/U-020): título, pré-resumo, ata, executivo e ações. Texto editável
(`text_edited` -> `revisions`), "gerar de novo" por aba como job `llm` de uma tarefa (desabilitado enquanto há job `llm` ativo
para a reunião), marca "gerados antes do reprocessamento" (P7), aviso "não gerado: estimado US$ x, acima do alerta; gerar?"
(P5) e selo de custo da reunião. A recarga por assinatura nunca apaga uma edição em curso (F3.1 R3-01): com o editor em foco
ou modificado, a versão nova só acende o aviso "recarregar?". Só metadados e texto: nada de SDK nem de rede aqui. F5: cada
edição gravada (título, ata, executivo, ações) chama `on_edited()`, que reindexa a reunião para a busca (o controlador
espera 30 s sem edição antes de enfileirar). F5 L4 (F5A-26): `LlmTabsGroup` junta as abas, o selo de custo e o "gerar de
novo" para a revisão e para a reunião aberta (as duas recarregam igual: ocupado, gerado antes do reprocessamento, pulado
pelo alerta); o título tem um modo compacto, numa linha, para o cabeçalho da reunião aberta."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..model import JOB_STEP_LABELS, ActionStatus, JobKind, JobStatus, JobStep, SummaryKind
from ..store.jobs import ACTIVE

if TYPE_CHECKING:
    from ..store import Store

KIND_STEP: dict[str, JobStep] = {SummaryKind.MINUTES: JobStep.MINUTES, SummaryKind.EXECUTIVE: JobStep.EXECUTIVE,
                                 SummaryKind.PRE_SUMMARY: JobStep.PRE_SUMMARY}
KIND_TITLE: dict[str, str] = {SummaryKind.PRE_SUMMARY: "Pré-resumo", SummaryKind.MINUTES: "Ata", SummaryKind.EXECUTIVE: "Executivo"}
INDEXED_KINDS = frozenset({SummaryKind.MINUTES, SummaryKind.EXECUTIVE})   # F5: o pré-resumo não entra na busca
STALE_MARK = "gerados antes do reprocessamento"
NEWER_NOTICE = ("Chegou uma versão nova enquanto você editava: \"Salvar edição\" grava o seu texto sobre a versão nova (o texto "
                "da LLM fica guardado como original); \"Recarregar\" mostra a nova e descarta o que não foi salvo.")
ACTIONS_NEWER_NOTICE = "Há ações novas ou alteradas; recarregar?"
ACTION_COLUMNS = ("Ação", "Dono", "Prazo", "Estado", "Quando")
STATUS_LABELS = {ActionStatus.OPEN: "aberta", ActionStatus.DONE: "feita", ActionStatus.DISCARDED: "descartada"}

type ActionView = tuple[tuple[int, str, str, str, str, str], ...]     # (id, texto, dono, prazo, estado, quando) por linha


def llm_job_active(store: Store, meeting_id: int) -> bool:
    return bool(store._q("SELECT 1 FROM processing_jobs WHERE meeting_id = ? AND kind = ? AND status IN (?, ?, ?) LIMIT 1",
                         (meeting_id, JobKind.LLM, *ACTIVE)))


def request_llm_step(store: Store, meeting_id: int, step: JobStep) -> tuple[int | None, str]:
    """Job `llm` de uma tarefa pela fila, sob demanda (P6; `revise` e `identify` pela leitura e pela aba Vozes, F3.1 R3-05):
    devolve (id do job, mensagem para o usuário); recusa com job `llm` ativo ou sem provedor com chave."""
    from ..pipeline.llm_steps import enqueue_llm_job

    if llm_job_active(store, meeting_id):
        return None, "Já há um job de LLM em andamento para esta reunião; espere terminar."
    job_id = enqueue_llm_job(store, meeting_id, [step])
    if job_id is None:
        return None, "Nenhum provedor de LLM ativo com chave: informe a chave em Configurações, Chaves de acesso."
    return job_id, f"{step_label(step)}: gerando pela fila (3 a 5 s para começar)…"


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
    base = f"{what} · {fmt_usd(float(cost))}" if cost else what
    skipped = [step_label(s) for s in llm.get("skipped") or {}]
    if skipped:                                         # puladas pelo alerta de custo; "gerar?" na revisão (F3.1 R3-13)
        return f"{base}; acima do alerta de custo, não gerado: {', '.join(skipped)} (gere pela revisão)"
    return base if cost else f"{base}."


class TextTab(QWidget):
    """Pré-resumo, ata ou executivo: texto editável, origem, marca de reprocessamento e "gerar de novo". O editor guarda a
    versão que mostra (`summary_id` e o texto carregado); uma versão nova chegada com o editor em foco ou modificado não o
    toca: acende o aviso e o botão "Recarregar"; "Salvar edição" grava o texto do editor sobre a versão atual, como edição
    (o texto da LLM continua em `text_llm`), para a edição nunca ficar numa versão superada que nenhuma tela mostra (R3-01)."""

    def __init__(self, store: Store, meeting_id: int, kind: SummaryKind, on_regenerate: Callable[[JobStep], Any],
                 parent: QWidget | None = None, on_edited: Callable[[], Any] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.kind = kind
        self.on_regenerate = on_regenerate
        self.on_edited = on_edited                               # F5: reindexar a reunião (ata e executivo)
        self.summary_id: int | None = None
        self._loaded: tuple[int | None, str] | None = None       # (versão, texto) que estão no editor
        self.newer = False                                       # chegou versão nova durante a edição
        self._last: tuple[set[str], dict[str, str], bool] = (set(), {}, False)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #e67e22;")
        self.editor = QPlainTextEdit()
        self.btn_save = QPushButton("Salvar edição")
        self.btn_regen = QPushButton("Gerar de novo")
        self.btn_reload = QPushButton("Recarregar")
        self.btn_reload.setVisible(False)
        self.btn_save.clicked.connect(self.save)
        self.btn_regen.clicked.connect(lambda: self.on_regenerate(KIND_STEP[str(self.kind)]))
        self.btn_reload.clicked.connect(self.load_newer)
        row = QHBoxLayout()
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_regen)
        row.addWidget(self.btn_reload)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.notice)
        lay.addWidget(self.editor, 1)
        lay.addLayout(row)

    def editing(self) -> bool:
        return self.editor.hasFocus() or self.editor.document().isModified()

    def reload(self, stale: set[str], skipped: dict[str, str], busy: bool, *, force: bool = False) -> None:
        self._last = (stale, skipped, busy)
        row = self.store.summaries.current(self.meeting_id, self.kind)
        current = (int(row["id"]) if row else None, self.store.summaries.display_text(row))
        if current != self._loaded:
            if force or self._loaded is None or not (self.newer or self.editing()):
                self._show(current)
            else:
                self.newer = True
        shown = row
        if self.summary_id != current[0]:                        # a versão aberta ficou para trás: o rótulo fala dela
            shown = self.store.summaries.get(self.summary_id) if self.summary_id is not None else None
        if shown is None:
            self.info.setText("ainda não gerado")
        else:
            edited = " · editado por você" if shown.get("text_edited") else ""
            when = str(shown.get("created_at") or "")[:16].replace("T", " ")
            self.info.setText(f"{shown.get('provider')} · {shown.get('model')} · {when}{edited}")
        step = KIND_STEP[str(self.kind)]
        notes = []
        if self.newer:
            notes.append(NEWER_NOTICE)
        if str(self.kind) in stale:
            notes.append(f"{KIND_TITLE[str(self.kind)]}: {STALE_MARK}; use \"Gerar de novo\" para refazer com a transcrição atual.")
        if str(step) in skipped and row is None:
            notes.append(f"{KIND_TITLE[str(self.kind)]} não gerado: {skipped[str(step)]}; gerar?")
        self.notice.setText("\n".join(notes))
        self.btn_reload.setVisible(self.newer)
        self.btn_regen.setEnabled(not busy)
        self.btn_regen.setToolTip("há um job de LLM em andamento para esta reunião" if busy else "")
        self.btn_save.setEnabled(self.summary_id is not None)

    def _show(self, current: tuple[int | None, str]) -> None:
        self._loaded = current
        self.summary_id = current[0]
        self.newer = False
        if self.editor.toPlainText() != current[1]:
            self.editor.setPlainText(current[1])
        self.editor.document().setModified(False)

    def load_newer(self) -> None:
        """"Recarregar": mostra a versão atual e descarta o que não foi salvo."""
        self.reload(*self._last, force=True)

    def save(self) -> bool:
        """Grava o editor na versão atual (a que chegou durante a edição, se chegou) e limpa a marca de modificado."""
        current = self.store.summaries.current(self.meeting_id, self.kind)
        target = int(current["id"]) if current is not None else self.summary_id
        if target is None:
            return False
        text = self.editor.toPlainText()
        row = self.store.summaries.get(target)
        original = str((row or {}).get("text_llm") or "")
        changed = self.store.summaries.set_text_edited(target, None if text == original else text)
        self.summary_id = target
        self.newer = False
        self._loaded = (target, self.store.summaries.display_text(self.store.summaries.get(target)))
        self.editor.document().setModified(False)
        self.reload(*self._last)
        if changed and self.on_edited is not None and str(self.kind) in INDEXED_KINDS:
            self.on_edited()
        return changed


class TitleTab(QWidget):
    """Título da reunião: o do usuário (editável, com `revisions`) e a sugestão da LLM à parte. `compact` (F5, cabeçalho da
    reunião aberta): uma linha só, Enter salva, e "usar a sugestão" e "gerar de novo" num menu "LLM"."""

    def __init__(self, store: Store, meeting_id: int, on_regenerate: Callable[[JobStep], Any], parent: QWidget | None = None,
                 on_edited: Callable[[], Any] | None = None, *, compact: bool = False):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_regenerate = on_regenerate
        self.on_edited = on_edited                               # F5: o título entra em todo trecho da busca
        self.compact = compact
        self._busy = False
        self.title = QLineEdit()
        self.suggestion = QLabel("")
        self.suggestion.setWordWrap(True)
        self.btn_use = QPushButton("Usar sugestão da LLM")
        self.btn_save = QPushButton("Salvar título")
        self.btn_regen = QPushButton("Gerar de novo")
        self.btn_use.clicked.connect(self.use_suggestion)
        self.btn_save.clicked.connect(self.save)
        self.btn_regen.clicked.connect(lambda: self.on_regenerate(JobStep.TITLE))
        if compact:
            self._compact_layout()
            return
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

    def _compact_layout(self) -> None:
        self.title.setObjectName("meetingTitle")
        self.title.setPlaceholderText("título da reunião")
        self.title.setToolTip("Edite e tecle Enter (ou \"Salvar\") para gravar o título")
        self.title.returnPressed.connect(self.save)
        self.btn_save.setText("Salvar")
        self.act_use = QAction("Usar sugestão da LLM", self)
        self.act_regen = QAction("Gerar título de novo", self)
        self.act_use.triggered.connect(self.use_suggestion)
        self.act_regen.triggered.connect(lambda: self.on_regenerate(JobStep.TITLE))
        menu = QMenu(self)
        menu.addAction(self.act_use)
        menu.addAction(self.act_regen)
        self.btn_menu = QToolButton()
        self.btn_menu.setText("LLM")                      # a seta do menu é do próprio Qt (com "▾" no texto saíam duas)
        self.btn_menu.setToolTip("Sugestão de título da LLM")
        self.btn_menu.setMenu(menu)
        self.btn_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.title, 1)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_menu)

    def reload(self, busy: bool) -> None:
        self._busy = busy
        m = self.store.get_meeting(self.meeting_id) or {}
        if not self.title.hasFocus() and not self.title.isModified():         # título digitado e não salvo fica (R3-01)
            self.title.setText(str(m.get("title") or ""))
        llm = m.get("title_llm")
        self.suggestion.setText(f"Sugestão da LLM: {llm}" if llm else "Sugestão da LLM: ainda não gerada")
        self.btn_use.setEnabled(bool(llm))
        self.btn_regen.setEnabled(not busy)
        if self.compact:                                                     # sem título seu, a sugestão aparece apagada
            self.title.setPlaceholderText(str(llm) if llm else "título da reunião")
            self.act_use.setEnabled(bool(llm))
            self.act_use.setText(f"Usar sugestão da LLM: {llm}" if llm else "Sugestão da LLM: ainda não gerada")
            self.act_regen.setEnabled(not busy)

    def use_suggestion(self) -> None:
        m = self.store.get_meeting(self.meeting_id) or {}
        if m.get("title_llm"):
            self.title.setText(str(m["title_llm"]))
            self.save()

    def save(self) -> bool:
        text = self.title.text().strip()
        changed = bool(text) and text != str((self.store.get_meeting(self.meeting_id) or {}).get("title") or "")
        if changed:
            self.store.update_title(self.meeting_id, text)
        self.title.setModified(False)
        self.reload(self._busy)
        if changed and self.on_edited is not None:
            self.on_edited()
        return changed


class ActionsTab(QWidget):
    """Ações da ata, uma por linha: texto editável na célula e estado no combo. A recarga compara o que a tabela mostra com
    o banco e só a reconstrói quando algo mudou; com a tabela em edição ou com o foco, acende "recarregar?" (R3-01)."""

    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, on_edited: Callable[[], Any] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_edited = on_edited                               # F5: ações vigentes entram na busca
        self.table = QTableWidget(0, len(ACTION_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(ACTION_COLUMNS))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.note = QLabel("")
        self.btn_reload = QPushButton("Recarregar ações")
        self.btn_reload.setVisible(False)
        self.btn_reload.clicked.connect(lambda: self.reload(force=True))
        self._loading = False
        self._shown: ActionView | None = None
        self.newer = False
        row = QHBoxLayout()
        row.addWidget(self.note, 1)
        row.addWidget(self.btn_reload)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)

    def _view(self) -> ActionView:
        people = {int(p["id"]): str(p["name"]) for p in self.store.people.list_people()}
        out: list[tuple[int, str, str, str, str, str]] = []
        for a in self.store.actions.list_for_meeting(self.meeting_id):
            owner_pid = a.get("owner_person_id")
            owner = people.get(int(owner_pid)) if owner_pid is not None else (a.get("owner_text") or "")
            t = a.get("t_ms")
            when = f"{int(t) // 60000:02d}:{(int(t) // 1000) % 60:02d}" if t is not None else "sem evidência"
            out.append((int(a["id"]), str(a.get("text") or ""), str(owner or ""), str(a.get("due") or ""),
                        str(a.get("status") or ActionStatus.OPEN), when))
        return tuple(out)

    def editing(self) -> bool:
        """Célula em edição ou foco na tabela (inclusive num combo de estado)."""
        if self.table.state() == QAbstractItemView.State.EditingState:
            return True
        focus = QApplication.focusWidget()
        return focus is not None and (focus is self.table or self.table.isAncestorOf(focus))

    def reload(self, force: bool = False) -> None:
        view = self._view()
        if view == self._shown and not force:
            return
        if not force and self._shown is not None and self.editing():
            self.newer = True
            self.note.setText(ACTIONS_NEWER_NOTICE)
            self.btn_reload.setVisible(True)
            return
        self._fill(view)

    def _fill(self, view: ActionView) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(view))
            for r, (aid, text, owner, due, status, when) in enumerate(view):
                for c, value in enumerate((text, owner, due, None, when)):
                    if c == 3:
                        combo = QComboBox()
                        for st, label in STATUS_LABELS.items():
                            combo.addItem(label, str(st))
                        combo.setCurrentIndex(max(0, combo.findData(status)))
                        combo.currentIndexChanged.connect(lambda _i, a=aid, cb=combo: self._status_changed(a, cb))
                        self.table.setCellWidget(r, c, combo)
                        continue
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, aid)
                    if c != 0:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r, c, item)
        finally:
            self._loading = False
        self._shown = view
        self.newer = False
        self.btn_reload.setVisible(False)
        self.note.setText(f"{len(view)} ação(ões); edite o texto na própria célula e o estado na coluna Estado." if view
                          else "nenhuma ação na ata")

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is not None:
            self._after_write(int(aid), self.store.actions.set_text(int(aid), item.text().strip()))

    def _status_changed(self, action_id: int, combo: QComboBox) -> None:
        if not self._loading:
            self._after_write(action_id, self.store.actions.set_status(action_id, ActionStatus(str(combo.currentData()))))

    def _after_write(self, action_id: int, written: bool) -> None:
        """A própria edição não conta como versão nova; ação já superada (a ata mudou) recusa a escrita e pede recarga."""
        if written and self.on_edited is not None:
            self.on_edited()
        if written and not self.newer:
            self._shown = self._view()
        elif not written and str((self.store.actions.get(action_id) or {}).get("status")) == ActionStatus.SUPERSEDED:
            self.newer = True
            self.note.setText("Esta ação é de uma ata anterior e não foi alterada; recarregue as ações.")
            self.btn_reload.setVisible(True)


class LlmTabsGroup(QObject):
    """As abas da LLM de uma reunião (F5A-26), usadas pela revisão de falantes e pela reunião aberta: título, pré-resumo,
    ata, executivo e ações, o selo de custo e o "gerar de novo" pela fila. `reload()` recarrega tudo de uma vez (ocupado,
    gerado antes do reprocessamento, pulado pelo alerta de custo; a recarga nunca apaga uma edição em curso, R3-01) e
    devolve se há job `llm` ativo. `on_message` recebe o texto do "gerar de novo"; `after_regenerate` recarrega quem mostra
    o grupo (a revisão também atualiza o botão da identidade)."""

    def __init__(self, store: Store, meeting_id: int, *, on_launch: Callable[[bool], Any] | None = None,
                 on_edited: Callable[[], Any] | None = None, on_message: Callable[[str], Any] | None = None,
                 after_regenerate: Callable[[], Any] | None = None, compact_title: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_launch = on_launch
        self.on_message = on_message
        self.after_regenerate = after_regenerate
        self.regenerated: list[int] = []                         # ids dos jobs `llm` criados por "gerar de novo"
        self.title_tab = TitleTab(store, meeting_id, self.regenerate, on_edited=on_edited, compact=compact_title)
        self.text_tabs: dict[str, TextTab] = {str(k): TextTab(store, meeting_id, k, self.regenerate, on_edited=on_edited)
                                              for k in (SummaryKind.PRE_SUMMARY, SummaryKind.MINUTES, SummaryKind.EXECUTIVE)}
        self.actions_tab = ActionsTab(store, meeting_id, on_edited=on_edited)
        self.cost = QLabel("")
        self.cost.setStyleSheet("color: #888; font-size: 11px;")

    def reload(self) -> bool:
        busy = llm_job_active(self.store, self.meeting_id)
        current = self.store.segments.current_job_id(self.meeting_id)
        stale = set(self.store.summaries.stale_after_reprocess(self.meeting_id, current))
        info = last_llm_info(self.store, self.meeting_id)
        skipped = {str(k): str(v) for k, v in (info.get("skipped") or {}).items()}
        self.title_tab.reload(busy)
        for tab in self.text_tabs.values():
            tab.reload(stale, skipped, busy)
        self.actions_tab.reload()
        badge = cost_badge(self.store, self.meeting_id)
        parts = [badge] if badge else []
        if busy:
            parts.append("LLM em andamento")
        elif info.get("status") == "erro" and info.get("error"):
            parts.append(f"última LLM falhou: {info['error']}")
        self.cost.setText(" · ".join(parts))
        return busy

    def regenerate(self, step: JobStep) -> int | None:
        """"Gerar de novo": job `llm` de uma tarefa pela fila (P6); a versão anterior vira `superada` quando a nova chegar."""
        job_id, message = request_llm_step(self.store, self.meeting_id, step)
        if self.on_message is not None:
            self.on_message(message)
        if job_id is None:
            return None
        self.regenerated.append(job_id)
        if self.on_launch is not None:
            self.on_launch(True)
        if self.after_regenerate is not None:
            self.after_regenerate()
        else:
            self.reload()
        return job_id


def step_label(step: JobStep | str) -> str:
    return JOB_STEP_LABELS.get(str(step), str(step)).removeprefix("LLM: ")


__all__ = ["ActionsTab", "JobStatus", "LlmTabsGroup", "TextTab", "TitleTab", "cost_badge", "last_llm_info", "llm_done_text",
           "llm_job_active", "request_llm_step", "step_label"]
