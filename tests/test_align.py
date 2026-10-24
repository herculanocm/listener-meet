import numpy as np

from listener.capture.align import estimate_offset


def _noise(n, seed=1):
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32) * 0.2


def test_offset_detects_delayed_copy():
    sr = 16000
    left = _noise(sr * 2)
    shift = int(0.05 * sr)                                    # R atrasado 50 ms
    right = np.concatenate([np.zeros(shift, np.float32), left[:-shift]]) + _noise(sr * 2, seed=2) * 0.2
    res = estimate_offset(left, right, sr)
    assert res is not None
    assert abs(res["offset_ms"] - 50.0) < 1.0
    assert res["correlation"] > 0.5


def test_offset_negative_and_uncorrelated():
    sr = 16000
    left = _noise(sr)
    shift = int(0.02 * sr)                                    # R adiantado 20 ms
    right = np.concatenate([left[shift:], np.zeros(shift, np.float32)])
    res = estimate_offset(left, right, sr)
    assert res is not None and abs(res["offset_ms"] + 20.0) < 1.0
    other = estimate_offset(left, _noise(sr, seed=9), sr)
    assert other is not None and other["correlation"] < 0.1


def test_offset_needs_signal():
    sr = 16000
    assert estimate_offset(np.zeros(sr), _noise(sr), sr) is None
    assert estimate_offset(_noise(100), _noise(100), sr) is None
