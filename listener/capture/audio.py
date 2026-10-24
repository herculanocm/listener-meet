"""Conversões de áudio puras: mono, reamostragem, FLAC em memória, RMS."""
from __future__ import annotations

import io
from math import gcd

import numpy as np


def to_mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        return x
    if x.shape[1] == 1:
        return x[:, 0]
    return x.mean(axis=1, dtype=np.float32)


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    from scipy.signal import resample_poly

    g = gcd(sr_from, sr_to)
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32)


def rms(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float32)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def encode_flac(mono: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf

    pcm = (np.clip(np.asarray(mono, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, pcm, sample_rate, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def decode_flac(data: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples[:, 0], int(sr)
