"""Soma das capturas de um app (D-055): a raiz dita o ritmo e o horário; as outras fontes entram na ordem em que chegaram,
cada amostra uma vez só; uma fonte adiantada guarda no máximo `max_lag_s`; a soma fica em [-1, 1]."""
from __future__ import annotations

import numpy as np

from listener.capture.mixer import FrameMixer


def _collect():
    out: list[tuple[np.ndarray, float]] = []
    return out, lambda block, t: out.append((block.copy(), t))


def test_the_main_source_paces_and_the_others_are_added_in_order():
    out, cb = _collect()
    mixer = FrameMixer(2, 10, 2, cb)
    mixer.feed(1, np.full((3, 2), 0.25, np.float32), 5.0)            # a outra chega antes: espera a raiz
    assert out == []
    mixer.feed(0, np.full((2, 2), 0.5, np.float32), 1.0)
    mixer.feed(0, np.full((2, 2), 0.5, np.float32), 1.2)
    assert [t for _b, t in out] == [1.0, 1.2]                         # o horário é o da raiz
    assert np.allclose(out[0][0], 0.75) and np.allclose(out[1][0][0], 0.75) and np.allclose(out[1][0][1], 0.5)
    mixer.feed(1, np.full((1, 2), 0.25, np.float32), 5.3)             # atrasada: entra no próximo bloco da raiz
    mixer.feed(0, np.zeros((2, 2), np.float32), 1.4)
    assert np.allclose(out[2][0][0], 0.25) and np.allclose(out[2][0][1], 0.0)


def test_a_source_added_later_is_summed_from_the_next_root_block_on():
    """F5.9 (item 3): `add_source` devolve o índice novo; o que a fonte nova entrega entra no próximo bloco da raiz, e o que
    já estava guardado das outras não muda."""
    out, cb = _collect()
    mixer = FrameMixer(1, 10, 1, cb)                                  # só a raiz: o mixer existe mesmo com um alvo
    mixer.feed(0, np.full(2, 0.5, np.float32), 1.0)
    assert np.allclose(out[0][0][:, 0], 0.5)
    assert mixer.add_source() == 1 and mixer.add_source() == 2
    mixer.feed(2, np.full(3, 0.25, np.float32), 1.1)
    mixer.feed(1, np.full(1, 0.1, np.float32), 1.1)
    mixer.feed(0, np.full(2, 0.5, np.float32), 1.2)
    assert np.allclose(out[1][0][:, 0], [0.85, 0.75])                 # 0,5 + 0,1 + 0,25; depois 0,5 + 0,25
    mixer.feed(0, np.zeros(2, np.float32), 1.4)
    assert np.allclose(out[2][0][:, 0], [0.25, 0.0])                  # o resto da fonte 2


def test_mono_blocks_clip_and_a_runaway_source_keeps_only_the_recent_lag():
    out, cb = _collect()
    mixer = FrameMixer(3, 4, 1, cb, max_lag_s=1.0)                    # até 4 quadros guardados por fonte
    mixer.feed(1, np.arange(10, dtype=np.float32) / 100, 0.0)         # 10 quadros: ficam os 4 mais recentes (0,06 a 0,09)
    mixer.feed(2, np.full(2, 0.9, np.float32), 0.0)
    mixer.feed(0, np.full(5, 0.5, np.float32), 2.0)
    block, t = out[0]
    assert t == 2.0 and block.shape == (5, 1)
    assert np.allclose(block[:, 0], [1.0, 1.0, 0.58, 0.59, 0.5])      # 0,5 + 0,06 + 0,9 = 1,46 vira 1,0
