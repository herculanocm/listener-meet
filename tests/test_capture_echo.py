"""F5.18 (PF-07): a regra do eco do L numa função pura (`capture/echo.py`), partilhada pelo ao vivo e pelo pipeline pesado."""
from __future__ import annotations

import numpy as np

from listener.capture.echo import ECHO_OWN_SPEECH_MS, apply_echo_mask, echo_mask, echo_verdict, is_echo

SR = 16000
CORR_MIN = 0.25
SILENCE = 0.004


def _noise(seconds: float, seed: int, amp: float = 0.3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * amp).astype(np.float32)


def _delayed(x: np.ndarray, ms: int, gain: float = 0.5) -> np.ndarray:
    out = np.zeros_like(x)
    k = ms * SR // 1000
    out[k:] = x[:-k] * gain
    return out


def test_the_room_sound_leaking_into_the_microphone_is_echo():
    r = _noise(5.0, 1)
    l = _delayed(r, 20) + _noise(5.0, 2, amp=0.01)
    v = echo_verdict(l, r, SR, CORR_MIN, SILENCE)
    assert v.echo and v.l_signal and v.correlation is not None and v.correlation >= CORR_MIN
    assert v.own_speech_ms < ECHO_OWN_SPEECH_MS
    assert is_echo(l, r, SR, CORR_MIN, SILENCE)


def test_independent_speech_is_not_echo_nor_is_a_silent_r_nor_a_zero_threshold():
    r = _noise(5.0, 1)
    own = _noise(5.0, 7)
    assert not is_echo(own, r, SR, CORR_MIN, SILENCE)                       # fala própria, sem correlação
    assert not is_echo(_delayed(r, 20), np.zeros_like(r), SR, CORR_MIN, SILENCE)   # R calado: nada a vazar
    assert not is_echo(_delayed(r, 20), r, SR, 0.0, SILENCE)               # limiar zero: a conferência está desligada
    assert not is_echo(np.zeros(0, np.float32), r, SR, CORR_MIN, SILENCE)   # L vazio


def test_half_a_second_of_own_speech_where_r_is_silent_spares_the_block():
    r = _noise(5.0, 1)
    r[: SR] = 0.0                                                             # o primeiro segundo do R cala
    l = _delayed(r, 20)
    l[: SR] = _noise(1.0, 9)[: SR]                                           # e o L fala sozinho nele: fala própria
    v = echo_verdict(l, r, SR, CORR_MIN, SILENCE)
    assert v.own_speech_ms >= ECHO_OWN_SPEECH_MS and not v.echo


def test_echo_mask_marks_only_the_echo_blocks_and_apply_zeroes_them_in_a_copy():
    r = _noise(30.0, 3)
    l = _delayed(r, 20)
    own = _noise(10.0, 11)
    l[10 * SR:20 * SR] = own                                                 # o bloco do meio é fala própria
    mask = echo_mask(l, r, SR, 10.0, CORR_MIN, SILENCE)
    assert mask.tolist() == [True, False, True]
    cleaned = apply_echo_mask(l, mask, SR, 10.0)
    assert not np.any(cleaned[: 10 * SR]) and not np.any(cleaned[20 * SR:])
    assert np.array_equal(cleaned[10 * SR:20 * SR], l[10 * SR:20 * SR]) and l is not cleaned and np.any(l[: SR])


def test_a_window_shorter_than_a_block_or_without_r_is_never_echo():
    assert echo_mask(_noise(3.0, 1), _noise(3.0, 1), SR, 10.0, CORR_MIN, SILENCE).tolist() == [False]
    assert echo_mask(_noise(25.0, 1), np.zeros(0, np.float32), SR, 10.0, CORR_MIN, SILENCE).tolist() == [False, False]
