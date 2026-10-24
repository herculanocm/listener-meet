"""Pedaços de um canal com preenchimento de buracos por timestamp.

Roda na thread de áudio: só acumula arrays e corta pedaços; nada de codificação aqui.
Os pedaços saem como RawChunk (float32, taxa nativa da fonte) para uma thread de trabalho
que faz mono, reamostragem, FLAC e grava no banco.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass
class RawChunk:
    channel: str
    idx: int
    start_rel: float
    sample_rate: int
    channels: int
    frames: int
    gap_frames: int
    data: np.ndarray          # float32, shape (frames, channels)


class ChannelChunker:
    def __init__(self, channel: str, sample_rate: int, channels: int, on_chunk: Callable[[RawChunk], None],
                 chunk_seconds: float = 60.0, gap_tolerance: float = 0.25, start_rel: float = 0.0):
        self.channel = channel
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.on_chunk = on_chunk
        self.chunk_frames = max(1, int(round(chunk_seconds * self.sample_rate)))
        self.gap_tolerance = float(gap_tolerance)
        self.start_rel = float(start_rel)
        self._parts: list[np.ndarray] = []
        self._buffered = 0
        self._buffer_gap = 0
        self._written = 0            # frames já entregues em pedaços + no buffer
        self.idx = 0
        self.gap_frames_total = 0
        self.late_blocks = 0
        self.closed = False

    @property
    def position_rel(self) -> float:
        return self.start_rel + self._written / self.sample_rate

    @property
    def frames_written(self) -> int:
        return self._written

    def write(self, block: np.ndarray, t_rel: float | None = None) -> None:
        if self.closed:
            return
        block = self._normalize(block)
        if t_rel is not None:
            gap = t_rel - self.position_rel
            if gap > self.gap_tolerance:
                n = int(round(gap * self.sample_rate))
                self._append(np.zeros((n, self.channels), dtype=np.float32), gap=True)
            elif gap < -self.gap_tolerance:
                self.late_blocks += 1   # chegou cedo demais (jitter); não descartamos
        self._append(block, gap=False)

    def close(self, end_rel: float | None = None) -> None:
        if self.closed:
            return
        if end_rel is not None:
            gap = end_rel - self.position_rel
            if gap > self.gap_tolerance:
                self._append(np.zeros((int(round(gap * self.sample_rate)), self.channels), dtype=np.float32), gap=True)
        if self._buffered > 0:
            self._emit(self._buffered)
        self.closed = True

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
                raise ValueError(f"esperava {self.channels} canais, veio {arr.shape[1]}")
        return arr

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
        data = np.concatenate(self._parts) if len(self._parts) > 1 else self._parts[0]
        start_rel = self.position_rel - n / self.sample_rate
        chunk = RawChunk(self.channel, self.idx, round(start_rel, 6), self.sample_rate, self.channels,
                         int(n), int(self._buffer_gap), np.ascontiguousarray(data))
        self._parts, self._buffered, self._buffer_gap = [], 0, 0
        self.idx += 1
        self.on_chunk(chunk)
