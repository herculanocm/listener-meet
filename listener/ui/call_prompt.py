"""F5.7 (L3, U-094): o cartão do aviso de reunião sem gravação, no canto inferior direito da tela principal. Só a janela: quem
decide quando ele aparece e o que cada resposta faz é a cola (`ui/call_watch.py`), sobre as regras (`ui/call_rules.py`).

- Janela como o aviso da F5.3 (A-14): `Tool | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus` com
  `WA_ShowWithoutActivating`; nenhum filho pega foco, o clique no corpo não faz nada e o teclado nunca chega aqui (o Espaço de
  falar e o chat do Teams seguem no Teams). A cola tira a janela da captura de tela (A-13) antes de mostrá-la.
- 360 x ~136 px lógicos, no sistema de design da F5.4 e nos dois temas, sem folha de estilo: o cartão `card` com a borda
  `stroke.strong` e raio 8, a faixa de 4 px à esquerda em `info` e o ícone `speaker` (nunca vermelho nem o disco de gravar).
- Textos: o cabeçalho em legenda semibold, com o ✕ de 32 x 32 (vale "Agora não"); a pergunta em `body_strong`; o apoio em
  legenda; o lembrete diz que a reunião segue sem gravar e há quanto tempo começou. O app vai pelo nome (`label` das regras),
  nunca pelo título da janela (D-053).
- Botões: "Gravar…", o primário com o ícone `record`, e "Agora não", da mesma largura e com 32 px de altura.
- Fica 20 s (fixos, fora do `ui.toast_seconds`); com o mouse em cima, não some, e ao sair recomeça a contar. Sumir sem clique
  emite `expired`. Um diálogo modal à vista o esconde com o prazo parado (`pause`) e, fechado, ele volta com o que restava
  (`resume`, R-08). Fechado (`finish`), nada mais religa o prazo, nem o `leaveEvent` que o `hide` manda.
- Leitor de tela: nomes acessíveis e, ao aparecer, o evento de alerta (`announce`); [chute] o Narrador o lê sem foco, o dono
  confere no Windows.
- A janela fica viva no Python até o Qt apagá-la (`keep_until_destroyed`): a resposta fecha o cartão dentro do clique dele, e
  sem o registro a última referência sumiria ali, apagando o botão no meio do próprio evento."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..log import get_logger
from .common import keep_until_destroyed
from .components import IconButton
from .icons import set_icon
from .theme import PRIMARY, Tone, note, qcolor, role, set_kind, surface_tokens
from .toast import ACCENT_W, CLOSE_PX, ICON_PX, MARGIN, TOAST_FLAGS, TOAST_WIDTH
from .widget_parts import shortcut_text

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent, QMouseEvent, QPaintEvent

log = get_logger("ui.call_prompt")

PROMPT_MS = 20_000               # 20 s na tela, fixos (U-094)
BUTTON_H = 32
HEADER = "Reunião detectada · listener-meet"
DISMISS_TIP = "Não avisa de novo até esta chamada acabar"
CLOSE_TEXT = "Fechar o aviso (vale Agora não)"


def since_text(seconds: int) -> str:
    """"8 s" ou, de 2 min em diante, "2 min"."""
    seconds = max(0, int(seconds))
    return f"{seconds} s" if seconds < 120 else f"{seconds // 60} min"


def record_tip(label: str, combo: str) -> str:
    """A dica do "Gravar…", com o atalho de iniciar como o Windows o escreve (sem atalho, sem os parênteses)."""
    keys = f" ({shortcut_text(combo)})" if combo else ""
    return f"Abre o \"Antes de gravar\" com o {label} como fonte{keys}"


def prompt_texts(label: str, seconds: int, reminder: bool) -> tuple[str, str]:
    """A pergunta e o apoio do aviso (ou do lembrete)."""
    if reminder:
        return (f"Ainda sem gravar: a reunião do {label} segue com áudio.", f"Começou há {since_text(max(seconds, 120))}.")
    return f"Gravar a reunião do {label}?", f"Áudio de chamada há {since_text(seconds)}. Nada está sendo gravado."


class CallPrompt(QWidget):
    """O cartão. `record_clicked` no "Gravar…", `dismissed` no "Agora não" e no ✕, `expired` quando some sem clique."""
    record_clicked = Signal()
    dismissed = Signal()
    expired = Signal()

    def __init__(self, label: str, seconds: int, reminder: bool = False, record_tip: str = "",
                 duration_ms: int = PROMPT_MS) -> None:
        super().__init__(None, TOAST_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)     # os cantos arredondados do `paintEvent`
        self.setObjectName("callPrompt")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(TOAST_WIDTH)
        keep_until_destroyed(self)                   # R-06: viva no Python até o `deleteLater` (fechada no próprio clique)
        self.duration_ms = duration_ms
        self.hovered = False
        self.paused = False
        self._done = False
        self._left = duration_ms                     # o prazo que restava ao pausar (R-08)
        self.question_text, self.detail_text = prompt_texts(label, seconds, reminder)
        self._build(record_tip)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self.setAccessibleName(f"{HEADER}. {self.question_text} {self.detail_text}")
        self.setAccessibleDescription("Aviso sem foco: use o mouse ou a bandeja (o atalho de iniciar abre o \"Antes de gravar\")")
        height = self.heightForWidth(TOAST_WIDTH)                         # a altura para a largura de verdade (como o aviso)
        self.resize(TOAST_WIDTH, height if height > 0 else self.sizeHint().height())

    def _build(self, record_tip: str) -> None:
        self.icon = self._label("", "callIcon")
        self.icon.setFixedSize(ICON_PX, ICON_PX)
        set_icon(self.icon, "speaker", Tone.INFO, ICON_PX)
        self.title = self._label(HEADER, "callTitle")
        role(self.title, "caption")
        font = self.title.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.question = self._label(self.question_text, "callQuestion")
        role(self.question, "body_strong")
        self.detail = self._label(self.detail_text, "callDetail")
        note(self.detail)
        self.close_button = IconButton("close", CLOSE_TEXT, Tone.SECONDARY)
        self.close_button.setFixedSize(CLOSE_PX, CLOSE_PX)
        self.record_button = self._button("Gravar…", record_tip)
        set_icon(self.record_button, "record", Tone.ON_ACCENT)
        set_kind(self.record_button, PRIMARY)
        self.later_button = self._button("Agora não", DISMISS_TIP)
        for button in (self.close_button, self.record_button, self.later_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self.dismissed)
        self.later_button.clicked.connect(self.dismissed)
        self.record_button.clicked.connect(self.record_clicked)
        self._lay_out()

    def _lay_out(self) -> None:
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(self.icon)
        head.addWidget(self.title, 1)
        head.addWidget(self.close_button)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 8, 10, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self.record_button, 1)                       # os dois na mesma largura
        buttons.addWidget(self.later_button, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(ACCENT_W + 14, 4, 4, 12)
        lay.setSpacing(2)
        lay.addLayout(head)
        lay.addWidget(self.question)
        lay.addWidget(self.detail)
        lay.addLayout(buttons)

    @staticmethod
    def _label(text: str, name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)                   # o nome do app vem do sistema: nunca vira HTML
        label.setWordWrap(name in ("callQuestion", "callDetail"))
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return label

    @staticmethod
    def _button(text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setAccessibleName(text.rstrip("…"))
        button.setToolTip(tip)
        button.setFixedHeight(BUTTON_H)
        return button

    # ---------------------------------------------------------------- tempo, posição e leitor de tela
    def start(self) -> None:
        """Conta os 20 s (ao aparecer e ao sair o mouse); com o mouse em cima, pausado ou fechado, não."""
        if not (self.hovered or self.paused or self._done):
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

    def pause(self) -> bool:
        """R-08: um diálogo modal abriu (o cartão não receberia o clique): some com o prazo parado. True se escondeu."""
        if self._done or self.paused or not self.isVisible():
            return False
        left = self._timer.remainingTime()
        self._left = left if left > 0 else self.duration_ms     # com o mouse em cima, o prazo estava parado: volta inteiro
        self.paused = True
        self._timer.stop()
        self.hide()
        return True

    def resume(self) -> bool:
        """O diálogo fechou: o cartão volta com o prazo que restava (não é um aviso novo). True se voltou."""
        if self._done or not self.paused:
            return False
        self.paused = False
        self.show()
        self._timer.start(self._left)
        return True

    def place(self) -> None:
        """No canto inferior direito da área livre da tela principal, a 16 px das bordas."""
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.x() + g.width() - self.width() - MARGIN, g.y() + g.height() - self.height() - MARGIN)

    def announce(self) -> bool:
        """O alerta para o leitor de tela, sem foco; False quando não há leitor ativo (ou o evento falhou)."""
        try:
            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))
        except Exception as e:
            log.debug("aviso de reunião: o alerta do leitor de tela falhou (%s)", type(e).__name__)
            return False
        return QAccessible.isActive()

    def _on_timeout(self) -> None:
        self.expired.emit()

    # ---------------------------------------------------------------- pintura e mouse
    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """O cartão do tema com a borda forte (flutua sobre qualquer janela) e a faixa em `info`."""
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcolor(t.stroke_strong), 1))
        p.setBrush(qcolor(t.card))
        radius = t.metrics.radius_card
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(t.tone(Tone.INFO)))
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


__all__ = ["HEADER", "PROMPT_MS", "CallPrompt", "prompt_texts", "record_tip", "since_text"]
