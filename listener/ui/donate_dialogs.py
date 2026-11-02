"""F5.12 L2 (U-123 a U-125): as janelas do apoio, no sistema de design da F5.4 e nos dois temas, sem cor nova (CP7).

- `SupportSection`: o coração, "Apoie o projeto", o texto da spec e os dois caminhos ("Doar pelo Pix…" e "Doar pelo
  PayPal…"). Mora no Sobre, na página Sobre das Configurações e no diálogo pequeno (`SupportDialog`), que é o "Apoiar o
  projeto…" da bandeja e o link do rodapé do Histórico; ali o Pix é o primário (pergunta 6).
- `PayDialog`: o Pix (U-124: a chave com "Copiar chave", o valor sugerido e os três passos) ou o PayPal (U-125: o e-mail com
  "Copiar e-mail", o valor sugerido e "Abrir o PayPal"), com "Pronto, doei" e "Fechar". O primário é a ação do meio de
  pagamento (copiar ou abrir): o Enter nunca informa um apoio que não houve.
- Quem copia, abre o navegador e grava é o `Support` (`ui/donate.py`); as janelas devolvem a escolha no resultado do `exec`
  e ligam os sinais a métodos (R-06)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .. import about
from .components import Card
from .donate import PIX, SUPPORT_HEAD, SUPPORT_TEXT
from .icons import set_icon
from .keys import WrapLabel
from .theme import METRICS, PRIMARY, Tone, note, primary, role, set_kind

if TYPE_CHECKING:
    from .donate import Support

PIX_RESULT, PAYPAL_RESULT, DONATED_RESULT = 2, 3, 4    # o resultado do `exec` além de Rejected (0) e Accepted (1)
CHOICE_TITLE = "Apoiar o projeto"
PIX_BUTTON, PAYPAL_BUTTON = "Doar pelo Pix…", "Doar pelo PayPal…"
DONE_BUTTON = "Pronto, doei"
HEART_PX = 20
# U-124 e U-125: o título, o rótulo do e-mail, o botão de copiar, o valor sugerido e o aviso depois de copiar
TEXTS: dict[str, dict[str, str]] = {
    "pix": {"title": "Doar pelo Pix", "key": "Chave Pix (e-mail)", "copy": "Copiar chave", "amount": about.PIX_AMOUNT,
            "copied": "Chave copiada. Agora é só colar no app do banco."},
    "paypal": {"title": "Doar pelo PayPal", "key": "E-mail do PayPal", "copy": "Copiar e-mail",
               "amount": f"{about.PAYPAL_AMOUNT} (ou {about.PIX_AMOUNT})", "copied": "E-mail copiado."},
}
PIX_STEPS = ("1. Abra o app do seu banco \u203a Pix \u203a Pagar com chave.", "2. Cole a chave.",
             f"3. Pague {about.PIX_AMOUNT}, ou o que quiser, e confirme.")
PAYPAL_OPEN = "Abrir o PayPal"
PAYPAL_NOTE = "Abre a página de enviar dinheiro do PayPal no navegador e copia o e-mail para você colar lá."
PAYPAL_OPENED = "O PayPal abriu no navegador; o e-mail já está copiado."
PAYPAL_FAILED = "O navegador não abriu. O e-mail está copiado: abra o PayPal no navegador e cole o e-mail."


def _wrap(text: str) -> QLabel:
    label = WrapLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def _fit(button: QPushButton) -> QPushButton:
    """O botão no tamanho do texto (não estica na linha)."""
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return button


class SupportSection(Card):
    """"Apoie o projeto" (U-121, U-123): o coração, o texto e os dois caminhos. `primary`: o Pix é o primário da janela (no
    diálogo pequeno; no Sobre e nas Configurações, o primário é outro)."""
    pix_clicked = Signal()
    paypal_clicked = Signal()

    def __init__(self, *, primary_pix: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(SUPPORT_HEAD, parent)
        self.heart = QLabel()
        self.heart.setFixedSize(HEART_PX, HEART_PX)
        set_icon(self.heart, "heart", Tone.ACCENT, HEART_PX)
        self.heart.setAccessibleName("Coração")
        self.text = _wrap(SUPPORT_TEXT)
        top = QHBoxLayout()
        top.setSpacing(METRICS.form_row)
        top.addWidget(self.heart, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.text, 1)
        self.pix = _fit(QPushButton(PIX_BUTTON))
        self.paypal = _fit(QPushButton(PAYPAL_BUTTON))
        if primary_pix:
            set_kind(self.pix, PRIMARY)
        self.pix.clicked.connect(self.pix_clicked)
        self.paypal.clicked.connect(self.paypal_clicked)
        buttons = QHBoxLayout()
        buttons.setSpacing(METRICS.form_row)
        buttons.addWidget(self.pix)
        buttons.addWidget(self.paypal)
        buttons.addStretch(1)
        self.content.addLayout(top)
        self.content.addLayout(buttons)


class SupportDialog(QDialog):
    """O "Apoiar o projeto…" (U-123): a seção numa janela pequena; o caminho escolhido fecha a janela (`PIX_RESULT`,
    `PAYPAL_RESULT`) e o `Support` abre o diálogo dele."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(CHOICE_TITLE)
        self.setMinimumWidth(480)
        self.section = SupportSection(primary_pix=True)
        self.section.pix_clicked.connect(self._pix)
        self.section.paypal_clicked.connect(self._paypal)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        m = METRICS.margin
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(METRICS.card_gap)
        lay.addWidget(self.section)
        lay.addWidget(buttons)

    def _pix(self) -> None:
        self.done(PIX_RESULT)

    def _paypal(self) -> None:
        self.done(PAYPAL_RESULT)


