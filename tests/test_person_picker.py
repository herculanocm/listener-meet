"""F5.25: a escolha da pessoa na revisão com busca. A parte pura (o filtro sem acento e sem maiúscula por nome, cargo e
empresa, a ordem com texto, as seções com o campo vazio, os nomes parecidos e o percentual da nota) e a janela."""
from __future__ import annotations

from dataclasses import replace

import pytest

from listener.ui.person_picker import (ALL_TITLE, BY_VOICE_TITLE, EXPECTED_TITLE, SIMILAR_TITLE, Candidate, PersonIndex, fold,
                                       percent, top_by_voice)

ME = Candidate(1, "Rui Alves", "Rui Alves", score=0.31)
ANA = Candidate(2, "Ana Souza — CFO · Acme", "Ana Souza", "CFO", "Acme", score=0.58)
ANA_TI = Candidate(3, "Ana Lima — TI", "Ana Lima", "TI", score=0.20)
BIA = Candidate(4, "Bia Lima — CFO", "Bia Lima", "CFO", score=0.51)
CAIO = Candidate(5, "Caio Reis — TI", "Caio Reis", "TI", score=0.12, expected=True)
DAVI = Candidate(6, "Davi Luz — Vendas", "Davi Luz", "Vendas")
JOAO = Candidate(7, "João Silva", "João Silva")
CONCEICAO = Candidate(8, "Maria Conceição — Jurídico", "Maria Conceição", "Jurídico")
PEOPLE = [ME, ANA, ANA_TI, BIA, CAIO, DAVI, JOAO, CONCEICAO]          # a ordem de `list_people`: você primeiro


def _ids(rows) -> list[int]:
    return [c.person_id for c in rows]


# ---------------------------------------------------------------- normalização e casamento (Comportamento 2 e 3)
def test_fold_drops_accents_and_case():
    assert fold("João") == "joao" and fold("CONCEIÇÃO") == "conceicao" and fold("Straße") == "strasse"


@pytest.mark.parametrize(("typed", "found"), [
    ("joao", [7]), ("JOÃO", [7]), ("conceicao", [8]), ("sou", [2]),              # sem acento, sem maiúscula, em qualquer posição
    ("ana cfo", [2]), ("cfo ana", [2]),                                            # cada palavra, em qualquer campo
    ("acme", [2]), ("jurid", [8]),                                                 # a empresa e o cargo
    ("  davi   luz ", [6]),                                                         # espaços sobrando
])
def test_every_typed_word_must_appear_in_name_role_or_company(typed, found):
    assert _ids(PersonIndex(PEOPLE, []).view(typed).rows) == found


def test_the_email_is_not_searched():
    people = [replace(ANA, label="Ana Souza")]                                     # o e-mail nem chega ao índice
    assert PersonIndex(people, []).view("acme.com").rows == []


def test_a_typo_still_finding_someone_inside_a_word_is_a_result():
    """D-111: "joa silva" acha "João Silva" por pedaço (Comportamento 3), sem passar por "Nomes parecidos"."""
    view = PersonIndex(PEOPLE, []).view("joa silva")
    assert _ids(view.rows) == [7] and not view.similar and view.select_first


# ---------------------------------------------------------------- a ordem com texto (Comportamento 4)
def test_with_text_by_voice_first_then_name_prefix_then_the_rest_expected_first():
    people = [ME, replace(ANA, expected=False), ANA_TI, BIA, CAIO, DAVI,
              Candidate(9, "Lia Ana — TI", "Lia Ana", "TI", expected=True), Candidate(10, "Mariana — TI", "Mariana", "TI"),
              Candidate(11, "Zeca — TI", "Zeca", "TI", expected=True)]
    index = PersonIndex(people, top_by_voice([(2, 0.58), (4, 0.51), (3, 0.20)], None, {p.person_id for p in people}))
    assert index.top == [2, 4]
    assert _ids(index.view("ana").rows) == [2, 9, 3, 10]           # Pela voz; prefixo, a esperada antes; o resto ("mariAna")
    assert _ids(index.view("ti").rows) == [5, 9, 11, 3, 10]        # ninguém pela voz casa; sem prefixo no nome: esperadas, então a ordem


