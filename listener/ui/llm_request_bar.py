"""F5.6 L1b (item 11, A-13, A-14): "Gerar ata e ações" e "Gerar resumo" na reunião aberta, cada um com o estado num selo,
da leitura única por recarga (`llm_requests.request_states`, A-15): "não gerado", "pedido" (com "espera N vozes" quando o
pedido espera os nomes), "gerando", "gerado em dd/mm", "desatualizado (motivo)" e "falhou" (o erro na dica).

O botão abre o popup do pedido pelo `on_ask(espécie)` de quem mostra a barra (`LlmTabsGroup`); com job de LLM ativo, segue
ligado (o pedido entra na fila atrás, A-10), salvo quando a própria espécie está "gerando". Em "falhou", o botão vira "Gerar
de novo", que pede sem o popup (Comportamento 2; a regra fica em `llm_ask.retries`), salvo quando a transcrição mudou
(`STALE_TRANSCRIPT`), que pergunta. Sem chave ou sem falas (`off`), os dois apagados com o motivo na dica e numa nota embaixo,
que quebra a linha (revisão do L1b, R-07 e R-08: ao lado dos botões, a nota pedia ~850 px e cortava a reunião estreita).
Selos de estado do tema (U-084); sinais ligados a métodos (R-06)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..pipeline.llm_requests import RequestState, Species, State
from ..pipeline.llm_steps import STALE_TRANSCRIPT
from ..rag.export import fmt_datetime
from .components import Badge
from .theme import Tone, note

LABELS = {Species.MINUTES: "Gerar ata e ações", Species.SUMMARY: "Gerar resumo"}
RETRY = "Gerar de novo"
TIPS = {Species.MINUTES: "Pede à LLM a ata e as ações desta reunião (pergunta antes, com o custo)",
        Species.SUMMARY: "Pede à LLM o resumo executivo e o pré-resumo, que saem da ata (pergunta antes, com o custo)"}
RETRY_TIP = "Pede de novo o que falhou, sem perguntar (o custo já foi aceito)"
RETRY_ASK_TIP = "Pede de novo o que falhou; a transcrição mudou, então pergunta antes, com o custo"
RUNNING_TIP = "Já está sendo gerado pela fila."
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


class RequestBar(QWidget):
    """Os dois botões de pedido com o selo do estado de cada um (a linha da LLM no cabeçalho da reunião aberta)."""

    def __init__(self, on_ask: Callable[[Species], Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_ask = on_ask
        self.states: dict[Species, RequestState] = {}
        self.buttons: dict[Species, QPushButton] = {}
        self.badges: dict[Species, Badge] = {}
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for sp, slot in ((Species.MINUTES, self._ask_minutes), (Species.SUMMARY, self._ask_summary)):
            button = QPushButton(LABELS[sp])
            button.clicked.connect(slot)
            badge = Badge(str(State.NONE), Tone.NEUTRAL)
            badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)     # o selo no tamanho do texto (U-075)
            self.buttons[sp], self.badges[sp] = button, badge
            lay.addWidget(button)
            lay.addWidget(badge)
            lay.addSpacing(16)
        lay.addStretch(1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        note(self.note)
        self.note.setVisible(False)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(lay)
        column.addWidget(self.note)

    def show_states(self, states: Mapping[Species, RequestState], off: str = "", waiting: Mapping[Species, int] | None = None) -> None:
        """Os estados da recarga (A-15); `off`: por que nada pode ser pedido (sem chave, sem falas), vazio = pode; `waiting`:
        as vozes que o pedido adiado de cada espécie espera."""
        self.states = dict(states)
        for sp, button in self.buttons.items():
            rs = states[sp]
            badge = self.badges[sp]
            badge.setText(state_text(rs, (waiting or {}).get(sp, 0)))
            badge.set_tone(STATE_TONE[rs.state])
            badge.setToolTip(rs.error if rs.state == State.FAILED else "")
            failed, running = rs.state == State.FAILED, rs.state == State.RUNNING
            button.setText(RETRY if failed else LABELS[sp])
            button.setEnabled(not off and not running)
            button.setToolTip(off or (RUNNING_TIP if running else self._retry_tip(rs) if failed else TIPS[sp]))
        self.note.setText(off)
        self.note.setVisible(bool(off))

    @staticmethod
    def _retry_tip(rs: RequestState) -> str:
        return RETRY_ASK_TIP if STALE_TRANSCRIPT in rs.error else RETRY_TIP

    def _ask_minutes(self) -> None:
        self.on_ask(Species.MINUTES)

    def _ask_summary(self) -> None:
        self.on_ask(Species.SUMMARY)


__all__ = ["LABELS", "RETRY", "RETRY_ASK_TIP", "RETRY_TIP", "RUNNING_TIP", "STATE_TONE", "RequestBar", "state_text"]
