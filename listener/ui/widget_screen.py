"""F5.27 (U-182, U-183): a linha da tela no widget de gravação, fora do `ui/widget.py` (perto do limite). O controller liga o
`state_changed` do host da tela a `RecordingWidget.set_screen_state(estado, rótulo)` e os sinais do widget
(`screen_start_requested(chave)`, `screen_switch_requested(chave)`, `screen_stop_requested()`) ao host.

- A linha mora no canto direito do cabeçalho, embaixo dos marcadores: a altura do widget não muda (A-16 da F5.4: 106 px na
  base). O ícone da tela e o texto, cortado sem alargar e inteiro na dica.
- O texto pelo estado do host: "Tela: Monitor 2" gravando (também ao começar, pausada e falhando: a tela não parou; falhando, no
  tom de aviso e com o aviso da spec na dica); "Tela: parada" com o monitor perdido (U-183); "Tela: desligada" sem tela nesta
  gravação ou parada pelo usuário ou pela queda (U-182).
- O clique abre o menu. Com tela: os outros monitores (lidos da plataforma na hora, comparados pelo número do rótulo, o único que
  o host manda) e "Parar de gravar a tela". Sem tela (desligada, parada ou perdida): os monitores, para começar no meio da
  reunião (CP8). O menu, uma janela própria, sai da captura de tela enquanto a tela é gravada (pergunta 9), como o widget.
- A linha só aparece com monitores na plataforma (fora do Windows, nada) ou com um estado do host que não seja "off".
Os sinais levam a chave do monitor; o log, só o número dele."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QSize, Qt, Signal, SignalInstance
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QVBoxLayout, QWidget

from ..log import get_logger
from .icons import set_icon
from .theme import Tone, note, tone
from .widget_parts import CLICK_SLOP_PX, ICON_PX, ElidedLabel, hbox, icon_label

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..platform.screen_types import Monitor

log = get_logger("ui.widget")

SCREEN_ON = frozenset({"starting", "recording", "paused", "failing"})     # a tela segue nesta gravação
OFF_TEXT, LOST_TEXT = "Tela: desligada", "Tela: parada"
STOP_TEXT = "Parar de gravar a tela"
FAILING_TIP = "A gravação da tela não está conseguindo ler o monitor."
LINE_TIP = "Clique para escolher o monitor ou parar a tela"
START, SWITCH, STOP = "start", "switch", "stop"
LINE_MAX_W = 150                                  # o cabeçalho tem 336 px úteis: a hora, o estado e esta coluna


def screen_text(state: str, label: str) -> tuple[str, Tone]:
    """O texto e o tom da linha pelo estado do host."""
    if state in SCREEN_ON:
        return f"Tela: {label or 'monitor'}", Tone.WARNING if state == "failing" else Tone.SECONDARY
    if state == "lost":
        return LOST_TEXT, Tone.WARNING
    return OFF_TEXT, Tone.SECONDARY


def monitor_number(label: str) -> str:
    """"Monitor 2 · 3840 x 2160 · principal" vira "Monitor 2" (o que o host manda e o que vai ao log)."""
    return label.split(" · ", maxsplit=1)[0]


class ScreenText(ElidedLabel):
    """O texto da linha, cortado na largura (até `LINE_MAX_W`)."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    @override
    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(LINE_MAX_W, self.fontMetrics().horizontalAdvance(self.full_text) + 4), hint.height())

    @override
    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())


class ScreenLine(QWidget):
    """O ícone da tela e o texto do estado; um clique sem arrastar, no ícone ou no texto, emite `clicked` (o widget monta o
    menu); o arrasto do widget começa fora da linha."""
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state, self.label = "off", ""
        self._press: QPoint | None = None
        self.icon = icon_label("screen", Tone.SECONDARY)
        self.text = ScreenText()
        note(self.text)
        for w in (self.icon, self.text):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)    # o clique é da linha inteira
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self.set_state("off", "")

    def set_state(self, state: str, label: str) -> bool:
        """True quando mudou (o estilo só muda assim, M-12)."""
        if (state, label) == (self.state, self.label) and self.text.full_text:
            return False
        self.state, self.label = state, label
        text, value = screen_text(state, label)
        tip = f"{text}\n{FAILING_TIP}" if state == "failing" else f"{text}\n{LINE_TIP}"
        self.text.set_full_text(text, tip)
        tone(self.text, value)
        set_icon(self.icon, "screen", value, ICON_PX)
        self.setToolTip(tip)
        self.setAccessibleName(text)
        self.updateGeometry()
        return True

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        press, self._press = self._press, None
        if press is not None and (event.position().toPoint() - press).manhattanLength() < CLICK_SLOP_PX:
            self.clicked.emit()
        event.accept()


