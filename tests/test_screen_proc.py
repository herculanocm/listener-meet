"""F5.27 (lote 2): o filho `--screen` em processo (o `serve` de verdade numa thread, pipes de verdade, banco temporário no papel
WORKER, tela de mentira) conduzido pelo `ScreenClient`: o protocolo, os pedaços no banco que o app lê, a pausa, a troca, o
monitor perdido e de volta, o EOF, "database is locked" e o índice repetido, e o despacho de `--screen`."""
from __future__ import annotations

import io
import itertools
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("av")

import listener.__main__ as entry
from listener.platform.common import process
from listener.screen_rec import client as sc
from listener.screen_rec import proc
from listener.screen_rec.segment import decode_segment
from listener.store import Store
from listener.store.screen import ScreenRepo
from tests.fake_live import ThreadChild
from tests.fake_screen import SMALL, WIDE, MutableScreen


def wait_for(pred: Callable[[], Any], timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condição não chegou no prazo")


class Run:
    """Um filho em processo com o seu cliente; `msgs` guarda as linhas recebidas."""

    def __init__(self, store: Store, screen: MutableScreen, *, key: str = SMALL.key, fps: int = 5, paused: bool = False,
                 **kw: Any):
        self.store, self.screen = store, screen
        self.meeting_id = store.create_meeting("tela", None, None, None)
        store.screen.ensure_file(self.meeting_id)
        self.msgs: list[dict[str, Any]] = []
        self.children: list[ThreadChild] = []
        hello = sc.ScreenHello(time.perf_counter(), key, fps=fps, max_side=1920, paused=paused, chunk_s=1)
        argv = process.screen_command(store.base_dir, self.meeting_id)
        self.client = sc.ScreenClient(self._spawn(kw), argv, None, hello, self.msgs.append, meeting_id=self.meeting_id)
        self.client.start()

    def _spawn(self, kw: dict[str, Any]) -> Callable[..., ThreadChild]:
        db, mid, screen = self.store.base_dir, self.meeting_id, self.screen
        kw = {"check_s": 0.1, "retry_base_s": 0.01, "drain_s": 5.0, **kw}

        def target(stdin: Any, stdout: Any) -> int:
            with Store.open_worker(db) as worker:
                worker.core.release_audio()
                return proc.serve(stdin, stdout, worker, mid, screen, **kw)

        def spawn(argv: list[str], env: Any = None) -> ThreadChild:
            child = ThreadChild(target, pid=7000 + len(self.children), argv=argv)
            self.children.append(child)
            return child
        return spawn

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in list(self.msgs) if m["type"] == kind]

    def wait(self, kind: str, n: int = 1, timeout: float = 10.0) -> list[dict[str, Any]]:
        return wait_for(lambda: len(self.of(kind)) >= n and self.of(kind), timeout)

    def finish(self) -> dict[str, Any]:
        self.client.close(10.0)
        assert self.client.wait_closed(15.0)
        return self.wait(sc.EXIT)[0]

    def chunks(self) -> list[Any]:
        return self.store.screen.list_chunks(self.meeting_id)


def test_hello_round_trips_and_a_bad_hello_is_refused():
    hello = sc.ScreenHello(12.5, "DISPLAY2", fps=2, max_side=1280, paused=True, chunk_s=30)
    assert sc.ScreenHello.from_msg(sc.decode_line(sc.encode_line(hello.to_msg()))) == hello
    with pytest.raises(sc.ProtocolError):
        sc.ScreenHello.from_msg({**hello.to_msg(), "v": 99})
    with pytest.raises(sc.ProtocolError):
        sc.ScreenHello.from_msg({"type": sc.HELLO, "v": sc.PROTOCOL_VERSION, "t0_perf": 1.0, "monitor_key": ""})
    with pytest.raises(sc.ProtocolError):
        sc.decode_line(b"[1, 2]\n")


def test_the_child_writes_chunks_with_growing_idx_in_the_worker_store_and_eof_writes_the_last(store):
    run = Run(store, MutableScreen())
    started = run.wait(sc.STARTED)[0]
    assert started["monitor"] == 1 and (started["width"], started["height"]) == (64, 48)
    run.wait(sc.CHUNK)                                                    # o pedaço de 1 s, gravado pelo `screen-write`
    end = run.finish()
    assert end["expected"] and end["code"] == 0 and end["bye"]
    bye = run.client.bye
    assert bye is not None and bye["unwritten"] == 0 and bye["written"] >= 2 and bye["stats"]["frames"] >= 5
    chunks = run.chunks()                                                 # o app lê o que o filho gravou
    assert [c["idx"] for c in chunks] == list(range(len(chunks))) == [m["idx"] for m in run.of(sc.CHUNK)]
    assert all(a["t_end_ms"] <= b["t_start_ms"] for a, b in itertools.pairwise(chunks))
    data = store.screen.get_chunk(run.meeting_id, 0)
    assert data is not None and len(list(decode_segment(data))) == chunks[0]["frames"]
    assert run.of(sc.STATS) and "frames" in run.of(sc.STATS)[-1]["stats"]


