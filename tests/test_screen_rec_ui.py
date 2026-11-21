"""F5.27 lote 3: a gravação da tela do lado da interface (pytest-qt, offscreen, a plataforma falsa com dois monitores e o print
sintético). O "Antes de gravar" (U-180, U-181): a caixa nasce desligada (e apagada sem monitores), a prévia só roda com ela
ligada e para ao desligar, o último monitor vem marcado só se existir, o "Gravar" sem monitor pede a escolha, o
`fields["screen_monitor"]` e, sem a exclusão da captura, só os rótulos (A-34). O widget (U-182, U-183): cada estado e os
sinais pelo menu, sem mudar a altura. As chaves nas Configurações e o bloco `screen_rec` do `--check`, com e sem tela."""
from __future__ import annotations

from typing import Any

import pytest

from listener.model import EventType

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

MONITOR_1, MONITOR_2 = "Monitor 1 · 1920 x 1080 · principal", "Monitor 2 · 2560 x 1440"


def _warmup(qtbot: Any, platform: Any, store: Any, *, show: bool = True) -> Any:
    from listener.ui.warmup import WarmupDialog

    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    if show:
        dlg.show()
    return dlg


def _wait_preview(qtbot: Any, box: Any) -> None:
    qtbot.waitUntil(lambda: box.previews >= 1 and box._task is None, timeout=5000)


def _shows_image(tile: Any) -> bool:
    """A miniatura chegou: o ícone deixou de ser o lugar transparente."""
    image = tile.icon().pixmap(tile.iconSize()).toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 0


# ---------------------------------------------------------------- o "Antes de gravar" (U-180, U-181)
def test_the_screen_box_is_born_off_and_greyed_out_without_monitors(qtbot, platform, store):
    from listener.ui.warmup_screen import ONLY_WINDOWS, PRIVACY, TOGGLE_TEXT

    store.set_setting("screen_rec.monitor", "DISPLAY2")
    dlg = _warmup(qtbot, platform, store)
    box = dlg.screen_box
    assert box.check.text() == TOGGLE_TEXT and box.check.isEnabled() and not box.check.isChecked()
    assert not box.isVisibleTo(dlg) and not box.running and box.previews == 0 and platform.grabbed == []
    assert box.privacy.text() == PRIVACY == ("Tudo o que aparecer neste monitor fica gravado, inclusive notificações. A bandeja, "
                                             "os avisos e as sugestões do listener-meet não aparecem.")
    assert dlg.result_config()[1]["screen_monitor"] is None                       # desligada: o contrato manda None
    platform.screen_monitors = ()                                                 # fora do Windows
    other = _warmup(qtbot, platform, store)
    assert not other.screen_box.check.isEnabled() and other.screen_box.check.toolTip() == ONLY_WINDOWS


def test_the_preview_runs_only_with_the_box_on_and_stops_when_it_turns_off(qtbot, platform, store):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage

    dlg = _warmup(qtbot, platform, store)
    box = dlg.screen_box
    assert box.excluded is True and platform.excluded                            # o "Antes de gravar" saiu da captura
    box.check.setChecked(True)
    assert box.isVisibleTo(dlg) and box.running and set(box.tiles) == {"DISPLAY1", "DISPLAY2"}
    assert [t.toolTip() for t in box.tiles.values()] == [MONITOR_1, MONITOR_2]
    assert box.tiles["DISPLAY1"].text() == "Monitor 1\n1920 x 1080 · principal"
    assert box.tiles["DISPLAY2"].toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    _wait_preview(qtbot, box)
    assert {(r.width, r.height) for r in platform.grabbed} == {(1920, 1080), (2560, 1440)}
    assert all(_shows_image(t) for t in box.tiles.values())
    seq, asked = box._seq, box.previews
    box.check.setChecked(False)
    assert not box.running and not box.isVisibleTo(dlg) and box._seq == seq + 1
    box.tick()
    assert box.previews == asked                                                  # desligada: nenhuma volta nova
    box.check.setChecked(True)
    _wait_preview(qtbot, box)
    tile = box.tiles["DISPLAY1"]
    before = tile.icon().cacheKey()
    stale = QImage(96, 54, QImage.Format.Format_RGB32)
    stale.fill(QColor("red"))
    box._on_thumbs(seq, {"DISPLAY1": stale})                                      # a resposta velha é descartada
    assert tile.icon().cacheKey() == before
    box._on_thumbs(box._seq, {"DISPLAY1": stale})                                 # a da volta de agora entra
    assert tile.icon().cacheKey() != before
    dlg.reject()
    assert not box.running


