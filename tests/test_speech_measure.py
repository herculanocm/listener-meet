"""Revisão da F5.4 (R-06): a altura da fala sai do C++ (`QFontMetrics.boundingRect`, uma chamada por fala), não de um
`QTextLayout` montado em Python por fala e por largura (a transcrição de 3.000 falas redimensionava ~60 % mais devagar). A
medida e a pintura quebram igual (a mesma tabulação na largura do cabeçalho) e o desenho é o de antes: a altura bate com a
do `QTextLayout` com o nome em negrito; o cabeçalho que não cabe na linha e a palavra maior que a linha seguem no desenho
de antes."""
from __future__ import annotations

import random
from typing import Any

import pytest

pytest.importorskip("PySide6")

WORDS = ("a", "reunião", "de", "hoje", "tratou", "do", "orçamento", "trimestre", "e", "das", "metas", "vendas", "com", "equipe",
         "comercial", "bem-vindo", "e-mail", "R$", "1.234,56", "(nota)", "—", "ok,", "sim;", "não!", "2026/10/28")
NAMES = (("Ana", ""), ("Voz 2", ""), ("Bruno Carvalho", "CFO"), ("Maria Aparecida dos Santos", "Gerente de TI"), ("", ""))


def _rows(n: int) -> list[tuple[str, str, str]]:
    rng = random.Random(7)                                    # noqa: S311 (texto de teste, não segredo)
    return [(*rng.choice(NAMES), " ".join(rng.choices(WORDS, k=rng.randint(1, 70)))) for _ in range(n)]


def _model(rows: list[tuple[str, str, str]]) -> Any:
    from PySide6.QtGui import QStandardItem, QStandardItemModel

    from listener.ui.speech_delegate import ROLE_ROLE, SPEAKER_ROLE, TEXT_ROLE

    model = QStandardItemModel()
    for name, role, text in rows:
        item = QStandardItem(text)
        for key, value in ((SPEAKER_ROLE, name), (ROLE_ROLE, role), (TEXT_ROLE, text)):
            item.setData(value, key)
        model.appendRow(item)
    return model


def test_the_height_comes_from_cpp_without_a_python_layout_per_speech(qtbot, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QTextLayout
    from PySide6.QtWidgets import QListView, QStyleOptionViewItem

    from listener.ui import speech_delegate

    created: list[int] = []

    class Counted(QTextLayout):
        def __init__(self, *args: Any) -> None:
            created.append(1)
            super().__init__(*args)

    monkeypatch.setattr(speech_delegate, "QTextLayout", Counted)
    view = QListView()
    qtbot.addWidget(view)
    rows = [(name, role, text) for name, role, text in _rows(300) if name != NAMES[3][0]]   # cabeçalhos que cabem na linha
    model = _model(rows)
    delegate = speech_delegate.SpeechDelegate(view)
    opt = QStyleOptionViewItem()
    opt.initFrom(view)
    opt.rect = QRect(0, 0, 700, 20)
    for row in range(model.rowCount()):
        assert delegate.sizeHint(opt, model.index(row, 0)).height() > 0
    assert len(created) <= 1                                                         # só a sonda da altura da linha, uma vez


def test_the_measured_height_is_the_painted_one_and_the_drawing_of_before(qtbot):
    from PySide6.QtWidgets import QListView

    from listener.ui.speech_delegate import EXACT, SpeechDelegate

    view = QListView()
    qtbot.addWidget(view)
    delegate = SpeechDelegate(view)
    body = view.font()
    fast = 0
    for width in (260, 451, 777):
        for name, role, text in _rows(400):
            height, indent = delegate._measure(body, name, role, text, width)
            before = round(delegate._layout(body, name, role, text, width).boundingRect().height())
            if indent != EXACT:
                fast += 1
                painted = round(delegate.body_layout(body, text, indent, width).boundingRect().height())
                assert height == painted, (width, name, len(text))                    # a medida é a da pintura
            assert height == before, (width, name, len(text))                        # e o desenho é o de antes
    assert fast > 900                                                                # quase tudo pelo caminho do C++
    long_word = "x" * 400
    long_head = "Nome Muito Comprido " * 6
    assert delegate._measure(body, "Ana", "", long_word, 300)[1] == EXACT            # a palavra maior que a linha
    assert delegate._measure(body, long_head, "Gerente", "texto", 300)[1] == EXACT   # o cabeçalho maior que a linha
