"""F5.20 L1 (C7): o leitor incremental da resposta da sugestão. O JSON do fake em três pedaços; o mesmo texto cortado em
todas as posições devolve as mesmas opções; chaves, colchetes, aspas e barras dentro do texto; um objeto inválido no meio é
descartado e o seguinte entregue; sem `options`, nada; `options` depois de outra chave; `"options": null`; o `done` depois do
`]`."""
from __future__ import annotations

import json

from listener.llm.suggest_parse import OptionsReader

OPTIONS = [{"kind": "Perguntar", "text": 'Com { chaves } [ e ] "aspas" \\ barra', "facts": [1, 2]},
           {"kind": "Posicionar", "text": "Segunda", "facts": []}]
DOC = json.dumps({"options": OPTIONS, "thread_summary": 'O fio com {"options": [{}]} dentro.'}, ensure_ascii=False)


def _feed(*pieces: str) -> tuple[list[dict], OptionsReader]:
    r = OptionsReader()
    out: list[dict] = []
    for p in pieces:
        out += r.feed(p)
    return out, r


def test_the_fake_json_in_three_pieces():
    n = -(-len(DOC) // 3)
    out, r = _feed(*(DOC[i:i + n] for i in range(0, len(DOC), n)))
    assert out == OPTIONS and r.done and r.dropped == 0 and r.text == DOC


def test_cutting_anywhere_gives_the_same_options():
    for i in range(len(DOC) + 1):
        out, r = _feed(DOC[:i], DOC[i:])
        assert out == OPTIONS and r.done, i


def test_an_invalid_object_in_the_middle_is_dropped_and_the_next_delivered():
    text = '{"options": [{"kind": "Perguntar", "text": "a", "facts": [1,]}, {"kind": "Posicionar", "text": "b", "facts": []}]}'
    out, r = _feed(text)
    assert out == [{"kind": "Posicionar", "text": "b", "facts": []}] and r.dropped == 1 and r.done


def test_no_options_key_delivers_nothing_and_null_is_not_the_list():
    assert _feed('{"thread_summary": "x", "outros": [{"kind": "Perguntar"}]}')[0] == []
    assert _feed('{"options": null, "thread_summary": "x"}')[0] == []


def test_options_after_another_key_and_a_fence_around_the_json():
    text = '```json\n{"thread_summary": "fio: [x]", "options": [{"kind": "Pedir tempo", "text": "c", "facts": [3]}]}\n```'
    out, r = _feed(text)
    assert out == [{"kind": "Pedir tempo", "text": "c", "facts": [3]}] and r.done


def test_nothing_after_the_closing_bracket_counts():
    out, r = _feed('{"options": [{"kind": "Perguntar", "text": "a", "facts": []}], ',
                   '"x": [{"kind": "Posicionar", "text": "b", "facts": []}]}')
    assert len(out) == 1 and r.done
