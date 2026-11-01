"""F5.11 L2, a reunião aberta: o cabeçalho numa linha, com o "⋯" e a legenda (U-102); o título que grava no Enter, ao sair do
campo e ao fechar, com o Esc de volta ao gravado (A-22); "Reprocessar…" que pergunta antes, com o tempo e o custo (P-05); os
`[mm:ss]` da ata e o "Quando" das Ações tocando a fala mais perto, só dentro da duração (U-104, A-24); os atalhos do player
sem tirar a tecla de quem a usa, por tipo de foco (U-105, A-38, P-06); a duração antes de tocar (U-118) e os títulos da ata
um passo abaixo do título da janela (U-119). As linhas de pedido das abas (U-103, A-23, A-37) estão em
`tests/test_llm_request_bar.py`."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from listener.model import ClusterStatus, SummaryKind
from listener.store.actions import ActionInsert
from listener.store.segments import ClusterInsert
from listener.store.summaries import SummaryInsert
from tests.fake_player import FakeOutputs
from tests.test_rag_index import _meeting, _people

pytest.importorskip("PySide6")

MINUTES_MD = "## Decisões\n\n- Aprovar a auditoria [01:10]\n- Fechar a proposta [02:21]\n\n[digitado](listener://play?t=999999)\n"


@pytest.fixture
def players():
    """Os players do teste param e esperam a thread no fim, mesmo com uma asserção falhando (veja `tests/test_rag_ui.py`)."""
    made: list[Any] = []
    yield made.append
    for player in reversed(made):
        player.shutdown()


def _window(qtbot: Any, store: Any, players: Any, **kw: Any) -> SimpleNamespace:
    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player

    who = _people(store)
    mid, job = _meeting(store, who)
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", MINUTES_MD, "gemini", "m", "minutes/1.0", job, None,
                                               "pt-BR"))
    player = Player(store, FakeOutputs())
    players(player)
    win = MeetingWindow(store, mid, player=player, **kw)
    qtbot.addWidget(win)
    win.show()
    return SimpleNamespace(mid=mid, job=job, sid=sid, who=who, win=win, player=player)


# ---------------------------------------------------------------- U-102: o cabeçalho
def test_the_header_is_one_line_with_the_more_menu_and_the_legend(qtbot, store, players):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton

    reviewed: list[int] = []
    m = _window(qtbot, store, players, on_review=reviewed.append)
    win = m.win
    centers = [w.mapTo(win, QPoint(0, w.height() // 2)).y() for w in (win.llm.title_tab.title, win.btn_export, win.btn_more)]
    assert max(centers) - min(centers) <= 6 and win.btn_more.text() == "⋯" and win.btn_more.accessibleName() == "Mais ações"
    assert not any(b.text() in ("Salvar", "LLM", "Gerar ata e ações", "Gerar resumo") for b in win.findChildren(QPushButton)
                   if b.isVisibleTo(win) and not win.tabs.isAncestorOf(b))           # nada disso no cabeçalho
    shown = [a.text() for a in win.more_menu.actions() if a.isVisible() and not a.isSeparator()]
    assert shown[0] == "Revisar falantes…" and not win.btn_review.isVisible()         # todas as vozes com nome: no "⋯"
    for text in ("Revisar a transcrição com IA", "Reprocessar…", win.language_bar.action.text(), win.llm.title_tab.act_regen.text()):
        assert text in shown, text
    assert all(w in win.info.text() for w in ("5 min", "Participantes: Ana — CFO, Bia", "Idioma: pt-BR"))   # a legenda
    win.act_review.trigger()
    assert reviewed == [m.mid]
    store.segments.insert_clusters([ClusterInsert(m.mid, m.job, "R", "SPK_9", 10, None, None, ClusterStatus.UNNAMED)])
    win.reload()                                                                     # uma voz pendente: o botão à vista
    assert win.btn_review.isVisible() and not win.act_review.isVisible()


def test_the_title_saves_on_enter_on_leaving_and_on_close_and_esc_goes_back(qtbot, store, players):
    from PySide6.QtCore import Qt

    edited: list[tuple[Any, ...]] = []
    m = _window(qtbot, store, players, on_edited=lambda *a: edited.append(a))
    title, stored = m.win.llm.title_tab.title, lambda: store.get_meeting(m.mid)["title"]
    revisions = lambda: store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'meeting' AND field = 'title'")[0][0]  # noqa: E731
    qtbot.keyClicks(title, " revisto")
    qtbot.keyClick(title, Qt.Key.Key_Return)                                         # o Enter grava (sai o "Salvar")
    assert stored() == "Orçamento 2027 revisto" and revisions() == 1 and edited == [(m.mid, "edit")]
    qtbot.keyClicks(title, " de novo")
    qtbot.keyClick(title, Qt.Key.Key_Escape)                                         # o Esc volta ao gravado, sem gravar
    assert title.text() == "Orçamento 2027 revisto" and not title.isModified() and revisions() == 1
    title.editingFinished.emit()                                                     # sair do campo sem mudança: nada
    qtbot.keyClicks(title, " final")
    title.editingFinished.emit()                                                     # sair do campo mexido grava
    assert stored() == "Orçamento 2027 revisto final" and revisions() == 2
    title.selectAll()
    qtbot.keyClick(title, Qt.Key.Key_Delete)
    title.editingFinished.emit()                                                     # vazio não grava
    assert stored() == "Orçamento 2027 revisto final" and revisions() == 2
    title.setText("Comitê")                         # o `keyClicks` do QTest derruba o processo com letra fora do ASCII
    title.setModified(True)
    m.win.close()                                                                    # fechar com o título mexido grava
    assert stored() == "Comitê" and revisions() == 3


def test_reprocess_in_the_more_menu_asks_first_with_time_and_cost(qtbot, store, players):
    from listener.pipeline import reprocess

    m = _window(qtbot, store, players)
    asked: list[tuple[str, str, bool]] = []

    def cancel(_parent: Any, text: str, button: str, boxes: Any) -> None:
        asked.append((text, button, boxes is not None))

    m.win.language_bar.confirm = cancel
    m.win.language_bar.act_reprocess.trigger()
    text, button, boxes = asked[0]
    assert text.startswith("Reprocessar esta reunião?") and "Tempo estimado" in text and button == "Reprocessar" and boxes
    assert not reprocess.active_jobs(store, m.mid)                                   # cancelou: nada na fila
    m.win.language_bar.confirm = lambda *_a: frozenset()
    m.win.language_bar.act_reprocess.trigger()
    jobs = reprocess.active_jobs(store, m.mid)
    assert len(jobs) == 1 and jobs[0]["kind"] == "reprocess"                         # confirmou: no mesmo idioma


# ---------------------------------------------------------------- U-104 e U-119: a leitura da ata
def test_the_minutes_stamps_play_the_nearest_speech_only_inside_the_duration(qtbot, store, players):
    from PySide6.QtCore import QUrl

    from listener.ui.theme import font_for

    m = _window(qtbot, store, players)
    m.win.show_tab("ata")
    ata = m.win.llm.text_tabs[str(SummaryKind.MINUTES)]
    doc = ata.reader.document()
    hrefs, heading = set(), None
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().headingLevel() and heading is None:
            heading = block.begin().fragment().charFormat().font().pixelSize()
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isAnchor():
                hrefs.add(fmt.anchorHref())
            it += 1
        block = block.next()
    assert {"listener://play?t=70000", "listener://play?t=141000"} <= hrefs
    assert heading == font_for("body_strong").pixelSize() < m.win.llm.title_tab.title.font().pixelSize()   # U-119
    assert ata.editor.toPlainText() == MINUTES_MD                                    # o editor (e o .md) ficam como estão
    ata.reader.anchorClicked.emit(QUrl("listener://play?t=70000"))                    # [01:10]: a fala das 70 s
    ata.reader.anchorClicked.emit(QUrl("listener://play?t=141000"))                   # [02:21]: a mais perto, até 5 s (a das 140 s)
    assert [r.t_ms for r in m.player.requests] == [69_850, 139_850]
    for link in ("listener://play?t=999999", "https://exemplo.com", "listener://open?m=1&t=0"):
        ata.reader.anchorClicked.emit(QUrl(link))                                    # fora da duração ou outro link: nada
    assert len(m.player.requests) == 2
    store.actions.insert_many([ActionInsert(m.mid, m.sid, "Mandar a planilha", None, m.who["bia"], None, 200_000, None, m.job)])
    m.win.reload()
    table = m.win.llm.actions_tab.table
    assert table.item(0, 4).text() == "03:20" and table.item(0, 4).toolTip()
    table.cellClicked.emit(0, 4)                                                     # o "Quando" das Ações toca
    table.cellClicked.emit(0, 0)                                                     # a Ação, não
    assert [r.t_ms for r in m.player.requests][2:] == [199_850]


# ---------------------------------------------------------------- U-105 e A-38: as teclas, por tipo de foco
def test_space_and_arrows_reach_the_player_only_outside_widgets_that_use_them(qtbot, store, players, monkeypatch):
    from PySide6.QtCore import Qt

    m = _window(qtbot, store, players)
    win = m.win
    seen: list[str] = []
    for name in ("toggle", "back", "forward"):
        monkeypatch.setattr(win.bar, name, lambda _n=name: seen.append(_n))
    win.transcript.on_space = win.bar.toggle
    assert win.bar.time.text() == "00:00 / 05:00"                                    # U-118: a duração antes de tocar
    win.setFocus()                                                                   # a janela, sem widget que use a tecla
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Right, Qt.Key.Key_Left):
        qtbot.keyClick(win, key)
    qtbot.keyClick(win, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)          # com modificador: não é do player
    assert seen == ["toggle", "forward", "back"]
    title = win.llm.title_tab.title                                                  # campo de texto: a tecla é dele
    title.setFocus()
    qtbot.keyClick(title, Qt.Key.Key_Space)
    qtbot.keyClick(title, Qt.Key.Key_Left)
    assert title.text().endswith(" ") and seen == ["toggle", "forward", "back"]
    qtbot.keyClick(title, Qt.Key.Key_Escape)
    button = win.btn_more                                                            # botão: o Espaço o aperta
    button.setFocus()
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Right):
        qtbot.keyClick(button, key)
    assert win.more_menu.isVisible() and seen == ["toggle", "forward", "back"]
    win.more_menu.close()
    tab_bar, before = win.tabs.tabBar(), win.tabs.currentIndex()                      # abas: as setas trocam de aba
    tab_bar.setFocus()
    qtbot.keyClick(tab_bar, Qt.Key.Key_Right)
    qtbot.keyClick(tab_bar, Qt.Key.Key_Space)
    assert win.tabs.currentIndex() == before + 1 and seen == ["toggle", "forward", "back"]
    win.show_tab("transcricao")                                                      # P-06: na lista, o Espaço pausa e retoma
    view = win.transcript.view
    view.setFocus()
    view.setCurrentIndex(win.transcript.model.index(1))
    qtbot.keyClick(view, Qt.Key.Key_Space)
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert seen[-1] == "toggle" and len(seen) == 4 and not win.transcript.played and view.currentIndex().row() == 2
    qtbot.keyClick(view, Qt.Key.Key_Return)                                          # o Enter segue tocando a fala
    assert list(win.transcript.played) == [(win.transcript.model.rows[2].play_from_ms, win.transcript.model.rows[2].t_end_ms)]
    for tip, name in ((win.bar.btn_play.toolTip(), "Espaço"), (win.bar.btn_back.toolTip(), "←"), (win.bar.btn_fwd.toolTip(), "→")):
        assert f"(tecla {name})" in tip                                               # a dica mostra a tecla
