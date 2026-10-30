"""Dublê do fluxo de saída do player (F5 L4): registra os quadros escritos e as chamadas (`start`, `abort`, `stop`,
`close`); `permits` (um semáforo) deixa passar só tantos `write` quantos o teste liberar (posição no meio do som; sem ele
o som inteiro sai na hora) e `entered` conta os `write` começados (o teste sabe que há um em voo); `fail` faz a fábrica
levantar (sem dispositivo). R7-02: `RealtimeOutput` toca em tempo real com um buffer pequeno e conta os buracos. D-067: a
fábrica recebe o endpoint pedido (None = o padrão de agora, `system`); `known` é a lista da biblioteca (o padrão fora dela
abre a reserva `library_default`, com o `endpoint_id` vazio; pedido explícito fora dela é `LookupError`); `probe` é a leitura
do padrão, contada em `probes`, que `probe_error` faz falhar. Nada de áudio real."""
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
        self.endpoint_id = "fake-out-0"          # D-067: o endpoint que a plataforma diz ter aberto (o SPEAKERS da falsa)
        self.device = self.endpoint_id           # D-067: onde o som sai de fato (a reserva diz "" e toca aqui)

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
    """Um dispositivo que toca em tempo real com `buffer_s` de buffer (R7-02, o MME tem ~0,18 s), fiel ao WASAPI do
    PortAudio (D-067, R-01 e R-07, medido em 30/10): `write` volta quando o bloco coube e nunca diz `underflowed`; o espaço
    livre (`write_available`) volta ao tamanho do buffer quando ele esvazia; `stop` não espera o buffer tocar e anota em
    `cut_s` o que ficou nele (o fim cortado). Cada buraco vai para `gaps` como (segundo do áudio, duração em s); o primeiro
    bloco depois de `start` não conta; `abort` descarta o buffer."""

    def __init__(self, rate: int = 16000, buffer_s: float = 0.18, tolerance_s: float = 0.01):
        super().__init__(rate, latency=buffer_s)
        self.buffer_s = buffer_s
        self.tolerance_s = tolerance_s
        self.gaps: list[tuple[float, float]] = []
        self.cut_s: float | None = None          # o que ficou no buffer no `stop` (None antes de parar)
        self._end: float | None = None           # instante em que o que está no buffer acaba de tocar
        self._fresh = False

    @property
    def write_available(self) -> int:
        if self._end is None:
            raise RuntimeError("Stream is stopped")             # como o PortAudio antes do `start`
        buffered = max(0.0, self._end - time.monotonic()) * self.samplerate
        return max(0, round(self.buffer_s * self.samplerate - buffered))

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
        return False                             # o PortAudio não diz (R-07): o motor mede pelo buffer vazio

    def abort(self) -> None:
        super().abort()
        self._end = None

    def stop(self) -> None:
        super().stop()
        self.cut_s = max(0.0, self._end - time.monotonic()) if self._end is not None else 0.0   # o WASAPI não espera
        self._end = None


class FakeOutputs:
    """A fábrica injetada no `Player`: cada abertura cria um `FakeOutput` (guardado em `opened`)."""

    def __init__(self, rate: int = 16000, latency: float = 0.0, permits: threading.Semaphore | None = None,
                 fail: Exception | None = None, system: str = "", known: set[str] | None = None, rates: dict[str, int] | None = None,
                 realtime: bool = False):
        self.rate = rate
        self.latency = latency
        self.permits = permits
        self.fail = fail
        self.opened: list[FakeOutput] = []
        self.system = system                     # D-067: o padrão de saída do "Windows" agora
        self.known = known                       # a lista da biblioteca (None = todos)
        self.library_default = "alto-falante"    # a reserva quando o padrão não está na lista
        self.rates = rates or {}                 # a taxa de cada endpoint (padrão `rate`)
        self.realtime = realtime                 # `RealtimeOutput` (toca em tempo real) no lugar do `FakeOutput`
        self.requested: list[str | None] = []
        self.probes = 0
        self.probe_error: Exception | None = None

    def __call__(self, endpoint_id: str | None = None) -> FakeOutput:
        self.requested.append(endpoint_id)
        if self.fail is not None:
            raise self.fail
        wanted = endpoint_id or self.system
        if self.known is not None and wanted not in self.known:
            if endpoint_id:
                raise LookupError(f"a saída {endpoint_id} não está na lista")
            wanted = ""
        rate = self.rates.get(wanted, self.rate)
        out = RealtimeOutput(rate) if self.realtime else FakeOutput(rate, self.latency, self.permits)
        out.endpoint_id, out.device = wanted, wanted or self.library_default
        self.opened.append(out)
        return out

    def probe(self) -> str | None:
        self.probes += 1
        if self.probe_error is not None:
            raise self.probe_error
        return self.system or None
