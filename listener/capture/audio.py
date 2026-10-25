"""Conversões de áudio puras: mono, reamostragem (com e sem estado), FLAC em memória, RMS (F1.2 M-11, M-15)."""
from __future__ import annotations

import io
from functools import cache
from math import ceil, gcd
from typing import Any

import numpy as np

INT16_SCALE = np.float32(32767.0)


def to_mono(x: np.ndarray) -> np.ndarray:
    """Média dos canais em float32. Estéreo pela soma dos dois (18x mais rápido que `mean(axis=1)`, medido)."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        return x
    n_ch = x.shape[1]
    if n_ch == 1:
        return x[:, 0]
    if n_ch == 2:
        return (x[:, 0] + x[:, 1]) * np.float32(0.5)
    return x @ np.full(n_ch, 1.0 / n_ch, dtype=np.float32)


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Reamostragem de um sinal inteiro (sem estado): pedaços já gravados, teste de eco, reprodução."""
    if sr_from == sr_to or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    from scipy.signal import resample_poly

    g = gcd(sr_from, sr_to)
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32, copy=False)


class StreamResampler:
    """Reamostragem por blocos com estado (overlap-save): a saída é idêntica à do sinal inteiro, sem tique na emenda.

    Guarda uma cauda de `tail_frames` amostras de entrada (contexto do filtro) e retém `hold` amostras ainda não
    emitidas (metade do filtro do `resample_poly`); `pending_in` diz quantas amostras de entrada já recebidas ainda
    não saíram, para quem precisa carimbar o tempo da saída. `flush()` entrega o resto e zera o estado.
    """

    def __init__(self, sr_from: int, sr_to: int, tail_frames: int = 480):
        g = gcd(sr_from, sr_to)
        self.sr_from, self.sr_to = int(sr_from), int(sr_to)
        self.up, self.down = sr_to // g, sr_from // g
        half = 10 * max(self.up, self.down)                       # metade do filtro, no domínio interpolado
        half_in = ceil(half / self.up)                             # em amostras de entrada
        self.hold = ceil(half_in / self.down) * self.down          # retenção, múltiplo de `down`
        self.ctx = ceil(max(2 * self.hold, tail_frames) / self.down) * self.down
        self._buf = np.zeros(0, np.float32)
        self._rem = np.zeros(0, np.float32)                        # resto menor que `down`
        self._emitted = 0                                          # amostras de `_buf` já emitidas

    @property
    def pending_in(self) -> int:
        return len(self._buf) - self._emitted + len(self._rem)

    def process(self, x: np.ndarray) -> np.ndarray:
        from scipy.signal import resample_poly

        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if len(self._rem):
            x = np.concatenate([self._rem, x])
        n_ok = (len(x) // self.down) * self.down
        self._rem = x[n_ok:].copy()
        if n_ok == 0:
            return np.zeros(0, np.float32)
        x = x[:n_ok]
        self._buf = np.concatenate([self._buf, x]) if len(self._buf) else x
        y = resample_poly(self._buf, self.up, self.down).astype(np.float32, copy=False)
        k0 = self._emitted * self.up // self.down
        k1 = max(k0, (len(self._buf) - self.hold) * self.up // self.down)
        out = y[k0:k1].copy()
        self._emitted = max(self._emitted, len(self._buf) - self.hold)
        drop = len(self._buf) - self.ctx
        if drop > 0:
            self._buf = self._buf[drop:]
            self._emitted -= drop
        return out

    def flush(self) -> np.ndarray:
        from scipy.signal import resample_poly

        x = self._rem
        if len(x) % self.down:
            x = np.concatenate([x, np.zeros(self.down - len(x) % self.down, np.float32)])
        if len(x):
            self._buf = np.concatenate([self._buf, x]) if len(self._buf) else x
        self._rem = np.zeros(0, np.float32)
        if not len(self._buf):
            return np.zeros(0, np.float32)
        y = resample_poly(self._buf, self.up, self.down).astype(np.float32, copy=False)
        out = y[self._emitted * self.up // self.down:].copy()
        self.reset()
        return out

    def reset(self) -> None:
        self._buf = np.zeros(0, np.float32)
        self._rem = np.zeros(0, np.float32)
        self._emitted = 0


def rms(x: np.ndarray) -> float:
    """RMS pelo produto escalar do vetor achatado (sem temporário; 5x mais rápido, medido)."""
    f = np.asarray(x, dtype=np.float32).reshape(-1)
    return float(np.sqrt(np.dot(f, f) / f.size)) if f.size else 0.0


def to_int16(mono: np.ndarray) -> np.ndarray:
    """float32 [-1, 1] -> int16 com arredondamento (o `astype` direto trunca para zero, M-11); um só temporário."""
    y = np.multiply(np.asarray(mono, dtype=np.float32), INT16_SCALE)
    np.clip(y, -INT16_SCALE, INT16_SCALE, out=y)
    np.rint(y, out=y)
    return y.astype(np.int16)


ENCODE_STEP = 65536   # amostras por chamada: temporários pequenos mesmo num pedaço de 60 s (M-03)


def _flac_writer(buf: io.BytesIO, sample_rate: int) -> Any:
    import soundfile as sf

    return sf.SoundFile(buf, mode="w", samplerate=int(sample_rate), channels=1, format="FLAC", subtype="PCM_16")


def encode_flac(mono: np.ndarray, sample_rate: int) -> bytes:
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    buf = io.BytesIO()
    with _flac_writer(buf, sample_rate) as f:
        for i in range(0, len(x), ENCODE_STEP):
            f.write(to_int16(x[i:i + ENCODE_STEP]))
    return buf.getvalue()


@cache
def silent_flac(sample_rate: int, frames: int) -> bytes:
    """FLAC de um pedaço 100 % silêncio, codificado uma vez por (taxa, frames), direto de int16 (M-03)."""
    buf = io.BytesIO()
    zeros = np.zeros(min(int(frames), ENCODE_STEP), np.int16)
    with _flac_writer(buf, sample_rate) as f:
        left = int(frames)
        while left > 0:
            f.write(zeros[:min(left, len(zeros))])
            left -= len(zeros)
    return buf.getvalue()


def decode_flac(data: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples[:, 0], int(sr)
