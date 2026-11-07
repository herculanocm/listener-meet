"""F5.18, lote 4, o worker leve (PF-20): `identify`, `revise` e o título sem ata juntos num `TaskGroup`, o título depois da ata
quando ela está no job, a contagem de tokens pela rede só no job automático; o `claim` que só monta o snapshot com um pendente
devido; o `--idle-exit` padrão de 600 s do leve; a soltura da sessão do e5 na volta ociosa e a saída quando o `user_version`
do banco não é o do código."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from listener import log as applog
from listener.llm import local_embed
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask, Request
from listener.model import JobKind, JobStatus, JobStep
from listener.pipeline import llm_steps, worker
from listener.store import Store
from tests.fake_local import Clock, Factories, ready_folder
from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

pytestmark = pytest.mark.usefixtures("no_summary_gate")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


class OrderedFake(FakeProvider):
    """Anota o começo e o fim de cada chamada; a `identify` só termina depois de a `title` (ou a `revise`) ter começado, ou de
    um teto curto: assim a ordem observada diz o que correu junto."""

    def __init__(self, hold_until: tuple[LlmTask, ...] = ()):
        super().__init__()
        self.order: list[str] = []
        self.hold_until = hold_until
        self.token_counts = 0

    async def complete(self, req: Request) -> Any:
        self.order.append(f"start:{req.task}")
        if req.task == LlmTask.IDENTIFY:
            for _ in range(400):
                if any(f"start:{t}" in self.order for t in self.hold_until):
                    break
                await asyncio.sleep(0.005)
        comp = await super().complete(req)
        self.order.append(f"end:{req.task}")
        return comp

    async def count_tokens(self, req: Request) -> int | None:
        self.token_counts += 1
        return await super().count_tokens(req)


def _ready_meeting(platform: Any, store: Store) -> int:
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    return mid


def test_title_and_revise_run_alongside_identify_when_the_job_has_no_minutes(platform, store):
    mid = _ready_meeting(platform, store)
    store.set_setting("llm.max_parallel", 3)                                                # um lugar para cada uma das três
    auto = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM)          # o núcleo automático: identify, title, revise
    fake = OrderedFake(hold_until=(LlmTask.TITLE, LlmTask.REVISE))
    runner, status, _ = _run_llm(store, fake)
    assert runner.job_id == int(auto["id"]) and status == JobStatus.DONE
    assert set(runner.tasks) == {JobStep.IDENTIFY, JobStep.TITLE, JobStep.REVISE}
    order = fake.order
    ended = order.index(f"end:{LlmTask.IDENTIFY}")
    assert order.index(f"start:{LlmTask.TITLE}") < ended and order.index(f"start:{LlmTask.REVISE}") < ended   # as três juntas
    assert fake.token_counts == 1                                                           # automático: a contagem pela rede
    assert store.get_meeting(mid)["title_llm"] and all(s["text_llm"] for s in store.segments.list_segments(mid) if s["text"])


def test_title_waits_for_the_minutes_when_they_are_in_the_job(platform, store):
    mid = _ready_meeting(platform, store)
    _run_llm(store, FakeProvider())                                                         # o núcleo automático
    assert llm_steps.enqueue_llm_job(store, mid, ["identify", "minutes", "title", "executive"], requested=True)
    fake = OrderedFake()
    runner, status, _ = _run_llm(store, fake)
    assert status == JobStatus.DONE and runner.auto is False
    order = fake.order
    assert order.index(f"start:{LlmTask.MINUTES}") > order.index(f"end:{LlmTask.IDENTIFY}")  # a ata, depois da identidade
    assert order.index(f"start:{LlmTask.EXECUTIVE}") > order.index(f"end:{LlmTask.MINUTES}")  # o executivo, depois da ata
    assert order.index(f"start:{LlmTask.TITLE}") > order.index(f"end:{LlmTask.MINUTES}")     # o título, depois da ata
    assert fake.token_counts == 0                                                           # pedido manual: sem a rede
    assert llm_steps.enqueue_llm_job(store, mid, ["executive", "pre_summary"], requested=True)
    fake3 = OrderedFake()
    _, status, _ = _run_llm(store, fake3)                                                   # sem ata no job, o resto roda igual
    assert status == JobStatus.DONE and {o.split(":")[1] for o in fake3.order} == {str(LlmTask.EXECUTIVE), str(LlmTask.PRE_SUMMARY)}


def test_claim_builds_the_snapshot_only_with_a_due_pending_job(store):
    mid = store.create_meeting("Reunião", None, None, None)
    store.finish_meeting(mid, 5000)
    built: list[int] = []

    def snapshot() -> str:
        built.append(1)
        return '{"provider_id": "x"}'

    assert store.jobs.claim(pid=1, config_json=snapshot, kinds=("llm", "index")) is None and built == []     # nada pendente
    store.jobs.enqueue_index(mid, "edit", delay_s=600)
    assert store.jobs.claim(pid=1, config_json=snapshot, kinds=("llm", "index")) is None and built == []     # pendente, mas não devido
    job = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    row = store.jobs.claim(pid=1, config_json=snapshot, kinds=("llm", "index"))
    assert row is not None and int(row["id"]) == job and built == [1] and row["config_json"] == '{"provider_id": "x"}'
    assert store.jobs.claim(pid=2, config_json=snapshot, kinds=("llm", "index")) is None and built == [1]    # o leve vivo segura


def test_light_idle_exit_default_is_ten_minutes():
    assert worker.idle_exit_s(None, light=True) == worker.LIGHT_IDLE_EXIT_S == 600.0
    assert worker.idle_exit_s(None, light=False) == worker.IDLE_EXIT_S == 60.0
    assert worker.idle_exit_s(5, light=True) == 5.0 and worker.idle_exit_s(0.0, light=True) == 0.0


def test_idle_light_worker_releases_the_e5_session_and_leaves_when_the_schema_moved(store, tmp_path, monkeypatch):
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    folder = ready_folder(tmp_path / "modelos")
    clock, factories = Clock(), Factories()
    session = factories.local_session(clock=clock)
    session.configure(worker.LOCAL_EMBED_THREADS)                                       # como o leve a deixa (A-03)
    monkeypatch.setattr(local_embed, "extra_installed", lambda: True)
    monkeypatch.setattr(local_embed, "_SESSION", session)
    session.preload(folder)
    assert local_embed.loaded()
    assert worker._serve(store, once=False, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    assert local_embed.loaded()                                                     # parada há menos de 120 s: fica
    clock.t += worker.LOCAL_RELEASE_S + 1
    assert worker._serve(store, once=False, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    assert not local_embed.loaded()                                                 # a volta ociosa a soltou
    monkeypatch.setattr(worker, "MAIN_SCHEMA_VERSION", worker.MAIN_SCHEMA_VERSION + 1)
    t0 = time.monotonic()
    assert worker._serve(store, once=False, idle_exit_s=100.0, poll_s=0.0, light=True) == 0       # sairia em 100 s: sai na hora
    assert time.monotonic() - t0 < 10
    applog.flush()                                                                      # o log do leve grava em thread
    text = (tmp_path / "logs" / "listener-llm.log").read_text(encoding="utf-8")
    assert "encerrando (o app migra com o leve fora)" in text and "sessão solta depois de 2 min sem uso" in text


class CancelDuringGroupFake(FakeProvider):
    """Pede o cancelamento do job de dentro da primeira chamada da LLM: o grupo termina, os blocos do `revise` que ainda não
    saíram não chamam a rede, e o `acheckpoint` do fim do grupo fecha o job como `cancelado` (R-01 da revisão do lote 4)."""

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.calls: list[str] = []

    async def complete(self, req: Request) -> Any:
        self.calls.append(str(req.task))
        if len(self.calls) == 1:
            job = next(j for j in self.store.jobs.active() if str(j["kind"]) == str(JobKind.LLM))
            self.store.jobs.request_cancel(int(job["id"]))
        return await super().complete(req)


def test_cancel_requested_during_the_group_closes_the_job_as_cancelled_and_skips_the_revise_blocks(platform, store):
    mid = _ready_meeting(platform, store)
    store.set_setting("llm.max_parallel", 1)                                                # uma chamada por vez
    fake = CancelDuringGroupFake(store)
    runner, status, _ = _run_llm(store, fake)
    assert status == JobStatus.CANCELLED and runner.status == JobStatus.CANCELLED
    assert str(JobStatus.CANCELLED) == str(store.jobs.get(runner.job_id)["status"])
    assert str(LlmTask.REVISE) not in fake.calls                                            # os blocos viram o pedido e não saíram
    assert not any(s["text_llm"] for s in store.segments.list_segments(mid))
