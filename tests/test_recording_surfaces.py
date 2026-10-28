"""F5.4 L2 (U-060 a U-070; A-16 a A-19): as superfícies da gravação no tema. O widget de 360 px com a altura do estado e a
borda de baixo no lugar, os botões de ícone com o atalho na dica, o mudo e os canais com ícone, a hora em h:mm:ss e o
alerta com o tipo; o medidor com trilho e zonas; os avisos com o ícone do tipo e o fechar de 32 px; o painel Dispositivos em
duas linhas com a marca, o selo e a faixa; o "Antes de gravar" em duas colunas com a faixa das chaves ao lado do Gravar e
o `fit_to_screen`; as fontes com medidor e o selo "Reunião"; o resumo e a bandeja."""
from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor

from listener.ui import icons, theme
from listener.ui.theme import LIGHT, Tone

pytest.importorskip("pytestqt")


def near(color: QColor, expected: str, tol: int = 12) -> bool:
    e = QColor(expected)
    return all(abs(a - b) <= tol for a, b in ((color.red(), e.red()), (color.green(), e.green()), (color.blue(), e.blue())))


def _icon(w: Any) -> str:
    return str(w.property(icons.ICON_PROP))


# ---------------------------------------------------------------- widget (U-060 a U-062, A-16)
def test_the_widget_grows_up_with_the_alert_and_keeps_its_bottom(qtbot):
    from listener.ui.widget import WIDTH, RecordingWidget

    w = RecordingWidget(size=(500, 200), pos=QPoint(100, 300))          # o tamanho salvo não vale (A-16)
    qtbot.addWidget(w)
    grew: list[int] = []
    w.height_changed.connect(lambda: grew.append(w.height()))
    bottom = w.y() + w.height()
    assert (w.width(), w.height()) == (WIDTH, 106) and w.minimumSize() == w.maximumSize()
    w.show_alert("saída sem sinal: a reunião está tocando em outro app?", "Dispositivos…", "devices")
    assert w.height() == 146 and w.y() + w.height() == bottom and grew == [146]           # +40, a borda de baixo no lugar
    w.show_alert("saída sem sinal: a reunião está tocando em outro app?", "Dispositivos…", "devices")
    assert grew == [146]                                                                  # o mesmo alerta: nada muda
    w.show_alert("banco lento: 3 pedaço(s) na fila")                                      # sem botão: a mesma altura
    assert w.height() == 146 and not w.alert_btn.isVisibleTo(w) and grew == [146]
    w.show_alert(None)
    assert w.height() == 106 and w.y() + w.height() == bottom and grew == [146, 106]
    live = RecordingWidget(live_line=True)
    qtbot.addWidget(live)
    live.show_alert("x", kind="error")
    assert live.height() == 170                                                           # Aceite 4: o maior estado


def test_icon_buttons_carry_name_shortcut_and_the_cancel_stays_apart(qtbot):
    from listener.ui.widget import BUTTON_SIZE, RecordingWidget

    keys = {"pause": "ctrl+alt+shift+p", "mark": "ctrl+alt+shift+m", "mute": "ctrl+alt+shift+u", "stop": "ctrl+alt+shift+s"}
    w = RecordingWidget(shortcuts=keys)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    buttons = [w.btn_pause, w.btn_mark, w.btn_mute, w.btn_devices, w.btn_stop, w.btn_cancel]
    for b in buttons:
        assert (b.width(), b.height()) == BUTTON_SIZE and b.text() and b.accessibleName() == b.text()          # U-061
        assert b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and not b.icon().isNull()
    assert w.btn_pause.toolTip().endswith("(Ctrl+Alt+Shift+P)") and w.btn_stop.toolTip() == "Parar e salvar (Ctrl+Alt+Shift+S)"
    assert w.btn_mute.toolTip().endswith("(Ctrl+Alt+Shift+U)") and "(" not in w.btn_cancel.toolTip()
    assert _icon(w.btn_stop) == "stop:danger:20"                                          # Parar em vermelho
    gaps = [b.x() - (a.x() + a.width()) for a, b in pairwise(buttons)]
    assert gaps[-1] > 3 * max(gaps[:-1])                                                  # Cancelar afastado


