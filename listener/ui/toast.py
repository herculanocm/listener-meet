"""Aviso que some sozinho (F5.3 L3, U-048), no lugar do balão da bandeja: no canto da tela, por cima de tudo, sem roubar o
foco (A-14) e fora da captura de tela (A-13). O controlador decide o que avisa (item 12, textos em `ui/notices.py`) e chama
o gerente só na thread da interface (A-10); aqui ficam a janela (`Toast`), a posição e o gerente (`ToastManager`).

- Gerente: até 3 avisos empilhados e o resto na fila; os iguais (mesmo texto e ação) viram um só, com a contagem; cada um
  some em `ui.toast_seconds` (o mouse em cima segura); o clique fecha e faz a ação no giro seguinte do laço (ela pode abrir
  um diálogo modal); o "✕" fecha. A janela fechada sai por `deleteLater` no giro seguinte, nunca dentro do próprio evento.
- Posição: gravando, na tela do widget de gravação e, quando a pilha o cruzaria, acima dele (sem espaço acima, no canto);
  fora da gravação, no canto inferior direito do `availableGeometry()` da tela principal.
- Captura (A-13): a plataforma tira cada janela da captura ao criá-la; se não conseguiu e o app está gravando, o aviso mostra
  só o texto curto (o tipo, sem o título da reunião). Reavaliado nos avisos à vista quando a gravação começa ou termina
  (`ToastManager.refresh_privacy`, que o controlador chama) e a cada aviso que chega, novo ou igual (R-10). [chute] O Teams
  respeita a exclusão: o dono confere compartilhando.
- Janela (A-14): `Tool | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus` com
  `WA_ShowWithoutActivating`, nunca `activateWindow`; o alvo do clique vem à frente pelo controlador (`bring_to_front`). Com
  um diálogo modal aberto (o warm-up), o aviso aparece e some sem receber clique.
- Visual sóbrio, como o widget de gravação: fundo escuro, texto claro e a faixa na cor do tipo (`ui/common.py`); F5.4: as
  cores são os tokens do escuro, nos dois esquemas, até o L2 (U-068) levar o aviso ao tema."""
from __future__ import annotations

import functools
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ..log import get_logger
from .common import NOTICE_BACKGROUND, NOTICE_BORDER, NOTICE_COLORS, NOTICE_MUTED, NOTICE_TEXT
from .notices import Notice, NoticeAction, NoticeKind, Target

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent, QMouseEvent, QPaintEvent

    from ..platform.base import Platform

log = get_logger("ui.toast")

MAX_VISIBLE = 3                  # avisos na tela; os outros esperam na fila
MAX_TEXT = 300                   # caracteres na tela; o texto inteiro vai à dica
TOAST_WIDTH = 360                # px lógicos (o Qt escala no DPI da tela)
MARGIN, GAP = 16, 8              # da borda da tela e entre avisos
RADIUS, ACCENT_W = 10, 4         # cantos e a faixa do tipo
TOAST_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
               | Qt.WindowType.WindowDoesNotAcceptFocus)
STYLE = (f"QLabel {{ color: {NOTICE_TEXT}; }} QLabel#toastTitle {{ font-size: 11px; font-weight: 600; }} "
         f"QLabel#toastMuted {{ color: {NOTICE_MUTED}; font-size: 11px; }} "
         f"QToolButton {{ color: {NOTICE_MUTED}; background: transparent; border: none; font-size: 15px; padding: 0 4px; }} "
         f"QToolButton:hover {{ color: {NOTICE_TEXT}; }}")
KIND_TITLES: dict[str, str] = {NoticeKind.READY: "Pronto", NoticeKind.INFO: "Informação", NoticeKind.ERROR: "Erro"}
ACTION_HINTS: dict[str, str] = {Target.MEETING: "Clique para abrir a reunião", Target.MEETING_CHAT: "Clique para abrir o chat da reunião",
                                Target.QUEUE: "Clique para abrir a fila", Target.SETTINGS: "Clique para abrir as Configurações",
                                Target.HISTORY: "Clique para abrir o Histórico"}

type Rect = tuple[int, int, int, int]        # x, y, largura, altura (como em `common.clamp_to_screens`)


# ---------------------------------------------------------------- posição (A-14)
def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def stack_positions(sizes: list[tuple[int, int]], area: Rect, avoid: Rect | None = None) -> list[tuple[int, int]]:
    """O canto de cima à esquerda de cada aviso (`sizes` em largura e altura, o primeiro embaixo): alinhados à direita da
    `area` e empilhados do canto inferior direito para cima; se a pilha cruzaria o widget de gravação (`avoid`), ela sobe para
    cima dele (sem espaço acima, fica no canto: abaixo dele nunca caberia)."""
    if not sizes:
        return []
    ax, ay, aw, ah = area
    total = sum(h for _w, h in sizes) + GAP * (len(sizes) - 1)
    width = max(w for w, _h in sizes)
    right, bottom = ax + aw - MARGIN, ay + ah - MARGIN
    if avoid is not None and _overlaps((right - width, bottom - total, width, total), avoid) and \
            avoid[1] - GAP - total >= ay + MARGIN:
        bottom = avoid[1] - GAP
    positions: list[tuple[int, int]] = []
    y = bottom
    for w, h in sizes:
        y -= h
        positions.append((right - w, y))
        y -= GAP
    return positions


