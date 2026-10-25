"""Dublê do fluxo de saída do player (F5 L4): registra os quadros escritos e as chamadas (`start`, `abort`, `stop`,
`close`); `permits` (um semáforo) deixa passar só tantos `write` quantos o teste liberar (posição no meio do som; sem ele
o som inteiro sai na hora); `fail` faz a fábrica levantar (sem dispositivo). Nada de áudio real."""
from __future__ import annotations

import threading
from typing import Any

import numpy as np


class FakeOutput:
    def __init__(self, rate: int = 16000, latency: float = 0.0, permits: threading.Semaphore | None = None):
        self.samplerate = float(rate)
        self.latency = float(latency)
        self.permits = permits
        self.chunks: list[np.ndarray] = []
        self.calls: list[str] = []
        self.wrote = threading.Event()

    def start(self) -> None:
        self.calls.append("start")

    def write(self, data: np.ndarray) -> Any:
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
