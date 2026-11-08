"""F5.20 L1 (C6): a compactação local das legendas da sugestão ao vivo, pura. Interjeição curta fora e a longa fica; "sim",
"não" e números sempre ficam; a repetição exata em 10 s fora (também o eco entre o L e o R) e em 11 s fica; o corte em 300 no
último espaço com "…"; a junção do mesmo falante até 60 s e a quebra aos 61 s; a linha nova a cada 300 caracteres com o id
dela; os ids com os juntados; o `trim` pelo começo; a medida crua e a compactada; `mentions` por palavra inteira, sem acento
e com 3 letras no mínimo."""
from __future__ import annotations

from typing import Any

from listener.llm.suggest_compact import ELLIPSIS, LINE_MAX, PARTICIPANT, clip, compact, first_name, mentions, speaker, trim

ME = "Carla — CTO"


def _row(rid: int, t0: int, text: str, label: str | None = "Ana — CFO", channel: str = "R") -> dict[str, Any]:
    return {"id": rid, "t_start_ms": t0, "t_end_ms": t0 + 3000, "text": text, "label": label, "channel": channel}


def _texts(rows: list[dict[str, Any]]) -> list[str]:
    return [ln.text for ln in compact(rows, ME).lines]


def test_a_short_interjection_goes_and_a_long_one_stays():
    rows = [_row(1, 0, "Ok."), _row(2, 70_000, "Uhum"), _row(3, 140_000, "Tá bom."), _row(4, 210_000, "Certo, mas o prazo é sexta.")]
    assert _texts(rows) == ["Certo, mas o prazo é sexta."]


def test_yes_no_and_numbers_always_stay():
    rows = [_row(1, 0, "Sim."), _row(2, 70_000, "Não."), _row(3, 140_000, "15"), _row(4, 210_000, "...")]
    assert _texts(rows) == ["Sim.", "Não.", "15"]                            # só pontuação é vazio


def test_an_exact_repeat_within_10_s_goes_and_at_11_s_stays():
    within = [_row(1, 0, "O corte fica."), _row(2, 70_000, "Outra coisa."), _row(3, 80_000, "Outra coisa.")]
    assert _texts(within) == ["O corte fica.", "Outra coisa."]
    after = [_row(1, 0, "O corte fica."), _row(2, 70_000, "Outra coisa."), _row(3, 81_000, "Outra coisa.")]
    assert _texts(after) == ["O corte fica.", "Outra coisa. Outra coisa."]  # 11 s depois fica (e junta: o mesmo falante)


def test_two_people_agreeing_both_stay_and_yes_no_and_numbers_never_go_as_repeats():
    """R-04: a repetição só sai do mesmo falante (alucinação) ou do outro canal (eco); "sim", "não" e números nunca."""
    rows = [_row(1, 0, "Sim.", "Ana"), _row(2, 2000, "Sim.", "Bruno"), _row(3, 4000, "Não!", "Davi"), _row(4, 6000, "não", "Eva"),
            _row(5, 7000, "15", "Ana"), _row(6, 8000, "15?", "Bruno"), _row(7, 9000, "Sim.", "Ana"), _row(8, 9500, "sim", None, "L")]
    assert [ln.segment_id for ln in compact(rows, ME).lines] == [1, 2, 3, 4, 5, 6, 7, 8]
    same = [_row(1, 0, "O prazo é sexta.", "Ana"), _row(2, 3000, "O prazo é sexta.", "Ana"), _row(3, 4000, "O prazo é sexta.", "Bruno")]
    assert [ln.segment_id for ln in compact(same, ME).lines] == [1, 3]       # a do mesmo falante sai; a do Bruno fica


def test_in_the_room_the_l_without_a_label_is_a_participant():
    """R-06: na sala presencial o L é a sala; sem rótulo, "Participante", como no R."""
    rows = [_row(1, 0, "Bom dia a todos.", None, "L")]
    assert compact(rows, ME).lines[0].speaker == ME and compact(rows, ME, in_room=True).lines[0].speaker == PARTICIPANT


def test_the_echo_between_l_and_r_within_10_s_goes():
    rows = [_row(1, 5000, "O corte fica em oito por cento."), _row(2, 6000, "o corte fica em oito por cento", None, "L")]
    c = compact(rows, ME)
    assert [ln.render() for ln in c.lines] == ["#1 [00:05] Ana — CFO: O corte fica em oito por cento."] and c.ids == {1}


def test_a_long_text_is_cut_at_the_last_space_with_an_ellipsis():
    text = " ".join(["palavra"] * 60)                                       # 479 caracteres
    out = clip(text)
    assert len(out) <= LINE_MAX and out.endswith(ELLIPSIS) and out[:-1].split(" ")[-1] == "palavra"
    assert clip("curto") == "curto" and _texts([_row(1, 0, text)]) == [out]


def test_the_same_speaker_merges_up_to_60_s_and_breaks_at_61_s():
    merged = compact([_row(1, 0, "Primeira."), _row(2, 60_000, "Segunda.")], ME)
    assert [(ln.segment_id, ln.text) for ln in merged.lines] == [(1, "Primeira. Segunda.")] and merged.ids == {1, 2}
    apart = compact([_row(1, 0, "Primeira."), _row(2, 61_000, "Segunda.")], ME)
    assert [(ln.segment_id, ln.text) for ln in apart.lines] == [(1, "Primeira."), (2, "Segunda.")]
    other = compact([_row(1, 0, "Primeira."), _row(2, 1000, "Segunda.", "Bruno — CTO")], ME)          # outro falante: outra linha
    assert len(other.lines) == 2


def test_a_new_line_every_300_characters_keeps_its_own_id():
    long = "x" * 200
    c = compact([_row(1, 0, long), _row(2, 4000, long + "y"), _row(3, 8000, "fim.")], ME)
    assert [ln.segment_id for ln in c.lines] == [1, 2] and c.lines[1].text == f"{long}y fim." and c.line_ids == ((1,), (2, 3))


def test_the_speaker_without_a_label_is_the_asker_on_l_and_participant_on_r():
    assert speaker({"label": None, "channel": "L"}, ME) == ME and speaker({"label": "", "channel": "R"}, ME) == PARTICIPANT
    assert speaker({"label": "Ana — CFO", "channel": "R"}, ME) == "Ana — CFO"


def test_trim_drops_from_the_start_keeps_one_and_measures_the_window_that_stayed():
    rows = [_row(1, 0, "a" * 100, "Ana — CFO"), _row(2, 70_000, "Ok."), _row(3, 140_000, "b" * 50, "Bruno"), _row(4, 210_000, "c" * 40)]
    c = compact(rows, ME)
    assert c.raw_chars == 100 + 3 + 50 + 40 and c.input_chars == 190 and c.until_caption_id == 4 and c.t_until_ms == 213_000
    t = trim(c, 100)
    assert [ln.segment_id for ln in t.lines] == [3, 4] and t.ids == {3, 4} and t.input_chars == 90 and t.raw_chars == 90
    assert trim(c, 10).lines == (c.lines[-1],) and trim(c, 1000) is c
    assert compact([], ME).lines == () and compact([], ME).raw_chars == 0


def test_mentions_by_whole_word_without_accent_and_three_letters():
    assert first_name("Carla Dias") == "Carla" and first_name("  ") == ""
    assert mentions("Valeu, Carla! E agora?", "Carla") and mentions("o JOÃO falou", "joão") and mentions("o Joao falou", "João")
    assert not mentions("Anastácia vai mandar", "Ana") and mentions("Valeu, Ana.", "Ana")
    assert not mentions("lu, pode falar?", "Lu") and not mentions("qualquer coisa", "")
