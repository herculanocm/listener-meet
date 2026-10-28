"""F5.4 L1 (A-13, A-03, A-07, A-08, A-09): os tokens claro e escuro com a matriz dos pares usados no contraste (texto 4,5:1;
ícone, traço forte e falante 3:1; `disabled` fora), a fonte com a queda para "Segoe UI", o `apply` com o esquema explícito
(o offscreen não tem esquema), a troca de esquema e o contraste alto, o `release` e o fator de texto do sistema."""
from __future__ import annotations

import dataclasses
import re
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtWidgets import QWidget

from listener.platform.base import text_scale_from_percent
from listener.ui import theme
from listener.ui.style import ThemeStyle
from listener.ui.theme import DARK, FONTS, LIGHT, Tone
from tests.fake_platform import FakePlatform

HEX = re.compile(r"^#[0-9a-f]{6}$")


def _colors(t: theme.Tokens) -> dict[str, str]:
    return {f.name: getattr(t, f.name) for f in dataclasses.fields(t) if f.name != "scheme" and isinstance(getattr(t, f.name), str)}


# ---------------------------------------------------------------- tokens
@pytest.mark.parametrize("t", [LIGHT, DARK], ids=["claro", "escuro"])
def test_used_pairs_reach_the_minimum_contrast(t):
    failures = [f"{fg} sobre {bg}: {theme.contrast(getattr(t, fg), getattr(t, bg)):.2f} < {minimum}"
                for fg, backgrounds, minimum in theme.CONTRAST_PAIRS for bg in backgrounds
                if theme.contrast(getattr(t, fg), getattr(t, bg)) < minimum]
    failures += [f"falante {color} sobre {bg}: {theme.contrast(color, getattr(t, bg)):.2f}" for color in t.speakers
                 for bg in theme.SPEAKER_SURFACES if theme.contrast(color, getattr(t, bg)) < theme.GRAPHIC_MIN]
    assert not failures


def test_matrix_names_real_tokens_and_leaves_out_what_the_wcag_exempts():
    names = set(_colors(LIGHT))
    pairs = [(fg, bg) for fg, backgrounds, _minimum in theme.CONTRAST_PAIRS for bg in backgrounds]
    assert {name for pair in pairs for name in pair} <= names
    assert not any("disabled" in pair for pair in pairs)                                   # o apagado é isento
    assert not {("placeholder", "selected"), ("warning", "selected"), ("stroke_strong", "selected")} & set(pairs)   # A-13
    assert {("text", "selected"), ("secondary", "selected"), ("on_accent", "accent"), ("danger", "danger_bg")} <= set(pairs)


def test_tokens_are_frozen_with_slots_and_complete():
    for t in (LIGHT, DARK):
        assert not hasattr(t, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.text = "#000000"  # type: ignore[misc]
        assert all(HEX.match(c) for c in _colors(t).values()), _colors(t)
        assert len(t.speakers) == len(set(t.speakers)) == 8 and all(HEX.match(c) for c in t.speakers)
        assert t.speaker(8) == t.speaker(0) and t.speaker(9) == t.speaker(1)
    assert set(_colors(LIGHT)) == set(_colors(DARK)) and LIGHT.fonts is DARK.fonts and LIGHT.metrics is DARK.metrics
    assert (LIGHT.accent, DARK.accent, LIGHT.window, DARK.window) == ("#005fb8", "#60cdff", "#f3f3f3", "#202020")
    assert LIGHT.tone(Tone.DANGER) == "#c42b1c" and DARK.background(Tone.WARNING) == "#433519"
    with pytest.raises(ValueError, match="fundo"):
        LIGHT.background(Tone.TEXT)
    assert (FONTS.caption.px, FONTS.body.px, FONTS.subtitle.px, FONTS.clock.px) == (12, 14, 20, 24)
    assert (LIGHT.metrics.control_h, LIGHT.metrics.list_item_h, LIGHT.metrics.radius, LIGHT.metrics.radius_card) == (32, 36, 4, 8)


def test_contrast_functions_moved_to_the_theme_and_common_reexports_them():
    from listener.ui import common

    assert (common.contrast, common.luminance, common.text_on, common.mix) == (theme.contrast, theme.luminance, theme.text_on,
                                                                               theme.mix)
    assert theme.contrast(theme.BLACK, theme.WHITE) == pytest.approx(21.0)


# ---------------------------------------------------------------- fonte
def test_font_falls_back_to_segoe_ui_with_semibold_weight(qapp, monkeypatch):
    from PySide6.QtGui import QFont

    monkeypatch.setattr(theme, "_families", frozenset)                               # o offscreen: nenhuma família
    body, title = theme.make_font(FONTS.body, 1.5), theme.make_font(FONTS.subtitle)
    assert (body.family(), body.pixelSize(), body.weight()) == ("Segoe UI", 21, QFont.Weight.Normal)
    assert (title.family(), title.pixelSize(), title.weight()) == ("Segoe UI", 20, QFont.Weight.DemiBold)
    monkeypatch.setattr(theme, "_families", lambda: frozenset({FONTS.subtitle.family}))
    variable = theme.make_font(FONTS.subtitle)                 # item 5: o 600 vem da família "… Semibold", não do DemiBold
    assert (variable.family(), variable.weight()) == (FONTS.subtitle.family, QFont.Weight.Normal)


# ---------------------------------------------------------------- apply
@pytest.fixture
def restore_theme(qapp: Any, tmp_path: Any) -> Iterator[None]:
    """O tema da sessão (claro, texto 1,0) de volta depois do teste; peça antes do `monkeypatch` (sai depois dele)."""
    from PySide6.QtCore import Qt

    yield
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Light)


