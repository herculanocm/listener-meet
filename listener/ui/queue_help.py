"""A Ajuda da fila (F5.3 item 9, U-047, A-09): o que cada tipo faz, as etapas de cada tipo na ordem em que rodam, os estados
com a cor da barra, as colunas novas e o que cada botão faz. Os textos de tipos, estados e etapas vêm de `model.py`; a
ordem das etapas, dos próprios runners (a mesma que eles seguem); os textos dos botões e das colunas, de quem os cria
(`ui/queue.py`)."""
from __future__ import annotations

import html
from collections.abc import Mapping

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from ..model import JOB_KIND_HELP, JOB_KIND_LABELS, JOB_STATUS_HELP, JOB_STEP_HELP, JOB_STEP_LABELS, JobKind, JobStatus, JobStep
from ..pipeline.estimate import STEP_ORDER
from .common import JOB_STATUS_COLOR_NAMES, JOB_STATUS_HATCHED, job_status_color

type StepGroup = tuple[JobStep, ...]            # etapas que rodam juntas (quase sempre uma só)
type HelpItems = Mapping[str, str]              # nome -> o que faz, de botões e colunas


def kind_steps(kind: str) -> tuple[StepGroup, ...]:
    """As etapas de um tipo na ordem em que rodam, lidas dos runners: o processamento (`estimate.STEP_ORDER`, depois do
    download dos modelos), a LLM (`llm_steps.TASK_ORDER`, com executivo, pré-resumo e título juntos, `PARALLEL`) e o índice
    (`index_steps.STEP_WEIGHTS`). O perfil de trecho ainda não roda (F6). A espera pela gravação vale para qualquer tipo e
    fica fora daqui."""
    from ..pipeline.index_steps import STEP_WEIGHTS as INDEX_STEPS
    from ..pipeline.llm_steps import PARALLEL, TASK_ORDER

    if kind in (JobKind.MEETING, JobKind.REPROCESS):
        return ((JobStep.DOWNLOAD,), *((step,) for step in STEP_ORDER))
    if kind == JobKind.LLM:
        groups: list[StepGroup] = []
        for step in TASK_ORDER:
            if step not in PARALLEL:
                groups.append((step,))
            elif not groups or groups[-1][0] not in PARALLEL:
                groups.append(tuple(s for s in TASK_ORDER if s in PARALLEL))
        return tuple(groups)
    if kind == JobKind.INDEX:
        return tuple((JobStep(step),) for step in INDEX_STEPS)
    return ()


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _step_html(step: JobStep) -> str:
    return f"<b>{_esc(JOB_STEP_LABELS[step])}</b>: {_esc(JOB_STEP_HELP[step])}"


def _group_html(group: StepGroup) -> str:
    if len(group) == 1:
        return _step_html(group[0])
    return "ao mesmo tempo:<ul>" + "".join(f"<li>{_step_html(step)}</li>" for step in group) + "</ul>"


def _status_html(status: JobStatus) -> str:
    """O estado com a amostra da cor da barra, nos tokens do tema (F5.4), e o nome da cor (quem não distingue cores lê o
    nome)."""
    mark, color = ("▨" if status in JOB_STATUS_HATCHED else "■"), job_status_color(status)
    return (f'<li><span style="color:{color}">{mark}</span> <b>{_esc(status)}</b> ({_esc(JOB_STATUS_COLOR_NAMES[status])}): '
            f"{_esc(JOB_STATUS_HELP[status])}</li>")


def _items_html(items: HelpItems) -> str:
    return "<ul>" + "".join(f"<li><b>{_esc(name)}</b>: {_esc(text)}</li>" for name, text in items.items()) + "</ul>"


def queue_help_html(buttons: HelpItems, columns: HelpItems) -> str:
    """O texto da Ajuda em HTML: tipos com as etapas na ordem (reprocessar remete a processar), a espera que vale para
    qualquer tipo, estados, colunas e botões."""
    parts = ["<h3>O que cada tipo faz</h3>"]
    for kind in JobKind:
        parts.append(f"<p><b>{_esc(JOB_KIND_LABELS[kind])}</b>: {_esc(JOB_KIND_HELP[kind])}</p>")
        groups = () if kind == JobKind.REPROCESS else kind_steps(kind)
        if groups:
            parts.append("<ol>" + "".join(f"<li>{_group_html(group)}</li>" for group in groups) + "</ol>")
    parts.append(f"<p>Em qualquer tipo, {_step_html(JobStep.WAITING)}</p>")
    parts.append("<h3>Estados</h3><ul>" + "".join(_status_html(status) for status in JobStatus) + "</ul>")
    parts.append("<h3>Colunas</h3>" + _items_html(columns))
    parts.append("<h3>Botões</h3>" + _items_html(buttons))
    return "".join(parts)


class QueueHelpDialog(QDialog):
    """A janela da Ajuda: não modal, para ler com a fila ao lado."""

    def __init__(self, buttons: HelpItems, columns: HelpItems, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Ajuda da fila")
        self.setMinimumSize(560, 520)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setHtml(queue_help_html(buttons, columns))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(close)
