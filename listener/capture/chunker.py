"""Pedaços de um canal com preenchimento de buracos por timestamp e métricas de deriva (F1.1 A7).

Roda na thread de trabalho (F1.2 M-16), já em mono e na taxa gravada: acumula arrays e corta pedaços; nada de
codificação aqui. Silêncio sai em fatias de um buffer fixo (M-03) e um pedaço 100 % buraco vira `RawChunk(silent=True)`
sem dados, para o FLAC em cache.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..log import get_logger

log = get_logger("capture.chunker")


@dataclass(frozen=True, slots=True)
class RawChunk:
    channel: str
    idx: int
    start_rel: float
    sample_rate: int
    channels: int
    frames: int
    gap_frames: int
    data: np.ndarray | None       # float32, shape (frames, channels); None quando `silent`
    silent: bool = False          # 100 % preenchimento de buraco: nenhum dado, só zeros


class ChannelChunker:
    def __init__(self, channel: str, sample_rate: int, channels: int, on_chunk: Callable[[RawChunk], None],
                 chunk_seconds: float = 60.0, gap_tolerance: float = 0.25, start_rel: float = 0.0):
        self.channel = channel
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.on_chunk = on_chunk
        self.chunk_frames = max(1, round(chunk_seconds * self.sample_rate))
        self.gap_tolerance = float(gap_tolerance)
        self.start_rel = float(start_rel)
        # buffer de silêncio só leitura (1 s, ou o pedaço se for menor): buracos saem em fatias dele (M-03)
        self._zeros = np.zeros((min(self.chunk_frames, self.sample_rate), self.channels), dtype=np.float32)
        self._parts: list[np.ndarray] = []
        self._buffered = 0
        self._buffer_gap = 0
        self._written = 0            # frames já entregues em pedaços + no buffer
        self.idx = 0
        self.gap_frames_total = 0
        # métricas de deriva (segundos): só instrumentação, sem mudar o comportamento (P22)
        self.fills = 0               # vezes que um buraco acima da tolerância foi preenchido
        self.late_blocks = 0         # blocos que chegaram adiantados além da tolerância (não descartamos)
        self.max_lag = 0.0           # maior atraso do dispositivo em relação ao relógio (inclui buracos)
        self.max_creep = 0.0         # maior atraso abaixo da tolerância: a deriva lenta que não vira preenchimento
        self.max_lead = 0.0          # maior adiantamento
        self.closed = False

    @property
    def position_rel(self) -> float:
        return self.start_rel + self._written / self.sample_rate

    def write(self, block: np.ndarray, t_rel: float | None = None) -> None:
        if self.closed:
            return
        block = self._normalize(block)
        if t_rel is not None:
            gap = t_rel - self.position_rel
            if gap > self.max_lag:
                self.max_lag = gap
            if gap > self.gap_tolerance:
                self.fills += 1
                self._append_silence(round(gap * self.sample_rate))
            elif gap > 0:
                if gap > self.max_creep:
                    self.max_creep = gap
            else:
                if -gap > self.max_lead:
                    self.max_lead = -gap
                if gap < -self.gap_tolerance:
                    self.late_blocks += 1   # chegou cedo demais (jitter ou dispositivo adiantado); não descartamos
        if len(block):
            self._append(block, gap=False)

    def close(self, end_rel: float | None = None) -> None:
        if self.closed:
            return
        if end_rel is not None:
            gap = end_rel - self.position_rel
            if gap > self.gap_tolerance:
                self._append_silence(round(gap * self.sample_rate))
        if self._buffered > 0:
            self._emit(self._buffered)
        self.closed = True

    def drift_stats(self) -> dict[str, Any]:
        """Métricas em ms para o evento `stop` e o `--check` (F1.1 A7)."""
        return {"fills": self.fills, "late_blocks": self.late_blocks, "gap_frames": self.gap_frames_total,
                "max_lag_ms": round(self.max_lag * 1000, 1), "max_creep_ms": round(self.max_creep * 1000, 1),
                "max_lead_ms": round(self.max_lead * 1000, 1)}

    # ---- interno ----
    def _normalize(self, block: np.ndarray) -> np.ndarray:
        arr = np.asarray(block, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[1] != self.channels:
            if self.channels == 1:
                arr = arr.mean(axis=1, keepdims=True, dtype=np.float32)
            elif arr.shape[1] == 1:
                arr = np.repeat(arr, self.channels, axis=1)
            else:
                # formato inesperado (fonte trocada no meio): reduz e segue, sem derrubar a thread (M-06)
                log.warning("canal %s: esperava %d canais, veio %d; reduzindo", self.channel, self.channels, arr.shape[1])
                arr = np.repeat(arr.mean(axis=1, keepdims=True, dtype=np.float32), self.channels, axis=1)
        return arr

    def _append_silence(self, n: int) -> None:
        """Buraco em fatias do buffer de zeros: nunca aloca o buraco inteiro (M-03)."""
        while n > 0:
            take = min(n, len(self._zeros), self.chunk_frames - self._buffered)
            self._append(self._zeros[:take], gap=True)
            n -= take

    def _append(self, frames: np.ndarray, gap: bool) -> None:
        off, total = 0, len(frames)
        while off < total:
            room = self.chunk_frames - self._buffered
            take = min(room, total - off)
            self._parts.append(frames[off:off + take])
            self._buffered += take
            if gap:
                self._buffer_gap += take
                self.gap_frames_total += take
            self._written += take
            off += take
            if self._buffered >= self.chunk_frames:
                self._emit(self._buffered)

    def _emit(self, n: int) -> None:
        silent = self._buffer_gap == n
        data: np.ndarray | None = None
        if not silent:
            data = np.concatenate(self._parts) if len(self._parts) > 1 else np.ascontiguousarray(self._parts[0])
        start_rel = self.position_rel - n / self.sample_rate
        chunk = RawChunk(self.channel, self.idx, round(start_rel, 6), self.sample_rate, self.channels,
                         int(n), int(self._buffer_gap), data, silent)
        self._parts, self._buffered, self._buffer_gap = [], 0, 0
        self.idx += 1
        self.on_chunk(chunk)