def test_with_text_the_first_row_is_selected_and_the_count_is_of_matches():
    view = PersonIndex(PEOPLE, []).view("lima")
    assert _ids(view.rows) == [3, 4] and view.matched == 2 and view.select_first
    assert [s.title for s in view.sections] == [""]                                 # sem seções


# ---------------------------------------------------------------- campo vazio: as seções (U-174, U-176)
def test_empty_field_has_the_three_sections_each_person_once():
    top = top_by_voice([(2, 0.58), (4, 0.51), (1, 0.31)], None, {p.person_id for p in PEOPLE})
    view = PersonIndex([*PEOPLE[:4], replace(BIA, person_id=40, expected=True), *PEOPLE[4:]], top).view("")
    assert [s.title for s in view.sections] == [BY_VOICE_TITLE, EXPECTED_TITLE, ALL_TITLE]
    assert [_ids(s.rows) for s in view.sections] == [[2, 4], [40, 5], [1, 3, 6, 7, 8]]   # você primeiro em "Todas"
    assert len(set(_ids(view.rows))) == len(view.rows) == view.matched == 9 and view.select_first


def test_empty_field_without_by_voice_selects_nothing():
    view = PersonIndex(PEOPLE, []).view("")
    assert [s.title for s in view.sections] == [EXPECTED_TITLE, ALL_TITLE] and not view.select_first


def test_an_expected_person_in_by_voice_appears_only_there():
    view = PersonIndex(PEOPLE, [5]).view("")
    assert [(s.title, _ids(s.rows)) for s in view.sections][:1] == [(BY_VOICE_TITLE, [5])]
    assert _ids(view.rows).count(5) == 1 and EXPECTED_TITLE not in [s.title for s in view.sections]


@pytest.mark.parametrize(("ranked", "suggested", "top"), [
    ([(2, 0.9), (4, 0.8), (3, 0.7), (5, 0.6)], None, [2, 4, 3]),                    # até 3
    ([(2, 0.9), (4, 0.8), (3, 0.7), (5, 0.6)], 4, [2, 3, 5]),                       # sem a já sugerida
    ([(2, 0.46), (4, 0.45), (3, 0.449)], None, [2, 4]),                             # o piso de 0,45 (D-089)
    ([(99, 0.9), (2, 0.5)], None, [2]),                                              # só quem está na lista
    ([], None, []),
])
def test_top_by_voice_cuts_the_ranking_like_before(ranked, suggested, top):
    assert top_by_voice(ranked, suggested, {1, 2, 3, 4, 5}) == top


# ---------------------------------------------------------------- nomes parecidos (U-175)
def test_a_typo_without_result_brings_similar_names():
    view = PersonIndex(PEOPLE, []).view("jaoo silva")
    assert view.similar and view.select_first and view.matched == 0
    assert [(s.title, _ids(s.rows)) for s in view.sections] == [(SIMILAR_TITLE, [7])]
    assert _ids(PersonIndex(PEOPLE, []).view("cieo").rows) == [5]                  # o primeiro nome ("caio")


def test_similar_names_need_three_letters_and_a_close_name():
    index = PersonIndex(PEOPLE, [])
    for typed in ("xq", "zzzzzz"):
        view = index.view(typed)
        assert view.sections == () and not view.select_first and not view.similar


def test_at_most_three_similar_names_most_similar_first():
    people = [Candidate(i, n, n) for i, n in enumerate(["Mariano", "Maria", "Mariana", "Marian", "Mraia"], 1)]
    assert _ids(PersonIndex(people, []).view("mraian").rows) == [5, 4, 1]       # 0,91, 0,83 e 0,77 (Mariana, 0,77, fica de fora)