def _screen_area(anchor: Rect | None) -> Rect:
    """A área livre da tela do widget de gravação (gravando) ou a da tela principal."""
    screen = None
    if anchor is not None:
        x, y, w, h = anchor
        screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2))
    g = (screen or QGuiApplication.primaryScreen()).availableGeometry()
    return g.x(), g.y(), g.width(), g.height()


# ---------------------------------------------------------------- janela
class Toast(QWidget):
    """Uma janela de aviso: o tipo, o texto, a contagem dos iguais, o "✕" e, com ação, a dica do clique. Emite `clicked(self)`
    no clique fora do "✕" e `closed(self)` no "✕" ou no fim do prazo; o mouse em cima segura o prazo."""
    clicked = Signal(object)
    closed = Signal(object)

    def __init__(self, notice: Notice, duration_ms: int, count: int = 1) -> None:
        super().__init__(None, TOAST_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)     # os cantos arredondados do `paintEvent`
        self.setObjectName("toast")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(TOAST_WIDTH)
        self.setStyleSheet(STYLE)
        self.notice = notice
        self.duration_ms = duration_ms
        self.count = count
        self.hovered = False
        self.private = False
        self.excluded = False            # A-13: a janela saiu da captura de tela (o gerente marca ao criar)
        self.title = self._label(f"{KIND_TITLES[notice.kind]} · listener-meet", "toastTitle")
        self.badge = self._label("", "toastMuted")
        self.body = self._label("", "toastText")
        self.body.setWordWrap(True)
        self.hint = self._label(ACTION_HINTS[notice.action.target] if notice.action is not None else "", "toastMuted")
        self.hint.setVisible(notice.action is not None)
        if notice.action is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button = QToolButton()
        self.close_button.setText("✕")                          # o mesmo do "cancelar" do widget de gravação
        self.close_button.setToolTip("Fechar o aviso")
        self.close_button.setAccessibleName("Fechar o aviso")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self._finish)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.badge)
        head.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(ACCENT_W + 14, 8, 8, 10)
        lay.setSpacing(4)
        lay.addLayout(head)
        lay.addWidget(self.body)
        lay.addWidget(self.hint)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)
        self._show_count()
        self.set_private(False)

    @staticmethod
    def _label(text: str, name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)                       # um título com "<b>" fica texto (R7-07)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)   # o clique é do aviso inteiro
        return label

    def set_private(self, private: bool) -> None:
        """Gravando sem a exclusão da captura (A-13): só o texto curto, também na dica e na leitura de tela."""
        self.private = private
        text = self.notice.display_text(private)
        shown = text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"
        self.body.setText(shown)
        self.body.setToolTip(text if shown != text else "")
        self.setAccessibleName(f"Aviso: {text}")
        # a altura para a largura de verdade: o `adjustSize` de uma janela mede pela largura do `sizeHint` (~250 px) e
        # reservava uma linha que o texto não usa
        height = self.heightForWidth(TOAST_WIDTH)
        self.resize(TOAST_WIDTH, height if height > 0 else self.sizeHint().height())

    def start(self) -> None:
        """Conta o prazo de novo (ao aparecer, ao juntar um igual, ao sair o mouse); com o mouse em cima, segura."""
        if not self.hovered:
            self._timer.start(self.duration_ms)

    def stop(self) -> None:
        self._timer.stop()

    def remaining_ms(self) -> int:
        """Instrumentação dos testes: -1 com o prazo parado."""
        return self._timer.remainingTime()

    def bump(self, duration_ms: int) -> None:
        """Chegou um igual: a contagem sobe e o prazo recomeça (a privacidade o gerente já reavaliou no `show`, R-10)."""
        self.count += 1
        self.duration_ms = duration_ms
        self._show_count()
        self.start()

    def _show_count(self) -> None:
        self.badge.setText(f"{self.count}x" if self.count > 1 else "")      # "3x": o mesmo aviso chegou 3 vezes
        self.badge.setVisible(self.count > 1)

    def _finish(self) -> None:
        self.closed.emit(self)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(NOTICE_BORDER)))
        p.setBrush(QColor(NOTICE_BACKGROUND))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(NOTICE_COLORS[self.notice.kind]))
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
        event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self)
        event.accept()


# ---------------------------------------------------------------- gerente
@dataclass(slots=True)
class _Waiting:
    notice: Notice
    duration_ms: int
    count: int = 1


