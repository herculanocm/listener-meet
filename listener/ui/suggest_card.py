"""F5.20 (C11, U-154, U-155, U-157, U-159): o cartão da sugestão ao vivo, sob o chat da bandeja de legendas (A-17: um painel
próprio, não um item da lista: botões de verdade por opção pedem widgets).

- Cabeçalho fixo: a lâmpada, "Sugestão · hh:mm · até [mm:ss]", o modo ("Falar" e "Me situar", exclusivos) e "Recolher".
- Corpo com rolagem: as opções chegando uma a uma (`OptionRow`, em `ui/suggest_option.py`: o tipo em selo, "Boa", "Ruim"
  e "Usei" à direita, a fala inteira, os fatos com [mm:ss] clicáveis ou "sem evidência", e os motivos do "Ruim" em chips
  logo abaixo); o estado
  ("pedindo…", "cancelada", "falhou: …" com "Tentar de novo"); "Nova sugestão", "Cancelar" e o custo; "Como sugerir agora"
  com os chips, "Texto…" e "Limpar"; "Objetivo…" e o "ajustada por".
- Nada em menu nem em dica que mostre a sugestão: são janelas fora da exclusão da captura (A-14, D-095). Os botões nunca são
  padrão (A-29): a bandeja não ativa, e o Enter não pode disparar nada; nenhum pega o foco.
- A altura (`preferred_height`) vai de 120 px a 45 % da bandeja; recolhido, só o cabeçalho. A bandeja a aplica (`fit_card`).
- F5.21: os widgets da tela (`ui/suggest_screen_row.py`): a chave "Com a tela" e a seta na linha de "Nova sugestão", a linha
  "com a tela · rótulo" e a confirmação da primeira vez no corpo, acima das opções; "Tentar sem a tela" quando o pedido com a
  tela falha (A-18).
O cartão não grava nada: emite sinais, e o host (`ui/suggest_host.py`) grava e decide."""
from __future__ import annotations

import functools
from collections.abc import Iterable, Sequence
from typing import override

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..llm.suggest_task import SuggestOption, SuggestResult
from ..model import SuggestMode
from .components import IconButton
from .icons import set_icon
from .suggest_option import BUTTON_PX, REASONS, OptionRow, chip_button, facts_html, plain
from .suggest_screen_row import ScreenRow
from .theme import Tone, note, role
from .widget_parts import ElidedLabel, icon_label

CARD_MIN_H = 120
CARD_SHARE = 0.45
CHIPS = ("mais curta", "mais firme", "em pergunta", "com números", "em inglês")
ASKING, CANCELLED, NO_NEWS = "pedindo…", "cancelada", "nada novo desde [{t}]"
FINISHED = "gravação encerrada: as avaliações seguem valendo"
# as dicas nunca repetem o texto do rótulo: a dica é uma janela fora da exclusão da captura (A-14, D-095)
TITLE_TIP, NUDGE_TIP = "A sugestão mais recente", "Vale para as próximas sugestões até Limpar"
ADJUSTED_TIP = "As suas avaliações nesta reunião"


