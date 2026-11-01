"""F5.6 L1b (item 11, A-13, A-14): o pedido de "Gerar ata e ações" e de "Gerar resumo" na reunião aberta, com o estado num
selo, da leitura única por recarga (`llm_requests.request_states`, A-15): "não gerado", "pedido" (com "espera N vozes" quando
o pedido espera os nomes), "gerando", "gerado em dd/mm", "desatualizado (motivo)" e "falhou" (o erro na dica). F5.11 L2
(U-103, A-23): a barra do cabeçalho virou uma linha por espécie em cada aba de LLM (`RequestLine`, em Ata, Ações e Resumo,
dentro de uma `RequestPage`, que só a cria quando a aba aparece); o botão da linha é o pedido da aba.

O botão abre o popup do pedido pelo `on_ask(espécie)` de quem mostra a linha (`LlmTabsGroup`); com job de LLM ativo, segue
ligado (o pedido entra na fila atrás, A-10), salvo quando a própria espécie está "gerando". Com versão, ele diz "Gerar de
novo" (A-23); em "falhou", o "Gerar de novo" pede sem o popup (Comportamento 2; a regra fica em `llm_ask.retries`), salvo
quando a transcrição mudou (`STALE_TRANSCRIPT`), que pergunta. Sem chave ou sem falas (`off`), apagado com o motivo na dica
e numa nota embaixo, que quebra a linha (revisão do L1b, R-07 e R-08: ao lado do botão, a nota alargava a reunião
estreita). Selos de estado do tema (U-084); sinais ligados a métodos (R-06). F5.6 L2 (A-29): "falhou" com a resposta fora
do esquema numa tarefa com instruções do usuário (`RequestState.custom`) diz isso na nota e mostra "Restaurar o prompt
padrão" ao lado dela (montado só na primeira vez), que chama o `on_restore` com as tarefas: as instruções voltam às do app,
e o "Gerar de novo" pede com elas."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle, QVBoxLayout, QWidget

from ..pipeline.llm_requests import RequestState, Species, State
from ..pipeline.llm_steps import STALE_TRANSCRIPT
from ..rag.export import fmt_datetime
from .components import Badge
from .llm_ask import step_label
from .theme import Tone, note

LABELS = {Species.MINUTES: "Gerar ata e ações", Species.SUMMARY: "Gerar resumo"}
RETRY = "Gerar de novo"
TIPS = {Species.MINUTES: "Pede à LLM a ata e as ações desta reunião (pergunta antes, com o custo)",
        Species.SUMMARY: "Pede à LLM o resumo executivo e o pré-resumo, que saem da ata (pergunta antes, com o custo)"}
RETRY_TIP = "Pede de novo o que falhou, sem perguntar (o custo já foi aceito)"
RETRY_ASK_TIP = "Pede de novo o que falhou; a transcrição mudou, então pergunta antes, com o custo"
RUNNING_TIP = "Já está sendo gerado pela fila."
RESTORE = "Restaurar o prompt padrão"
RESTORE_TIP = "Volta às instruções do app (Configurações, página Prompts); depois, \"Gerar de novo\" pede com elas."
RESTORED = "Instruções do app de volta (Configurações, página Prompts): clique em \"Gerar de novo\"."
STATE_TONE = {State.FAILED: Tone.DANGER, State.RUNNING: Tone.INFO, State.REQUESTED: Tone.INFO, State.STALE: Tone.WARNING,
              State.DONE: Tone.SUCCESS, State.NONE: Tone.NEUTRAL}


def state_text(rs: RequestState, waiting: int = 0) -> str:
    """O texto do selo: "gerado em 29/10", "desatualizado (nomes)", "pedido · espera 2 vozes" ou o nome do estado."""
    if rs.state == State.DONE:
        day = fmt_datetime(rs.when)[:5]
        return f"gerado em {day}" if day else str(rs.state)
    if rs.state == State.STALE:
        return f"desatualizado ({', '.join(rs.reasons)})" if rs.reasons else str(rs.state)
    if rs.state == State.REQUESTED and waiting:
        return f"pedido · espera {waiting} {'voz' if waiting == 1 else 'vozes'}"
    return str(rs.state)


def custom_text(tasks: tuple[str, ...]) -> str:
    """A-29: a nota do "falhou" com instruções do usuário: "A resposta saiu fora do formato com as suas instruções de ata"."""
    names = [step_label(t) for t in tasks]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
    return f"A resposta saiu fora do formato com as suas instruções de {joined}."


class RequestLine(QWidget):
    """F5.11 (U-103, A-23): a linha de pedido de uma espécie numa aba da reunião aberta, com a lógica do `show_states` da barra
    da F5.6: o selo do estado ("pedido · espera N vozes" inclusive), a nota (sem chave, sem falas, a resposta fora do formato
    com as instruções do usuário), o "Restaurar o prompt padrão" e o botão de pedido: "Gerar ata e ações" (ou "Gerar resumo")
    sem versão; "Gerar de novo" com versão e em "falhou", que pede de novo sem o popup (Comportamento 2 da F5.6)."""

    def __init__(self, species: Species, on_ask: Callable[[Species], Any], parent: QWidget | None = None,
                 on_restore: Callable[[tuple[str, ...]], Any] | None = None) -> None:
        super().__init__(parent)
        self.species = species
        self.on_ask = on_ask
        self.on_restore = on_restore
        self.state = RequestState(State.NONE)
        self.custom: tuple[str, ...] = ()                    # A-29: as tarefas que o "Restaurar o prompt padrão" restaura
        self.restore: QPushButton | None = None              # montado no primeiro "falhou" com instruções suas
        self.badge = Badge(str(State.NONE), Tone.NEUTRAL)
        self.badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)     # o selo no tamanho do texto (U-075)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        note(self.note)
        self.note.setVisible(False)
        self.button = QPushButton(LABELS[species])
        self.button.clicked.connect(self._ask)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.badge)
        row.addStretch(1)
        row.addWidget(self.button)
        self.bottom = QHBoxLayout()                          # a nota embaixo, que quebra a linha (R-07 e R-08 da F5.6)
        self.bottom.setContentsMargins(0, 0, 0, 0)
        self.bottom.setSpacing(8)
        self.bottom.addWidget(self.note, 1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(row)
        column.addLayout(self.bottom)

    def show_state(self, rs: RequestState, off: str = "", waiting: int = 0) -> None:
        """O estado da recarga (A-15); `off`: por que nada pode ser pedido (sem chave, sem falas), vazio = pode; `waiting`: as
        vozes que o pedido adiado espera."""
        self.state = rs
        self.badge.setText(state_text(rs, waiting))
        self.badge.set_tone(STATE_TONE[rs.state])
        self.badge.setToolTip(rs.error if rs.state == State.FAILED else "")
        failed, running = rs.state == State.FAILED, rs.state == State.RUNNING
        again = failed or rs.state in (State.DONE, State.STALE)                  # A-23: com versão, "Gerar de novo"
        self.button.setText(RETRY if again else LABELS[self.species])
        self.button.setEnabled(not off and not running)
        self.button.setToolTip(off or (RUNNING_TIP if running else self._retry_tip(rs) if failed else TIPS[self.species]))
        self.custom = () if off else tuple(dict.fromkeys(rs.custom))
        text = off or (custom_text(self.custom) if self.custom else "")
        self.note.setText(text)
        self.note.setVisible(bool(text))
        if self.custom and self.on_restore is not None:
            self._restore_button().setVisible(True)
        elif self.restore is not None:
            self.restore.setVisible(False)

    def _restore_button(self) -> QPushButton:
        if self.restore is None:
            self.restore = QPushButton(RESTORE, self)        # o pai já na criação (veja a `InfoBar`)
            self.restore.setToolTip(RESTORE_TIP)
            self.restore.clicked.connect(self._restore)
            self.bottom.addWidget(self.restore, 0)
            QWidget.setTabOrder(self.button, self.restore)   # R-05: logo depois do pedido, não no fim
        return self.restore

    def _restore(self) -> None:
        if self.custom and self.on_restore is not None:
            self.on_restore(self.custom)

    @staticmethod
    def _retry_tip(rs: RequestState) -> str:
        return RETRY_ASK_TIP if STALE_TRANSCRIPT in rs.error else RETRY_TIP

    def _ask(self) -> None:
        self.on_ask(self.species)


class RequestPage(QWidget):
    """Uma aba da reunião aberta (Ata, Ações, Resumo) com a linha de pedido em cima do conteúdo (A-23). A linha nasce quando a
    aba aparece (a abertura não cria o que está escondido); até lá, o estado da última recarga fica guardado."""

    def __init__(self, content: QWidget, species: Species, on_ask: Callable[[Species], Any],
                 on_restore: Callable[[tuple[str, ...]], Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = content
        self.species = species
        self.on_ask = on_ask
        self.on_restore = on_restore
        self.line: RequestLine | None = None
        self._state: tuple[RequestState, str, int] = (RequestState(State.NONE), "", 0)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addWidget(content, 1)

    def show_state(self, rs: RequestState, off: str = "", waiting: int = 0) -> None:
        self._state = (rs, off, waiting)
        if self.line is not None:
            self.line.show_state(rs, off, waiting)

    def ensure_line(self) -> RequestLine:
        """A linha desta aba, criada na primeira vez (na abertura da aba ou num teste), com o último estado."""
        if self.line is None:
            self.line = RequestLine(self.species, self.on_ask, self, on_restore=self.on_restore)
            inner = self.content.layout()                    # alinhada com o conteúdo da aba (sem layout, a margem do estilo)
            margins = inner.contentsMargins() if inner is not None else None
            gap = self.style().pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin)
            self.line.setContentsMargins(margins.left() if margins else gap, margins.top() if margins else gap,
                                         margins.right() if margins else gap, 0 if margins else gap // 2)
            self._lay.insertWidget(0, self.line)
            self.line.show_state(*self._state)
            self.line.setVisible(True)                       # numa aba já à vista, o filho novo não aparece sozinho
        return self.line

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.line is None:
            self.ensure_line()


__all__ = ["LABELS", "RESTORE", "RESTORED", "RESTORE_TIP", "RETRY", "RETRY_ASK_TIP", "RETRY_TIP", "RUNNING_TIP", "STATE_TONE",
           "RequestLine", "RequestPage", "custom_text", "state_text"]