def test_mute_pause_saving_and_the_channels_change_icon_not_only_color(qtbot):
    from listener.model import Health
    from listener.ui.widget import RecordingWidget

    w = RecordingWidget()
    qtbot.addWidget(w)
    ok = {"L": Health.OK, "R": Health.OK, "db": Health.OK}
    w.update_state(1.0, {}, ok, False, False, 0)
    assert (_icon(w.health_l), _icon(w.health_r)) == ("mic:success:16", "speaker:success:16")
    assert _icon(w.btn_mute) == "mic:text:20" and not w.btn_mute.isChecked()
    w.update_state(2.0, {}, {**ok, "L": Health.MUTED}, False, True, 0)
    assert _icon(w.btn_mute) == "mic-off:on_accent:20" and w.btn_mute.isChecked()          # U-062: o microfone cortado
    assert _icon(w.health_l) == "mic-off:neutral:16" and w.status_label.text() == "gravando (mudo)"
    assert "mudo" in w.health_l.toolTip() and w.health_l.accessibleName() == w.health_l.toolTip()
    w.update_state(3.0, {}, {**ok, "L": Health.LOST, "R": Health.ALERT}, True, False, 0)
    assert (_icon(w.health_l), _icon(w.health_r)) == ("error:danger:16", "warning:danger:16")
    assert _icon(w.btn_pause) == "play:text:20" and w.btn_pause.text() == "Retomar"
    w.update_state(4.0, {}, ok, False, False, 0, saving=True)
    assert not any(b.isEnabled() for b in (w.btn_pause, w.btn_mark, w.btn_mute, w.btn_devices, w.btn_stop, w.btn_cancel))


def test_the_clock_switches_to_hours_without_moving_the_state(qtbot):
    from listener.ui.widget import RecordingWidget, clock_text, shortcut_text

    assert [clock_text(s) for s in (0, 65, 3599, 3600, 3725, 36125)] == ["00:00", "01:05", "59:59", "1:00:00", "1:02:05",
                                                                          "10:02:05"]
    assert shortcut_text("ctrl+alt+shift+p") == "Ctrl+Alt+Shift+P" and shortcut_text("") == ""
    w = RecordingWidget()
    qtbot.addWidget(w)
    width = w.time_label.width()
    for s in (5, 754, 1111, 3599):
        w.update_state(s, {}, {}, False, False, 0)
        assert w.time_label.width() == width                     # a largura do dígito mais largo: o layout fica parado
    w.update_state(3725, {}, {}, False, False, 0)
    assert w.time_label.text() == "1:02:05" and w.time_label.width() > width


def test_the_alert_line_has_the_icon_and_color_of_its_kind(qtbot):
    from PySide6.QtGui import QPalette

    from listener.ui.widget import RecordingWidget

    w = RecordingWidget()
    qtbot.addWidget(w)
    for kind, value, icon in (("info", Tone.INFO, "info"), ("success", Tone.SUCCESS, "success"),
                              ("warning", Tone.WARNING, "warning"), ("error", Tone.DANGER, "error")):
        w.show_alert(f"alerta {kind}", kind=kind)
        assert _icon(w.alert_icon) == f"{icon}:{value.value}:16" and w.alert_icon.isVisibleTo(w)
        assert w.alert_label.palette().color(QPalette.ColorRole.WindowText).name() == LIGHT.tone(value)


def test_the_alert_and_the_caption_lines_show_the_text_as_written(qtbot):
    """Revisão da F5.4: a linha do alerta e a da legenda são texto puro: um nome de aparelho ou uma fala com "<b>" aparece
    como foi escrito, sem virar texto formatado."""
    from PySide6.QtCore import Qt

    from listener.ui.widget import RecordingWidget

    w = RecordingWidget(live_line=True)
    qtbot.addWidget(w)
    w.show_alert("sem sinal no <b>Fone USB</b>", kind="warning")
    for line in (w.alert_label, w.caption_line):
        assert line.textFormat() == Qt.TextFormat.PlainText
    assert w.alert_label.full_text == "sem sinal no <b>Fone USB</b>"


def test_the_marker_popup_has_the_flag_and_the_hour(qtbot):
    from listener.ui.widget import MarkerPopup

    popup = MarkerPopup(3725.0)
    qtbot.addWidget(popup)
    assert popup.time.text() == "[1:02:05]" and popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert any(_icon(c) == "flag:accent:16" for c in popup.children() if hasattr(c, "property"))


# ---------------------------------------------------------------- medidor (U-063)
def test_the_meter_has_a_visible_track_and_flat_zones_by_dbfs(qtbot):
    from listener.ui.meters import LevelMeter, db_fraction, track_color, zone_stops

    assert (db_fraction(-60), db_fraction(-18), db_fraction(-6), db_fraction(0)) == (0.0, 0.7, 0.9, 1.0)
    assert [c for _at, c in zone_stops(LIGHT)] == [LIGHT.success] * 2 + [LIGHT.warning] * 2 + [LIGHT.danger] * 2
    for t in (LIGHT, theme.DARK):
        assert theme.contrast(track_color(t), t.card) > theme.contrast(t.stroke, t.card) + 0.4      # o trilho aparece
    meter = LevelMeter(height=10)
    meter.resize(200, 10)
    qtbot.addWidget(meter)
    meter.set_rms(1.0)                                            # cheio: as três zonas à vista
    img = meter.grab().toImage()
    y = 5
    assert near(img.pixelColor(100, y), LIGHT.success) and near(img.pixelColor(160, y), LIGHT.warning)
    assert near(img.pixelColor(190, y), LIGHT.danger)
    empty = LevelMeter(height=10)
    empty.resize(200, 10)
    qtbot.addWidget(empty)
    assert near(empty.grab().toImage().pixelColor(100, y), track_color(LIGHT))


