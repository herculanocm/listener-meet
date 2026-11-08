"""F5.21 (U-164, U-166, U-168; A-26, A-28, A-31): os widgets da tela no cartão da sugestão, fora do `ui/suggest_card.py` para
ele seguir abaixo de 400 linhas. O cartão só os põe nos layouts dele; o host liga os sinais.

- A chave "Com a tela" (o monitor) e a seta do menu "Qual tela", na linha de "Nova sugestão" (A-26: o cabeçalho não comporta
  mais nada a 320 px). Ligada, os pedidos seguintes desta gravação mandam a tela; nasce desligada a cada gravação (A-31).
- A linha "com a tela · janela do Teams" no corpo, como link que rola o chat até a miniatura (U-166).
- A confirmação da primeira vez com cada provedor, em linha, sem janela e sem roubar o foco (U-168): "Mandar" e "Agora não".
Nenhum botão é padrão nem pega o foco (A-29 da F5.20); nenhuma dica leva a tela nem o título da janela (A-14, D-095)."""
from __future__ import annotations

import html

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget

from .components import IconButton
from .suggest_option import BUTTON_PX, plain
from .theme import note

TOGGLE_TIP = "Mandar a tela junto nos próximos pedidos (Ctrl+Alt+Shift+T manda só no próximo)"
PICK_TIP = "Qual tela vai junto"
LINE_TIP = "Mostrar a miniatura no chat"
CONSENT = "A imagem da tela vai para o {provider} junto com o pedido."


class ScreenRow(QObject):
    toggled = Signal(bool)                 # "Com a tela" ligada ou desligada pelo clique
    pick_requested = Signal()              # a seta: o host abre o menu dos monitores
    consent_answered = Signal(bool)        # "Mandar" (True) ou "Agora não" (False)
    line_clicked = Signal()                # a linha "com a tela · …": o chat rola até a miniatura

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.toggle = IconButton("screen", "Com a tela")
        self.toggle.setFixedSize(BUTTON_PX, BUTTON_PX)
        self.toggle.setCheckable(True)
        self.toggle.setToolTip(TOGGLE_TIP)
        plain(self.toggle).clicked.connect(self._toggled)
        self.pick = QToolButton()
        self.pick.setArrowType(Qt.ArrowType.DownArrow)
        self.pick.setAccessibleName("Qual tela")
        self.pick.setToolTip(PICK_TIP)
        plain(self.pick).clicked.connect(self.pick_requested)
        self.line = QLabel("")
        self.line.setTextFormat(Qt.TextFormat.RichText)
        self.line.setToolTip(LINE_TIP)
        self.line.linkActivated.connect(self._line)
        note(self.line)
        self.line.hide()
        self.consent = QWidget()
        self.consent_text = QLabel("")
        self.consent_text.setWordWrap(True)
        note(self.consent_text)
        self.btn_send, self.btn_skip = QPushButton("Mandar"), QPushButton("Agora não")
        plain(self.btn_send).clicked.connect(self._send)
        plain(self.btn_skip).clicked.connect(self._skip)
        row = QHBoxLayout(self.consent)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.consent_text, 1)
        row.addWidget(self.btn_send)
        row.addWidget(self.btn_skip)
        self.consent.hide()

    def set_state(self, visible: bool, enabled: bool, tip: str) -> None:
        """À vista com `suggest.screen`; apagada com o motivo na dica (sem o modelo de imagem, fora do Windows)."""
        for w in (self.toggle, self.pick):
            w.setVisible(visible)
            w.setEnabled(enabled)
        self.toggle.setToolTip(TOGGLE_TIP if enabled else tip)

    def set_on(self, on: bool) -> None:
        self.toggle.setChecked(on)

    def show_line(self, text: str | None) -> None:
        self.line.setText(f'<a href="screen">{html.escape(text)}</a>' if text else "")
        self.line.setVisible(bool(text))

    def ask(self, provider: str | None) -> None:
        """A confirmação da primeira vez com `provider`; nulo a esconde."""
        self.consent_text.setText(CONSENT.format(provider=provider) if provider else "")
        self.consent.setVisible(provider is not None)

    def _toggled(self, checked: bool) -> None:
        self.toggled.emit(checked)

    def _line(self, _link: str) -> None:
        self.line_clicked.emit()

    def _send(self, *_args: object) -> None:
        self.consent.hide()
        self.consent_answered.emit(True)

    def _skip(self, *_args: object) -> None:
        self.consent.hide()
        self.consent_answered.emit(False)


__all__ = ["CONSENT", "LINE_TIP", "PICK_TIP", "TOGGLE_TIP", "ScreenRow"]
