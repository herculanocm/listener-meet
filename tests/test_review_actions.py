"""F5.11 L1, a revisão de vozes pelo resultado: os rótulos por estado da voz (U-098, A-19, sem gênero), a linha do que a
confirmação ensina e como desfazer (U-101; sem vetor, sem promessa), o Enter que faz o que o principal diz e nada com ele
apagado, o "Ouvir 5 s" pintado no cartão selecionado (U-099, A-20; o Espaço segue), o "Mais" com M, I e E (U-100, A-21) e
o nome comprido cortado no meio numa coluna fixa. A P-03 (ignorar ou mesclar numa voz sem pessoa tira o reforço) está em
`tests/test_review.py`, com a lógica sem Qt."""
from __future__ import annotations

from dataclasses import replace

import pytest

from listener.model import ClusterStatus
from listener.pipeline import review
from listener.pipeline.review import ReviewItem
from listener.pipeline.voices import VoiceNames
from listener.ui.review_actions import CHOOSE_TEXT, NO_CONTEXT, UNDO, Primary, ReviewActions, action_labels
from tests.test_review import _processed

pytest.importorskip("PySide6")

BASE = ReviewItem(7, "SPK_1", "R", ClusterStatus.AMBIGUOUS, 60_000, 3, "Ana", 0.7, 0.03, None, True)
NAMES = VoiceNames({7: 2, 9: 3})
CONTEXT = {"llm_person_id": 4, "llm_name": "Rodrigo", "llm_confidence": 0.8}


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


# ---------------------------------------------------------------- A-19: a tabela, sem Qt
@pytest.mark.parametrize(("case", "item", "primary", "text", "context"), [
    ("pela voz", BASE, Primary.CONFIRM, "É Ana", None),
    ("pela voz e pelo contexto", replace(BASE, **CONTEXT), Primary.CONFIRM, "É Ana", "É Rodrigo, pelo contexto"),
    ("sem nome, parecida com", replace(BASE, status=ClusterStatus.UNNAMED), Primary.CONFIRM, "É Ana", None),
    ("automática", replace(BASE, status=ClusterStatus.AUTO, **CONTEXT), Primary.CONFIRM, "Confirmar Ana", None),   # CP11
    ("sem candidato", replace(BASE, status=ClusterStatus.UNNAMED, person_id=None, person_name="", **CONTEXT), Primary.CHOOSE,
     CHOOSE_TEXT, "É Rodrigo, pelo contexto"),
    ("ignorada", replace(BASE, status=ClusterStatus.IGNORED), Primary.CONFIRM, "É Ana", None),
    ("confirmada", replace(BASE, status=ClusterStatus.CONFIRMED), Primary.NONE, "É Ana", None),
    ("mesclada", replace(BASE, merged_into=9, **CONTEXT), Primary.NONE, "É a mesma que a Voz 3", None),
])
def test_action_labels_follow_the_state_of_the_voice(case, item, primary, text, context):
    acts = action_labels(item, NAMES)
    assert (acts.primary, acts.primary_text, acts.context_text) == (primary, text, context), case
    shown = f"{acts.primary_text} {acts.context_text or NO_CONTEXT} {acts.line}"
    assert "pyannote" not in shown and "LLM" not in shown                    # U-106: o vocabulário da pergunta 2
    assert " o Ana" not in shown and " a Ana" not in shown                   # P-01: sem gênero


