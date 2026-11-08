"""F5.20 (C11, U-155): uma opção do cartão da sugestão (`OptionRow`) e os botões que o cartão partilha, fora do
`ui/suggest_card.py` para ele ficar abaixo de 400 linhas (revisão do L2, R-12; como o chat da bandeja na D-095).

- A opção: o tipo em selo, "Boa", "Ruim" e "Usei" à direita, a fala inteira, os fatos com [mm:ss] clicáveis (`fact:<id>`)
  ou "sem evidência", e os motivos do "Ruim" em chips logo abaixo, escondidos até o "Ruim".
- As avaliações só ligam com o id gravado (`set_option_id`); "Boa" e "Ruim" são exclusivos, clicar de novo no marcado
  desfaz (nota nula), um motivo marcado manda o "Ruim" com ele, e "Usei" fica à parte.
- Nenhum botão é padrão nem pega o foco (A-29): a bandeja não ativa, e o Enter iria para o app de reunião."""
from __future__ import annotations

import functools
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..llm.prompts.common import stamp
from ..llm.suggest_task import SuggestOption
from ..model import Evidence, RatingReason
from .components import Badge, IconButton
from .theme import Tone, note, role

BUTTON_PX = 32
REASONS = tuple(str(r) for r in RatingReason)


def plain(button: QAbstractButton) -> QAbstractButton:
    """A-29: nenhum botão do cartão é padrão nem pega o foco (a bandeja não ativa; o Enter iria para o app de reunião)."""
    if isinstance(button, QPushButton):
        button.setDefault(False)
        button.setAutoDefault(False)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def chip_button(text: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setAccessibleName(text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    role(b, "caption")
    plain(b)
    return b


def toggle_button(icon: str, name: str) -> IconButton:
    b = IconButton(icon, name)
    b.setFixedSize(BUTTON_PX, BUTTON_PX)
    b.setCheckable(True)
    plain(b)
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
        self.good, self.bad = toggle_button("thumb-up", "Boa"), toggle_button("thumb-down", "Ruim")
        self.used = toggle_button("check", "Usei")
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
            chip = chip_button(reason)
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


__all__ = ["BUTTON_PX", "REASONS", "OptionRow", "chip_button", "facts_html", "plain", "toggle_button"]
