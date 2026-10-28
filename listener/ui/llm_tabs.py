"""Abas da LLM na revisão (F3 item 11, U-019/U-020): título, pré-resumo, ata, executivo e ações. Texto editável
(`text_edited` -> `revisions`), "gerar de novo" por aba como job `llm` de uma tarefa (desabilitado enquanto há job `llm` ativo
para a reunião), marca "gerados antes do reprocessamento" (P7), aviso "não gerado: estimado US$ x, acima do alerta; gerar?"
(P5) e selo de custo da reunião. A recarga por assinatura nunca apaga uma edição em curso (F3.1 R3-01): com o editor em foco
ou modificado, a versão nova só acende o aviso "recarregar?". Só metadados e texto: nada de SDK nem de rede aqui. F5: cada
edição gravada (título, ata, executivo, ações) chama `on_edited()`, que reindexa a reunião para a busca (o controlador
espera 30 s sem edição antes de enfileirar). F5 L4 (F5A-26): `LlmTabsGroup` junta as abas, o selo de custo e o "gerar de
novo" para a revisão e para a reunião aberta (as duas recarregam igual: ocupado, gerado antes do reprocessamento, pulado
pelo alerta); o título tem um modo compacto, numa linha, para o cabeçalho da reunião aberta. U-042 (F5.2): a aba da ata
diz quando ela espera a identificação das vozes ("Gerar ata mesmo assim") ou quando os nomes mudaram depois dela
("Atualizar a ata com os nomes"); o "gerar de novo" de uma tarefa adiada a libera com as outras. F5.4 L4: a ata, o executivo e o
pré-resumo (`TextTab`, com a leitura formatada, A-15) foram para `ui/minutes_view.py` e as ações (`ActionsTab`, U-080) para
`ui/actions_table.py`, reexportadas daqui; o título do cabeçalho da reunião em 20 px, sem moldura até o foco, e o menu "LLM"
num botão com a seta inteira (U-078)."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget

from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import JOB_STEP_LABELS, JobKind, JobStatus, JobStep, SummaryKind
from ..pipeline import summary_gate
from ..store.jobs import ACTIVE
from .actions_table import ActionsTab
from .minutes_view import KIND_STEP, TextTab
from .style import FLAT_PROP
from .theme import note, role

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.llm_tabs")


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
        self.btn_regen.clicked.connect(self.regenerate)
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
        role(self.title, "subtitle")                      # F5.4 (U-078): o título da reunião em 20 px,
        self.title.setProperty(FLAT_PROP, True)           # sem moldura até o foco (ou o mouse), pelo `ThemeStyle`
        self.title.setPlaceholderText("título da reunião")
        self.title.setToolTip("Edite e tecle Enter (ou \"Salvar\") para gravar o título")
        self.title.returnPressed.connect(self.save)
        self.btn_save.setText("Salvar")
        self.act_use = QAction("Usar sugestão da LLM", self)
        self.act_regen = QAction("Gerar título de novo", self)
        self.act_use.triggered.connect(self.use_suggestion)
        self.act_regen.triggered.connect(self.regenerate)
        menu = QMenu(self)
        menu.addAction(self.act_use)
        menu.addAction(self.act_regen)
        self.btn_menu = QPushButton("LLM")               # U-078: o botão com menu leva a seta inteira, com o espaço dela
        self.btn_menu.setToolTip("Sugestão de título da LLM")
        self.btn_menu.setMenu(menu)
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

    def regenerate(self) -> None:
        self.on_regenerate(JobStep.TITLE)

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
        note(self.cost)

    def reload(self) -> bool:
        busy = llm_job_active(self.store, self.meeting_id)
        current = self.store.segments.current_job_id(self.meeting_id)
        stale = set(self.store.summaries.stale_after_reprocess(self.meeting_id, current))
        info = last_llm_info(self.store, self.meeting_id)
        skipped = {str(k): str(v) for k, v in (info.get("skipped") or {}).items()}
        self._summary_state()
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

    def _summary_state(self) -> None:
        """U-042: a aba da ata diz quando a ata espera a identificação (e oferece gerar agora) ou quando os nomes mudaram
        depois dela (e oferece atualizar); nada sai sozinho."""
        ata = self.text_tabs[str(SummaryKind.MINUTES)]
        try:
            waiting = summary_gate.deferred(self.store, self.meeting_id)
            changed = waiting is None and summary_gate.names_changed(self.store, self.meeting_id)
        except Exception as e:  # o aviso nunca impede as abas de carregar
            log.debug("reunião %s: estado da ata adiada: %s", self.meeting_id, e)
            waiting, changed = None, False
        if waiting is not None and self.store.summaries.current(self.meeting_id, SummaryKind.MINUTES) is None:
            ata.set_extra(f"A ata espera a identificação de {waiting.pending} voz(es): confirme em \"Revisar falantes…\" ou "
                          "gere agora com as vozes que faltam sem nome.", "Gerar ata mesmo assim", self.release_now)
        elif changed:
            ata.set_extra("Os nomes mudaram depois desta ata.", "Atualizar a ata com os nomes", self.refresh_names)
        else:
            ata.set_extra("")

    def release_now(self) -> int | None:
        """"Gerar ata mesmo assim": as tarefas adiadas entram na fila já, com as vozes que faltam sem nome."""
        job_id = summary_gate.release(self.store, self.meeting_id, "gerar ata mesmo assim", force=True)
        return self._queued(job_id, "Ata: gerando pela fila com as vozes que faltam sem nome…" if job_id is not None else
                            "A ata não saiu agora: veja a fila (processamento em curso ou sem chave de LLM).")

    def refresh_names(self) -> int | None:
        """"Atualizar a ata com os nomes": ata, executivo e pré-resumo de novo pela fila, com os nomes de agora (D-057)."""
        from ..pipeline.llm_steps import enqueue_llm_job

        if llm_job_active(self.store, self.meeting_id):
            return self._queued(None, "Já há um job de LLM em andamento para esta reunião; espere terminar.")
        job_id = enqueue_llm_job(self.store, self.meeting_id, list(summary_gate.REFRESH))
        log.info("reunião %s: atualizar a ata com os nomes pedido (job llm %s)", self.meeting_id, job_id)
        return self._queued(job_id, "Atualizando a ata com os nomes pela fila…" if job_id is not None else
                            "Nenhum provedor de LLM ativo com chave: informe a chave em Configurações, Chaves de acesso.")

    def _queued(self, job_id: int | None, message: str) -> int | None:
        if self.on_message is not None:
            self.on_message(message)
        if job_id is not None:
            self.regenerated.append(job_id)
            if self.on_launch is not None:
                self.on_launch(True)
        if self.after_regenerate is not None:
            self.after_regenerate()
        else:
            self.reload()
        return job_id

    def regenerate(self, step: JobStep) -> int | None:
        """"Gerar de novo": job `llm` de uma tarefa pela fila (P6); a versão anterior vira `superada` quando a nova chegar.
        Tarefa que espera a identificação (U-042, A-14) é liberada com as outras adiadas, para a ata não sair duas vezes."""
        waiting = summary_gate.deferred(self.store, self.meeting_id)
        if waiting is not None and str(step) in waiting.tasks:
            job = summary_gate.release(self.store, self.meeting_id, f"gerar de novo: {step}", force=True)
            return self._queued(job, f"{step_label(step)}: gerando pela fila com as outras tarefas que esperavam…" if job
                                is not None else "Não saiu agora: veja a fila (processamento em curso ou sem chave de LLM).")
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


__all__ = ["KIND_STEP", "ActionsTab", "JobStatus", "LlmTabsGroup", "TextTab", "TitleTab", "cost_badge", "last_llm_info",
           "llm_done_text", "llm_job_active", "request_llm_step", "step_label"]
