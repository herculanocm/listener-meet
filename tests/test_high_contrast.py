"""Revisão da F5.4 (R-03): com o contraste alto do Windows fica a paleta do sistema, e os tokens saem dela; o que o app pinta à
mão segue o sistema. O texto de cartão, faixa, selo, fala e linha selecionada tem pelo menos 4,5:1 sobre o fundo pintado,
num tema de contraste escuro e num claro (paletas como as do Windows 11: "Aquático", "Céu noturno" e "Deserto"). A medida é
no que foi pintado: o fundo é a cor mais comum da área do texto e o texto, a cor de maior contraste com ele."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6")

# janela, texto, base, destaque, texto do destaque, texto apagado
HC_PALETTES = {
    "aquatico": ("#202020", "#ffffff", "#202020", "#8ee3f0", "#263b50", "#a6a6a6"),
    "ceu-noturno": ("#000000", "#ffffff", "#000000", "#d6b4fd", "#2b2b2b", "#a6a6a6"),
    "deserto": ("#fffaef", "#3d3d3d", "#fffaef", "#903909", "#fff5e3", "#676767"),
}


@pytest.fixture(params=list(HC_PALETTES), ids=list(HC_PALETTES))
def high_contrast(request: Any, qapp: Any, monkeypatch: Any, tmp_path: Any) -> Iterator[tuple[str, ...]]:
    """O contraste alto ligado com a paleta de sistema dada (o que o `QPalette()` do app daria no Windows); no fim, o tema
    claro da sessão de volta."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    from listener.ui import theme
    from tests.fake_platform import FakePlatform

    window, text, base, highlight, highlighted, disabled = HC_PALETTES[request.param]
    roles = QPalette.ColorRole
    pal = QPalette()
    for role, value in ((roles.Window, window), (roles.WindowText, text), (roles.Base, base), (roles.Text, text),
                        (roles.Button, window), (roles.ButtonText, text), (roles.Highlight, highlight),
                        (roles.HighlightedText, highlighted), (roles.PlaceholderText, disabled)):
        pal.setColor(role, QColor(value))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled))

    def system(app: Any) -> Any:
        app.setPalette(pal)
        return app.palette()

    monkeypatch.setattr(theme, "_system_palette", system)
    monkeypatch.setattr(theme, "_high_contrast", lambda: True)
    theme.notifier().on_contrast_changed(Qt.ContrastPreference.HighContrast)
    yield HC_PALETTES[request.param]
    monkeypatch.undo()
    theme.apply(qapp, FakePlatform(tmp_path), Qt.ColorScheme.Light)


def _measure(img: Any, left: int, top: int, width: int, height: int) -> tuple[str, float]:
    """O fundo (a cor mais comum) da área e o contraste da cor que mais contrasta com ele (o traço cheio do texto)."""
    from PySide6.QtGui import QColor

    from listener.ui.theme import contrast

    colors = Counter(QColor(img.pixel(x, y)).name() for x in range(left, left + width) for y in range(top, top + height))
    background = colors.most_common(1)[0][0]
    return background, max(contrast(c, background) for c in colors)


def _area(widget: Any, root: Any, rect: Any = None) -> tuple[int, int, int, int]:
    from PySide6.QtCore import QPoint

    r = rect if rect is not None else widget.rect()
    at = widget.mapTo(root, QPoint(r.left(), r.top()))
    return at.x(), at.y(), r.width(), r.height()


def test_hand_painted_text_follows_the_system_palette_with_4_5_to_1(qtbot, high_contrast):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QLabel, QListView, QListWidget, QVBoxLayout, QWidget

    from listener.ui.components import Badge, Card, InfoBar
    from listener.ui.speech_delegate import ROLE_ROLE, SPEAKER_ROLE, STAMP_ROLE, TEXT_ROLE, SpeechDelegate
    from listener.ui.theme import CONTRAST_SCHEME, TEXT_MIN, Tone, tokens

    window, text, base, highlight, highlighted, _disabled = high_contrast
    root = QWidget()
    lay = QVBoxLayout(root)
    card = Card("Captura")
    label = QLabel("Texto dentro do cartão")
    card.content.addWidget(label)
    bar = InfoBar("warning", "Aviso numa faixa")
    badge = Badge("pausado", Tone.WARNING)
    rows = QListWidget()
    rows.addItems(["Linha comum", "Linha selecionada"])
    speech = QListView()
    model = QStandardItemModel(speech)
    for i, line in enumerate(("Ana disse o que a ata vai registrar", "Bia respondeu com o prazo")):
        item = QStandardItem(line)
        for role, value in ((STAMP_ROLE, 61_000 * i), (SPEAKER_ROLE, line.split()[0]), (ROLE_ROLE, "CFO"), (TEXT_ROLE, line)):
            item.setData(value, role)
        model.appendRow(item)
    speech.setModel(model)
    delegate = SpeechDelegate(speech)
    speech.setItemDelegate(delegate)
    for w in (card, bar, badge, rows, speech):
        lay.addWidget(w)
    qtbot.addWidget(root)
    root.resize(520, 520)
    root.show()
    rows.setCurrentRow(1)
    speech.selectionModel().setCurrentIndex(model.index(0, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect)
    qtbot.waitExposed(root)
    img = root.grab().toImage()
    speech_row = speech.visualRect(model.index(0, 0))
    speech_text = speech_row.adjusted(delegate.gutter(), 0, 0, 0)
    plain_row = speech.visualRect(model.index(1, 0))
    areas = {
        "rótulo no cartão": (_area(label, root), window),
        "faixa": (_area(bar.label, root), window),
        "selo": (_area(badge, root), window),
        "linha selecionada": (_area(rows.viewport(), root, rows.visualItemRect(rows.item(1))), highlight),
        "fala selecionada": (_area(speech.viewport(), root, speech_text), highlight),
        "fala": (_area(speech.viewport(), root, plain_row.adjusted(delegate.gutter(), 0, 0, 0)), base),
    }
    failures = []
    for name, (area, expected) in areas.items():
        background, ratio = _measure(img, *area)
        if background != expected or ratio < TEXT_MIN:
            failures.append(f"{name}: fundo {background} (esperado {expected}), texto {ratio:.2f}:1")
    assert not failures, failures
    t = tokens()
    assert t.scheme == CONTRAST_SCHEME and (t.window, t.text, t.input, t.selected, t.selected_text) == (window, text, base, highlight,
                                                                                                        highlighted)
