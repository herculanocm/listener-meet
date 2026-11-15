"""F5.25: a escolha da pessoa na revisão com busca. A parte pura (o filtro sem acento e sem maiúscula por nome, cargo e
empresa, a ordem com texto, as seções com o campo vazio, os nomes parecidos e o percentual da nota) e a janela."""
from __future__ import annotations

from dataclasses import replace

import pytest

from listener.ui.person_picker import (ALL_TITLE, BY_VOICE_TITLE, EXPECTED_TITLE, SIMILAR_TITLE, Candidate, PersonIndex, fold,
                                       percent, top_by_voice)

ME = Candidate(1, "Herculano", "Herculano", score=0.31)
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
