"""Reprodução a partir do banco: decodifica os pedaços de um intervalo e toca (sounddevice, portável)."""
from __future__ import annotations

import numpy as np

from ..capture.audio import decode_flac


def decode_range(store, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> tuple[np.ndarray, int]:
    chunks = store.get_chunks_range(meeting_id, channel, t0_ms, t1_ms)
    if not chunks:
        return np.zeros(0, dtype=np.float32), 16000
    parts, sr = [], int(chunks[0]["sample_rate"])
    base_ms = int(chunks[0]["t_start_ms"])
    for c in chunks:
        samples, sr_c = decode_flac(c["flac"])
        if sr_c != sr:
            from ..capture.audio import resample
            samples = resample(samples, sr_c, sr)
        parts.append(samples)
    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    a = max(0, int((t0_ms - base_ms) * sr / 1000))
    b = max(a, int((t1_ms - base_ms) * sr / 1000))
    return audio[a:b], sr


def play(samples: np.ndarray, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate)


def stop() -> None:
    import sounddevice as sd

    sd.stop()
