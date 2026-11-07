"""Fontes de áudio das sondas: a reunião do banco real decodificada em `mode=ro` (`store.playback.decode_range`, como o
worker), um arquivo wav/flac (soundfile) ou um sintético de 15 s (seno mais ruído) quando nada mais há. Tudo float32 mono
a 16 kHz; a fonte só se identifica pelo id da reunião ou por "arquivo"/"sintético" (D-059: nunca título nem caminho)."""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Any

import numpy as np

SR = 16000
FRAME_MS = 20


@dataclass(slots=True)
class Source:
    label: str                       # "reuniao:<id>", "arquivo" ou "sintetico"
    mono: np.ndarray                 # o canal medido (o R da reunião, ou a mistura do arquivo)
    left: np.ndarray | None = None   # L e R quando a fonte tem os dois (reunião; arquivo estéreo)
    right: np.ndarray | None = None
    sample_rate: int = SR

    @property
    def seconds(self) -> float:
        return len(self.mono) / self.sample_rate

    def describe(self) -> dict[str, Any]:
        return {"source": self.label, "audio_s": round(self.seconds, 1), "has_lr": self.left is not None and self.right is not None}


def to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    x = np.ascontiguousarray(np.asarray(x, dtype=np.float32).reshape(-1))
    if sr == SR:
        return x
    from scipy.signal import resample_poly

    g = gcd(SR, sr)
    return np.ascontiguousarray(resample_poly(x, SR // g, sr // g).astype(np.float32))


def decode_channel(store: Any, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> np.ndarray:
    """O canal de uma reunião entre dois instantes (buracos viram silêncio), a 16 kHz; canal sem pedaços vira zeros."""
    from listener.store.playback import decode_range

    samples, sr = decode_range(store, meeting_id, channel, t0_ms, t1_ms)
    if samples.size == 0:
        return np.zeros(max(0, round((t1_ms - t0_ms) * SR / 1000)), np.float32)
    return to_16k(samples, sr)


def meeting_ms(store: Any, meeting_id: int) -> int:
    from listener.pipeline.windows import meeting_duration_ms

    return int(meeting_duration_ms(store, meeting_id))


def from_meeting(store: Any, meeting_id: int, minutes: float, channel: str = "R", both: bool = True) -> Source:
    total = meeting_ms(store, meeting_id)
    if total <= 0:
        raise SystemExit(f"a reunião {meeting_id} não tem áudio")
    end = min(total, round(minutes * 60_000)) if minutes and minutes > 0 else total
    main = decode_channel(store, meeting_id, channel, 0, end)
    if not both:
        return Source(f"reuniao:{meeting_id}", main)
    other = decode_channel(store, meeting_id, "L" if channel == "R" else "R", 0, end)
    left, right = (other, main) if channel == "R" else (main, other)
    return Source(f"reuniao:{meeting_id}", main, left, right)


def from_file(path: str | Path, minutes: float) -> Source:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if minutes and minutes > 0:
        data = data[: int(minutes * 60 * sr)]
    if data.shape[1] >= 2:
        left, right = to_16k(data[:, 0], sr), to_16k(data[:, 1], sr)
        return Source("arquivo", to_16k(data.mean(axis=1), sr), left, right)
    return Source("arquivo", to_16k(data[:, 0], sr))


def synthetic(seconds: float = 15.0, seed: int = 7) -> Source:
    """Seno modulado mais ruído (não é fala: só prova que as sondas rodam sem banco)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR), dtype=np.float32) / SR
    tone = 0.2 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
    noise = 0.01 * rng.standard_normal(t.size).astype(np.float32)
    mono = (tone + noise).astype(np.float32)
    return Source("sintetico", mono, mono * 0.3, mono)


def resolve(args: Any, store: Any) -> Source:
    """`--audio` primeiro, depois `--meeting` (precisa do banco real); sem nada, o sintético com aviso."""
    minutes = float(getattr(args, "minutes", 0) or 0)
    if getattr(args, "audio", None):
        return from_file(args.audio, minutes)
    meeting = getattr(args, "meeting", None)
    if meeting is not None and store is not None:
        return from_meeting(store, int(meeting), minutes, str(getattr(args, "channel", "R") or "R"))
    from .common import out

    out("aviso", message="sem --audio e sem reunião do banco real: áudio sintético de 15 s (não é fala)")
    return synthetic(max(15.0, minutes * 60) if minutes else 15.0)


# ---------------------------------------------------------------- trechos com fala
def frame_rms(x: np.ndarray, sr: int = SR) -> np.ndarray:
    from listener.pipeline.windows import frame_rms as rms

    return rms(x, sr, FRAME_MS)


def speech_runs(x: np.ndarray, silence_rms: float, min_ms: int = 1500, sr: int = SR) -> list[tuple[int, int]]:
    """Trechos contíguos com sinal (RMS por quadro de 20 ms acima do limiar), em amostras, do mais longo ao mais curto."""
    loud = frame_rms(x, sr) > silence_rms
    frame = max(1, int(sr * FRAME_MS / 1000))
    runs: list[tuple[int, int]] = []
    start = -1
    for i, v in enumerate([*loud.tolist(), False]):
        if v and start < 0:
            start = i
        elif not v and start >= 0:
            if (i - start) * FRAME_MS >= min_ms:
                runs.append((start * frame, i * frame))
            start = -1
    return sorted(runs, key=lambda r: r[0] - r[1])


def speech_window(x: np.ndarray, seconds: float, silence_rms: float = 0.004, sr: int = SR) -> np.ndarray:
    """A janela de `seconds` com mais quadros de fala (passo de 1 s); áudio curto volta inteiro, preenchido com zeros."""
    n = int(seconds * sr)
    if len(x) <= n:
        return np.pad(np.asarray(x, np.float32), (0, n - len(x)))
    loud = (frame_rms(x, sr) > silence_rms).astype(np.int32)
    per_window = n // max(1, int(sr * FRAME_MS / 1000))
    step = sr // max(1, int(sr * FRAME_MS / 1000))                # quadros por segundo
    cumulative = np.concatenate([[0], np.cumsum(loud)])
    starts = np.arange(0, max(1, len(loud) - per_window), step)
    scores = cumulative[np.minimum(starts + per_window, len(loud))] - cumulative[starts]
    best = int(starts[int(np.argmax(scores))])
    a = best * max(1, int(sr * FRAME_MS / 1000))
    return np.ascontiguousarray(x[a:a + n])


def speech_pieces(x: np.ndarray, count: int, lo_s: float, hi_s: float, silence_rms: float = 0.004, sr: int = SR) -> list[np.ndarray]:
    """`count` trechos de fala de `lo_s` a `hi_s` segundos, dos trechos contínuos mais longos (cortados em `hi_s`); faltando
    trechos naturais, janelas de `lo_s` com mais fala, deslocadas para não repetir áudio."""
    pieces: list[np.ndarray] = []
    for a, b in speech_runs(x, silence_rms, int(lo_s * 1000), sr):
        pieces.append(np.ascontiguousarray(x[a:min(b, a + int(hi_s * sr))]))
        if len(pieces) >= count:
            return pieces
    offset = 0
    while len(pieces) < count:
        window = speech_window(x[offset:], lo_s, silence_rms, sr)
        pieces.append(window)
        offset += len(window)
        if offset >= len(x):
            offset = 0
    return pieces


def audio_window(x: np.ndarray, idx: int = 0, channel: str = "R", t0_ms: int = 0, sr: int = SR) -> Any:
    """Um `AudioWindow` do pipeline com o trecho inteiro (como o runner entrega aos adaptadores)."""
    from listener.pipeline.types import AudioWindow

    x = np.ascontiguousarray(x, dtype=np.float32)
    return AudioWindow(idx, channel, t0_ms, t0_ms + round(len(x) * 1000 / sr), x, sr, bool(np.any(x)), 0)


def save_npy(x: np.ndarray, path: Path) -> Path:
    np.save(path, np.ascontiguousarray(x, dtype=np.float32))
    return path.with_suffix(".npy") if path.suffix != ".npy" else path


def load_npy(path: str | Path) -> np.ndarray:
    return np.ascontiguousarray(np.load(str(path)).astype(np.float32, copy=False))