class SuggestCard(QFrame):
    request_clicked = Signal(object)             # o `SuggestMode` do pedido ("Nova sugestão", "Tentar de novo")
    cancel_clicked = Signal()
    rated = Signal(int, object, object)
    used_toggled = Signal(int, bool)
    nudge_chip_toggled = Signal(str, bool)
    nudge_text_requested = Signal()
    nudge_cleared = Signal()
    goal_requested = Signal()
    fact_clicked = Signal(int)
    collapsed_changed = Signal(bool)
    content_changed = Signal()                   # a altura preferida mudou (uma opção chegou, um estado novo)
    plain_requested = Signal(object)             # F5.21: "Tentar sem a tela" (o modo do pedido)
    screen_clicked = Signal()                    # F5.21: o "[tela]" de uma opção

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("suggestcard")
        self.mode = SuggestMode.SPEAK
        self.asked = SuggestMode.SPEAK              # o modo do pedido em curso (R-08 da revisão do L2: o seletor muda o próximo)
        self.collapsed = False
        self.rows: list[OptionRow] = []
        self.screen_row = ScreenRow(self)
        self._build()
        self.hide()

    # ---------------------------------------------------------------- montagem
    def _build(self) -> None:
        self.line = line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        self.title = ElidedLabel()
        role(self.title, "body_strong")
        self.btn_speak, self.btn_orient = chip_button("Falar"), chip_button("Me situar")
        for b in (self.btn_speak, self.btn_orient):
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.btn_speak)
        self.mode_group.addButton(self.btn_orient)
        self.btn_speak.setChecked(True)
        self.btn_speak.clicked.connect(functools.partial(self._set_mode, SuggestMode.SPEAK))
        self.btn_orient.clicked.connect(functools.partial(self._set_mode, SuggestMode.ORIENT))
        self.btn_collapse = IconButton("chevron-down", "Recolher")
        self.btn_collapse.setFixedSize(BUTTON_PX, BUTTON_PX)
        plain(self.btn_collapse).clicked.connect(self.toggle_collapsed)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(icon_label("lightbulb", Tone.ACCENT))
        head.addWidget(self.title, 1)
        for w in (self.btn_speak, self.btn_orient, self.btn_collapse):
            head.addWidget(w)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(8)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(10)
        body.addWidget(self.screen_row.consent)
        body.addWidget(self.screen_row.line)
        body.addLayout(self.rows_box)
        body.addLayout(self._state_row())
        body.addLayout(self._actions_row())
        body.addLayout(self._nudge_rows())
        body.addLayout(self._footer_row())
        body.addStretch(1)
        self.body_area = QScrollArea()
        self.body_area.setFrameShape(QFrame.Shape.NoFrame)
        self.body_area.setWidgetResizable(True)
        self.body_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_area.setWidget(self.body)
        self.body_area.viewport().setAutoFillBackground(False)
        self.body.setAutoFillBackground(False)
        self.lay = lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(line)
        lay.addLayout(head)
        lay.addWidget(self.body_area, 1)
        self.head = head

    def _state_row(self) -> QHBoxLayout:
        self.state = QLabel("")
        self.state.setWordWrap(True)
        note(self.state)
        self.btn_retry = QPushButton("Tentar de novo")
        plain(self.btn_retry).clicked.connect(self._request)
        self.btn_retry.hide()
        self.btn_plain = QPushButton("Tentar sem a tela")
        plain(self.btn_plain).clicked.connect(self._plain)
        self.btn_plain.hide()
        row = QHBoxLayout()
        row.addWidget(self.state, 1)
        row.addWidget(self.btn_retry)
        row.addWidget(self.btn_plain)
        return row

    def _actions_row(self) -> QHBoxLayout:
        self.btn_new = QPushButton("Nova sugestão")
        plain(self.btn_new).clicked.connect(self._request)
        self.btn_cancel = QPushButton("Cancelar")
        plain(self.btn_cancel).clicked.connect(self.cancel_clicked)
        self.btn_cancel.hide()
        self.cost = QLabel("")
        note(self.cost)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.screen_row.toggle)
        row.addWidget(self.screen_row.pick)
        row.addStretch(1)
        row.addWidget(self.cost)
        return row

    def _nudge_rows(self) -> QVBoxLayout:
        title = QLabel("Como sugerir agora")
        note(title, Tone.TEXT)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.chips: dict[str, QToolButton] = {}
        for i, text in enumerate(CHIPS):
            chip = chip_button(text)
            chip.clicked.connect(functools.partial(self._chip_clicked, text))
            self.chips[text] = chip
            grid.addWidget(chip, i // 3, i % 3)
        self.btn_text = QToolButton()
        self.btn_text.setText("Texto…")
        self.btn_text.setToolTip("Escrever como a IA deve sugerir (abre um campo fora da captura de tela)")
        role(self.btn_text, "caption")
        self.btn_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        plain(self.btn_text).clicked.connect(self.nudge_text_requested)
        grid.addWidget(self.btn_text, 1, 2)
        self.nudge = ElidedLabel()
        note(self.nudge)
        self.btn_clear = QPushButton("Limpar")
        self.btn_clear.setFlat(True)
        plain(self.btn_clear).clicked.connect(self.nudge_cleared)
        line = QHBoxLayout()
        line.addWidget(self.nudge, 1)
        line.addWidget(self.btn_clear)
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(title)
        box.addLayout(grid)
        box.addLayout(line)
        self.set_nudge("")
        return box

    def _footer_row(self) -> QHBoxLayout:
        self.adjusted = ElidedLabel()
        note(self.adjusted)
        self.btn_goal = IconButton("flag", "Objetivo…")
        self.btn_goal.setFixedSize(BUTTON_PX, BUTTON_PX)
        self.btn_goal.setToolTip("Meu objetivo nesta reunião (abre um campo fora da captura de tela)")
        plain(self.btn_goal).clicked.connect(self.goal_requested)
        row = QHBoxLayout()
        row.addWidget(self.adjusted, 1)
        row.addWidget(self.btn_goal)
        return row

    # ---------------------------------------------------------------- estados
    def begin(self, mode: SuggestMode, hhmm: str, t_until_ms: int | None, no_news: int | None = None) -> None:
        """Um pedido começou: as opções de antes saem; "pedindo…"; "Cancelar" à vista. O cartão recolhido abre (R-01 da
        revisão do L2: o pedido é pago, e as opções chegariam escondidas)."""
        self._set_mode(mode)
        self.asked = mode
        self.set_collapsed(False)
        self.clear_rows()
        self.set_title(hhmm, t_until_ms)
        self._state(ASKING if no_news is None else f"{ASKING} {NO_NEWS.format(t=stamp(no_news))}")
        self.btn_retry.hide()
        self.btn_plain.hide()
        self.screen_row.show_line(None)
        self.btn_cancel.show()
        self.btn_new.setEnabled(False)
        self.cost.setText("")
        self.show()
        self.content_changed.emit()

    def add_option(self, option: SuggestOption) -> OptionRow:
        row = OptionRow(option, self.asked == SuggestMode.ORIENT)
        row.rated.connect(self.rated)
        row.used_toggled.connect(self.used_toggled)
        row.fact_clicked.connect(self.fact_clicked)
        row.screen_clicked.connect(self.screen_clicked)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        row.show()                                       # já: o Qt só mostraria no próximo laço, e a altura viria sem ela
        self.content_changed.emit()
        return row

    def finish(self, result: SuggestResult, option_ids: Sequence[int], adjusted: str) -> None:
        """A resposta inteira: as avaliações ligam com os ids gravados, o custo e o "ajustada por"."""
        self.keep(result, option_ids)
        self.cost.setText(("custo estimado " if result.cost_estimated else "") + fmt_usd(result.cost_usd))
        self.set_adjusted(adjusted)
        self._state("")
        self.btn_cancel.hide()
        self.btn_new.setEnabled(True)
        self.content_changed.emit()

    def keep(self, result: SuggestResult, option_ids: Sequence[int]) -> None:
        """As opções que chegaram ligam com os ids gravados, e o custo conhecido aparece. Vale também para o pedido cancelado
        ou que falhou depois de algumas opções (prazo, fora do esquema), que o L1 grava (R-07 da revisão do L2)."""
        for row, option_id in zip(self.rows, option_ids, strict=False):
            row.set_option_id(option_id)
        if result.cost_usd is not None:
            self.cost.setText(("custo estimado " if result.cost_estimated else "") + fmt_usd(result.cost_usd))

    def fail(self, message: str, retry: bool, plain_retry: bool = False) -> None:
        """"Tentar de novo" só com "Nova sugestão" à vista: depois do Parar, os dois somem (R-07 da revisão do L2); F5.21
        (A-18): o pedido com a tela que falhou oferece também "Tentar sem a tela", sem uma segunda chamada sozinha."""
        self._state(f"falhou: {message}")
        self.btn_retry.setVisible(retry and not self.btn_new.isHidden())
        self.btn_plain.setVisible(plain_retry and retry and not self.btn_new.isHidden())
        self.btn_cancel.hide()
        self.btn_new.setEnabled(retry)
        self.content_changed.emit()

    def stopped(self, text: str) -> None:
        """O teto de custo (A-13): "paradas nesta reunião…", um estado próprio, sem "falhou:" nem "Tentar de novo" (os estados
        do cartão na seção Interface da spec; R-04 da revisão do L3)."""
        self._state(text)
        self.btn_retry.hide()
        self.btn_cancel.hide()
        self.btn_new.setEnabled(False)
        self.content_changed.emit()

    def cancelled(self) -> None:
        self._state(CANCELLED)
        self.btn_cancel.hide()
        self.btn_new.setEnabled(True)
        self.content_changed.emit()

    def set_finished(self) -> None:
        """Parar ou Cancelar: sem pedir de novo nem cancelar; as avaliações seguem."""
        self.btn_new.hide()
        self.btn_cancel.hide()
        self.btn_retry.hide()
        self.btn_plain.hide()
        for b in (self.btn_speak, self.btn_orient, self.btn_text, self.btn_goal, self.screen_row.toggle, self.screen_row.pick,
                  *self.chips.values()):
            b.setEnabled(False)
        if not self.state.text():
            self._state(FINISHED)

    def set_can_request(self, can: bool) -> None:
        self.btn_new.setEnabled(can and self.btn_cancel.isHidden())
        self.btn_retry.setEnabled(can)

    def set_title(self, hhmm: str, t_until_ms: int | None) -> None:
        until = f" · até [{stamp(t_until_ms)}]" if t_until_ms is not None else ""
        self.title.set_full_text(f"Sugestão · {hhmm}{until}", TITLE_TIP)

    def set_nudge(self, text: str) -> None:
        self.nudge.set_full_text(f"Como sugerir agora: {text}" if text else "Como sugerir agora: como o assistente achar melhor",
                                 NUDGE_TIP)
        self.btn_clear.setVisible(bool(text))

    def set_chips(self, selected: Iterable[str]) -> None:
        wanted = set(selected)
        for text, chip in self.chips.items():
            chip.setChecked(text in wanted)

    def set_adjusted(self, text: str) -> None:
        self.adjusted.set_full_text(f"ajustada por: {text}" if text else "", ADJUSTED_TIP)

    def clear_rows(self) -> None:
        """As opções de antes saem escondidas: o `deleteLater` só apaga na volta ao laço de eventos, e até lá a linha fora do
        layout seguiria pintada por cima das novas."""
        for row in self.rows:
            self.rows_box.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []

    def _state(self, text: str) -> None:
        self.state.setText(text)
        self.state.setVisible(bool(text))

    def _set_mode(self, mode: SuggestMode, *_args: object) -> None:
        self.mode = mode
        (self.btn_orient if mode == SuggestMode.ORIENT else self.btn_speak).setChecked(True)

    def _request(self, *_args: object) -> None:
        self.request_clicked.emit(self.mode)

    def _plain(self, *_args: object) -> None:
        self.plain_requested.emit(self.mode)

    def _chip_clicked(self, text: str, *_args: object) -> None:
        self.nudge_chip_toggled.emit(text, self.chips[text].isChecked())

    # ---------------------------------------------------------------- recolher e altura
    def toggle_collapsed(self, *_args: object) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        self.body_area.setVisible(not collapsed)
        name, icon = ("Expandir", "chevron-up") if collapsed else ("Recolher", "chevron-down")
        self.btn_collapse.setText(name)
        self.btn_collapse.setAccessibleName(name)
        self.btn_collapse.setToolTip(name)
        set_icon(self.btn_collapse, icon, Tone.TEXT, 20)
        self.collapsed_changed.emit(collapsed)
        self.content_changed.emit()

    def head_height(self) -> int:
        """A linha de cima, o espaço e o cabeçalho: o recolhido inteiro (R-09 da revisão do L2: a conta à mão cortava 2 px)."""
        return self.line.sizeHint().height() + self.lay.spacing() + max(BUTTON_PX, self.head.sizeHint().height())

    def preferred_height(self, tray_height: int) -> int:
        """De 120 px a 45 % da bandeja (A-17); recolhido, só o cabeçalho."""
        if self.collapsed:
            return self.head_height()
        want = self.head_height() + 6 + self.body.sizeHint().height()
        return max(CARD_MIN_H, min(want, int(tray_height * CARD_SHARE)))

    @override
    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.head_height())


__all__ = ["CARD_MIN_H", "CARD_SHARE", "CHIPS", "REASONS", "OptionRow", "SuggestCard", "facts_html"]