def test_session_app_has_the_theme(qapp):
    from PySide6.QtGui import QPalette

    roles, pal = QPalette.ColorRole, qapp.palette()
    style = qapp.style()
    assert isinstance(style, ThemeStyle) and style.base_name == "Fusion" and style.tokens is LIGHT
    assert theme.tokens() is LIGHT and qapp.font().pixelSize() == FONTS.body.px
    for role, value in ((roles.Window, LIGHT.window), (roles.WindowText, LIGHT.text), (roles.Base, LIGHT.input),
                        (roles.Text, LIGHT.text), (roles.Accent, LIGHT.accent), (roles.PlaceholderText, LIGHT.placeholder),
                        (roles.Link, LIGHT.accent), (roles.Highlight, LIGHT.accent), (roles.HighlightedText, LIGHT.on_accent)):
        assert pal.color(role).name() == value, role
    assert pal.color(QPalette.ColorGroup.Disabled, roles.Text).name() == LIGHT.disabled


def test_dark_scheme_and_text_scale_without_rebuilding_the_style(qapp, tmp_path, restore_theme):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette

    seen: list[theme.Tokens] = []
    theme.notifier().changed.connect(seen.append)
    platform = FakePlatform(tmp_path)
    platform.text_scale_factor = 1.5
    style = qapp.style()
    assert theme.apply(qapp, platform, Qt.ColorScheme.Dark) is DARK
    assert qapp.style() is style and style.tokens is DARK                              # sem repolir as janelas abertas
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == DARK.window
    assert qapp.font().pixelSize() == 21 and theme.text_scale() == 1.5 and theme.tokens() is DARK
    theme.apply(qapp, platform, Qt.ColorScheme.Dark)                                   # o mesmo de novo: nada a avisar
    assert seen == [DARK]
    theme.notifier().changed.disconnect(seen.append)


def test_scheme_change_follows_the_system_unless_forced(qapp, tmp_path, restore_theme, monkeypatch):
    from PySide6.QtCore import Qt

    theme.notifier().on_scheme_changed(Qt.ColorScheme.Dark)
    assert theme.tokens() is LIGHT                                                     # o esquema pedido no `apply` fica
    theme.apply(qapp, FakePlatform(tmp_path))
    assert theme.tokens() is LIGHT                                                     # A-03: `Unknown` é claro

    class DarkHints:
        def colorScheme(self) -> Qt.ColorScheme:  # noqa: N802 (o nome do QStyleHints)
            return Qt.ColorScheme.Dark

    monkeypatch.setattr(theme._STATE, "hints", DarkHints())
    theme.notifier().on_scheme_changed(Qt.ColorScheme.Dark)                            # o Windows trocou para o escuro
    assert theme.tokens() is DARK and qapp.style().tokens is DARK