def test_the_last_monitor_comes_marked_only_if_it_still_exists(qtbot, platform, store):
    from listener.ui.theme import TONE_PROP

    store.set_setting("screen_rec.monitor", "DISPLAY2")
    box = _warmup(qtbot, platform, store).screen_box
    box.check.setChecked(True)
    assert box.chosen == "DISPLAY2" and box.tiles["DISPLAY2"].isChecked() and not box.tiles["DISPLAY1"].isChecked()
    assert [t.property(TONE_PROP) for t in box.tiles.values()] == [None, "accent"]          # o escolhido em destaque
    store.set_setting("screen_rec.monitor", "DISPLAY9")                          # o último saiu: nenhum marcado
    gone = _warmup(qtbot, platform, store).screen_box
    gone.check.setChecked(True)
    assert gone.chosen is None and not any(t.isChecked() for t in gone.tiles.values())


def test_record_with_the_box_on_and_no_monitor_asks_for_the_choice(qtbot, platform, store):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from listener.ui.warmup_screen import PICK_MONITOR

    dlg = _warmup(qtbot, platform, store)
    box = dlg.screen_box
    box.check.setChecked(True)
    dlg.accept()
    assert dlg.isVisible() and dlg.result() != QDialog.DialogCode.Accepted and box.pick_note.isVisibleTo(dlg)
    assert box.pick_note.text() == PICK_MONITOR and box.running                 # a prévia segue com o diálogo aberto
    qtbot.mouseClick(box.tiles["DISPLAY2"], Qt.MouseButton.LeftButton)
    assert box.chosen == "DISPLAY2" and not box.pick_note.isVisibleTo(dlg)
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted and not box.running
    assert dlg.result_config()[1]["screen_monitor"] == "DISPLAY2"                # o `Monitor.key` (contrato)
    assert store.get_setting("screen_rec.monitor") == ""                         # quem grava a escolha é o controller


def test_a_chosen_monitor_that_left_before_record_asks_again(qtbot, platform, store):
    from PySide6.QtWidgets import QDialog

    store.set_setting("screen_rec.monitor", "DISPLAY2")
    dlg = _warmup(qtbot, platform, store)
    dlg.screen_box.check.setChecked(True)
    platform.screen_monitors = platform.screen_monitors[:1]                       # o Monitor 2 desligado com o diálogo aberto
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted and set(dlg.screen_box.tiles) == {"DISPLAY1"}
    assert dlg.screen_box.chosen is None and dlg.result_config()[1]["screen_monitor"] is None


def test_without_the_capture_exclusion_only_the_labels(qtbot, platform, store):
    from PySide6.QtCore import Qt

    platform.capture_exclusion = False
    dlg = _warmup(qtbot, platform, store)
    box = dlg.screen_box
    box.check.setChecked(True)
    assert box.excluded is False and not box.running and box.previews == 0 and platform.grabbed == []
    assert all(t.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly for t in box.tiles.values())
    assert box.privacy.isVisibleTo(dlg)


def test_the_thumbnail_is_scaled_off_the_interface_and_a_failed_grab_skips_the_monitor(platform):
    from listener.ui.warmup_screen import thumb_width, thumbnails, tile_text

    out = thumbnails(platform, [(m.key, m.rect) for m in platform.screen_monitors], 96, 54)
    assert {k: (i.width(), i.height()) for k, i in out.items()} == {"DISPLAY1": (96, 54), "DISPLAY2": (96, 54)}
    assert out["DISPLAY1"].pixelColor(48, 27).alpha() == 255                       # a imagem é própria: o print já morreu
    platform.grab_fail = True
    assert thumbnails(platform, [("DISPLAY1", platform.screen_monitors[0].rect)], 96, 54) == {}
    assert [thumb_width(n) for n in (1, 2, 3, 4, 8)] == [88, 88, 88, 67, 64]
    assert tile_text("Monitor 3") == "Monitor 3"


# ---------------------------------------------------------------- o widget (U-182, U-183)
def _widget(qtbot: Any, platform: Any = None) -> Any:
    from listener.ui.widget import RecordingWidget

    w = RecordingWidget(platform=platform)
    qtbot.addWidget(w)
    w.show()
    return w