def right_column(markers_icon: QLabel, markers_label: QLabel, line: ScreenLine) -> QVBoxLayout:
    """O canto direito do cabeçalho: os marcadores em cima e a linha da tela embaixo, como o estado e o selo à esquerda."""
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    column.addStretch(1)
    marks = hbox(None, 4)
    marks.addStretch(1)
    marks.addWidget(markers_icon)
    marks.addWidget(markers_label)
    column.addLayout(marks)
    column.addWidget(line, 0, Qt.AlignmentFlag.AlignRight)
    column.addStretch(1)
    return column


class ScreenMenuMixin:
    """`set_screen_state` e o menu da tela, no `RecordingWidget` (que declara os sinais e monta a linha)."""
    platform: Platform | None
    screen_line: ScreenLine
    screen_start_requested: SignalInstance
    screen_switch_requested: SignalInstance
    screen_stop_requested: SignalInstance

    def screen_monitors(self) -> tuple[Monitor, ...]:
        """Os monitores de agora (no Windows, uma leitura barata na thread da interface, como o menu da F5.21)."""
        if self.platform is None:
            return ()
        try:
            return tuple(self.platform.monitors())
        except OSError as e:
            log.warning("widget: os monitores não foram lidos (%s)", type(e).__name__)
            return ()

    def set_screen_state(self, state: str, label: str) -> None:
        """O estado do host da tela ("off", "starting", "recording", "paused", "lost", "failing", "stopped") e o rótulo do
        monitor ("Monitor 2" ou "")."""
        line = self.screen_line
        if line.set_state(state, label) and state != "off" and line.isHidden():
            line.show()

    def screen_menu(self) -> QMenu:
        """O menu da linha: com tela, os outros monitores e "Parar de gravar a tela"; sem tela, os monitores para começar."""
        on = self.screen_line.state in SCREEN_ON
        menu = QMenu(self)                                       # type: ignore[call-overload]
        for m in self.screen_monitors():
            if on and monitor_number(m.label) == self.screen_line.label:
                continue                                         # o que já grava não volta no menu
            action = menu.addAction(m.label)
            action.setData(f"{SWITCH if on else START}:{m.key}")
        if on:
            menu.addSeparator()
            menu.addAction(STOP_TEXT).setData(f"{STOP}:")
        elif menu.isEmpty():
            menu.addAction("Nenhum monitor encontrado").setEnabled(False)
        menu.triggered.connect(self._screen_action)
        return menu

    def _open_screen_menu(self) -> None:
        menu = self.screen_menu()
        menu.aboutToHide.connect(menu.deleteLater)
        if self.platform is not None and self.screen_line.state in SCREEN_ON:
            try:                                                 # pergunta 9: o menu também fica fora do vídeo
                self.platform.exclude_from_capture(int(menu.winId()))
            except Exception as e:
                log.debug("menu da tela segue na captura (%s)", type(e).__name__)
        line = self.screen_line
        menu.popup(line.mapToGlobal(line.rect().bottomLeft()))

    def _screen_action(self, action: QAction) -> None:
        kind, _sep, key = str(action.data() or "").partition(":")
        if kind == STOP:
            log.info("widget: parar de gravar a tela")
            self.screen_stop_requested.emit()
            return
        if kind not in (START, SWITCH) or not key:
            return
        label = next((m.label for m in self.screen_monitors() if m.key == key), "")
        log.info("widget: %s a tela no %s", "trocar" if kind == SWITCH else "começar", monitor_number(label) or "monitor")
        (self.screen_switch_requested if kind == SWITCH else self.screen_start_requested).emit(key)


__all__ = ["FAILING_TIP", "LOST_TEXT", "OFF_TEXT", "SCREEN_ON", "STOP_TEXT", "ScreenLine", "ScreenMenuMixin", "monitor_number",
           "right_column", "screen_text"]
