"""Widget flutuante de gravação: sempre no topo, arrastável, com cantos arredondados; posição presa a um monitor existente
(F1.1 A9). Popup do marcador trazido para a frente mesmo com outro app em foco (A10). Estilos e dicas só mudam quando o
estado muda (M-12; `style_changes` conta as trocas de tom).
F4: selo do ao vivo ("ao vivo", "legenda atrasada", "ao vivo parou") sempre que o ao vivo foi lançado; com `live.in_widget`,
uma linha com a última legenda (`Ana — CFO: texto`, cortada com reticências e inteira na dica) cujo clique abre a janela
"Ao vivo". F5.3 L4 (U-049, U-050): o botão dos dispositivos abre o painel (desligado salvando); a linha de alerta tem um
botão genérico (rótulo e ação dados por quem a mostra, A-21): "Usar" no aparelho novo, "Dispositivos…" na saída sem sinal.
F5.4 L2, no tema (claro ou escuro, como o resto do app; sem folha de estilo):
- U-060, A-16: 360 px fixos (o `ui.widget_size` salvo é ignorado) e a altura vem do estado: 106 px na base, +24 com a linha
  da legenda e +40 com o alerta, no máximo 170 (Aceite 4); ao crescer, a borda de baixo fica onde estava e `height_changed`
  avisa quem empilha os avisos. O alerta fica numa linha embaixo, com o ícone e a cor do tipo, cortado sem alargar.
- U-061: botões só de ícone, 36 x 32, com o nome no `text()` e no `accessibleName` e o atalho na dica; Parar em vermelho;
  Cancelar afastado, na ponta. U-063: medidores com trilho e zonas (`ui/meters.py`); o fundo é um caminho em cache (5 Hz).
- U-062: o mudo é o microfone cortado, no botão ligado (preenchido em destaque, como a alternância do Windows 11); L, R e
  o ao vivo com ícone, que muda de forma com o estado (não só a cor, U-054); salvando, tudo apagado; h:mm:ss depois de 1 h."""
from __future__ import annotations

from collections.abc import Mapping
from typing import override

from PySide6.QtCore import QPoint, QRectF, Qt, Signal, SignalInstance
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..log import get_logger
from ..model import AppState, Health
from .common import HEALTH_TEXT, HEALTH_TONES, STATE_TONES, clamp_to_screens
from .components import INFO_KINDS, IconButton
from .icons import set_icon
from .meters import LevelMeter
from .theme import Tone, note, qcolor, role, surface_tokens, tone
from .widget_parts import (CHANNEL_ICONS, CHANNEL_NAMES, HEALTH_ICONS, ICON_PX, CaptionLine, ElidedLabel, MarkerPopup, caption_text,
                           channel_letter, clock_text, hbox, health_icon, icon_label, shortcut_text)

log = get_logger("ui.widget")
WIDTH = 360                                   # U-060, A-16: fixa
MARGIN_X, MARGIN_Y, ROW_GAP = 12, 7, 4
HEAD_H, METERS_H, BUTTONS_H = 36, 16, 32      # 7 + 36 + 4 + 16 + 4 + 32 + 7 = 106 (Aceite 4)
CAPTION_H, ALERT_H = 20, 36                   # +24 com a legenda e +40 com o alerta: 170 no máximo
BUTTON_SIZE = (36, 32)                        # U-061
METER_PX = 6
# selo por estado do ao vivo (`Controller.live_status`): texto e tom; os estados fora daqui escondem o selo
LIVE_BADGES: dict[str, tuple[str, Tone]] = {
    "starting": ("ao vivo", Tone.NEUTRAL), "restarting": ("ao vivo", Tone.NEUTRAL), "live": ("ao vivo", Tone.SUCCESS),
    "degraded": ("legenda atrasada", Tone.WARNING), "skipping": ("legenda atrasada", Tone.WARNING),
    "crashed": ("ao vivo parou", Tone.DANGER),
}
def _screen_rects() -> list[tuple[int, int, int, int]]:
    out = []
    for s in QGuiApplication.screens():
        g = s.availableGeometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out


