"""F5.20 (C11, U-154, U-155, U-157, U-159): o cartão da sugestão ao vivo, sob o chat da bandeja de legendas (A-17: um painel
próprio, não um item da lista: botões de verdade por opção pedem widgets).

- Cabeçalho fixo: a lâmpada, "Sugestão · hh:mm · até [mm:ss]", o modo ("Falar" e "Me situar", exclusivos) e "Recolher".
- Corpo com rolagem: as opções chegando uma a uma (`OptionRow`: o tipo em selo, "Boa", "Ruim" e "Usei" à direita, a fala
  inteira, os fatos com [mm:ss] clicáveis ou "sem evidência", e os motivos do "Ruim" em chips logo abaixo); o estado
  ("pedindo…", "cancelada", "falhou: …" com "Tentar de novo"); "Nova sugestão", "Cancelar" e o custo; "Como sugerir agora"
  com os chips, "Texto…" e "Limpar"; "Objetivo…" e o "ajustada por".
- Nada em menu nem em dica que mostre a sugestão: são janelas fora da exclusão da captura (A-14, D-095). Os botões nunca são
  padrão (A-29): a bandeja não ativa, e o Enter não pode disparar nada; nenhum pega o foco.
- A altura (`preferred_height`) vai de 120 px a 45 % da bandeja; recolhido, só o cabeçalho. A bandeja a aplica (`fit_card`).
O cartão não grava nada: emite sinais, e o host (`ui/suggest_host.py`) grava e decide."""
from __future__ import annotations

import functools
import html
from collections.abc import Iterable, Sequence
from typing import override

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..llm.suggest_task import SuggestOption, SuggestResult
from ..model import Evidence, RatingReason, SuggestMode
from .components import Badge, IconButton
from .icons import set_icon
from .theme import Tone, note, role
from .widget_parts import ElidedLabel, icon_label

CARD_MIN_H = 120
CARD_SHARE = 0.45
BUTTON_PX = 32
CHIPS = ("mais curta", "mais firme", "em pergunta", "com números", "em inglês")
REASONS = tuple(str(r) for r in RatingReason)
ASKING, CANCELLED, NO_NEWS = "pedindo…", "cancelada", "nada novo desde [{t}]"
FINISHED = "gravação encerrada: as avaliações seguem valendo"
# as dicas nunca repetem o texto do rótulo: a dica é uma janela fora da exclusão da captura (A-14, D-095)
TITLE_TIP, NUDGE_TIP = "A sugestão mais recente", "Vale para as próximas sugestões até Limpar"
ADJUSTED_TIP = "As suas avaliações nesta reunião"