# ---------------------------------------------------------------- avisos (U-068)
def test_the_notice_shows_the_kind_icon_a_count_badge_and_a_32px_close(qtbot):
    from listener.ui.components import Badge, IconButton
    from listener.ui.notices import Notice, NoticeKind
    from listener.ui.toast import CLOSE_PX, Toast

    toast = Toast(Notice(NoticeKind.ERROR, "Planejamento: erro na LLM"), 6000, count=3)
    qtbot.addWidget(toast)
    assert isinstance(toast.close_button, IconButton) and (toast.close_button.width(), toast.close_button.height()) == (CLOSE_PX,) * 2
    assert toast.close_button.accessibleName() == "Fechar o aviso" and _icon(toast.icon) == "error:danger:16"
    assert isinstance(toast.badge, Badge) and toast.badge.text() == "3x" and toast.badge.isVisibleTo(toast)
    toast.show()
    qtbot.waitExposed(toast)
    img = toast.grab().toImage()
    assert near(img.pixelColor(toast.width() // 2, toast.height() - 4), LIGHT.card, 4)      # o cartão do tema
    assert near(img.pixelColor(7, toast.height() // 2), LIGHT.danger)                        # a faixa do tipo


# ---------------------------------------------------------------- painel Dispositivos (U-064)
def test_the_devices_panel_rows_have_two_lines_a_mark_a_badge_and_the_result_bar(qtbot):
    from listener.ui.devices_panel import DevicesPanel, PanelView, Row

    panel = DevicesPanel()
    qtbot.addWidget(panel)
    mics = (Row("mic:", "Padrão do Windows (acompanha): agora Mic", current=True, name="Padrão do Windows (acompanha)",
                detail="Microfone · agora Mic"),
            Row("mic:bt", "Fone BT · novo", name="Fone BT", detail="Microfone", new=True))
    panel.show_view(PanelView(mics, (Row("other", "Outro app…", name="Outro app…", detail="Gravar só o som de um app"),)))
    panel.set_status("trocando…", busy=True)
    assert panel.status_bar.kind == "info" and panel.status.text() == "trocando…" and panel.status_bar.isVisibleTo(panel)
    panel.set_status("Não deu para trocar o microfone.", error=True)
    assert panel.status_bar.kind == "error"
    panel.set_status("")
    assert not panel.status_bar.isVisibleTo(panel)
    panel.show()
    qtbot.waitExposed(panel)
    lst = panel.mics
    first, second = lst.visualItemRect(lst.item(0)), lst.visualItemRect(lst.item(1))
    assert first.height() >= LIGHT.metrics.list_item_h and lst.item(0).text().endswith("(em uso)")
    img = lst.viewport().grab().toImage()
    row_colors = {img.pixelColor(x, y).name() for x in range(second.right() - 60, second.right()) for y in
                  range(second.top(), second.bottom())}
    assert any(near(QColor(c), LIGHT.info_bg, 3) for c in row_colors)                      # o selo "Novo"
    mark = {img.pixelColor(x, y).name() for x in range(first.left(), first.left() + 30) for y in range(first.top(), first.bottom())}
    assert any(near(QColor(c), LIGHT.accent, 40) for c in mark)                             # a marca do atual


# ---------------------------------------------------------------- Antes de gravar (U-065 a U-067, A-17)
def test_fit_rect_keeps_the_whole_frame_inside_the_free_area():
    from listener.ui.common import fit_rect

    area = (0, 0, 1280, 672)                                                       # o notebook a 150 % (A-17)
    assert fit_rect((100, 50, 984, 631), (980, 600), area) == (100, 41, 980, 600)   # cabe: só sobe para dentro
    assert fit_rect((0, 0, 1044, 752), (1040, 731), area) == (0, 0, 1040, 651)     # alta demais: encolhe o de dentro
    assert fit_rect((0, 0, 1044, 752), (1040, 731), area, (900, 700)) == (0, 0, 1040, 700)   # nunca abaixo do mínimo
    assert fit_rect((-10000, 900, 400, 300), (396, 279), (1920, 0, 1280, 672)) == (1920, 372, 396, 279)


def test_the_warmup_has_two_columns_the_keys_bar_by_the_record_button_and_fits(qtbot, store, platform):
    from PySide6.QtGui import QGuiApplication

    from listener.ui.components import InfoBar
    from listener.ui.sources import MEETING_ROLE, MeterCell
    from listener.ui.warmup import WarmupDialog

    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    keys = dlg.bar("keys")
    assert isinstance(keys, InfoBar) and keys.isVisibleTo(dlg) and dlg.keys_note is keys.label
    bar, record = keys.geometry(), dlg.record_btn.mapTo(dlg, QPoint(0, 0))
    assert bar.right() < record.x() and bar.top() <= record.y() <= bar.bottom()          # U-066: ao lado do Gravar
    assert dlg.record_btn.property(theme.KIND_PROP) == theme.PRIMARY and _icon(dlg.record_btn) == "record:on_accent:16"
    area, frame = (dlg.screen() or QGuiApplication.primaryScreen()).availableGeometry(), dlg.frameGeometry()
    assert area.top() <= frame.top() and frame.bottom() <= area.bottom() and frame.left() >= area.left()      # A-17
    assert frame.right() <= area.right() or dlg.width() == dlg.minimumWidth()      # o offscreen (800 px, fontes falsas): no mínimo
    table = dlg.sources.table

    def meeting_rows() -> list[int]:
        return [r for r in range(table.rowCount()) if table.item(r, 0) is not None and table.item(r, 0).data(MEETING_ROLE)]

    qtbot.waitUntil(lambda: bool(meeting_rows()), timeout=3000)                            # a primeira lista de apps
    meeting = meeting_rows()
    assert meeting and table.item(meeting[0], 0).text() == "meeting" and "●" not in table.item(meeting[0], 0).text()   # U-067
    assert all(isinstance(table.cellWidget(r, 2), MeterCell) for r in range(table.rowCount()))
    assert set(dlg._bars) == {"keys"}                     # as outras faixas só são montadas quando têm o que dizer
    dlg._set_echo("")
    assert set(dlg._bars) == {"keys"}
    dlg._set_echo("Sem eco: o microfone não ouve a saída (correlação 0,10).", "success")
    assert dlg.bar("echo").isVisibleTo(dlg) and dlg.bar("echo").kind == "success" and dlg.echo_label.text().startswith("Sem eco")
    dlg._set_echo("")
    assert not dlg.bar("echo").isVisibleTo(dlg) and not dlg.bar("mic").isVisibleTo(dlg)
    dlg.reject()


# ---------------------------------------------------------------- resumo (U-069) e bandeja (U-070, A-19)
def test_the_summary_has_the_success_icon_and_lists_markers_only_when_there_are(qtbot, store):
    from PySide6.QtWidgets import QLabel

    from listener.ui.summary import SummaryDialog

    mid = store.create_meeting("Comitê", None, None, None)
    store.finish_meeting(mid, 1000)
    empty = SummaryDialog(store, mid, {"duration_s": 1.0})
    qtbot.addWidget(empty)
    assert empty.markers is None and any(_icon(c) == "success:success:32" for c in empty.findChildren(QLabel))
    store.add_marker(mid, 12_000, "decisão")
    full = SummaryDialog(store, mid, {"duration_s": 1.0})
    qtbot.addWidget(full)
    assert full.markers is not None and full.markers.count() == 1
    warned = SummaryDialog(store, mid, {"duration_s": 1.0, "unsaved": ["L-3"]})
    qtbot.addWidget(warned)
    assert any(_icon(c) == "warning:warning:32" for c in warned.findChildren(QLabel))
    texts = {c.text() for c in warned.findChildren(QLabel)}             # revisão da F5.4: o título diz que houve falha
    assert "Gravação salva com falha" in texts and "Gravação salva" not in texts and warned.windowTitle() == "Gravação salva com falha"
    assert "Gravação salva" in {c.text() for c in full.findChildren(QLabel)}


def test_the_tray_icon_is_the_brand_with_a_state_badge_of_shape_and_color(qapp):
    from listener.model import AppState
    from listener.ui.tray import ICON_SIZES, TRAY_TOKENS, icon_pixmap, make_icon

    assert {s.width() for s in make_icon(AppState.RECORDING).availableSizes()} == set(ICON_SIZES)
    t = TRAY_TOKENS

    def colors(state: str, size: int = 32) -> set[str]:
        img = icon_pixmap(state, size).toImage()
        return {img.pixelColor(x, y).name() for x in range(size // 2, size) for y in range(size // 2, size)
                if img.pixelColor(x, y).alpha() == 255}

    idle, rec, paused, saving = (colors(s) for s in (AppState.IDLE, AppState.RECORDING, AppState.PAUSED, AppState.SAVING))
    assert t.danger not in idle and t.danger in rec and t.warning in paused and t.info in saving and t.accent in idle
    assert t.danger in colors(AppState.RECORDING, 16)                                       # legível a 16 px
