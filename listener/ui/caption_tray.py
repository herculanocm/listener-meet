"""F5.17 (U-140, U-142, U-145, U-146): a bandeja de legendas, a legenda ao vivo como chat num painel próprio, fora do
widget de gravação, que flutua por cima das janelas como ele. Na tela se chama "Legendas" ("bandeja" já é o ícone do
relógio). O chat mora em `ui/caption_chat.py`.

- Janela: de ferramenta, sem moldura, sempre por cima e sem ativar (`WindowDoesNotAcceptFocus`, `WA_ShowWithoutActivating`,
  como os avisos): o foco fica no app de reunião. `WA_AlwaysShowToolTips`, porque o Qt só mostra a dica de janela inativa
  com ele. Fora da captura de tela (U-145, A-13 da F5.3), de novo a cada `WinIdChange`; as flags nunca mudam em uso.
- Cabeçalho: o ícone e o selo do ao vivo (os do widget), "Legendas" (some abaixo de 340 px) e três botões só de ícone, de 32
  x 32: "Abrir a janela Ao vivo", "Minimizar" ou "Restaurar" e "Fechar". Arrasta pelo cabeçalho e muda de tamanho pelas
  bordas, à mão (`ui/caption_geometry.py`), dentro do cartão; recolhida, só a largura muda.
- Recolhida (U-142): o cabeçalho e a última legenda numa linha (o clique restaura). Fechada ou recolhida, não lê o banco: ao
  abrir, uma leitura por `after_id` traz o que faltou; recolhida, a linha vem da própria legenda.
- Encosto (U-146): `anchor` dá o retângulo do widget; encostada, `follow` a põe pelo lado e pela borda direita dele (o host
  chama quando o widget anda ou cresce), e a borda que toca o widget fica no lugar ao recolher e ao restaurar."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config.defaults import SPECS
from ..log import get_logger
from .caption_chat import WAITING, CaptionChat
from .caption_geometry import (DEFAULT_SIZE, MIN_H, MIN_W, Dock, Edge, Rect, anchored, default_dock, dock_rect, dock_text, docked,
                               edges_at, fit_dock, parse_dock, parse_rect, rect_text, resized)
from .common import clamp_to_screens
from .components import IconButton
from .icons import set_icon
from .live_text import MarkCaption
from .theme import Tone, note, qcolor, role, surface_tokens, tone
from .widget_parts import ICON_PX, LIVE_BADGES, CaptionLine, _screen_rects, caption_text, icon_label

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.captions")
FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
         | Qt.WindowType.WindowDoesNotAcceptFocus)
BUTTON_PX = 32                    # como o fechar do aviso (U-068)
MARGINS = (10, 8, 10, 10)         # as bordas que redimensionam (6 px) ficam dentro destas margens
SPACING = 6
TITLE_MIN_W = 340                 # abaixo disto o título sai (o selo e os botões ficam)
END_TEXT = {"stopping": "encerrando", "stopped": "encerrado"}
LINE_TIP = "Clique para abrir o chat"
CURSORS = {Edge.LEFT: Qt.CursorShape.SizeHorCursor, Edge.RIGHT: Qt.CursorShape.SizeHorCursor,
           Edge.TOP: Qt.CursorShape.SizeVerCursor, Edge.BOTTOM: Qt.CursorShape.SizeVerCursor,
           Edge.LEFT | Edge.TOP: Qt.CursorShape.SizeFDiagCursor, Edge.RIGHT | Edge.BOTTOM: Qt.CursorShape.SizeFDiagCursor,
           Edge.RIGHT | Edge.TOP: Qt.CursorShape.SizeBDiagCursor, Edge.LEFT | Edge.BOTTOM: Qt.CursorShape.SizeBDiagCursor}


def area_of(rect: Rect) -> Rect:
    """A área livre da tela onde está o centro de `rect` (a principal, fora de todas)."""
    x, y, w, h = rect
    screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2)) or QGuiApplication.primaryScreen()
    g = screen.availableGeometry()
    return g.x(), g.y(), g.width(), g.height()


class CaptionTray(QWidget):
    closed_by_user = Signal()             # "Fechar": escondida até o botão "Legendas" do widget
    open_live_requested = Signal()        # "Abrir a janela Ao vivo"

    def __init__(self, store: Store, meeting_id: int, *, parent: QWidget | None = None, platform: Platform | None = None,
                 anchor: Callable[[], Rect | None] | None = None, on_mark: MarkCaption | None = None,
                 on_notice: Callable[[str], Any] | None = None, marked: set[tuple[Any, ...]] | None = None,
                 speakers: dict[str, int] | None = None) -> None:
        super().__init__(parent, FLAGS)
        for attr in (Qt.WidgetAttribute.WA_TranslucentBackground, Qt.WidgetAttribute.WA_ShowWithoutActivating,
                     Qt.WidgetAttribute.WA_AlwaysShowToolTips):
            self.setAttribute(attr, True)
        self.setObjectName("captiontray")
        self.setMouseTracking(True)                       # o cursor das bordas
        self.store, self.platform, self.anchor = store, platform, anchor
        meeting = store.get_meeting(meeting_id) or {}
        self.setWindowTitle(f"Legendas — {meeting.get('title') or f'reunião {meeting_id}'}")
        self.collapsed = self.stale = False
        self.full_height = DEFAULT_SIZE[1]                # a da aberta (a recolhida e a encolhida sem espaço não a mudam)
        self.dock: Dock | None = None
        self.excluded: bool | None = None
        self._press: tuple[Edge, QPoint, Rect] | None = None     # (bordas, ou nenhuma no arrasto; ponto global; de onde partiu)
        self.chat = CaptionChat(store, meeting_id, platform=platform, on_mark=on_mark, on_notice=on_notice, marked=marked,
                                speakers=speakers)
        self._build()
        self.setMinimumWidth(MIN_W)
        self.resize(*DEFAULT_SIZE)
        self.exclude_from_capture()

    def _build(self) -> None:
        self.live_icon = icon_label("live", Tone.NEUTRAL)
        self.title = QLabel("Legendas")
        role(self.title, "body_strong")
        self.badge = QLabel("")
        note(self.badge, Tone.NEUTRAL)
        self.btn_open = self._button("document", "Abrir a janela Ao vivo", "Abrir a janela Ao vivo: o resumo parcial e as marcações")
        self.btn_open.clicked.connect(self.open_live_requested)
        self.btn_min = self._button("chevron-down", "Minimizar", "")
        self.btn_min.clicked.connect(self.toggle_collapsed)
        self.btn_close = self._button("close", "Fechar", "Fechar: o botão Legendas do widget mostra de novo")
        self.btn_close.clicked.connect(self.close_by_user)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACING)
        for w in (self.live_icon, self.title, self.badge):
            head.addWidget(w)
        head.addStretch(1)
        for b in (self.btn_open, self.btn_min, self.btn_close):
            head.addWidget(b)
        self.line = CaptionLine()
        note(self.line, Tone.TEXT)
        self.line.setFixedHeight(max(20, self.line.fontMetrics().height() + 4))
        self.line.set_full_text(WAITING, LINE_TIP)
        self.line.clicked.connect(self.restore)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*MARGINS)
        lay.setSpacing(SPACING)
        lay.addLayout(head)
        lay.addWidget(self.chat, 1)
        lay.addWidget(self.line)
        self._show_collapsed(False)

    @staticmethod
    def _button(icon: str, name: str, tip: str) -> IconButton:
        b = IconButton(icon, name)
        b.setFixedSize(BUTTON_PX, BUTTON_PX)
        if tip:
            b.setToolTip(tip)
        return b

    # ---------------------------------------------------------------- legendas e estado
    def on_caption(self, msg: object = None) -> None:
        """Uma legenda nova (o filho já gravou antes de emitir): fechada ou recolhida, só a linha e a marca do que falta."""
        if isinstance(msg, Mapping) and msg.get("text"):
            self._set_line(msg)
        if self.isHidden() or self.collapsed:
            self.stale = True
            return
        self.load_new()

    def load_new(self) -> None:
        self.stale = False
        try:
            newest = self.chat.load_new()
        except Exception as e:                           # o banco fechou na saída: a bandeja fica como está
            log.debug("bandeja de legendas: legendas não lidas: %s", e)
            return
        if newest is not None:
            self._set_line(newest)

    def _set_line(self, row: Mapping[str, Any]) -> None:
        """A última legenda na linha da recolhida. A dica não repete o texto (D-095): a dica é uma janela do Qt fora da
        exclusão da captura, e mostraria a legenda na tela compartilhada (U-145)."""
        self.line.set_full_text(caption_text(self.chat.label_of(row), str(row.get("text") or "")), LINE_TIP)

    def refresh_mark(self, key: tuple[Any, ...]) -> None:
        self.chat.refresh_mark(key)

    def set_live_state(self, state: str) -> None:
        badge = LIVE_BADGES.get(state)
        text, value = badge if badge is not None else (END_TEXT.get(state, ""), Tone.SECONDARY)
        self.badge.setText(text)
        tone(self.badge, value)
        set_icon(self.live_icon, "live", value, ICON_PX)

    def set_finished(self) -> None:
        """Parar ou Cancelar: sem "Criar marcador" até a bandeja fechar com o widget."""
        self.chat.finished = True

    # ---------------------------------------------------------------- recolher, fechar, mostrar
    def _show_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.chat.setVisible(not collapsed)
        self.line.setVisible(collapsed)
        name, icon = ("Restaurar", "chevron-up") if collapsed else ("Minimizar", "chevron-down")
        self.btn_min.setText(name)
        self.btn_min.setAccessibleName(name)
        self.btn_min.setToolTip("Restaurar o chat" if collapsed else "Minimizar: só a última legenda")
        set_icon(self.btn_min, icon, Tone.TEXT, 20)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        before = self._rect()                            # antes: o chat que reaparece já estica a janela até o mínimo dele
        self._show_collapsed(collapsed)
        self._apply_height(before)
        if not collapsed and self.stale and not self.isHidden():
            self.load_new()
        with suppress(Exception):
            self.store.set_setting("ui.caption_tray_collapsed", "1" if collapsed else "0")
        log.info("bandeja de legendas %s", "recolhida" if collapsed else "restaurada")

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def restore(self) -> None:
        self.set_collapsed(False)

    def close_by_user(self) -> None:
        self.hide()
        self.save_geometry()
        log.info("bandeja de legendas fechada")
        self.closed_by_user.emit()

    def show_again(self) -> None:
        """O botão "Legendas" do widget: de volta, encostada onde estava, com o que chegou enquanto estava fechada."""
        self.follow()
        self.show()
        if self.stale and not self.collapsed:
            self.load_new()

    # ---------------------------------------------------------------- geometria
    def _rect(self) -> Rect:
        g = self.geometry()
        return g.x(), g.y(), g.width(), g.height()

    def collapsed_height(self) -> int:
        head = max(BUTTON_PX, self.title.sizeHint().height(), self.badge.sizeHint().height())
        return MARGINS[1] + head + SPACING + self.line.minimumHeight() + MARGINS[3]

    def target_height(self) -> int:
        return self.collapsed_height() if self.collapsed else self.full_height

    def follow(self) -> None:
        """Encostada: a posição pelo widget (arrastado, crescido com um alerta) e pela altura de agora; sem espaço do lado
        guardado, do outro."""
        widget = self.anchor() if self.anchor is not None else None
        if self.dock is None or widget is None:
            return
        area, height = area_of(widget), self.target_height()
        need = height if self.collapsed else MIN_H
        r = dock_rect(widget, (self.width(), height), fit_dock(self.dock, widget, area, need), area, min_h=need)
        if r != self._rect():
            self.setGeometry(*r)

    def _apply_height(self, before: Rect) -> None:
        if self.dock is not None and self.anchor is not None and self.anchor() is not None:
            self.follow()
            return
        height = self.target_height()
        self.setGeometry(*anchored(before, height, None, area_of(before), min_h=height if self.collapsed else MIN_H))

    def restore_geometry(self) -> None:
        """A posição, o tamanho, o encosto e o recolhido da última gravação. Solta e fora dos monitores, ou sem nada salvo,
        encostada no widget (sem ele, no canto da tela)."""
        saved = parse_rect(self.store.get_setting("ui.caption_tray_geometry"))
        w, self.full_height = (max(MIN_W, saved[2]), max(MIN_H, saved[3])) if saved else DEFAULT_SIZE
        self.dock = parse_dock(self.store.get_setting("ui.caption_tray_dock"))
        self._show_collapsed(bool(self.store.get(SPECS["ui.caption_tray_collapsed"])))
        height = self.target_height()
        if self.dock is None and saved is not None:
            y = saved[1] + saved[3] - height if self.collapsed else saved[1]      # recolhida, pela borda de baixo
            pos = clamp_to_screens(saved[0], y, w, height, _screen_rects())
            if pos is not None:
                self.setGeometry(pos[0], pos[1], w, height)
                return
        widget = self.anchor() if self.anchor is not None else None
        if widget is None:
            ax, ay, aw, ah = area_of((0, 0, 1, 1))
            self.setGeometry(ax + aw - w - 16, ay + ah - height - 16, w, height)
            return
        if self.dock is None:
            self.dock = default_dock(widget, area_of(widget))
        self.resize(w, height)
        self.follow()

    def save_geometry(self) -> None:
        x, y, w, h = self._rect()
        if self.collapsed:
            y = y + h - self.full_height                  # a aberta, pela borda de baixo
        with suppress(Exception):                        # banco já fechado na saída: a posição anterior fica
            self.store.set_setting("ui.caption_tray_geometry", rect_text((x, y, w, self.full_height)))
            self.store.set_setting("ui.caption_tray_dock", dock_text(self.dock))

    def _settled(self, resizing: bool) -> None:
        """Soltou o arrasto ou a borda: a altura da aberta, o encosto (perto do widget, encosta) e a posição guardada."""
        if resizing and not self.collapsed:
            self.full_height = self.height()
        widget = self.anchor() if self.anchor is not None else None
        self.dock = docked(widget, self._rect()) if widget is not None else None
        self.follow()
        self.save_geometry()

    # ---------------------------------------------------------------- mouse
    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        p = event.position().toPoint()
        self._press = (edges_at(p.x(), p.y(), (self.width(), self.height()), self.collapsed), event.globalPosition().toPoint(),
                       self._rect())
        event.accept()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None or not event.buttons() & Qt.MouseButton.LeftButton:
            p = event.position().toPoint()
            shape = CURSORS.get(edges_at(p.x(), p.y(), (self.width(), self.height()), self.collapsed))
            if shape is None:
                self.unsetCursor()
            else:
                self.setCursor(shape)
            return
        edges, start, rect = self._press
        d = event.globalPosition().toPoint() - start
        if edges:
            minimum = (MIN_W, self.collapsed_height() if self.collapsed else MIN_H)
            self.setGeometry(*resized(rect, edges, d.x(), d.y(), minimum))
        else:
            self.move(rect[0] + d.x(), rect[1] + d.y())

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        press, self._press = self._press, None
        if press is not None and press[1] != event.globalPosition().toPoint():
            self._settled(bool(press[0]))

    @override
    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.unsetCursor()

    # ---------------------------------------------------------------- janela
    def exclude_from_capture(self) -> None:
        """U-145: fora da captura de tela; o "não" vai ao log uma vez por bandeja."""
        if self.platform is None:
            return
        try:
            ok = bool(self.platform.exclude_from_capture(int(self.winId())))
        except Exception as e:
            log.debug("exclusão da captura de tela: %s", e)
            ok = False
        if not ok and self.excluded is not False:
            log.warning("bandeja de legendas: a janela não saiu da captura de tela (pede o Windows 10 2004 ou mais novo)")
        self.excluded = ok

    @override
    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WinIdChange and self.excluded is not None:
            QTimer.singleShot(0, self, self.exclude_from_capture)   # a janela nativa foi refeita: a exclusão também
        return super().event(event)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.title.setVisible(self.width() >= TITLE_MIN_W)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcolor(t.card, 1 / 255))   # o canto quase transparente não deixa o clique passar (as bordas)
        p.setPen(QPen(qcolor(t.stroke), 1))
        p.setBrush(qcolor(t.card))
        radius = t.metrics.radius_card
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.end()


__all__ = ["CaptionTray", "area_of"]
