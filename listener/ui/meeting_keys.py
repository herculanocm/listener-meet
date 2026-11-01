"""Os atalhos do player na reunião aberta (F5.11 L2, U-105, A-25), fora do `ui/meeting.py` (A-35): Espaço toca e pausa, ← e →
voltam e avançam 10 s, com a reunião em foco.

A tecla chega pelo `keyPressEvent` da janela, que só recebe o que o widget com o foco não usou; e, com o foco num campo de
texto, botão, caixa, combo, tabela, lista ou nas abas, o atalho não age (A-38: nada é roubado). Desvio do A-25, pela leitura
mais segura: o `QShortcut` tomaria a tecla de todos e teria de devolvê-la a quem a usa; aqui ela nunca sai de quem a usa. Na
lista de falas, o Espaço pausa e retoma pela própria transcrição (`on_space`, P-06)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTabBar,
                               QTextEdit, QWidget)

if TYPE_CHECKING:
    from .player_bar import PlayerBar

OWN_KEYS = (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTabBar, QTextEdit)


def player_key(window: QWidget, bar: PlayerBar | None, event: QKeyEvent) -> bool:
    """True quando a tecla foi do player: Espaço (`toggle`: sem som desta reunião, toca da fala selecionada), ← (`back`) e →
    (`forward`); sem modificador e com o foco fora de quem usa a tecla."""
    if bar is None or event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier or isinstance(window.focusWidget(), OWN_KEYS):
        return False
    actions: dict[int, Callable[[], object]] = {Qt.Key.Key_Space: bar.toggle, Qt.Key.Key_Left: bar.back, Qt.Key.Key_Right: bar.forward}
    action = actions.get(event.key())                    # `Qt.Key` é `IntEnum`: o código da tecla acha o item
    if action is None:
        return False
    action()
    return True


__all__ = ["OWN_KEYS", "player_key"]