def test_the_widget_shows_each_screen_state_without_changing_its_height(qtbot, platform):
    from listener.ui.theme import TONE_PROP
    from listener.ui.widget_screen import FAILING_TIP

    w = _widget(qtbot, platform)
    line = w.screen_line
    assert line.isVisibleTo(w) and line.text.full_text == "Tela: desligada" and w.height() == 106
    texts = {}
    for state, label in (("starting", "Monitor 2"), ("recording", "Monitor 2"), ("paused", "Monitor 2"), ("failing", "Monitor 2"),
                         ("lost", "Monitor 2"), ("stopped", ""), ("off", "")):
        w.set_screen_state(state, label)
        texts[state] = (line.text.full_text, line.text.property(TONE_PROP))
    assert texts == {"starting": ("Tela: Monitor 2", "secondary"), "recording": ("Tela: Monitor 2", "secondary"),
                     "paused": ("Tela: Monitor 2", "secondary"), "failing": ("Tela: Monitor 2", "warning"),
                     "lost": ("Tela: parada", "warning"), "stopped": ("Tela: desligada", "secondary"),
                     "off": ("Tela: desligada", "secondary")}
    w.set_screen_state("failing", "Monitor 2")
    assert FAILING_TIP in line.text.toolTip() and w.height() == 106
    w.update_state(0, {}, {}, paused=False, muted=False, markers=0, saving=True)
    assert not line.isEnabled()                                                   # salvando, tudo apagado
    bare = _widget(qtbot)                                                         # sem a plataforma: só com o host dizendo
    assert not bare.screen_line.isVisibleTo(bare)
    bare.set_screen_state("off", "")
    assert not bare.screen_line.isVisibleTo(bare)
    bare.set_screen_state("recording", "Monitor 1")
    assert bare.screen_line.isVisibleTo(bare) and bare.height() == 106


def test_the_widget_menu_starts_switches_and_stops_the_screen(qtbot, platform):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMenu

    from listener.ui.widget_screen import STOP_TEXT

    w = _widget(qtbot, platform)
    started: list[str] = []
    switched: list[str] = []
    stopped: list[bool] = []
    w.screen_start_requested.connect(started.append)
    w.screen_switch_requested.connect(switched.append)
    w.screen_stop_requested.connect(lambda: stopped.append(True))

    def actions(menu: Any) -> dict[str, Any]:
        return {a.text(): a for a in menu.actions() if not a.isSeparator()}

    off = actions(w.screen_menu())
    assert list(off) == [MONITOR_1, MONITOR_2]                                    # sem tela: os monitores para começar
    off[MONITOR_2].trigger()
    assert (started, switched, stopped) == (["DISPLAY2"], [], [])
    w.set_screen_state("recording", "Monitor 2")
    on = actions(w.screen_menu())
    assert list(on) == [MONITOR_1, "Parar de gravar a tela"]                      # os outros e o parar
    on[MONITOR_1].trigger()
    on["Parar de gravar a tela"].trigger()
    assert (started, switched, stopped) == (["DISPLAY2"], ["DISPLAY1"], [True])
    w.set_screen_state("lost", "Monitor 2")                                       # perdida conta como sem tela
    assert list(actions(w.screen_menu())) == [MONITOR_1, MONITOR_2]
    w.set_screen_state("recording", "Monitor 1")
    platform.excluded.clear()
    qtbot.mouseClick(w.screen_line, Qt.MouseButton.LeftButton)                   # o clique abre; a tela gravando: fora da captura
    popup = QApplication.activePopupWidget()
    assert len(platform.excluded) == 1 and isinstance(popup, QMenu) and STOP_TEXT in [a.text() for a in popup.actions()]
    popup.close()



