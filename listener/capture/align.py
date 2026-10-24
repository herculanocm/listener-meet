"""Deslocamento e eco entre os canais L e R por correlação cruzada (U-016): alimenta o aviso do warm-up e o `--check`."""
from __future__ import annotations

import numpy as np


def estimate_offset(l: np.ndarray, r: np.ndarray, sample_rate: int, max_lag_ms: float = 500.0) -> dict | None:
    """Correlação cruzada normalizada entre L e R na mesma taxa.

    Devolve {"offset_ms": deslocamento de R em relação a L (positivo = R atrasado), "correlation": pico 0 a 1}
    ou None quando falta sinal (menos de 0,5 s ou um dos canais em silêncio)."""
    l = np.asarray(l, dtype=np.float64).reshape(-1)
    r = np.asarray(r, dtype=np.float64).reshape(-1)
    n = min(len(l), len(r))
    if n < sample_rate // 2:
        return None
    l, r = l[:n] - l[:n].mean(), r[:n] - r[:n].mean()
    nl, nr = float(np.sqrt(np.dot(l, l))), float(np.sqrt(np.dot(r, r)))
    if nl < 1e-6 or nr < 1e-6:
        return None
    size = 1 << (2 * n - 1).bit_length()
    cc = np.fft.irfft(np.fft.rfft(r, size) * np.conj(np.fft.rfft(l, size)), size)   # cc[k] = sum_t l[t] r[t + k]
    max_lag = min(n - 1, int(sample_rate * max_lag_ms / 1000))
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    vals = np.concatenate([cc[: max_lag + 1], cc[-max_lag:]]) if max_lag > 0 else cc[:1]
    k = int(np.argmax(np.abs(vals)))
    return {"offset_ms": float(lags[k] * 1000.0 / sample_rate), "correlation": float(abs(vals[k]) / (nl * nr))}
