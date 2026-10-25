"""Relógio único da sessão: todos os canais carimbam tempo relativo ao mesmo t0 (perf_counter, QPC no Windows).
`Clock` é o contrato; `ManualClock` é o relógio dirigido dos testes (F1.2 M-27)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, Self


class Clock(Protocol):
    def rel(self) -> float: ...                       # segundos desde o início da sessão
    def rel_from_perf(self, perf: float) -> float: ...  # converte um perf_counter em tempo da sessão
    def wall(self, rel: float) -> float: ...          # epoch correspondente a um instante da sessão


@dataclass(frozen=True, slots=True)
class SessionClock:
    t0_perf: float
    t0_wall: float

    @classmethod
    def start(cls) -> Self:
        return cls(t0_perf=time.perf_counter(), t0_wall=time.time())

    def rel(self) -> float:
        return time.perf_counter() - self.t0_perf

    def rel_from_perf(self, perf: float) -> float:
        return perf - self.t0_perf

    def wall(self, rel: float) -> float:
        return self.t0_wall + rel


class ManualClock:
    """Relógio que só anda quando mandam (`advance`); `perf` é o perf_counter fictício do instante atual."""

    def __init__(self, t0_perf: float = 1000.0, t0_wall: float = 1_700_000_000.0):
        self.t0_perf = t0_perf
        self.t0_wall = t0_wall
        self._now_ns = 0                      # inteiro: 0,1 s dez vezes dá exatamente 1 s

    @property
    def perf(self) -> float:
        return self.t0_perf + self.rel()

    def advance(self, seconds: float) -> None:
        self._now_ns += round(float(seconds) * 1e9)

    def rel(self) -> float:
        return self._now_ns / 1e9

    def rel_from_perf(self, perf: float) -> float:
        return perf - self.t0_perf

    def wall(self, rel: float) -> float:
        return self.t0_wall + rel


def fmt_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"