# ---------------------------------------------------------------- o percentual (U-173)
@pytest.mark.parametrize(("score", "shown"), [(0.58, "58 %"), (0.585, "59 %"), (1.0, "100 %"), (0.004, "0 %"), (-0.2, "0 %"),
                                              (None, ""), (float("nan"), "")])
def test_percent_is_the_cosine_times_100_without_decimals(score, shown):
    assert percent(score) == shown


# ---------------------------------------------------------------- a janela (U-171, U-172, U-176)
def _picker(qtbot, people=PEOPLE, top=(), threshold=0.6):
    from listener.ui.person_picker import PersonPicker

    picker = PersonPicker(None, PersonIndex(people, list(top)), question="Quem é a Voz 3?", threshold=threshold)
    qtbot.addWidget(picker)
    picker.show()
    return picker


def _key(picker, key, times=1):
    from PySide6.QtTest import QTest

    for _ in range(times):
        QTest.keyClick(picker.search, key)


def _shown(picker) -> list[str]:
    """As linhas da lista como o usuário as vê: os títulos com "#" e as pessoas com o rótulo e a nota."""
    out: list[str] = []
    for i in range(picker.tree.topLevelItemCount()):
        top = picker.tree.topLevelItem(i)
        children = [top.child(j) for j in range(top.childCount())]
        if children:
            out.append(f"# {top.text(0)}")
        out.extend(" | ".join(t for t in (item.text(0), item.text(1), item.text(2)) if t) for item in children or [top])
    return out


def test_the_field_is_born_with_the_focus_and_the_sections_with_the_voice_first(qtbot):
    from PySide6.QtCore import Qt

    picker = _picker(qtbot, top=[2, 4])
    assert picker.windowTitle() == "Outra pessoa" and picker.question.text() == "Quem é a Voz 3?"
    assert picker.focusWidget() is picker.search and picker.search.isClearButtonEnabled()
    assert picker.search.placeholderText() == "Buscar por nome, cargo ou empresa"
    assert _shown(picker) == ["# Pela voz", "Ana Souza — CFO · Acme | 58 %", "Bia Lima — CFO | 51 %",
                              "# Esperadas nesta reunião", "Caio Reis — TI | 12 %",
                              "# Todas", "Rui Alves | 31 %", "Ana Lima — TI | 20 %", "Davi Luz — Vendas", "João Silva",
                              "Maria Conceição — Jurídico"]
    assert picker.selected_id() == 2 and picker.count.text() == "8 pessoas"
    _key(picker, Qt.Key.Key_Down)
    _key(picker, Qt.Key.Key_Down)                                             # pula o título "Esperadas nesta reunião"
    assert picker.selected_id() == 5
    _key(picker, Qt.Key.Key_PageDown)
    assert picker.selected_id() == 8                                          # a página para no fim
    _key(picker, Qt.Key.Key_Enter)
    assert picker.picked.person_id == 8 and picker.result() == picker.DialogCode.Accepted


def test_without_by_voice_nothing_is_selected_and_enter_does_nothing(qtbot):
    from PySide6.QtCore import Qt

    picker = _picker(qtbot)
    assert picker.selected_id() is None and not picker.btn_choose.isEnabled()
    _key(picker, Qt.Key.Key_Return)
    assert picker.picked.person_id is None and picker.picked.new_name is None and picker.isVisible()
    _key(picker, Qt.Key.Key_Down)                                             # as setas escolhem a primeira de propósito
    assert picker.selected_id() == 5 and picker.btn_choose.isEnabled()