def _plain(button: QAbstractButton) -> QAbstractButton:
    """A-29: nenhum botão do cartão é padrão nem pega o foco (a bandeja não ativa; o Enter iria para o app de reunião)."""
    if isinstance(button, QPushButton):
        button.setDefault(False)
        button.setAutoDefault(False)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def _chip(text: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setAccessibleName(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    role(b, "caption")
    _plain(b)
    return b


def _toggle(icon: str, name: str) -> IconButton:
    b = IconButton(icon, name)
    b.setFixedSize(BUTTON_PX, BUTTON_PX)
    b.setCheckable(True)
    _plain(b)
    return b


def facts_html(option: SuggestOption) -> str:
    """Os fatos como links `fact:<caption_id>` com o [mm:ss] da legenda (o clique rola o chat até ela)."""
    return " · ".join(f'<a href="fact:{cid}">[{html.escape(stamp(t))}]</a>' for cid, t in option.facts)


class OptionRow(QWidget):
    """Uma opção: o tipo, a fala, os fatos e a avaliação. As avaliações só ligam com o id gravado (`set_option_id`)."""
    rated = Signal(int, object, object)          # option_id, nota (1, 0 ou None), motivo
    used_toggled = Signal(int, bool)
    fact_clicked = Signal(int)

    def __init__(self, option: SuggestOption, orient: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.option = option
        self.option_id: int | None = None
        self.badge = Badge(option.kind, Tone.INFO)
        self.good, self.bad, self.used = _toggle("thumb-up", "Boa"), _toggle("thumb-down", "Ruim"), _toggle("check", "Usei")
        self.used.setVisible(not orient)                   # "Me situar": uma avaliação só (U-159)
        self.good.clicked.connect(self._good)
        self.bad.clicked.connect(self._bad)
        self.used.clicked.connect(self._used)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        for b in (self.good, self.bad, self.used):
            head.addWidget(b)
        self.text = QLabel(option.text)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setAccessibleName(f"Opção {option.idx + 1}: {option.kind}")
        role(self.text, "body")
        self.facts = QLabel(facts_html(option) if option.evidence == Evidence.OK else "sem evidência")
        self.facts.setTextFormat(Qt.TextFormat.RichText if option.evidence == Evidence.OK else Qt.TextFormat.PlainText)
        self.facts.linkActivated.connect(self._fact)
        note(self.facts)
        self.reasons = QWidget()
        grid = QGridLayout(self.reasons)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.reason_group = QButtonGroup(self)
        self.reason_group.setExclusive(True)
        self.reason_chips: dict[str, QToolButton] = {}
        for i, reason in enumerate(REASONS):
            chip = _chip(reason)
            chip.clicked.connect(functools.partial(self._reason, reason))
            self.reason_group.addButton(chip)
            self.reason_chips[reason] = chip
            grid.addWidget(chip, i // 3, i % 3)
        self.reasons.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(head)
        lay.addWidget(self.text)
        lay.addWidget(self.facts)
        lay.addWidget(self.reasons)
        self.set_option_id(None)

    def set_option_id(self, option_id: int | None) -> None:
        self.option_id = option_id
        for b in (self.good, self.bad, self.used):
            b.setEnabled(option_id is not None)

    def set_rating(self, rating: int | None, reason: str | None = None, used: bool = False) -> None:
        """O estado gravado (a aba da reunião e os testes): os botões marcados sem emitir nada."""
        self.good.setChecked(rating == 1)
        self.bad.setChecked(rating == 0)
        self.used.setChecked(used)
        self.reasons.setVisible(rating == 0)
        if reason in self.reason_chips:
            self.reason_chips[reason].setChecked(True)

    def _good(self, checked: bool) -> None:
        if checked:
            self.bad.setChecked(False)
        self.reasons.hide()
        self._emit(1 if checked else None, None)

    def _bad(self, checked: bool) -> None:
        if checked:
            self.good.setChecked(False)
        self.reason_group.setExclusive(False)              # o motivo de antes sai (exclusivo, o grupo não desmarca)
        for chip in self.reason_chips.values():
            chip.setChecked(False)
        self.reason_group.setExclusive(True)
        self.reasons.setVisible(checked)
        self._emit(0 if checked else None, None)

    def _reason(self, reason: str, *_args: object) -> None:
        if self.bad.isChecked():
            self._emit(0, reason)

    def _used(self, checked: bool) -> None:
        if self.option_id is not None:
            self.used_toggled.emit(self.option_id, checked)

    def _emit(self, rating: int | None, reason: str | None) -> None:
        if self.option_id is not None:
            self.rated.emit(self.option_id, rating, reason)

    def _fact(self, link: str) -> None:
        kind, _sep, value = link.partition(":")
        if kind == "fact" and value.isdigit():
            self.fact_clicked.emit(int(value))


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("suggestcard")
        self.mode = SuggestMode.SPEAK
        self.collapsed = False
        self.rows: list[OptionRow] = []
        self._build()
        self.hide()

    # ---------------------------------------------------------------- montagem
    def _build(self) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        self.title = ElidedLabel()
        role(self.title, "body_strong")
        self.btn_speak, self.btn_orient = _chip("Falar"), _chip("Me situar")
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
        _plain(self.btn_collapse).clicked.connect(self.toggle_collapsed)
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
        lay = QVBoxLayout(self)
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
        _plain(self.btn_retry).clicked.connect(self._request)
        self.btn_retry.hide()
        row = QHBoxLayout()
        row.addWidget(self.state, 1)
        row.addWidget(self.btn_retry)
        return row

    def _actions_row(self) -> QHBoxLayout:
        self.btn_new = QPushButton("Nova sugestão")
        _plain(self.btn_new).clicked.connect(self._request)
        self.btn_cancel = QPushButton("Cancelar")
        _plain(self.btn_cancel).clicked.connect(self.cancel_clicked)
        self.btn_cancel.hide()
        self.cost = QLabel("")
        note(self.cost)
        row = QHBoxLayout()
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_cancel)
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
            chip = _chip(text)
            chip.clicked.connect(functools.partial(self._chip_clicked, text))
            self.chips[text] = chip
            grid.addWidget(chip, i // 3, i % 3)
        self.btn_text = QToolButton()
        self.btn_text.setText("Texto…")
        self.btn_text.setToolTip("Escrever como a IA deve sugerir (abre um campo fora da captura de tela)")
        role(self.btn_text, "caption")
        self.btn_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _plain(self.btn_text).clicked.connect(self.nudge_text_requested)
        grid.addWidget(self.btn_text, 1, 2)
        self.nudge = ElidedLabel()
        note(self.nudge)
        self.btn_clear = QPushButton("Limpar")
        self.btn_clear.setFlat(True)
        _plain(self.btn_clear).clicked.connect(self.nudge_cleared)
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
        _plain(self.btn_goal).clicked.connect(self.goal_requested)
        row = QHBoxLayout()
        row.addWidget(self.adjusted, 1)
        row.addWidget(self.btn_goal)
        return row

    # ---------------------------------------------------------------- estados
    def begin(self, mode: SuggestMode, hhmm: str, t_until_ms: int | None, no_news: int | None = None) -> None:
        """Um pedido começou: as opções de antes saem; "pedindo…"; "Cancelar" à vista."""
        self._set_mode(mode)
        self.clear_rows()
        self.set_title(hhmm, t_until_ms)
        self._state(ASKING if no_news is None else f"{ASKING} {NO_NEWS.format(t=stamp(no_news))}")
        self.btn_retry.hide()
        self.btn_cancel.show()
        self.btn_new.setEnabled(False)
        self.cost.setText("")
        self.show()
        self.content_changed.emit()

    def add_option(self, option: SuggestOption) -> OptionRow:
        row = OptionRow(option, self.mode == SuggestMode.ORIENT)
        row.rated.connect(self.rated)
        row.used_toggled.connect(self.used_toggled)
        row.fact_clicked.connect(self.fact_clicked)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        row.show()                                       # já: o Qt só mostraria no próximo laço, e a altura viria sem ela
        self.content_changed.emit()
        return row

    def finish(self, result: SuggestResult, option_ids: Sequence[int], adjusted: str) -> None:
        """A resposta inteira: as avaliações ligam com os ids gravados, o custo e o "ajustada por"."""
        for row, option_id in zip(self.rows, option_ids, strict=False):
            row.set_option_id(option_id)
        self.cost.setText(("custo estimado " if result.cost_estimated else "") + fmt_usd(result.cost_usd))
        self.set_adjusted(adjusted)
        self._state("")
        self.btn_cancel.hide()
        self.btn_new.setEnabled(True)
        self.content_changed.emit()

    def fail(self, message: str, retry: bool) -> None:
        self._state(f"falhou: {message}")
        self.btn_retry.setVisible(retry)
        self.btn_cancel.hide()
        self.btn_new.setEnabled(retry)
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
        for b in (self.btn_speak, self.btn_orient, self.btn_text, self.btn_goal, *self.chips.values()):
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
        for row in self.rows:
            self.rows_box.removeWidget(row)
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
        return max(BUTTON_PX, self.title.sizeHint().height()) + 6 + 1          # a linha de cima e o espaço

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