def test_high_contrast_keeps_the_system_palette(qapp, restore_theme, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette

    seen: list[theme.Tokens] = []
    theme.notifier().changed.connect(seen.append)
    monkeypatch.setattr(theme, "_high_contrast", lambda: True)
    theme.notifier().on_contrast_changed(Qt.ContrastPreference.HighContrast)
    assert qapp.palette().color(QPalette.ColorRole.Window).name() != LIGHT.window and seen == [LIGHT]
    theme.notifier().changed.disconnect(seen.append)


def test_release_puts_the_base_style_back_and_can_run_twice(qapp, restore_theme):
    first = theme.notifier()
    theme.release(qapp)
    theme.release(qapp)
    assert not isinstance(qapp.style(), ThemeStyle) and qapp.style().name().lower() == "fusion"
    assert theme.notifier() is not first


# ---------------------------------------------------------------- dicionários de cor em tokens (A-11)
def test_state_dictionaries_hold_tones_for_every_state():
    from listener.model import AppState, Health, JobStatus
    from listener.ui import common
    from listener.ui.widget import LIVE_BADGES

    assert set(common.HEALTH_TONES) == set(Health) and set(common.STATE_TONES) == set(AppState)
    assert set(common.JOB_STATUS_TONES) == set(JobStatus) and set(JobStatus) >= common.JOB_STATUS_HATCHED
    tones = [*common.HEALTH_TONES.values(), *common.STATE_TONES.values(), *common.JOB_STATUS_TONES.values(),
             *common.NOTICE_TONES.values(), *(tone for _text, tone in LIVE_BADGES.values())]
    assert all(isinstance(tone, Tone) for tone in tones)
    for t in (LIGHT, DARK):
        assert common.health_color(Health.LOST, t) == t.danger and common.health_color("???", t) == t.disabled
        assert common.state_color(AppState.SAVING, t) == t.info and common.job_status_color(JobStatus.RUNNING, t) == t.info


def test_current_theme_readers_follow_the_scheme(qapp, tmp_path, restore_theme):
    from PySide6.QtCore import Qt

    from listener.model import Health, JobStatus
    from listener.ui import common
    from listener.ui.transcript import PLAYING_TONE, SegmentsModel

    model = SegmentsModel([])
    light = model._playing_brush()
    assert light.gradient().stops()[-1][1].name() == LIGHT.background(PLAYING_TONE) and model._playing_brush() is light
    assert common.job_status_color(JobStatus.DONE) == LIGHT.success and common.health_color(Health.OK) == LIGHT.success
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Dark)
    assert common.job_status_color(JobStatus.DONE) == DARK.success and common.health_color(Health.OK) == DARK.success
    dark = model._playing_brush()                                                        # refeito com os tokens novos
    assert dark is not light and dark.gradient().stops()[0][1].name() == DARK.tone(PLAYING_TONE)


def _text_color(w) -> str:
    from PySide6.QtGui import QPalette

    return w.palette().color(QPalette.ColorRole.WindowText).name()


def test_dark_surfaces_stay_dark_until_l2(qtbot):
    """L1b: o widget, o marcador e o aviso são superfícies escuras (`theme.surface`), sem folha de estilo: a paleta é a do
    escuro e os tons saem dos tokens do escuro, com o tema claro."""
    from listener.model import AppState
    from listener.ui.notices import Notice, NoticeKind
    from listener.ui.toast import Toast
    from listener.ui.tray import icon_color
    from listener.ui.widget import MarkerPopup, RecordingWidget

    w = RecordingWidget()
    qtbot.addWidget(w)
    w.update_state(1.0, {"L": 0.1, "R": 0.1}, {"L": "ok", "R": "warn"}, False, False, 0)
    w.set_live_state("crashed")
    assert theme.tokens() is LIGHT and theme.surface_tokens(w.status_label) is DARK      # o widget escuro no tema claro
    assert (_text_color(w.status_label), _text_color(w.health_l), _text_color(w.live_badge)) == (DARK.danger, DARK.success, DARK.danger)
    assert _text_color(w.time_label) == DARK.text and _text_color(w.markers_label) == DARK.secondary
    assert not w.styleSheet() and not any(c.styleSheet() for c in w.findChildren(QWidget))
    popup, toast = MarkerPopup(3.0), Toast(Notice(NoticeKind.INFO, "texto"), 1000)
    qtbot.addWidget(popup)
    qtbot.addWidget(toast)
    assert theme.surface_tokens(popup.edit) is DARK and _text_color(toast.hint) == DARK.secondary and not toast.styleSheet()
    assert icon_color(AppState.RECORDING) == DARK.danger