class PayDialog(QDialog):
    """"Doar pelo Pix" ou "Doar pelo PayPal" (`method`); "Pronto, doei" devolve `DONATED_RESULT` e "Fechar" (ou Esc), o
    Rejected."""

    def __init__(self, method: str, support: Support, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.method, self.support = method, support
        self.texts = TEXTS[method]
        self.setWindowTitle(self.texts["title"])
        self.setMinimumWidth(460)
        self.lay = QVBoxLayout(self)
        m = METRICS.margin
        self.lay.setContentsMargins(m, m, m, m)
        self.lay.setSpacing(METRICS.form_row)
        self._key_rows()
        self.amount = _wrap(f"Valor sugerido: {self.texts['amount']}")
        self.lay.addWidget(self.amount)
        self.open_button: QPushButton | None = None
        if method == PIX:
            self._steps()
        else:
            self._paypal_rows()
        self._buttons()

    def _key_rows(self) -> None:
        """O e-mail num campo só de leitura (dá para selecionar), o botão de copiar e o aviso depois da cópia."""
        head = QLabel(self.texts["key"])
        role(head, "body_strong")
        self.key = QLineEdit(about.DONATION_EMAIL)
        self.key.setReadOnly(True)
        self.key.setAccessibleName(self.texts["key"])
        self.key.setMinimumWidth(self.key.fontMetrics().horizontalAdvance(about.DONATION_EMAIL) + 32)
        self.copy_button = QPushButton(self.texts["copy"])
        self.copy_button.clicked.connect(self._copy)
        row = QHBoxLayout()
        row.setSpacing(METRICS.form_row)
        row.addWidget(self.key, 1)
        row.addWidget(self.copy_button)
        self.status = _wrap("")
        note(self.status, Tone.SUCCESS)
        self.status.hide()
        self.lay.addWidget(head)
        self.lay.addLayout(row)
        self.lay.addWidget(self.status)

    def _steps(self) -> None:
        """U-124: os três passos no app do banco; o primário é o "Copiar chave"."""
        head = QLabel("Como doar")
        role(head, "body_strong")
        self.steps = _wrap("\n".join(PIX_STEPS))
        self.lay.addSpacing(METRICS.form_row)
        self.lay.addWidget(head)
        self.lay.addWidget(self.steps)
        primary(self, self.copy_button)

    def _paypal_rows(self) -> None:
        """U-125: "Abrir o PayPal", o primário, com o que ele faz embaixo."""
        self.open_button = _fit(QPushButton(PAYPAL_OPEN))
        self.open_button.setToolTip(PAYPAL_NOTE)
        self.open_button.clicked.connect(self._open)
        hint = _wrap(PAYPAL_NOTE)
        note(hint)
        self.lay.addSpacing(METRICS.form_row)
        self.lay.addWidget(self.open_button)
        self.lay.addWidget(hint)
        primary(self, self.open_button)

    def _buttons(self) -> None:
        self.done_button = QPushButton(DONE_BUTTON)
        self.done_button.setToolTip("Agradece e o convite para apoiar só volta daqui a 180 dias")
        self.done_button.clicked.connect(self._donated)
        self.close_button = QPushButton("Fechar")
        self.close_button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.done_button)
        row.addWidget(self.close_button)
        self.lay.addSpacing(METRICS.card_gap)
        self.lay.addLayout(row)

    def _copy(self) -> None:
        self.support.copy_email(self.method)
        self._say(self.texts["copied"], Tone.SUCCESS)

    def _open(self) -> None:
        ok = self.support.open_paypal()
        self._say(PAYPAL_OPENED if ok else PAYPAL_FAILED, Tone.SUCCESS if ok else Tone.WARNING)

    def _say(self, text: str, value: Tone) -> None:
        self.status.setText(text)
        note(self.status, value)
        self.status.show()

    def _donated(self) -> None:
        self.done(DONATED_RESULT)


__all__ = ["CHOICE_TITLE", "DONATED_RESULT", "DONE_BUTTON", "PAYPAL_BUTTON", "PAYPAL_RESULT", "PIX_BUTTON", "PIX_RESULT", "PIX_STEPS",
           "TEXTS", "PayDialog", "SupportDialog", "SupportSection"]