def test_the_learning_line_promises_only_what_the_confirmation_does():
    assert action_labels(BASE, NAMES).line == f"Ao confirmar, o perfil de voz de Ana aprende com esta fala. {UNDO}"
    assert action_labels(replace(BASE, has_centroid=False), NAMES).line == "Ao confirmar, as falas desta voz ficam com o nome de Ana."
    chooses = replace(BASE, person_id=None, person_name="")
    assert UNDO in action_labels(chooses, NAMES).line and "aprende" not in action_labels(replace(chooses, has_centroid=False), NAMES).line
    done = replace(BASE, status=ClusterStatus.CONFIRMED, learned=True)
    assert action_labels(done, NAMES).line == f"O perfil de voz de Ana aprendeu com esta fala. {UNDO}"
    carried = action_labels(replace(done, learned=False), NAMES).line          # o vetor está na voz de antes do reprocessamento
    assert carried == "As falas desta voz estão com o nome de Ana." and UNDO not in carried
    assert action_labels(replace(BASE, merged_into=9), NAMES).line == "Mesclada com a Voz 3."
    assert all(way in UNDO for way in ("escolha outra pessoa", "ignore", "mescle numa voz sem pessoa"))   # os três da P-03
    assert action_labels(None, NAMES) == ReviewActions(Primary.NONE, "Confirmar", None, "")


# ---------------------------------------------------------------- U-098: o Enter faz o que o principal diz
def test_enter_does_what_the_primary_says_and_nothing_when_it_is_off(qtbot, platform, store, monkeypatch):
    from PySide6.QtCore import Qt

    from listener.ui.review import ReviewDialog

    mid, items = _processed(platform, store)
    ana = store.people.create_person("Ana")
    store.segments.update_cluster(items[0].cluster_id, person_id=ana, status=ClusterStatus.AMBIGUOUS, similarity=0.7, margin=0.03)
    asked: list[int | None] = []
    monkeypatch.setattr(ReviewDialog, "ask_other_person", lambda self: asked.append(self.current_cluster_id()))
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.btn_confirm.text() == "É Ana" and "o perfil de voz de Ana aprende" in dlg.learn.text()
    qtbot.keyClick(dlg.list, Qt.Key.Key_Return)                                      # a sugestão pela voz: confirma
    assert store.segments.get_cluster(items[0].cluster_id)["status"] == ClusterStatus.CONFIRMED
    assert dlg.current_cluster_id() == items[1].cluster_id and dlg.btn_confirm.text() == CHOOSE_TEXT
    qtbot.keyClick(dlg.list, Qt.Key.Key_Return)                                      # sem candidato: a lista, ninguém confirmado
    assert asked == [items[1].cluster_id] and store.segments.get_cluster(items[1].cluster_id)["status"] == ClusterStatus.UNNAMED
    dlg.list.setCurrentRow(0)                                                        # a confirmada: o principal apagado
    assert not dlg.btn_confirm.isEnabled() and dlg.btn_confirm.text() == "É Ana" and "aprendeu" in dlg.learn.text()
    before = store._q("SELECT COUNT(*) FROM revisions")[0][0], len(store.list_events(mid))
    qtbot.keyClick(dlg.list, Qt.Key.Key_Return)
    assert (store._q("SELECT COUNT(*) FROM revisions")[0][0], len(store.list_events(mid))) == before   # nada gravado
    assert asked == [items[1].cluster_id] and dlg.isVisible() and dlg.current_cluster_id() == items[0].cluster_id


# ---------------------------------------------------------------- U-099: o "Ouvir 5 s" no cartão selecionado
def test_the_play_on_the_selected_card_plays_and_space_still_does(qtbot, platform, store):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QStyleOptionViewItem

    from listener.ui.review import ReviewDialog

    mid, items = _processed(platform, store)
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.resize(1200, 600)                       # a fonte do offscreen tem o dobro da largura: o cartão com o espaço do Windows
    dlg.show()
    dlg.list.viewport().grab()                                                       # o cartão selecionado pinta o botão
    rect = dlg.list.visualItemRect(dlg.list.item(1))
    play = dlg.cards.play_rect(rect, dlg.list.model().index(1, 0)).center().toPoint()
    qtbot.mouseClick(dlg.list.viewport(), Qt.MouseButton.LeftButton, pos=play)       # o cartão não selecionado: só seleciona
    assert dlg.list.currentRow() == 1 and dlg.played == []
    qtbot.mouseClick(dlg.list.viewport(), Qt.MouseButton.LeftButton, pos=play)       # agora o botão está à vista: toca
    assert dlg.played == [review.clip_range(store, items[1].cluster_id)]
    qtbot.mouseClick(dlg.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.topLeft() + QPoint(16, rect.height() - 8))
    assert len(dlg.played) == 1                                                      # fora do botão, o clique não toca
    qtbot.keyClick(dlg.list, Qt.Key.Key_Space)                                       # o Espaço segue
    assert len(dlg.played) == 2
    option = QStyleOptionViewItem()
    option.rect = rect
    tip = QHelpEvent(QEvent.Type.ToolTip, play, dlg.list.viewport().mapToGlobal(play))
    assert dlg.cards.helpEvent(tip, dlg.list, option, dlg.list.model().index(1, 0))       # a dica diz a tecla


