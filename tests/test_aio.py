"""D-058: o vigia dos laços do asyncio no worker. Uma chamada nativa presa no laço (a verificação de certificado do Windows
no aperto de mão TLS, 27/10) não deixa nenhum timeout do Python disparar e o heartbeat segue batendo; `watched` percebe o
laço parado e chama `on_stall` (no worker: grava, sai e o app relança)."""
from __future__ import annotations

import asyncio
import time

import pytest

from listener.llm import aio


@pytest.fixture
def stalls(monkeypatch):
    got: list[str] = []
    monkeypatch.setattr(aio._Stall, "on_stall", got.append)
    monkeypatch.setattr(aio._Stall, "limit_s", 0.4)
    monkeypatch.setattr(aio, "TICK_S", 0.05)
    return got


def test_without_the_watchdog_it_is_a_plain_await():
    async def work() -> int:
        return 7

    assert aio._Stall.on_stall is None and aio.run_async(aio.watched(work())) == 7


def test_a_loop_blocked_in_native_code_calls_on_stall(stalls):
    async def blocked() -> str:
        time.sleep(1.0)                                  # como a chamada nativa presa: o laço não roda mais nada
        return "voltou"

    assert aio.run_async(aio.watched(blocked())) == "voltou"
    assert len(stalls) == 1 and "não responde" in stalls[0]


def test_a_slow_call_that_awaits_is_not_a_stall(stalls):
    async def slow() -> str:
        await asyncio.sleep(1.0)                         # a rede demorando: o laço segue rodando
        return "ok"

    assert asyncio.run(aio.watched(slow())) == "ok" and stalls == []


def test_the_worker_logs_dumps_the_threads_and_exits(monkeypatch, caplog):
    from listener.pipeline import worker

    codes: list[int] = []
    dumped: list[int] = []
    monkeypatch.setattr(worker.os, "_exit", codes.append)
    monkeypatch.setattr(worker, "dump_all_threads", lambda: dumped.append(1))
    monkeypatch.setattr(worker, "shutdown", lambda: None)
    with caplog.at_level("ERROR", logger="listener.worker"):
        worker.exit_stalled("o laço do asyncio não responde há 120 s")
    assert codes == [worker.EXIT_STALLED] and dumped == [1] and "saindo para o app relançar" in caplog.text