# ---------------------------------------------------------------- widgets: tom, papel, primário, erro (L1b, A-02, A-10)
def test_tone_role_and_note_set_the_text_color_and_font_and_reset(qtbot):
    from PySide6.QtWidgets import QLabel

    label = QLabel("nota")
    qtbot.addWidget(label)
    theme.note(label)
    assert _text_color(label) == LIGHT.secondary and label.font().pixelSize() == FONTS.caption.px
    assert label.property(theme.TONE_PROP) == "secondary" and label.property(theme.FONT_PROP) == "caption"
    theme.tone(label, Tone.WARNING)
    assert _text_color(label) == LIGHT.warning
    theme.tone(label, None)
    assert _text_color(label) == LIGHT.text and label.property(theme.TONE_PROP) is None        # de volta à herdada
    theme.role(label, "subtitle")
    assert label.font().pixelSize() == FONTS.subtitle.px
    with pytest.raises(ValueError, match="papel"):
        theme.role(label, "titulo")


def test_the_theme_change_sweeps_the_tones_and_keeps_the_surfaces(qtbot, qapp, tmp_path, restore_theme):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout

    plain, dark_win = QLabel("nota"), QWidget()
    theme.tone(plain, Tone.SECONDARY)
    theme.surface(dark_win, DARK)
    inner = QLabel("no widget")
    QVBoxLayout(dark_win).addWidget(inner)
    theme.tone(inner, Tone.DANGER)                                      # nasceu fora da janela escura? `retone` resolve
    theme.retone(dark_win)
    for w in (plain, dark_win):
        qtbot.addWidget(w)
    assert (_text_color(plain), _text_color(inner)) == (LIGHT.secondary, DARK.danger)
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Dark)
    assert (_text_color(plain), _text_color(inner)) == (DARK.secondary, DARK.danger)       # a superfície fica no seu esquema


def test_primary_kind_invalid_and_qcolor(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

    win = QWidget()
    lay = QHBoxLayout(win)
    ok, other, field = QPushButton("Salvar"), QPushButton("Testar"), QLineEdit()
    for w in (ok, other, field):
        lay.addWidget(w)
    qtbot.addWidget(win)
    theme.primary(win, ok)
    assert ok.property(theme.KIND_PROP) == theme.PRIMARY and ok.isDefault() and not other.autoDefault()
    theme.set_kind(ok, None)
    assert ok.property(theme.KIND_PROP) is None and not ok.isDefault()
    theme.set_invalid(field, True)
    assert field.property(theme.INVALID_PROP) is True
    color = theme.qcolor(LIGHT.accent, 0.5)
    assert color.name() == LIGHT.accent and abs(color.alphaF() - 0.5) < 0.01 and theme.qcolor(LIGHT.text).alpha() == 255


def test_html_classes_come_from_the_tokens_without_literal_colors():
    css = theme.html_css(DARK)
    assert ".note { color: #c5c5c5; }" in css and f"a {{ color: {DARK.accent}; }}" in css
    assert all(f".{name} " in css for name in ("note", "success", "warning", "danger", "info", "accent"))


# ---------------------------------------------------------------- fator de texto (A-08)
@pytest.mark.parametrize(("value", "scale"), [(100, 1.0), (150, 1.5), (225, 2.25), (90, 1.0), (300, 2.25), ("150", 1.0),
                                              (None, 1.0), (True, 1.0)])
def test_text_scale_from_the_system_percent(value, scale):
    assert text_scale_from_percent(value) == pytest.approx(scale)


@pytest.mark.skipif(sys.platform != "win32", reason="o registro e o windows11 só existem no Windows")
def test_win32_text_scale_and_style_come_from_the_system():
    from listener.platform.win32.platform import Win32Platform

    platform = Win32Platform()
    assert 1.0 <= platform.text_scale() <= 2.25
    assert platform.ui_style() in ("windows11", "windowsvista")
