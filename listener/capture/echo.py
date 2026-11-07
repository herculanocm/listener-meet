"""Eco do canal L (F5.18, PF-07): a regra do ao vivo (F4) numa função pura, partilhada pelo filho `--live` e pelo pipeline
pesado. Um trecho do L é o som da reunião vazando no microfone (sem headset) quando o R tem sinal, a correlação cruzada
entre os dois (`capture/align.py::estimate_offset`) chega a `corr_min` e o L não tem fala própria por `ECHO_OWN_SPEECH_MS`
ou mais (quadros de `ECHO_FRAME_MS` em que o L soa e o R cala). Só numpy e o `estimate_offset`: nada do SO (CP15).
Medido em 07/11 (M-05): eco só na reunião sem headset (71 % dos blocos, correlação mediana 0,39); nas outras 13, 0 a 5 %
com a correlação p95 até 0,16, bem abaixo do limiar de 0,25 (`capture.echo_corr_min`)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .align import estimate_offset

ECHO_FRAME_MS = 100              # quadros da conferência de fala própria (L com sinal, R em silêncio)
ECHO_OWN_SPEECH_MS = 500         # tanta fala própria num trecho e ele nunca é tratado como eco


@dataclass(frozen=True, slots=True)
class EchoVerdict:
    l_signal: bool               # o L tem algum quadro acima do limiar de silêncio
    own_speech_ms: int           # ms de quadros em que o L soa e o R cala
    correlation: float | None    # a correlação cruzada máxima (None sem R ou sem medida)
    offset_ms: float | None      # o deslocamento do pico (None sem medida)
    echo: bool


def echo_verdict(l: np.ndarray, r: np.ndarray, sample_rate: int, corr_min: float, silence_rms: float) -> EchoVerdict:
    """O veredito de um trecho: `echo` só com R com sinal, `corr_min` positivo, menos de `ECHO_OWN_SPEECH_MS` de fala própria
    e correlação de `corr_min` ou mais; os campos servem ao log e às sondas (nunca o áudio)."""
    l = np.asarray(l, dtype=np.float32).reshape(-1)
    r = np.asarray(r, dtype=np.float32).reshape(-1)
    frame = max(1, ECHO_FRAME_MS * int(sample_rate) // 1000)
    n = min(len(l), len(r)) // frame
    own_ms, l_signal = 0, False
    if n:
        l_rms = np.sqrt(np.mean(np.square(l[:n * frame].reshape(n, frame)), axis=1))
        r_rms = np.sqrt(np.mean(np.square(r[:n * frame].reshape(n, frame)), axis=1))
        l_signal = bool(np.any(l_rms > silence_rms))
        own_ms = int(np.count_nonzero((l_rms > silence_rms) & (r_rms <= silence_rms))) * ECHO_FRAME_MS
    if not len(r) or not np.any(r) or not len(l) or own_ms >= ECHO_OWN_SPEECH_MS or corr_min <= 0:
        return EchoVerdict(l_signal, own_ms, None, None, False)       # sem a correlação (~11 ms) quando ela não decide
    res = estimate_offset(l, r, int(sample_rate))
    corr = float(res["correlation"]) if res else None
    offset = float(res["offset_ms"]) if res else None
    if corr is None:
        return EchoVerdict(l_signal, own_ms, corr, offset, False)
    return EchoVerdict(l_signal, own_ms, corr, offset, corr >= corr_min)


def is_echo(l: np.ndarray, r: np.ndarray, sample_rate: int, corr_min: float, silence_rms: float) -> bool:
    return echo_verdict(l, r, sample_rate, corr_min, silence_rms).echo


def echo_mask(l: np.ndarray, r: np.ndarray, sample_rate: int, block_s: float, corr_min: float, silence_rms: float) -> np.ndarray:
    """Por bloco de `block_s` de uma janela (o pipeline pesado, PF-07): True onde o L é eco do R. O resto da janela que não
    fecha um bloco inteiro entra no último bloco. Janela sem R (ou mais curta que um bloco) sai toda False."""
    l = np.asarray(l, dtype=np.float32).reshape(-1)
    r = np.asarray(r, dtype=np.float32).reshape(-1)
    block = max(1, int(block_s * int(sample_rate)))
    n = len(l) // block
    if n == 0 or not len(r):
        return np.zeros(max(1, n), dtype=bool)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        a = i * block
        b = len(l) if i == n - 1 else a + block
        out[i] = is_echo(l[a:b], r[a:b], sample_rate, corr_min, silence_rms)
    return out


def apply_echo_mask(l: np.ndarray, mask: np.ndarray, sample_rate: int, block_s: float) -> np.ndarray:
    """Zera no L (uma cópia) os blocos marcados em `mask`, no mesmo recorte de `echo_mask`; o áudio gravado não muda (CP3)."""
    out = np.array(l, dtype=np.float32, copy=True).reshape(-1)
    block = max(1, int(block_s * int(sample_rate)))
    n = len(out) // block
    for i, flagged in enumerate(mask[:n]):
        if flagged:
            a = i * block
            b = len(out) if i == n - 1 else a + block
            out[a:b] = 0.0
    return out


__all__ = ["ECHO_FRAME_MS", "ECHO_OWN_SPEECH_MS", "EchoVerdict", "apply_echo_mask", "echo_mask", "echo_verdict", "is_echo"]