# ---------------------------------------------------------------- U-100: o "Mais", com as teclas de sempre
def test_more_menu_has_the_rare_actions_and_m_i_e_still_work(qtbot, platform, store, monkeypatch):
    from PySide6.QtCore import Qt

    from listener.ui.review import ReviewDialog
    from listener.ui.review_cards import KeyButton

    mid, _items = _processed(platform, store)
    seen: list[str] = []

    def record(name: str):
        def method(_self: object) -> None:          # sem parâmetro a mais: o `triggered(bool)` não entra (como nos de verdade)
            seen.append(name)
        return method

    for name in ("ask_merge", "ignore_current", "edit_current"):
        monkeypatch.setattr(ReviewDialog, name, record(name))
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    actions = [a for a in dlg.btn_more.menu().actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["É a mesma que…\tM", "Ignorar\tI", "Editar pessoa\tE", "Sugerir de novo pelo contexto"]
    assert all(a.shortcut().isEmpty() for a in actions)                              # A-21: as teclas são do `handle_key`
    assert {b.key for b in dlg.findChildren(KeyButton)} == {"Enter", "L", "O", "N", ""}    # M, I, E e o tocar saíram da coluna
    assert dlg.btn_more.text() == "Mais" and dlg.btn_more.menu() is not None and not dlg.btn_more.grab().isNull()   # à esquerda
    for key in (Qt.Key.Key_M, Qt.Key.Key_I, Qt.Key.Key_E):
        qtbot.keyClick(dlg.list, key)
    for action in actions[:3]:
        action.trigger()
    assert dlg.act_merge.isEnabled() and dlg.act_ignore.isEnabled() and not dlg.act_edit.isEnabled()   # pendente: sem ficha
    assert seen == ["ask_merge", "ignore_current", "edit_current", "ask_merge", "ignore_current"]   # o E explica; o item apagado, não


# ---------------------------------------------------------------- A-19: a coluna fixa, o nome cortado no meio
def test_a_long_name_is_cut_in_the_middle_without_widening_the_column(qtbot, platform, store):
    from listener.ui.review import ReviewDialog

    mid, items = _processed(platform, store)
    long_name = "Maria Eduarda Albuquerque de Figueiredo"
    person = store.people.create_person(long_name)
    store.segments.update_cluster(items[1].cluster_id, person_id=person, status=ClusterStatus.AMBIGUOUS)
    store.segments.set_llm_suggestion(items[1].cluster_id, person, None, 0.8, "[]")
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(items[0].cluster_id))
    width = dlg.btn_confirm.width()
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(items[1].cluster_id))
    assert dlg.btn_confirm.text() == f"É {long_name}" and dlg.btn_llm.text() == f"É {long_name}, pelo contexto"
    assert dlg.btn_confirm.width() == width == dlg.btn_other.width()                   # a coluna não cresce com o nome
    assert dlg.btn_llm.width() < dlg.btn_llm.full_width                                # não cabe: corta (no meio, no `paintEvent`)
    assert dlg.btn_llm.toolTip() == f"É {long_name}, pelo contexto (tecla L)"           # inteiro na dica
    dlg.btn_llm.grab()
