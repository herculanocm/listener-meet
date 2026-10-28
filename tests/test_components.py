"""F5.4 L1b (item 3 e 4; A-10, A-20; U-053, U-057, U-059): os ícones SVG próprios (grade de 20 px, traço de 1,5 px,
recoloridos por `SourceIn`, em cache, no `package-data`) e os componentes do tema (`Card`, `InfoBar`, `Badge` e o
`BadgeDelegate`, `EmptyState`, `IconButton`, `HtmlView`), conferidos nos pixels e na troca de tema."""
from __future__ import annotations

import tomllib
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGroupBox, QTableWidget, QTableWidgetItem

from listener.ui import components, icons, theme
from listener.ui.theme import DARK, LIGHT, TONE_ROLE, Tone
from tests.fake_platform import FakePlatform


def near(color: QColor, expected: str, tol: int = 6) -> bool:
    e = QColor(expected)
    return all(abs(a - b) <= tol for a, b in ((color.red(), e.red()), (color.green(), e.green()), (color.blue(), e.blue())))


@pytest.fixture
def restore_theme(qapp: Any, tmp_path: Any) -> Iterator[None]:
    yield
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Light)


# ---------------------------------------------------------------- ícones (item 4, A-20)
def test_the_icon_set_is_complete_on_the_grid_and_packaged():
    names = icons.NAMES
    assert len(names) >= 26 and {"brand", "record", "pause", "stop", "mic-off", "flag", "search", "info", "success", "warning",
                                 "error", "close", "chevron-down"} <= names
    for name in names:
        svg = icons.svg(name).decode("utf-8")
        assert 'viewBox="0 0 20 20"' in svg and 'stroke-width="1.5"' in svg and "#" not in svg, name
        assert QSvgRenderer(icons.svg(name)).isValid(), name
    assert {p.name for p in resources.files("listener.ui.icons").iterdir() if p.name.endswith(".svg")} == {f"{n}.svg" for n in names}
    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["setuptools"]["package-data"]["listener.ui.icons"] == ["*.svg"]
    with pytest.raises(KeyError, match="desconhecido"):
        icons.svg("nao-existe")


def test_icons_are_recolored_by_source_in_and_cached_by_name_color_size_and_dpr(qapp):
    pm = icons.pixmap("record", LIGHT.danger, 20, 2.0)
    img = pm.toImage()
    assert (pm.width(), pm.devicePixelRatio()) == (40, 2.0) and img.pixelColor(20, 20).name() == LIGHT.danger
    assert img.pixelColor(0, 0).alpha() == 0                                                  # o fundo fica transparente
    assert icons.pixmap("record", LIGHT.danger, 20, 2.0) is pm and icons.pixmap("record", DARK.danger, 20, 2.0) is not pm
    icon = icons.tone_icon("stop", Tone.TEXT, LIGHT)
    assert not icon.isNull() and icon.availableSizes() and 1.0 in icons.screen_dprs()


def test_set_icon_marks_the_widget_and_the_theme_change_repaints_it(qapp, restore_theme, tmp_path):
    button = components.IconButton("mic", "Mudo", shortcut="Ctrl+Alt+M")
    assert button.property(icons.ICON_PROP) == "mic:text:20" and not button.icon().isNull()
    light = button.icon().pixmap(20, 20).toImage()
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Dark)
    dark = button.icon().pixmap(20, 20).toImage()
    opaque = [(x, y) for x in range(20) for y in range(20) if light.pixelColor(x, y).alpha() == 255]
    assert opaque and light.pixelColor(*opaque[0]).name() == LIGHT.text and dark.pixelColor(*opaque[0]).name() == DARK.text
    with pytest.raises(KeyError):
        icons.set_icon(button, "nao-existe")
    button.deleteLater()


