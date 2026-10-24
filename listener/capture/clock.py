"""Relógio único da sessão: todos os canais carimbam tempo relativo ao mesmo t0 (perf_counter, QPC no Windows)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SessionClock:
    t0_perf: float
    t0_wall: float

    @classmethod
    def start(cls) -> "SessionClock":
        return cls(t0_perf=time.perf_counter(), t0_wall=time.time())

    def rel(self) -> float:
        return time.perf_counter() - self.t0_perf

    def rel_from_perf(self, perf: float) -> float:
        return perf - self.t0_perf

    def wall(self, rel: float) -> float:
        return self.t0_wall + rel

    def wall_iso(self, rel: float = 0.0) -> str:
        return datetime.fromtimestamp(self.wall(rel)).isoformat(timespec="milliseconds")


def fmt_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"