def test_typing_filters_at_each_key_and_selects_the_first_row(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    picker = _picker(qtbot, top=[4])
    QTest.keyClicks(picker.search, "lim")
    assert _shown(picker) == ["Bia Lima — CFO | 51 %", "Ana Lima — TI | 20 %"]          # Pela voz antes
    assert picker.selected_id() == 4 and picker.count.text() == "2 de 8 pessoas"
    _key(picker, Qt.Key.Key_Down)
    _key(picker, Qt.Key.Key_Down)                                             # para na última
    assert picker.selected_id() == 3
    _key(picker, Qt.Key.Key_Up)
    assert picker.selected_id() == 4
    _key(picker, Qt.Key.Key_Backspace, 3)
    assert picker.count.text() == "8 pessoas" and _shown(picker)[0] == "# Pela voz"


def test_a_typo_brings_similar_names_and_enter_picks_the_first(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from listener.ui.theme import KIND_PROP, PRIMARY

    picker = _picker(qtbot)
    QTest.keyClicks(picker.search, "jaoo silva")
    assert _shown(picker) == ["# Nomes parecidos", "João Silva"] and picker.count.text() == 'Ninguém com "jaoo silva".'
    assert picker.btn_new.text() == 'Nova pessoa "jaoo silva"…' and picker.btn_new.property(KIND_PROP) != PRIMARY
    _key(picker, Qt.Key.Key_Return)
    assert picker.picked.person_id == 7


def test_no_result_offers_the_new_person_with_the_typed_name(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from listener.ui.theme import KIND_PROP, PRIMARY

    picker = _picker(qtbot)
    QTest.keyClicks(picker.search, "  Zuleica Prado ")
    assert _shown(picker) == [] and picker.count.text() == 'Ninguém com "Zuleica Prado".'
    assert not picker.btn_choose.isEnabled() and picker.btn_new.property(KIND_PROP) == PRIMARY
    _key(picker, Qt.Key.Key_Enter)
    assert picker.picked.new_name == "Zuleica Prado" and picker.picked.person_id is None


def test_the_new_person_name_is_cut_at_120_and_the_label_is_short(qtbot):
    picker = _picker(qtbot)
    picker.search.setText("x" * 200)
    assert len(picker.search.text()) == 120 and picker.btn_new.text() == f'Nova pessoa "{"x" * 23}…"…'
    picker.btn_new.click()
    assert picker.picked.new_name == "x" * 120


@pytest.mark.parametrize("typed", ["", "ana"])
def test_escape_closes_without_choosing(qtbot, typed):
    from PySide6.QtCore import Qt

    picker = _picker(qtbot, top=[2])
    picker.search.setText(typed)
    _key(picker, Qt.Key.Key_Escape)
    assert picker.picked.person_id is None and picker.picked.new_name is None
    assert picker.result() == picker.DialogCode.Rejected and not picker.isVisible()


def test_double_click_picks_the_row_and_the_titles_do_not_select(qtbot):
    from listener.ui.person_picker import PERSON_ROLE

    picker = _picker(qtbot, top=[2])
    title = picker.tree.topLevelItem(0)
    picker.tree.setCurrentItem(title)
    assert picker.selected_id() is None
    picker.tree.itemDoubleClicked.emit(title, 0)
    assert picker.picked.person_id is None
    picker.tree.itemDoubleClicked.emit(picker.rows[3], 0)
    assert picker.picked.person_id == picker.rows[3].data(0, PERSON_ROLE)


def test_the_score_column_has_the_percent_and_the_tip_the_threshold(qtbot):
    picker = _picker(qtbot, people=[ANA, DAVI, replace(BIA, taken="Voz 2")], threshold=0.6)
    ana, davi, bia = picker.rows
    assert (ana.text(2), davi.text(2), bia.text(1)) == ("58 %", "", "já na Voz 2")
    assert ana.toolTip(0) == ("O quanto esta voz se parece com o perfil de voz de Ana Souza. A partir de 60 %, o app costuma "
                              "reconhecer sozinho.")
    assert davi.toolTip(0) == "" and not picker.tree.isColumnHidden(1)
    assert _picker(qtbot).tree.isColumnHidden(1)                               # ninguém já decidido: sem a coluna