class ToastManager(QObject):
    """Os avisos na tela, só na thread da interface. `on_action` recebe a ação do aviso clicado; `recording` diz se o app está
    gravando (A-13); `anchor` dá o retângulo do widget de gravação, ou None fora da gravação (A-14)."""

    def __init__(self, platform: Platform, *, on_action: Callable[[NoticeAction], Any], recording: Callable[[], bool],
                 anchor: Callable[[], Rect | None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.platform = platform
        self.on_action = on_action
        self.recording = recording
        self.anchor = anchor
        self.toasts: list[Toast] = []                  # na tela, de baixo para cima
        self._waiting: deque[_Waiting] = deque()
        self._closing: list[Toast] = []
        self._closed = False
        self._exclusion_warned = False

    def waiting(self) -> list[tuple[Notice, int]]:
        """Os da fila, com a contagem (instrumentação dos testes)."""
        return [(w.notice, w.count) for w in self._waiting]

    def show(self, notice: Notice, seconds: int) -> None:
        """Mostra o aviso por `seconds`, junta-o ao igual (na tela ou na fila) ou o põe na fila com 3 na tela. Antes, confere a
        privacidade dos que estão à vista: o igual que se junta também (R-10)."""
        if self._closed:
            return
        self.refresh_privacy()
        duration_ms = max(1, int(seconds)) * 1000
        same = next((t for t in self.toasts if t.notice.key == notice.key), None)
        if same is not None:
            same.bump(duration_ms)
            return
        queued = next((w for w in self._waiting if w.notice.key == notice.key), None)
        if queued is not None:
            queued.count += 1
            queued.duration_ms = duration_ms
        elif len(self.toasts) < MAX_VISIBLE:
            self._open(notice, duration_ms, 1)
        else:
            self._waiting.append(_Waiting(notice, duration_ms))

    def _open(self, notice: Notice, duration_ms: int, count: int) -> None:
        toast = Toast(notice, duration_ms, count)
        toast.clicked.connect(self._on_clicked)
        toast.closed.connect(self._close)
        toast.excluded = self._exclude(toast)
        toast.set_private(self._private(toast))                               # antes de aparecer: nada vaza (A-13)
        self.toasts.append(toast)
        self._layout()
        toast.show()
        toast.start()

    def _private(self, toast: Toast) -> bool:
        """A-13: só o texto curto quando a janela não saiu da captura e o app está gravando."""
        return not toast.excluded and self.recording()

    def refresh_privacy(self) -> None:
        """A-13 (R-10): reavalia o texto dos avisos à vista; o controlador chama quando a gravação começa ou termina. Sem a
        exclusão da captura, um aviso que nasceu antes de gravar passa ao texto curto (o título da reunião sai da tela que
        pode estar compartilhada) e, terminada a gravação, volta ao inteiro. A altura muda junto: a pilha se refaz."""
        changed = 0
        for toast in self.toasts:
            private = self._private(toast)
            if private != toast.private:
                toast.set_private(private)
                changed += 1
        if changed:
            log.debug("aviso: %d à vista com o texto trocado pela gravação (sem a exclusão da captura)", changed)
            self._layout()

    def _exclude(self, toast: Toast) -> bool:
        """A-13: a janela fora da captura de tela; o primeiro "não" da sessão vai ao log."""
        try:
            ok = bool(self.platform.exclude_from_capture(int(toast.winId())))
        except Exception as e:
            log.debug("exclusão da captura de tela: %s", e)
            ok = False
        if not ok and not self._exclusion_warned:
            self._exclusion_warned = True
            log.warning("aviso na tela: a janela não saiu da captura de tela (pede o Windows 10 2004 ou mais novo); gravando, o "
                        "aviso mostra só o tipo")
        return ok

    def _on_clicked(self, toast: Toast) -> None:
        action = toast.notice.action
        self._close(toast)
        if action is not None:                              # depois do evento do clique: a ação pode abrir um diálogo modal
            QTimer.singleShot(0, self, functools.partial(self.on_action, action))

    def _close(self, toast: Toast) -> None:
        if toast not in self.toasts:
            return
        self.toasts.remove(toast)
        toast.stop()
        toast.hide()
        self._closing.append(toast)
        QTimer.singleShot(0, self, self._reap)               # sai no giro seguinte, fora do evento dela
        while self._waiting and len(self.toasts) < MAX_VISIBLE:
            item = self._waiting.popleft()
            self._open(item.notice, item.duration_ms, item.count)
        self._layout()

    def _reap(self) -> None:
        closing, self._closing = self._closing, []
        for toast in closing:
            toast.deleteLater()

    def _layout(self) -> None:
        if not self.toasts:
            return
        anchor = self.anchor()
        sizes = [(t.width(), t.height()) for t in self.toasts]
        for toast, (x, y) in zip(self.toasts, stack_positions(sizes, _screen_area(anchor), anchor), strict=True):
            toast.move(x, y)

    def shutdown(self) -> None:
        """A saída do app: nada mais aparece; os da tela e os da fila somem."""
        self._closed = True
        self._waiting.clear()
        for toast in self.toasts:
            toast.stop()
            toast.hide()
            self._closing.append(toast)
        self.toasts.clear()
        self._reap()


__all__ = ["ACTION_HINTS", "KIND_TITLES", "MAX_VISIBLE", "Toast", "ToastManager", "stack_positions"]
