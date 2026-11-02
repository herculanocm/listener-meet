"""F5.12 L2 (U-126): o cartão do convite para apoiar, no canto inferior direito como o aviso de reunião sem gravação da F5.7
(`ui/call_prompt.py`). Só a janela: quem decide quando ele aparece e o que cada resposta faz é a cola
(`ui/donate_invite.py`).

- Janela como o aviso da F5.3 (A-14): `Tool | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus` com
  `WA_ShowWithoutActivating`; nenhum filho pega foco e o teclado nunca chega aqui (o link "Já doei" também não). A cola o
  tira da captura de tela antes de mostrá-lo (A-13).
- 360 px de largura, no sistema de design da F5.4 e nos dois temas, sem folha de estilo: o cartão `card` com a borda
  `stroke.strong` e raio 8, a faixa de 4 px e o coração em `accent` (sem cor nova, CP7; nunca o vermelho da gravação).
- Textos: o cabeçalho em legenda semibold, com o ✕ de 32 x 32 (vale "Agora não"); o título com os números do banco em
  `body_strong`; a mensagem da spec em legenda (`invite_texts`, `ui/donate.py`). Botões de 32 px: "Doar R$ 0,99 pelo Pix"
  (o primário, a linha inteira: o Pix é o principal, pergunta 6), "Doar US$ 0,20 pelo PayPal" e "Agora não" dividindo a
  linha de baixo; embaixo, à direita, o link "Já doei".
- Some sozinho em 2 min (`expired`); com o mouse em cima, não some, e ao sair recomeça a contar. Fechado (`finish`), nada
  mais religa o prazo. Fica vivo no Python até o Qt apagá-lo (`keep_until_destroyed`): a resposta fecha o cartão dentro do
  clique dele."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import about
from ..log import get_logger
from .common import keep_until_destroyed
from .components import IconButton, LinkLabel
from .icons import set_icon
from .theme import PRIMARY, Tone, qcolor, role, set_kind, surface_tokens
from .toast import ACCENT_W, CLOSE_PX, ICON_PX, MARGIN, TOAST_FLAGS, TOAST_WIDTH

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent, QMouseEvent, QPaintEvent

log = get_logger("ui.donate")

CARD_MS = 120_000                # Comportamento 4: some sozinho em 2 min
BUTTON_H = 32
HEADER = f"Apoie o projeto · {about.APP_NAME}"
PIX_BUTTON = f"Doar {about.PIX_AMOUNT} pelo Pix"
PAYPAL_BUTTON = f"Doar {about.PAYPAL_AMOUNT} pelo PayPal"
LATER, DONATED_LINK = "Agora não", "Já doei"
CLOSE_TEXT = "Fechar o convite (vale Agora não)"


def _label(text: str, name: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)     # o clique no corpo não faz nada
    return label


def _button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setAccessibleName(text)
    button.setFixedHeight(BUTTON_H)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.ArrowCursor)
    return button


class InviteCard(QWidget):
    """O cartão (U-126). `pix_clicked` e `paypal_clicked` nos dois caminhos, `later` no "Agora não" e no ✕, `donated` no
    "Já doei" e `expired` quando some sem resposta."""
    pix_clicked = Signal()
    paypal_clicked = Signal()
    later = Signal()
    donated = Signal()
    expired = Signal()

    def __init__(self, title: str, message: str, duration_ms: int = CARD_MS) -> None:
        super().__init__(None, TOAST_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)     # os cantos arredondados do `paintEvent`
        self.setObjectName("donateInvite")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(TOAST_WIDTH)
        keep_until_destroyed(self)                   # R-06: viva no Python até o `deleteLater` (fechada no próprio clique)
        self.duration_ms = duration_ms
        self.hovered = self._done = False
        self._build(title, message)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self.setAccessibleName(f"{HEADER}. {title}. {message}")
        self.setAccessibleDescription("Convite sem foco: use o mouse; ele some sozinho em 2 minutos")
        height = self.heightForWidth(TOAST_WIDTH)                         # a altura para a largura de verdade (como o aviso)
        self.resize(TOAST_WIDTH, height if height > 0 else self.sizeHint().height())

    def _build(self, title: str, message: str) -> None:
        self.icon = _label("", "donateIcon")
        self.icon.setFixedSize(ICON_PX, ICON_PX)
        set_icon(self.icon, "heart", Tone.ACCENT, ICON_PX)
        self.header = _label(HEADER, "donateHeader")
        role(self.header, "caption")
        font = self.header.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.header.setFont(font)
        self.title = _label(title, "donateTitle", wrap=True)
        role(self.title, "body_strong")
        self.message = _label(message, "donateMessage", wrap=True)
        role(self.message, "caption")
        self.close_button = IconButton("close", CLOSE_TEXT, Tone.SECONDARY)
        self.close_button.setFixedSize(CLOSE_PX, CLOSE_PX)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.pix_button, self.paypal_button, self.later_button = _button(PIX_BUTTON), _button(PAYPAL_BUTTON), _button(LATER)
        set_kind(self.pix_button, PRIMARY)                                 # o Pix é o principal (pergunta 6)
        self.donated_link = LinkLabel(DONATED_LINK, keyboard=False, font_role="caption")
        self.close_button.clicked.connect(self.later)
        self.later_button.clicked.connect(self.later)
        self.pix_button.clicked.connect(self.pix_clicked)
        self.paypal_button.clicked.connect(self.paypal_clicked)
        self.donated_link.clicked.connect(self.donated)
        self._lay_out()

    def _lay_out(self) -> None:
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(self.icon)
        head.addWidget(self.header, 1)
        head.addWidget(self.close_button)
        first = QHBoxLayout()
        first.setContentsMargins(0, 8, 10, 0)
        first.addWidget(self.pix_button, 1)
        second = QHBoxLayout()
        second.setContentsMargins(0, 0, 10, 0)
        second.setSpacing(8)
        second.addWidget(self.paypal_button, 1)                            # os dois dividem a linha (o texto inteiro)
        second.addWidget(self.later_button, 1)
        link = QHBoxLayout()
        link.setContentsMargins(0, 2, 10, 0)
        link.addStretch(1)
        link.addWidget(self.donated_link)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(ACCENT_W + 14, 4, 4, 12)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self.title)
        lay.addWidget(self.message)
        for row in (first, second, link):
            lay.addLayout(row)

    # ---------------------------------------------------------------- tempo, posição e leitor de tela
    def start(self) -> None:
        """Conta os 2 min (ao aparecer e ao sair o mouse); com o mouse em cima ou fechado, não."""
        if not (self.hovered or self._done):
            self._timer.start(self.duration_ms)

    def remaining_ms(self) -> int:
        """Instrumentação dos testes: -1 com o prazo parado."""
        return self._timer.remainingTime()

    def finish(self) -> None:
        """Some e sai no giro seguinte do laço (nunca dentro do próprio evento)."""
        self._done = True
        self._timer.stop()
        self.hide()
        self.deleteLater()

    def place(self) -> None:
        """No canto inferior direito da área livre da tela principal, a 16 px das bordas."""
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.x() + g.width() - self.width() - MARGIN, g.y() + g.height() - self.height() - MARGIN)

    def announce(self) -> bool:
        """O alerta para o leitor de tela, sem foco; False sem leitor ativo (ou se o evento falhou)."""
        try:
            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))
        except Exception as e:
            log.debug("convite de apoio: o alerta do leitor de tela falhou (%s)", type(e).__name__)
            return False
        return QAccessible.isActive()

    def _on_timeout(self) -> None:
        self.expired.emit()

    # ---------------------------------------------------------------- pintura e mouse
    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """O cartão do tema com a borda forte (flutua sobre qualquer janela) e a faixa em `accent`."""
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcolor(t.stroke_strong), 1))
        p.setBrush(qcolor(t.card))
        radius = t.metrics.radius_card
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(t.tone(Tone.ACCENT)))
        p.drawRoundedRect(QRectF(6, 8, ACCENT_W, max(8, self.height() - 16)), 2, 2)
        p.end()

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.hovered = True
        self._timer.stop()

    @override
    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hovered = False
        self.start()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()                                                 # o corpo não faz nada

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()


__all__ = ["CARD_MS", "CLOSE_TEXT", "DONATED_LINK", "HEADER", "LATER", "PAYPAL_BUTTON", "PIX_BUTTON", "InviteCard"]
