"""Janelas de áudio sobre `playback.decode_range` (F2 item 4): buracos viram silêncio, reamostra para 16 kHz, corta no maior
silêncio a ±`search_s` do ponto nominal (sem sobreposição) ou, sem silêncio, corte seco com sobreposição (dedup pelo centro
da palavra fica com `align.merge_words`); janela sem fala (RMS por quadro) sai com `has_speech=False` e o chamador pula o
modelo (R-11). Nunca mais que uma janela (mais a busca) em memória; nada de `blobopen`."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

import numpy as np

from ..capture.audio import resample
from ..store.playback import decode_range
from .types import SAMPLE_RATE, AudioWindow

if TYPE_CHECKING:
    from ..store import Store

FRAME_MS = 20


def frame_rms(x: np.ndarray, sample_rate: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    """RMS por quadro de `frame_ms` sem temporário do tamanho do sinal (`einsum` soma os quadrados linha a linha)."""
    n = max(1, int(sample_rate * frame_ms / 1000))
    k = len(x) // n
    if k == 0:
        return np.zeros(0, np.float32)
    f = x[: k * n].reshape(k, n)
    return np.sqrt(np.einsum("ij,ij->i", f, f) / n).astype(np.float32, copy=False)


def find_silence_cut(x: np.ndarray, sample_rate: int, nominal: int, search: int, silence_rms: float,
                     min_silence_ms: int = 300) -> int | None:
    """Amostra de corte dentro do maior trecho silencioso de [nominal - search, nominal + search]: o ponto mais perto do
    nominal, com meio `min_silence_ms` de folga das bordas do silêncio; None se não há silêncio de `min_silence_ms` ou mais."""
    lo, hi = max(0, nominal - search), min(len(x), nominal + search)
    if hi <= lo:
        return None
    frame = max(1, int(sample_rate * FRAME_MS / 1000))
    r = frame_rms(x[lo:hi], sample_rate)
    silent = r < silence_rms
    best_len, best_start, run_start = 0, -1, -1
    for i, s in enumerate([*silent.tolist(), False]):
        if s and run_start < 0:
            run_start = i
        elif not s and run_start >= 0:
            if i - run_start > best_len:
                best_len, best_start = i - run_start, run_start
            run_start = -1
    if best_len * FRAME_MS < min_silence_ms:
        return None
    run_lo, run_hi = lo + best_start * frame, lo + (best_start + best_len) * frame
    inset = min((run_hi - run_lo) // 2, round(sample_rate * min_silence_ms / 2000))
    return int(min(max(nominal, run_lo + inset), run_hi - inset))


def meeting_duration_ms(store: Store, meeting_id: int) -> int:
    m = store.get_meeting(meeting_id) or {}
    dur = m.get("duration_ms")
    if not dur:
        dur = store.audio.covered_ms(meeting_id) or 0
    return int(dur)


type KnownWindows = Mapping[int, tuple[int, int, int]]     # idx -> (t0_ms, t1_ms, overlap_ms) das janelas já concluídas


def iter_windows(store: Store, meeting_id: int, channel: str, window_s: float, overlap_s: float = 0.0, *,
                 silence_rms: float = 0.004, cut_on_silence: bool = True, search_s: float = 15.0, min_silence_ms: int = 300,
                 target_rate: int = SAMPLE_RATE, duration_ms: int | None = None,
                 known: KnownWindows | None = None) -> Iterator[AudioWindow]:
    """Janelas consecutivas de até `window_s` sobre a linha do tempo da sessão. Com `cut_on_silence`, o fim de cada janela
    cai no maior silêncio perto do ponto nominal e não há sobreposição; sem silêncio (ou com `cut_on_silence=False`), a
    próxima janela começa `overlap_s` antes do fim desta e carrega `overlap_ms`. `known` (retomada, R2-21): janelas já
    gravadas saem sem decodificar (amostras vazias) enquanto a seguinte também for conhecida; a última conhecida é
    decodificada para o corte da próxima sair igual."""
    total = int(duration_ms if duration_ms is not None else meeting_duration_ms(store, meeting_id))
    if total <= 0:
        return
    win_ms, ov_ms, search_ms = int(window_s * 1000), int(overlap_s * 1000), int(search_s * 1000)
    t, idx, overlap = 0, 0, 0
    while t < total:
        if known and idx in known and (idx + 1) in known:
            k0, k1, kov = known[idx]
            yield AudioWindow(idx, channel, k0, k1, np.zeros(0, np.float32), target_rate, False, kov)
            t, idx, overlap = known[idx + 1][0], idx + 1, known[idx + 1][2]
            continue
        nominal_end = min(total, t + win_ms)
        last = nominal_end >= total
        fetch_end = nominal_end if last or not cut_on_silence else min(total, nominal_end + search_ms)
        samples, sr = decode_range(store, meeting_id, channel, t, fetch_end)
        if sr != target_rate and samples.size:
            samples = resample(samples, sr, target_rate)
            sr = target_rate
        if samples.size == 0:                                  # canal sem pedaços neste trecho: silêncio
            samples = np.zeros(round((fetch_end - t) * sr / 1000), np.float32)
        end_ms = nominal_end
        next_t = nominal_end - (ov_ms if not last else 0)
        next_overlap = ov_ms if not last else 0
        if cut_on_silence and not last:
            cut = find_silence_cut(samples, sr, round((nominal_end - t) * sr / 1000), round(search_ms * sr / 1000), silence_rms,
                                   min_silence_ms)
            if cut is not None and cut > 0:
                end_ms = t + round(cut * 1000 / sr)
                next_t, next_overlap = end_ms, 0
        n = min(len(samples), round((end_ms - t) * sr / 1000))
        chunk = samples[:n]                                    # vista: a janela não é copiada
        has_speech = bool(chunk.size) and bool(np.any(frame_rms(chunk, sr) > silence_rms))
        yield AudioWindow(idx, channel, t, end_ms, chunk, sr, has_speech, overlap)
        if next_t <= t:                                        # proteção contra janela que não anda
            next_t = end_ms
        t, idx, overlap = next_t, idx + 1, next_overlap