class RecordingWidget(QWidget):
    pause_clicked = Signal()
    mark_clicked = Signal()
    mute_clicked = Signal()
    stop_clicked = Signal()
    cancel_clicked = Signal()
    devices_clicked = Signal()            # F5.3 (U-049): o painel dos dispositivos
    alert_clicked = Signal(str)           # F5.3 (A-21): o botão da linha de alerta, com a ação dela
    settings_requested = Signal()
    live_clicked = Signal()               # F4: clique na linha da legenda abre a janela "Ao vivo"
    moved = Signal(QPoint)
    height_changed = Signal()             # F5.4 L2 (A-16): cresceu ou encolheu, com a borda de baixo no lugar

    def __init__(self, size: tuple[int, int] | None = None, pos: QPoint | None = None, live_line: bool = False,
                 shortcuts: Mapping[str, str] | None = None):
        """`size` (o `ui.widget_size` salvo) não vale mais: 360 px fixos e a altura do estado (A-16). `shortcuts` são os
        atalhos globais por ação (`pause`, `mark`, `mute`, `stop`), para a dica dos botões (U-061)."""
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)     # os cantos arredondados do `paintEvent`
        self.setObjectName("recwidget")
        self.live_line = live_line
        self.shortcuts = {k: shortcut_text(v) for k, v in (shortcuts or {}).items() if v}
        self._drag_offset: QPoint | None = None
        self._last_status: tuple[bool, bool, bool] | None = None    # (paused, muted, saving)
        self._last_health: dict[str, str] = {}
        self._last_alert: tuple[str | None, str, str, str, str] = (None, "", "", "", "")
        self._alert_action = ""
        self._last_live: tuple[str, str] | None = None
        self._path: QPainterPath | None = None
        self._clock_len = 0
        self.style_changes = 0                                      # instrumentação dos testes (M-12)
        self._build_head()
        self._build_meters()
        self._build_buttons()
        self._build_alert()
        self._lay_out()
        self.setFixedSize(WIDTH, self.target_height())
        self.move(self._initial_pos(pos))

    # ---------------------------------------------------------------- montagem
    def _build_head(self) -> None:
        self.time_label = QLabel("00:00")
        role(self.time_label, "clock")
        self.status_label = QLabel("gravando")
        role(self.status_label, "body_strong")
        tone(self.status_label, STATE_TONES[AppState.RECORDING])
        self.live_icon = icon_label("live", Tone.NEUTRAL)
        self.live_badge = QLabel("")
        note(self.live_badge, Tone.NEUTRAL)
        self.markers_icon = icon_label("flag", Tone.SECONDARY)
        self.markers_label = QLabel("")
        note(self.markers_label)
        for w in (self.live_icon, self.live_badge, self.markers_icon, self.markers_label):
            w.hide()                                      # o ao vivo e os marcadores só aparecem quando há
        self.time_label.setFixedHeight(self._head_h())
        self._set_clock("00:00")

    def _build_meters(self) -> None:
        self.health_l = icon_label(CHANNEL_ICONS["L"], Tone.DISABLED)
        self.health_r = icon_label(CHANNEL_ICONS["R"], Tone.DISABLED)
        self.meter_l = LevelMeter(height=METER_PX)
        self.meter_r = LevelMeter(height=METER_PX)
        self.caption_line = CaptionLine()
        note(self.caption_line, Tone.TEXT)
        self.caption_line.set_full_text("aguardando a primeira legenda…", "clique para abrir a janela Ao vivo")
        self.caption_line.clicked.connect(self.live_clicked)
        self.caption_line.setFixedHeight(max(CAPTION_H, self.caption_line.fontMetrics().height() + 4))
        self.caption_line.setVisible(self.live_line)

    def _build_buttons(self) -> None:
        self.btn_pause = self._btn("pause", "Pausar", "Pausar / retomar a gravação", "pause", self.pause_clicked)
        self.btn_mark = self._btn("flag", "Marcador", "Marcador com nota", "mark", self.mark_clicked)
        self.btn_mute = self._btn("mic", "Mudo global", "Mudo global: muta o microfone no sistema", "mute", self.mute_clicked)
        self.btn_mute.setCheckable(True)
        self.btn_devices = self._btn("headphones", "Dispositivos",
                                     "Dispositivos: trocar o microfone e a saída gravada sem parar", "", self.devices_clicked)
        self.btn_stop = self._btn("stop", "Parar e salvar", "Parar e salvar", "stop", self.stop_clicked, Tone.DANGER)
        self.btn_cancel = self._btn("close", "Cancelar e descartar", "Cancelar e descartar a gravação", "", self.cancel_clicked)

    def _build_alert(self) -> None:
        self.alert_icon = icon_label("warning", Tone.WARNING)
        self.alert_label = ElidedLabel()                  # F5.3: o nome de um aparelho não alarga o widget
        self.alert_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.alert_label.setFixedHeight(max(ALERT_H, self.alert_label.fontMetrics().height() + 4))
        note(self.alert_label, Tone.WARNING)
        self.alert_btn = QPushButton("")
        self.alert_btn.setFixedHeight(BUTTONS_H)
        self.alert_btn.clicked.connect(self._emit_alert)   # método, não lambda com `self`: o ciclo pelo C++ prendia o widget
        for w in (self.alert_icon, self.alert_label, self.alert_btn):
            w.hide()

    def _lay_out(self) -> None:
        state = QVBoxLayout()
        state.setContentsMargins(0, 0, 0, 0)
        state.setSpacing(0)
        state.addStretch(1)
        state.addWidget(self.status_label)
        live = QHBoxLayout()
        live.setSpacing(4)
        live.addWidget(self.live_icon)
        live.addWidget(self.live_badge)
        live.addStretch(1)
        state.addLayout(live)
        state.addStretch(1)
        head = hbox(self.time_label, 10)
        head.addLayout(state)
        head.addStretch(1)
        head.addWidget(self.markers_icon)
        head.addWidget(self.markers_label)
        meters = hbox(None, 4)
        for icon, channel, meter in ((self.health_l, "L", self.meter_l), (self.health_r, "R", self.meter_r)):
            if meter is self.meter_r:
                meters.addSpacing(8)
            meters.addWidget(icon)
            meters.addWidget(channel_letter(channel, METERS_H))
            meters.addWidget(meter, 1)
        buttons = hbox(None, 4)
        for b in (self.btn_pause, self.btn_mark, self.btn_mute, self.btn_devices, self.btn_stop):
            buttons.addWidget(b)
        buttons.addStretch(1)                             # U-061: Cancelar longe de Parar
        buttons.addWidget(self.btn_cancel)
        alert = hbox(self.alert_icon, 6)
        alert.addWidget(self.alert_label, 1)
        alert.addWidget(self.alert_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(MARGIN_X, MARGIN_Y, MARGIN_X, MARGIN_Y)
        lay.setSpacing(ROW_GAP)
        for line in (head, meters):
            lay.addLayout(line)
        lay.addWidget(self.caption_line)
        lay.addLayout(buttons)
        lay.addLayout(alert)

    def _head_h(self) -> int:
        """A linha da hora: 36 px, ou o que a fonte pedir com o Tamanho do texto do Windows acima de 100 %."""
        stacked = self.status_label.fontMetrics().height() + self.live_badge.fontMetrics().height() + 1
        return max(HEAD_H, self.time_label.fontMetrics().height() + 4, stacked)

    def target_height(self) -> int:
        """A altura do estado (A-16): a base, mais a legenda quando há a linha e o alerta quando ele está à vista."""
        h = 2 * MARGIN_Y + self.time_label.height() + ROW_GAP + METERS_H + ROW_GAP + BUTTONS_H
        if self.live_line:
            h += ROW_GAP + self.caption_line.height()
        if not self.alert_label.isHidden():
            h += ROW_GAP + self.alert_label.height()
        return h

    def _fit_height(self) -> None:
        """A altura nova com a borda de baixo onde estava (o widget mora num canto de baixo), sem passar do topo da tela dele;
        avisa quem empilha os avisos."""
        h = self.target_height()
        if h == self.height():
            return
        top = self.y() + self.height() - h
        screen = self.screen()
        if screen is not None:
            top = max(top, screen.availableGeometry().top())
        self.setFixedSize(WIDTH, h)
        self.move(self.x(), top)
        self.height_changed.emit()

    def _initial_pos(self, pos: QPoint | None) -> QPoint:
        screens = _screen_rects()
        if pos is not None:
            clamped = clamp_to_screens(pos.x(), pos.y(), self.width(), self.height(), screens)
            if clamped is not None:
                return QPoint(*clamped)
            log.info("posição salva do widget fora dos monitores atuais; voltando ao canto padrão")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def _emit_alert(self) -> None:
        """O clique no botão da linha de alerta, com a ação dela (A-21). Conectado como método do próprio widget: um lambda
        que captura `self` fica guardado do lado C++ do PySide, num ciclo que o coletor do Python não enxerga, e o widget
        sem dono vivia até a saída, quando a derrubada do Qt o destruía fora de ordem (0xC0000409 na saída)."""
        self.alert_clicked.emit(self._alert_action)

    def _buttons(self) -> tuple[IconButton, ...]:
        return (self.btn_pause, self.btn_mark, self.btn_mute, self.btn_devices, self.btn_stop, self.btn_cancel)

    def _btn(self, icon: str, name: str, tip: str, action: str, signal: SignalInstance, value: Tone = Tone.TEXT) -> IconButton:
        b = IconButton(icon, name, value)
        b.setFixedSize(*BUTTON_SIZE)
        self._set_tip(b, tip, action)
        b.clicked.connect(signal)
        return b

    def _set_tip(self, b: IconButton, tip: str, action: str) -> None:
        """U-061: a dica com o atalho global da ação, quando há."""
        shortcut = self.shortcuts.get(action, "")
        b.setToolTip(f"{tip} ({shortcut})" if shortcut else tip)

    @staticmethod
    def _name(b: IconButton, name: str) -> None:
        b.setText(name)
        b.setAccessibleName(name)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._path = None

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        if self._path is None:                            # o caminho do fundo, refeito só quando o tamanho muda
            self._path = QPainterPath()
            radius = t.metrics.radius_card
            self._path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcolor(t.stroke), 1))
        p.setBrush(qcolor(t.card))
        p.drawPath(self._path)
        p.end()

    # ---------------------------------------------------------------- estado
    def update_state(self, elapsed: float, levels: Mapping[str, float], health: Mapping[str, str], paused: bool, muted: bool,
                     markers: int, saving: bool = False) -> None:
        self._set_clock(clock_text(elapsed))
        self.meter_l.set_rms(levels.get("L", 0.0))
        self.meter_r.set_rms(levels.get("R", 0.0))
        status = (paused, muted, saving)
        if status != self._last_status:
            self._last_status = status
            self.style_changes += 1
            self._show_status(paused, muted, saving)
        for lab, ch in ((self.health_l, "L"), (self.health_r, "R")):
            h = health.get(ch, Health.OFF)
            if self._last_health.get(ch) != h:
                self._last_health[ch] = h
                self.style_changes += 1
                set_icon(lab, health_icon(ch, h), HEALTH_TONES.get(h, Tone.DISABLED), ICON_PX)
                text = f"canal {ch} ({CHANNEL_NAMES[ch]}): {HEALTH_TEXT.get(h, h)}"
                lab.setToolTip(text)
                lab.setAccessibleName(text)
        text = str(markers) if markers else ""
        if self.markers_label.text() != text:
            self.markers_label.setText(text)
            self.markers_label.setToolTip(f"{markers} marcador(es)")
            self.markers_label.setVisible(bool(text))
            self.markers_icon.setVisible(bool(text))

    def _set_clock(self, text: str) -> None:
        """A hora com a largura do dígito mais largo ([fato, 28/10] na Segoe UI Variable "12:34" tem 55 px e "00:05" 59): o
        rótulo não muda de largura a cada segundo (nada de refazer o layout no tique nem o estado tremendo ao lado)."""
        if len(text) != self._clock_len:
            self._clock_len = len(text)
            fm = self.time_label.fontMetrics()
            widest = max("0123456789", key=fm.horizontalAdvance)
            self.time_label.setFixedWidth(fm.horizontalAdvance("".join(widest if c.isdigit() else c for c in text)) + 2)
        self.time_label.setText(text)

    def _show_status(self, paused: bool, muted: bool, saving: bool) -> None:
        """O estado escrito, o botão de pausa (pausar ou retomar) e o do mudo (o microfone cortado, U-062); salvando, tudo
        apagado."""
        text = "salvando…" if saving else ("pausado" if paused else ("gravando (mudo)" if muted else "gravando"))
        state = AppState.SAVING if saving else (AppState.PAUSED if paused else AppState.RECORDING)
        self.status_label.setText(text)
        tone(self.status_label, STATE_TONES[state])
        self._name(self.btn_pause, "Retomar" if paused else "Pausar")
        set_icon(self.btn_pause, "play" if paused else "pause", Tone.TEXT, 20)
        self.btn_mute.setChecked(muted)                   # ligado, o `windows11` o preenche em `accent`: o ícone em `on_accent`
        set_icon(self.btn_mute, "mic-off" if muted else "mic", Tone.ON_ACCENT if muted else Tone.TEXT, 20)
        self.btn_mute.setAccessibleDescription("ligado" if muted else "desligado")
        for b in self._buttons():
            b.setEnabled(not saving)

    # ---------------------------------------------------------------- ao vivo (F4)
    def set_live_state(self, state: str, tip: str = "") -> None:
        """Selo pelo estado do ao vivo (`LIVE_BADGES`); outro estado esconde. Só mexe no estilo quando muda (M-12)."""
        badge = LIVE_BADGES.get(state)
        key = (state, tip)
        if key == self._last_live:
            return
        self._last_live = key
        if badge is None:
            self.live_badge.hide()
            self.live_icon.hide()
            return
        text, value = badge
        self.style_changes += 1
        self.live_badge.setText(text)
        tone(self.live_badge, value)
        set_icon(self.live_icon, "live", value, ICON_PX)
        for w in (self.live_badge, self.live_icon):
            w.setToolTip(tip or text)
            w.show()

    def set_caption(self, label: str | None, text: str) -> None:
        """A última legenda na linha (só existe com `live.in_widget`)."""
        if not self.live_line:
            return
        line = caption_text(label, text)
        self.caption_line.set_full_text(line, f"{line}\n\n(clique para abrir a janela Ao vivo)")

    def show_alert(self, text: str | None, button: str = "", action: str = "", tip: str = "", kind: str = "warning") -> None:
        """A linha de alerta embaixo (só muda quando o alerta muda, M-12), com o ícone e a cor do tipo (`info`, `success`,
        `warning`, `error`); `button` é o rótulo do botão e `action` o que o clique emite em `alert_clicked` (F5.3, A-21);
        sem rótulo, sem botão. A altura do widget acompanha (A-16)."""
        key = (text, button, action, tip, kind)
        if key == self._last_alert:
            return
        self._last_alert = key
        self._alert_action = action
        if text:
            value, icon = INFO_KINDS.get(kind, INFO_KINDS["warning"])
            set_icon(self.alert_icon, icon, value, ICON_PX)
            tone(self.alert_label, value)
            self.alert_label.set_full_text(text, tip or text)
            self.alert_btn.setText(button)
            self.alert_btn.setToolTip(tip)
            self.alert_icon.show()
            self.alert_label.show()
            self.alert_btn.setVisible(bool(button))
        else:
            for w in (self.alert_icon, self.alert_label, self.alert_btn):
                w.hide()
        self._fit_height()

    # ---------------------------------------------------------------- arrastar
    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.pos())

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.settings_requested.emit()


__all__ = ["HEALTH_ICONS", "LIVE_BADGES", "WIDTH", "CaptionLine", "ElidedLabel", "MarkerPopup", "RecordingWidget", "caption_text",
           "clock_text", "health_icon", "shortcut_text"]