def test_pause_closes_the_chunk_and_stops_grabbing_until_resume(store):
    screen = MutableScreen()
    run = Run(store, screen)
    run.wait(sc.STARTED)
    wait_for(lambda: len(screen.grabs) >= 2)
    run.client.send({"type": sc.PAUSE})
    run.wait(sc.CHUNK)                                                    # a pausa fecha o pedaço em curso
    grabs = len(screen.grabs)
    time.sleep(0.5)
    assert len(screen.grabs) == grabs
    run.client.send({"type": sc.RESUME})
    wait_for(lambda: len(screen.grabs) > grabs)
    run.finish()
    chunks = run.chunks()
    assert len(chunks) >= 2 and chunks[1]["t_start_ms"] - chunks[0]["t_end_ms"] >= 400   # o buraco da pausa fica entre dois


def test_switch_opens_a_new_chunk_with_the_new_monitor_size(store):
    run = Run(store, MutableScreen())
    run.wait(sc.STARTED)
    run.client.send({"type": sc.SWITCH, "monitor_key": WIDE.key})
    switched = run.wait(sc.SWITCHED)[0]
    assert switched["monitor"] == 2 and (switched["width"], switched["height"]) == (96, 54)
    time.sleep(0.3)
    run.finish()
    sizes = [(c["width"], c["height"]) for c in run.chunks()]
    assert sizes[0] == (64, 48) and sizes[-1] == (96, 54)


def test_a_lost_monitor_closes_the_chunk_and_only_the_same_monitor_coming_back_resumes(store):
    screen = MutableScreen()
    run = Run(store, screen)
    run.wait(sc.STARTED)
    wait_for(lambda: len(screen.grabs) >= 2)
    screen.unplug(SMALL.key)
    assert run.wait(sc.LOST)[0]["monitor"] == 1
    run.wait(sc.CHUNK)                                                    # o pedaço fechou ao perder o monitor
    run.client.send({"type": sc.RESUME})                                  # retomar a reunião com o monitor fora não captura
    grabs = len(screen.grabs)
    time.sleep(0.5)
    assert len(screen.grabs) == grabs and WIDE.key in screen.present      # o outro monitor segue ali e não é gravado
    screen.plug(SMALL.key)
    back = run.wait(sc.BACK)[0]
    assert back["monitor"] == 1 and (back["width"], back["height"]) == (64, 48)
    wait_for(lambda: len(screen.grabs) > grabs)
    assert all(r == SMALL.rect for r in screen.grabs)
    run.finish()
    assert len(run.chunks()) >= 2


def test_a_monitor_back_while_the_meeting_is_paused_stays_paused(store):
    screen = MutableScreen()
    run = Run(store, screen, paused=True)
    run.wait(sc.STARTED)
    screen.unplug(SMALL.key)
    run.wait(sc.LOST)
    screen.plug(SMALL.key)
    run.wait(sc.BACK)
    time.sleep(0.3)
    assert screen.grabs == []                                             # a reunião segue pausada: nenhum quadro
    run.finish()
    assert run.chunks() == []