def test_the_widget_still_drags_and_opens_the_settings_and_the_screen_line_does_not_drag(qtbot, platform):
    """O arrasto foi para o `DragMixin` (F5.27): arrastar move e emite `moved`, o duplo clique abre as Configurações e o
    clique na linha da tela não começa arrasto."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = _widget(qtbot, platform)
    moved: list[QPoint] = []
    opened: list[bool] = []
    w.moved.connect(moved.append)
    w.settings_requested.connect(lambda: opened.append(True))

    def event(kind: QEvent.Type, local: QPoint, buttons: Qt.MouseButton) -> QMouseEvent:
        return QMouseEvent(kind, QPointF(local), QPointF(w.mapToGlobal(local)), Qt.MouseButton.LeftButton, buttons,
                           Qt.KeyboardModifier.NoModifier)

    start = w.pos()
    w.mousePressEvent(event(QEvent.Type.MouseButtonPress, QPoint(10, 10), Qt.MouseButton.LeftButton))
    w.mouseMoveEvent(event(QEvent.Type.MouseMove, QPoint(40, 30), Qt.MouseButton.LeftButton))
    w.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, QPoint(40, 30), Qt.MouseButton.NoButton))
    assert w.pos() == start + QPoint(30, 20) and moved == [w.pos()]
    w.mouseDoubleClickEvent(event(QEvent.Type.MouseButtonDblClick, QPoint(10, 10), Qt.MouseButton.LeftButton))
    assert opened == [True]
    qtbot.mousePress(w.screen_line, Qt.MouseButton.LeftButton)
    assert w._drag_offset is None
    qtbot.mouseRelease(w.screen_line, Qt.MouseButton.LeftButton)
    popup = QApplication.activePopupWidget()                                      # o clique sem arrastar abriu o menu
    assert popup is not None
    popup.close()


# ---------------------------------------------------------------- Configurações (CP8)
def test_the_screen_settings_in_the_capture_page(qtbot, platform, store):
    from PySide6.QtWidgets import QFormLayout

    from listener.ui.settings_editors import IntEditor
    from listener.ui.settings_general import SCREEN_CARD
    from listener.ui.settings_min import SettingsDialog

    store.set_setting("screen_rec.fps", "3")
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    page = dlg.page("Captura")
    form = page.cards[SCREEN_CARD].content.itemAt(0).layout()
    assert isinstance(form, QFormLayout)
    labels = [form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget().text() for i in range(2)]
    assert labels == ["Quadros por segundo da tela", "Tamanho máximo da tela gravada (px)"]
    fps, side = dlg.editor("screen_rec.fps"), dlg.editor("screen_rec.max_side")
    assert isinstance(fps, IntEditor) and isinstance(side, IntEditor)
    assert fps.value() == 3 and side.value() == 1920 and "screen_rec.monitor" not in dlg._editors
    assert (fps.spin.minimum(), fps.spin.maximum(), side.spin.minimum(), side.spin.maximum()) == (1, 5, 960, 3840)
    side.set_raw("2560")
    dlg._save()
    assert store.get_setting("screen_rec.max_side") == "2560" and store.get_setting("screen_rec.fps") == "3"


# ---------------------------------------------------------------- o `--check`
def test_the_check_block_with_and_without_the_screen(store):
    from listener.screen_rec.report import NO_SCREEN, screen_rec_report
    from listener.store import Store
    from tests.test_screen_store import _meeting

    mid, plain = _meeting(store), _meeting(store)
    store.screen.ensure_file(mid)
    for idx, (t0, t1) in enumerate(((0, 60_000), (60_000, 120_000), (125_000, 185_000))):     # 5 s sem tela: o monitor saiu
        store.screen.insert_chunk(mid, idx=idx, t_start_ms=t0, t_end_ms=t1, frames=60, width=1920, height=1080, codec="libx264",
                                  data=b"x" * 500_000)
    store.add_event(mid, 0, EventType.SCREEN_START, {"monitor": 2, "width": 1920, "height": 1080})
    store.add_event(mid, 120_000, EventType.SCREEN_LOST, {"monitor": 2})
    store.add_event(mid, 120_000, EventType.SCREEN_STOP, {"reason": "perdido", "frames": 120, "skipped": 2, "failures": 0,
                                                          "cycle_ms_p95": 120.0})
    store.add_event(mid, 125_000, EventType.SCREEN_START, {"monitor": 2, "width": 1920, "height": 1080})
    store.add_event(mid, 150_000, EventType.SCREEN_SWITCH, {"monitor": 1, "width": 1920, "height": 1080})
    store.add_event(mid, 185_000, EventType.SCREEN_STOP, {"reason": "reuniao", "frames": 60, "skipped": 3, "failures": 1,
                                                          "cycle_ms_p95": 80.0})
    with Store.open_readonly(store.base_dir) as ro:
        out, none = screen_rec_report(ro, mid), screen_rec_report(ro, plain)
    assert none == {"enabled": False, "note": NO_SCREEN}
    assert (out["chunks"], out["mb"], out["covered_ms"], out["frames"], out["fps_effective"]) == (3, 1.5, 180_000, 180, 1.0)
    assert out["mb_per_hour"] == 30.0 and out["gate"]["disk"]["ok"] is True and out["idx_missing"] == 0
    assert out["gaps"] == {"n": 1, "total_ms": 5000, "list": [{"idx": 2, "t_ms": 120_000, "gap_ms": 5000}]}
    assert (out["skipped"], out["failures"], out["cycle_ms_p95"]) == (5, 1, 120.0)
    assert [s["reason"] for s in out["sessions"]] == ["perdido", "reuniao"]
    assert [(e["t_ms"], e["monitor"]) for e in out["lost"]] == [(120_000, 2)] and len(out["switches"]) == 1 == len(out["starts"]) - 1
    assert out["sizes"] == ["1920x1080"] and out["codecs"] == ["libx264"]
