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


def test_the_local_embedder_computing_in_the_loop_is_not_a_stall(store, tmp_path, monkeypatch, stalls):
    """R-06: o embedder local calcula no laço, sem ceder (F5.1, sem `to_thread`); a rodada inteira passa do limite do vigia,
    mas cada lote fica abaixo dele e o laço volta a rodar entre os lotes: o worker leve não sai no meio de um índice longo."""
    from listener.rag import index
    from tests.fake_local import FakeSession, long_meeting, use_local

    monkeypatch.setattr(aio._Stall, "limit_s", 0.5)
    use_local(store, tmp_path, monkeypatch)
    mid = long_meeting(store, 4)
    index.sync_text(store, mid)
    run = FakeSession.run

    def computing(self, names, feed):
        time.sleep(0.2)                                  # a CPU no laço, como a sessão do ONNX
        return run(self, names, feed)

    monkeypatch.setattr(FakeSession, "run", computing)
    t0 = time.monotonic()
    result = index.fill_vectors(store, mid, batch_size=1)
    assert time.monotonic() - t0 > 0.5 and result.missing == 0 and result.written == 5 and stalls == []


def test_the_manual_pause_of_an_llm_job_is_not_a_stall(store, stalls):
    """D-072: a pausa manual da fila esperava com o `sleep` dentro do laço do job `llm`; aos 120 s o vigia derrubava o worker
    leve, com um falso "chamada presa" no log e uma tentativa do job a menos. A espera agora roda fora do laço."""
    from listener.llm.fake import FakeProvider
    from listener.model import JobStatus
    from listener.pipeline import llm_steps
    from listener.pipeline.llm_steps import LlmJobRunner

    store.upsert_me("Eu")
    mid = store.create_meeting("t", None, None, None)
    store.finish_meeting(mid, 1000)
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-ABCDEFGH12345678", fast_model="fake-fast",
                                    strong_model="fake-strong")
    llm_steps.enqueue_test_job(store, mid)
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    store.set_setting("pipeline.paused_manual", True)
    waits: list[float] = []

    def wait(seconds: float) -> None:                    # 0,6 s de pausa (o vigia do teste dispara aos 0,4 s), depois solta
        time.sleep(seconds)
        waits.append(seconds)
        if len(waits) == 12:
            store.set_setting("pipeline.paused_manual", False)

    status = LlmJobRunner(store, job, provider=FakeProvider(), heartbeat_thread=False, sleep=wait, wait_poll_s=0.05).run()
    assert stalls == [] and status == JobStatus.DONE and len(waits) == 12


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
