"""F5.4 L1b (A-02, A-04, A-05, A-06; Aceite 3, U-055, U-056, U-058): a pintura do `ThemeStyle` sobre o Fusion do offscreen,
conferida nos pixels: primário e destrutivo preenchidos, a linha selecionada em `selected` com o tom do item vindo do tema, o
anel de foco só pelo teclado, a borda de erro, as abas sublinhadas, o grupo como cartão e as alturas mínimas do `polish`."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QStyle,
                               QStyleOption, QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from listener.ui import theme
from listener.ui.style import POLISHED_PROP, TITLE_INSET, ThemeStyle, kind_fill
from listener.ui.theme import DARK, LIGHT, TONE_ROLE, Tone
from tests.fake_platform import FakePlatform

St = QStyle.StateFlag


def near(color: QColor, expected: str, tol: int = 6) -> bool:
    e = QColor(expected)
    return all(abs(a - b) <= tol for a, b in ((color.red(), e.red()), (color.green(), e.green()), (color.blue(), e.blue())))


def shown(qtbot: Any, w: QWidget) -> QImage:
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w.grab().toImage()


def inside(w: QWidget, dx: int = 5) -> QPoint:
    """Um ponto dentro de `w` (na coordenada da janela), longe dos cantos redondos e do texto."""
    g = w.geometry()
    return QPoint(g.left() + dx, g.center().y())


def colors_in(img: QImage, rect: QRect) -> set[str]:
    return {img.pixelColor(x, y).name() for x in range(rect.left(), rect.right()) for y in range(rect.top(), rect.bottom())}


# ---------------------------------------------------------------- botões (A-02, U-056)
def test_primary_and_danger_are_filled_with_on_accent_text_and_the_rest_keeps_the_base(qtbot):
    box = QWidget()
    lay = QHBoxLayout(box)
    save, delete, cancel = QPushButton("Salvar"), QPushButton("Apagar"), QPushButton("Cancelar")
    for b in (save, delete, cancel):
        lay.addWidget(b)
    theme.primary(box, save)
    theme.set_kind(delete, theme.DANGER)
    img = shown(qtbot, box)
    assert near(img.pixelColor(inside(save)), LIGHT.accent) and near(img.pixelColor(inside(delete)), LIGHT.danger)
    assert not near(img.pixelColor(inside(cancel)), LIGHT.accent) and not near(img.pixelColor(inside(cancel)), LIGHT.danger)
    lightest = max(QColor(c).lightness() for c in colors_in(img, save.geometry().adjusted(6, 6, -6, -6)))
    assert lightest > 200 > QColor(LIGHT.accent).lightness()          # o texto do primário em `on_accent` (o offscreen não tem
    assert LIGHT.text in colors_in(img, cancel.geometry())            # fonte: só as caixas das letras), o do comum em `text`
    assert save.isDefault() and not cancel.isDefault() and not delete.isDefault()    # o Enter num campo fica no primário (R-02)


def test_fill_follows_the_state_and_disabled_goes_flat():
    assert kind_fill(LIGHT, theme.PRIMARY, St.State_Enabled) == LIGHT.accent
    assert kind_fill(LIGHT, theme.PRIMARY, St.State_Enabled | St.State_MouseOver) == LIGHT.accent_hover
    assert kind_fill(DARK, theme.PRIMARY, St.State_Enabled | St.State_Sunken) == DARK.accent_pressed
    assert kind_fill(LIGHT, theme.DANGER, St.State_Enabled) == LIGHT.danger
    assert kind_fill(LIGHT, theme.DANGER, St.State_Enabled | St.State_MouseOver) != LIGHT.danger
    assert kind_fill(LIGHT, theme.PRIMARY, St.State_None) == LIGHT.stroke                   # apagado


def test_a_toned_button_gets_the_tone_in_its_label(qtbot):
    tool = QToolButton()
    tool.setText("XX")
    tool.resize(64, 32)
    theme.tone(tool, Tone.DANGER)
    img = shown(qtbot, tool)
    assert any(near(QColor(c), LIGHT.danger, 12) for c in colors_in(img, tool.rect().adjusted(8, 6, -8, -6)))


# ---------------------------------------------------------------- foco, erro, abas e cartão (A-06, U-055)
@pytest.mark.parametrize(("keyboard", "ring"), [(False, False), (True, True)], ids=["clique", "teclado"])
def test_focus_ring_only_when_focus_came_from_the_keyboard(qapp, keyboard, ring):
    img = QImage(40, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(LIGHT.window))
    opt = QStyleOption()
    opt.rect = QRect(4, 4, 32, 32)
    opt.state = St.State_HasFocus | (St.State_KeyboardFocusChange if keyboard else St.State_None)
    p = QPainter(img)
    qapp.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, opt, p, None)
    p.end()
    assert near(img.pixelColor(5, 20), LIGHT.focus) is ring and near(img.pixelColor(20, 20), LIGHT.window)


def test_invalid_field_gets_the_danger_border_and_loses_it(qtbot):
    edit = QLineEdit("abc")
    edit.resize(160, 32)
    theme.set_invalid(edit, True)
    assert near(shown(qtbot, edit).pixelColor(0, 16), LIGHT.danger, 20)
    theme.set_invalid(edit, False)
    assert edit.property(theme.INVALID_PROP) is None and not near(edit.grab().toImage().pixelColor(0, 16), LIGHT.danger, 20)


def test_invalid_table_gets_the_danger_border_too(qtbot):
    """Revisão da F5.4 (R-09): a tabela de preços com erro ganha a borda `danger`, como o campo (o `CE_ShapedFrame`)."""
    table = QTableWidget(2, 2)
    table.resize(200, 120)
    theme.set_invalid(table, True)
    left = shown(qtbot, table).pixelColor(0, 60)
    theme.set_invalid(table, False)
    assert near(left, LIGHT.danger, 20) and not near(table.grab().toImage().pixelColor(0, 60), LIGHT.danger, 20)


def test_tabs_are_underlined_in_accent_only_on_the_current(qtbot):
    bar = QTabBar()
    bar.addTab("Ata")
    bar.addTab("Resumo")
    img = shown(qtbot, bar)
    first, second = bar.tabRect(0), bar.tabRect(1)
    assert near(img.pixelColor(first.center().x(), first.bottom() - 1), LIGHT.accent)
    assert not near(img.pixelColor(second.center().x(), second.bottom() - 1), LIGHT.accent)


def test_group_box_is_a_card_with_the_title_inside(qtbot):
    box = QGroupBox("Banco de dados")
    lay = QVBoxLayout(box)
    lay.addWidget(QLabel("Pasta"))
    top_before = lay.contentsMargins().top()
    img = shown(qtbot, box)
    assert near(img.pixelColor(box.width() // 2, box.height() - 6), LIGHT.card)
    assert lay.contentsMargins().top() == top_before + TITLE_INSET and box.property(POLISHED_PROP)
    box.style().polish(box)                                                                  # o espaço do título entra uma vez
    assert lay.contentsMargins().top() == top_before + TITLE_INSET


# ---------------------------------------------------------------- seleção e tom do item (A-05, U-058)
def test_selected_row_uses_selected_and_the_item_tone_is_read_at_paint_time(qtbot, tmp_path, restore_theme):
    table = QTableWidget(2, 2)
    for r in range(2):
        for c in range(2):
            table.setItem(r, c, QTableWidgetItem(f"Atrasada {r}{c}"))
    table.item(0, 0).setData(TONE_ROLE, Tone.DANGER.value)
    table.selectRow(1)
    table.resize(320, 140)
    img = shown(qtbot, table).copy()
    vp = table.viewport().mapTo(table, QPoint(0, 0))
    cell = table.visualItemRect(table.item(1, 1)).translated(vp)
    assert near(img.pixelColor(cell.right() - 3, cell.center().y()), LIGHT.selected)
    toned = table.visualItemRect(table.item(0, 0)).translated(vp)
    assert any(near(QColor(c), LIGHT.danger, 2) for c in colors_in(img, toned))
    theme.apply(qtbot_app(), FakePlatform(tmp_path), Qt.ColorScheme.Dark)                     # a troca de tema não deixa cor velha
    dark = table.grab().toImage()
    assert any(near(QColor(c), DARK.danger, 2) for c in colors_in(dark, toned))


def qtbot_app() -> Any:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


@pytest.fixture
def restore_theme(qapp: Any, tmp_path: Any) -> Iterator[None]:
    yield
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Light)


# ---------------------------------------------------------------- alturas mínimas (Aceite 3)
def test_polish_gives_the_minimum_click_targets_once_and_leaves_qt_parts_alone(qtbot):
    win = QWidget()
    lay = QVBoxLayout(win)
    button, tool, edit, combo, spin, check = QPushButton("Ok"), QToolButton(), QLineEdit(), QComboBox(), QSpinBox(), QCheckBox("Sala")
    tool.setText("✕")
    edit.setClearButtonEnabled(True)
    capped = QPushButton("baixo")
    capped.setMaximumHeight(20)                                                            # quem montou fixou: fica
    for w in (button, tool, edit, combo, spin, check, capped):
        lay.addWidget(w)
    shown(qtbot, win)
    assert min(w.height() for w in (button, tool, edit, combo, spin)) >= 32 and tool.width() >= 32 and check.height() >= 24
    assert capped.height() <= 20 and capped.minimumHeight() < 32
    inner = edit.findChildren(QToolButton)
    assert inner and all(b.minimumHeight() < 32 for b in inner)                             # o botão de limpar do campo


def test_polish_forwards_the_palette_and_the_app_to_the_base(qapp):
    style = ThemeStyle("Fusion", LIGHT)
    pal = QPalette()
    style.polish(pal)
    style.polish(qapp)
    assert isinstance(pal, QPalette)
    style.deleteLater()