def test_database_locked_is_retried_and_no_chunk_is_lost(store, monkeypatch):
    real = ScreenRepo.insert_chunk
    failures = {"n": 0}

    def flaky(self: ScreenRepo, *a: Any, **k: Any) -> int:
        if failures["n"] < 3:
            failures["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return real(self, *a, **k)

    monkeypatch.setattr(ScreenRepo, "insert_chunk", flaky)
    screen = MutableScreen()
    run = Run(store, screen)
    run.wait(sc.STARTED)
    wait_for(lambda: len(screen.grabs) >= 2)
    run.finish()
    bye = run.client.bye
    assert failures["n"] == 3 and bye is not None and bye["unwritten"] == 0
    assert len(run.chunks()) == bye["written"] == bye["stats"]["segments"] >= 1


def test_a_locked_first_index_read_is_retried_and_a_non_lock_failure_ends_the_child_with_an_error(store, monkeypatch):
    real_list = ScreenRepo.list_chunks
    locked = {"n": 0}

    def flaky_list(self: ScreenRepo, meeting_id: int) -> Any:
        if locked["n"] < 2:
            locked["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return real_list(self, meeting_id)

    monkeypatch.setattr(ScreenRepo, "list_chunks", flaky_list)
    screen = MutableScreen()
    run = Run(store, screen)
    run.wait(sc.STARTED)
    wait_for(lambda: len(screen.grabs) >= 2)
    run.finish()
    assert locked["n"] == 2 and run.client.bye is not None and run.client.bye["unwritten"] == 0 and len(run.chunks()) >= 1

    def broken(self: ScreenRepo, *a: Any, **k: Any) -> int:
        raise RuntimeError("arquivo da tela estranho")

    monkeypatch.setattr(ScreenRepo, "insert_chunk", broken)
    run = Run(store, MutableScreen())
    end = run.wait(sc.EXIT, timeout=15.0)[0]                              # a sessão para sozinha: o app relança
    assert end["code"] == 1 and not end["expected"] and run.of(sc.ERROR)[0]["retry"] is True
    assert run.client.bye is not None and run.client.bye["unwritten"] >= 1 and run.client.bye["written"] == 0


def test_idx_continues_after_the_chunks_already_there_and_skips_a_taken_one(store, monkeypatch):
    calls: list[int] = []
    real_next = proc.ChunkWriter._next_idx

    def stale(self: proc.ChunkWriter) -> int:                           # a primeira leitura vê o banco de antes de um filho anterior
        calls.append(1)
        return 0 if len(calls) == 1 else real_next(self)

    monkeypatch.setattr(proc.ChunkWriter, "_next_idx", stale)
    screen = MutableScreen()
    run = Run(store, screen, paused=True)
    for idx in (0, 1):                                                    # os pedaços de um filho anterior
        store.screen.insert_chunk(run.meeting_id, idx=idx, t_start_ms=idx * 10, t_end_ms=idx * 10 + 5, frames=1, width=64, height=48,
                                  codec="libx264", data=b"x")
    run.wait(sc.STARTED)
    run.client.send({"type": sc.RESUME})
    wait_for(lambda: len(screen.grabs) >= 2)
    run.finish()
    assert [c["idx"] for c in run.chunks()][:3] == [0, 1, 2] and len(calls) >= 2


def test_a_missing_hello_or_meeting_ends_with_an_error_line(store):
    out = io.BytesIO()
    mid = store.create_meeting("x", None, None, None)
    assert proc.serve(io.BytesIO(b""), out, store, mid, MutableScreen()) == 2
    assert sc.decode_line(out.getvalue().splitlines()[0])["type"] == sc.ERROR
    out = io.BytesIO()
    assert proc.serve(io.BytesIO(b""), out, store, 999, MutableScreen()) == 2


def test_a_crashed_child_is_reported_as_unexpected_and_close_never_blocks(store):
    msgs: list[dict[str, Any]] = []
    hello = sc.ScreenHello(0.0, "K")
    crashing = sc.ScreenClient(lambda argv, env: ThreadChild(lambda i, o: 1, argv=argv), ["x"], None, hello, msgs.append, meeting_id=1)
    crashing.start()
    wait_for(lambda: any(m["type"] == sc.EXIT for m in msgs))
    assert msgs[-1] == {"type": sc.EXIT, "code": 1, "expected": False, "bye": False}
    assert not crashing.send({"type": sc.PAUSE})                          # morto: o comando vira no-op

    def stuck(stdin: Any, stdout: Any) -> int:                            # ignora o EOF até o kill
        time.sleep(1.0)
        return 0

    msgs.clear()
    slow = sc.ScreenClient(lambda argv, env: ThreadChild(stuck, argv=argv), ["x"], None, hello, msgs.append, meeting_id=1)
    slow.start()
    t0 = time.monotonic()
    slow.close(5.0)
    assert time.monotonic() - t0 < 0.2 and slow.wait_closed(5.0)
    assert wait_for(lambda: [m for m in msgs if m["type"] == sc.EXIT])[0]["expected"]


def test_the_screen_flag_dispatches_to_the_screen_child_and_the_command_has_the_flags(monkeypatch):
    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(proc, "main", fake_main)
    assert entry._dispatch(["--screen", "--db", "D", "--meeting", "3"]) == 0
    assert seen == [["--db", "D", "--meeting", "3"]]
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert process.screen_command(Path("D"), 3) == [sys.executable, "-m", "listener", "--screen", "--db", "D", "--meeting", "3"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programas\listener-meet\listener-meet.exe")
    assert process.screen_command("D", 3) == [r"C:\Programas\listener-meet\listener-meet.exe", "--screen", "--db", "D", "--meeting", "3"]
