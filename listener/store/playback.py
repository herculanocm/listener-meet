"""Reprodução a partir do banco: decodifica os pedaços de um intervalo posicionando cada um pelo seu instante
(F1.1 A17: buracos entre pedaços viram silêncio em vez de deslocar o áudio) e toca (sounddevice, portável)."""
from __future__ import annotations

import numpy as np

from ..capture.audio import decode_flac, resample


def decode_range(store, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> tuple[np.ndarray, int]:
    chunks = store.get_chunks_range(meeting_id, channel, t0_ms, t1_ms)
    if not chunks:
        return np.zeros(0, dtype=np.float32), 16000
    sr = int(chunks[0]["sample_rate"])
    n = max(0, int(round((int(t1_ms) - int(t0_ms)) * sr / 1000)))
    out = np.zeros(n, dtype=np.float32)
    for c in chunks:
        samples, sr_c = decode_flac(c["flac"])
        if sr_c != sr:
            samples = resample(samples, sr_c, sr)
        start = int(round((int(c["t_start_ms"]) - int(t0_ms)) * sr / 1000))   # posição do pedaço na saída
        a_out, a_in = max(0, start), max(0, -start)
        length = min(len(samples) - a_in, n - a_out)
        if length > 0:
            out[a_out:a_out + length] = samples[a_in:a_in + length]
    return out, sr


def play(samples: np.ndarray, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate)


def stop() -> None:
    import sounddevice as sd

    sd.stop()