# ---------------------------------------------------------------- componentes (item 3)
def test_icon_button_keeps_the_name_for_screen_readers_and_the_minimum_target(qtbot):
    button = components.IconButton("pause", "Pausar", shortcut="Ctrl+Alt+P")
    qtbot.addWidget(button)
    assert (button.text(), button.accessibleName(), button.toolTip()) == ("Pausar", "Pausar", "Pausar (Ctrl+Alt+P)")
    assert button.minimumSize().width() >= 32 and button.minimumSize().height() >= 32
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def test_card_is_a_group_box_with_the_card_padding(qtbot):
    card = components.Card("Áudio")
    qtbot.addWidget(card)
    m = card.content.contentsMargins()
    assert isinstance(card, QGroupBox) and card.title() == "Áudio" and m.left() == m.right() == LIGHT.metrics.card_padding


def test_info_bar_shows_the_kind_by_icon_and_background(qtbot):
    bar = components.InfoBar("warning", "Falta a chave do Gemini.", "Abrir Configurações")
    bar.resize(360, 48)
    qtbot.addWidget(bar)
    bar.show()
    qtbot.waitExposed(bar)
    assert bar.icon.property(icons.ICON_PROP) == "warning:warning:20" and bar.action.isVisibleTo(bar)
    assert near(bar.grab().toImage().pixelColor(4, bar.height() // 2), LIGHT.warning_bg)
    bar.set_kind("error")
    assert near(bar.grab().toImage().pixelColor(4, bar.height() // 2), LIGHT.danger_bg)
    assert not components.InfoBar("info", "só texto").action.isVisibleTo(bar)
    with pytest.raises(ValueError, match="faixa"):
        bar.set_kind("rosa")


def test_badge_is_a_pill_in_the_status_tone(qtbot):
    badge = components.Badge("Concluído", Tone.SUCCESS)
    qtbot.addWidget(badge)
    badge.show()
    qtbot.waitExposed(badge)
    img = badge.grab().toImage()
    assert near(img.pixelColor(5, badge.height() // 2), LIGHT.success_bg)                     # à esquerda do texto
    assert badge.palette().color(badge.foregroundRole()).name() == LIGHT.success and badge.font().pixelSize() == 12
    with pytest.raises(ValueError, match="estado"):
        badge.set_tone(Tone.TEXT)


def test_badge_delegate_paints_a_pill_and_keeps_the_display_text(qtbot):
    table = QTableWidget(1, 2)
    item = QTableWidgetItem("Erro")
    item.setData(TONE_ROLE, Tone.DANGER.value)
    table.setItem(0, 0, item)
    table.setItem(0, 1, QTableWidgetItem("sem selo"))
    table.setItemDelegateForColumn(0, components.BadgeDelegate(table))
    table.resize(320, 120)
    qtbot.addWidget(table)
    table.show()
    qtbot.waitExposed(table)
    rect: QRect = table.visualItemRect(item)
    img = table.viewport().grab().toImage()
    assert item.data(Qt.ItemDataRole.DisplayRole) == "Erro"                                     # o leitor de tela lê o texto
    assert near(img.pixelColor(rect.left() + 8, rect.center().y()), LIGHT.danger_bg)


def test_empty_state_has_icon_sentence_and_primary_action(qtbot):
    empty = components.EmptyState("document", "Ata ainda não gerada", "Gerar ata")
    qtbot.addWidget(empty)
    assert empty.action.property(theme.KIND_PROP) == theme.PRIMARY and empty.label.text() == "Ata ainda não gerada"
    assert empty.icon.property(icons.ICON_PROP) == "document:secondary:48" and not empty.icon.pixmap().isNull()
    assert not components.EmptyState("search", "Nada encontrado").action.isVisibleTo(empty)


def test_html_view_paints_classes_from_the_tokens_and_rethemes(qtbot, qapp, restore_theme, tmp_path):
    view = components.HtmlView()
    qtbot.addWidget(view)
    source = "<p class='note'>nota</p><p class='danger'>erro</p>" + "<p>linha</p>" * 80
    view.setHtml(source)
    assert view.source_html == source and LIGHT.secondary in view.toHtml() and LIGHT.danger in view.toHtml()
    view.resize(200, 120)
    view.show()
    qtbot.waitExposed(view)
    view.verticalScrollBar().setValue(40)
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Dark)                            # a varredura chama `retheme`
    assert DARK.secondary in view.toHtml() and LIGHT.secondary not in view.toHtml() and view.verticalScrollBar().value() == 40
