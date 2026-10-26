"""Dublê do fluxo de saída do player (F5 L4): registra os quadros escritos e as chamadas (`start`, `abort`, `stop`,
`close`); `permits` (um semáforo) deixa passar só tantos `write` quantos o teste liberar (posição no meio do som; sem ele
o som inteiro sai na hora) e `entered` conta os `write` começados (o teste sabe que há um em voo); `fail` faz a fábrica
levantar (sem dispositivo). R7-02: `RealtimeOutput` toca em tempo real com um buffer pequeno e conta os buracos. Nada de
áudio real."""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np


class FakeOutput:
    def __init__(self, rate: int = 16000, latency: float = 0.0, permits: threading.Semaphore | None = None):
        self.samplerate = float(rate)
        self.latency = float(latency)
        self.permits = permits
        self.chunks: list[np.ndarray] = []
        self.calls: list[str] = []
        self.entered = 0
        self.wrote = threading.Event()

    def start(self) -> None:
        self.calls.append("start")

    def write(self, data: np.ndarray) -> Any:
        self.entered += 1
        if self.permits is not None and not self.permits.acquire(timeout=5.0):
            raise TimeoutError("o teste não liberou o write")
        self.chunks.append(np.array(data, np.float32, copy=True))
        self.wrote.set()
        return False

    def stop(self) -> None:
        self.calls.append("stop")

    def abort(self) -> None:
        self.calls.append("abort")

    def close(self) -> None:
        self.calls.append("close")

    @property
    def frames(self) -> np.ndarray:
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.float32)


class RealtimeOutput(FakeOutput):
    """Um dispositivo que toca em tempo real com `buffer_s` de buffer (R7-02, o MME tem ~0,18 s): `write` volta quando o
    bloco coube, como o bloqueante do PortAudio, e devolve `underflowed` quando o buffer esvaziou antes da escrita; cada
    buraco vai para `gaps` como (segundo do áudio, duração em s). O primeiro bloco depois de `start` não conta; `abort`
    descarta o buffer e `stop` espera o resto tocar."""

    def __init__(self, rate: int = 16000, buffer_s: float = 0.18, tolerance_s: float = 0.01):
        super().__init__(rate, latency=buffer_s)
        self.buffer_s = buffer_s
        self.tolerance_s = tolerance_s
        self.gaps: list[tuple[float, float]] = []
        self._end: float | None = None           # instante em que o que está no buffer acaba de tocar
        self._fresh = False

    def start(self) -> None:
        super().start()
        self._end, self._fresh = time.monotonic(), True

    def write(self, data: np.ndarray) -> Any:
        before = sum(len(c) for c in self.chunks)
        super().write(data)
        now = time.monotonic()
        end = self._end if self._end is not None else now
        under = not self._fresh and now - end > self.tolerance_s
        if under:
            self.gaps.append((before / self.samplerate, now - end))
        self._fresh = False
        self._end = max(end, now) + len(data) / self.samplerate
        wait = self._end - self.buffer_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return under

    def abort(self) -> None:
        super().abort()
        self._end = None

    def stop(self) -> None:
        super().stop()
        wait = self._end - time.monotonic() if self._end is not None else 0.0
        if wait > 0:
            time.sleep(wait)
        self._end = None


class FakeOutputs:
    """A fábrica injetada no `Player`: cada abertura cria um `FakeOutput` (guardado em `opened`)."""

    def __init__(self, rate: int = 16000, latency: float = 0.0, permits: threading.Semaphore | None = None,
                 fail: Exception | None = None):
        self.rate = rate
        self.latency = latency
        self.permits = permits
        self.fail = fail
        self.opened: list[FakeOutput] = []

    def __call__(self) -> FakeOutput:
        if self.fail is not None:
            raise self.fail
        out = FakeOutput(self.rate, self.latency, self.permits)
        self.opened.append(out)
        return out
