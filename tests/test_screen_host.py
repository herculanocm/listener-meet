"""F5.27 (lote 2): o `ScreenRecHost` (pytest-qt, offscreen) com o filho de verdade numa thread (`ThreadChild`, a tela de mentira):
os estados e os eventos, sem monitores, o monitor perdido e de volta com o aviso, a falha de leitura, a troca, os parâmetros, a
queda relançada uma vez; e no controlador: a pausa chega à tela e a parada espera o último pedaço antes da fila."""
from __future__ import annotations

import time
from typing import Any

import pytest

pytest.importorskip("av")
pytest.importorskip("pytestqt")

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import AppState
from listener.platform.screen_types import Monitor, ScreenRect
from listener.screen_rec import client as sc
from listener.screen_rec import proc
from listener.store import Store
from listener.store.screen import ScreenRepo
from listener.ui import screen_rec_host as host_mod
from tests.fake_live import ThreadChild
from tests.fake_screen import MutableScreen

D1 = Monitor("DISPLAY1", "Monitor 1 · 64 x 48 · principal", ScreenRect(0, 0, 64, 48), True)
D2 = Monitor("DISPLAY2", "Monitor 2 · 96 x 54", ScreenRect(64, 0, 96, 54), False)


def spawner(db: Any, screen: MutableScreen, children: list[ThreadChild]) -> Any:
    """`Platform.spawn_child` de teste: o `serve` de verdade numa thread, com a tela de mentira (as chaves do `FakePlatform`)."""
    def spawn(argv: list[str], env: Any = None) -> ThreadChild:
        mid = int(argv[argv.index("--meeting") + 1])

        def target(stdin: Any, stdout: Any) -> int:
            with Store.open_worker(db) as worker:
                return proc.serve(stdin, stdout, worker, mid, screen, check_s=0.1, retry_base_s=0.01, drain_s=5.0)
        child = ThreadChild(target, pid=8000 + len(children), argv=argv)
        children.append(child)
        return child
    return spawn


def events(store: Store, mid: int, kind: str) -> list[dict[str, Any]]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == kind]


class Env:
    def __init__(self, qapp: Any, store: Store, platform: Any, monkeypatch: Any):
        self.screen = MutableScreen(D1, D2)
        self.children: list[ThreadChild] = []
        monkeypatch.setattr(platform, "spawn_child", spawner(store.base_dir, self.screen, self.children))
        self.errors: list[str] = []
        self.host = host_mod.ScreenRecHost(platform, store, on_error=self.errors.append)
        self.states: list[tuple[str, str]] = []
        self.host.state_changed.connect(lambda s, label: self.states.append((s, label)))
        self.mid = store.create_meeting("tela", None, None, None)


@pytest.fixture
def env(qapp, store, platform, monkeypatch):
    e = Env(qapp, store, platform, monkeypatch)
    yield e
    e.host.shutdown()


def test_start_launches_the_child_emits_the_states_and_writes_the_events_until_the_meeting_stops(qtbot, env, store):
    host = env.host
    assert host.start(env.mid, time.perf_counter(), "DISPLAY1")
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=10000)
    assert host.monitor_label == "Monitor 1" and store.screen.file_for(env.mid) is not None
    qtbot.waitUntil(lambda: len(env.screen.grabs) >= 1, timeout=5000)
    host.stop()
    assert host.wait_closed(10.0)
    qtbot.waitUntil(lambda: bool(events(store, env.mid, "screen_stop")), timeout=5000)
    assert env.states[0] == ("starting", "Monitor 1") and ("recording", "Monitor 1") in env.states and env.states[-1] == ("off", "")
    assert events(store, env.mid, "screen_start") == [{"monitor": 1, "width": 64, "height": 48}]
    stop = events(store, env.mid, "screen_stop")[0]
    assert stop["reason"] == "reuniao" and stop["frames"] >= 1 and stop["segments"] == len(store.screen.list_chunks(env.mid)) >= 1
    assert all("DISPLAY" not in str(e["data"]) for e in store.list_events(env.mid))   # nunca a chave do monitor


def test_without_monitors_start_returns_false_and_launches_nothing(env, platform):
    platform.screen_monitors = ()
    assert not env.host.start(env.mid, time.perf_counter(), "DISPLAY1")
    assert env.children == [] and env.host.state == host_mod.OFF
    platform.screen_monitors = (D1,)
    assert not env.host.start(env.mid, time.perf_counter(), "DISPLAY9")          # o monitor escolhido saiu antes de gravar


def test_a_lost_monitor_shows_u183_until_it_comes_back_and_a_stop_while_lost_says_perdido(qtbot, env, store):
    host = env.host
    host.start(env.mid, time.perf_counter(), "DISPLAY1")
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=10000)
    env.screen.unplug("DISPLAY1")
    qtbot.waitUntil(lambda: host.state == "lost", timeout=5000)
    assert host.alert() == "A gravação da tela parou: o Monitor 1 saiu. O áudio segue."
    assert events(store, env.mid, "screen_lost") == [{"monitor": 1}]
    env.screen.plug("DISPLAY1")
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=5000)
    assert host.alert() is None and events(store, env.mid, "screen_start")[-1]["back"] is True
    env.screen.unplug("DISPLAY1")
    qtbot.waitUntil(lambda: host.state == "lost", timeout=5000)
    host.stop()
    assert host.wait_closed(10.0) and host.alert() is None
    qtbot.waitUntil(lambda: bool(events(store, env.mid, "screen_stop")), timeout=5000)
    assert events(store, env.mid, "screen_stop")[0]["reason"] == "perdido"


def test_the_reading_failure_shows_once_and_the_first_good_frame_clears_it(qtbot, env):
    host = env.host
    host.start(env.mid, time.perf_counter(), "DISPLAY1")
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=10000)
    client = host._session.client if host._session else None
    host._message.emit(client, {"type": sc.STATUS, "state": "falhando"})
    qtbot.waitUntil(lambda: host.state == "failing", timeout=2000)
    assert host.alert() == host_mod.FAILING_TEXT
    host._message.emit(client, {"type": sc.STATUS, "state": "ok"})
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=2000)
    assert host.alert() is None


def test_switch_writes_the_event_and_updates_the_label_and_params_go_once(qtbot, env, store, monkeypatch):
    host = env.host
    host.start(env.mid, time.perf_counter(), "DISPLAY1")
    qtbot.waitUntil(lambda: host.state == host_mod.RECORDING, timeout=10000)
    host.switch("DISPLAY2")
    qtbot.waitUntil(lambda: host.monitor_label == "Monitor 2", timeout=5000)
    assert events(store, env.mid, "screen_switch") == [{"monitor": 2, "width": 96, "height": 54}]
    assert env.states[-1] == ("recording", "Monitor 2")
    sent: list[dict[str, Any]] = []
    assert host._session is not None

    def record(msg: dict[str, Any]) -> bool:
        sent.append(msg)
        return True

    monkeypatch.setattr(host._session.client, "send", record)
    store.set_setting("screen_rec.fps", 2)
    host.settings_changed()
    host.settings_changed()
    assert sent == [{"type": sc.PARAMS, "fps": 2, "max_side": 1920}]


def test_a_crash_relaunches_once_with_the_pause_and_the_second_stops_with_the_notice(qtbot, env, store, platform, monkeypatch):
    hellos: list[dict[str, Any]] = []

    def crash(stdin: Any, stdout: Any) -> int:
        hellos.append(sc.decode_line(stdin.readline()))
        return 1

    children: list[ThreadChild] = []

    def spawn(argv: list[str], env: Any = None) -> ThreadChild:
        children.append(ThreadChild(crash, argv=argv))
        return children[-1]

    monkeypatch.setattr(platform, "spawn_child", spawn)
    host = env.host
    assert host.start(env.mid, time.perf_counter(), "DISPLAY1")
    host.pause()                                                       # a reunião pausou antes da queda
    qtbot.waitUntil(lambda: host.state == host_mod.STOPPED, timeout=10000)
    assert len(children) == 2 and [h["paused"] for h in hellos] == [False, True]
    assert env.errors == [host_mod.CRASHED_TEXT]
    assert [e["reason"] for e in events(store, env.mid, "screen_stop")] == ["queda", "queda"]


def _cfg() -> RecordConfig:
    return RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                        retry_base_s=0.001)


def test_the_controller_pauses_the_screen_and_the_stop_waits_for_the_last_chunk_before_processing(qtbot, qapp, store, platform,
                                                                                                   monkeypatch):
    from listener.ui import controller as ctl

    store.set_setting("live.enabled", False)
    screen = MutableScreen(D1, D2)
    children: list[ThreadChild] = []
    monkeypatch.setattr(platform, "spawn_child", spawner(store.base_dir, screen, children))
    real_insert = ScreenRepo.insert_chunk

    def slow_insert(self: ScreenRepo, *a: Any, **k: Any) -> int:          # o último pedaço demora a gravar
        time.sleep(0.8)
        return real_insert(self, *a, **k)

    monkeypatch.setattr(ScreenRepo, "insert_chunk", slow_insert)
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    seen: list[tuple[int, bool]] = []
    real_enqueue = c._enqueue_processing

    def enqueue(meeting_id: int) -> int | None:
        seen.append((len(store.screen.list_chunks(meeting_id)), all(ch.poll() is not None for ch in children)))
        return real_enqueue(meeting_id)

    monkeypatch.setattr(c, "_enqueue_processing", enqueue)
    try:
        assert c.start_recording(_cfg(), {"title": "com tela", "screen_monitor": "DISPLAY1"})
        assert store.get_setting("screen_rec.monitor") == "DISPLAY1" and len(children) == 1
        assert c.widget is not None and platform.excluded[-1] == int(c.widget.winId())      # o widget fora da captura
        qtbot.waitUntil(lambda: c.screen.state == host_mod.RECORDING, timeout=10000)
        qtbot.waitUntil(lambda: len(screen.grabs) >= 1, timeout=5000)
        c.toggle_pause()
        assert c.screen.state == host_mod.PAUSED
        c.toggle_pause()
        assert c.screen.state == host_mod.RECORDING
        platform.pump(0.6)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=15000)
        assert seen and seen[0][0] >= 1 and seen[0][1]                    # o pedaço gravado e o filho fora antes da fila
        assert c.screen.state == host_mod.OFF
    finally:
        c.emergency_stop()


def _controller(qapp: Any, store: Store, platform: Any, monkeypatch: Any, screen: MutableScreen, children: list[ThreadChild]) -> Any:
    from listener.ui import controller as ctl

    store.set_setting("live.enabled", False)
    monkeypatch.setattr(platform, "spawn_child", spawner(store.base_dir, screen, children))
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    return c


def test_the_app_exit_mid_recording_writes_the_last_chunk_and_the_stop_event_before_the_store_closes(qtbot, qapp, store, platform,
                                                                                                    monkeypatch):
    screen, children = MutableScreen(D1, D2), list[ThreadChild]()
    c = _controller(qapp, store, platform, monkeypatch, screen, children)
    real_insert, real_close = ScreenRepo.insert_chunk, store.close
    closed_with: list[bool] = []

    def slow_insert(self: ScreenRepo, *a: Any, **k: Any) -> int:
        time.sleep(0.8)
        return real_insert(self, *a, **k)

    def close() -> None:
        closed_with.append(all(ch.poll() is not None for ch in children))
        real_close()

    monkeypatch.setattr(ScreenRepo, "insert_chunk", slow_insert)
    monkeypatch.setattr(store, "close", close)
    assert c.start_recording(_cfg(), {"title": "sair gravando", "screen_monitor": "DISPLAY1"})
    mid = c.recorder.meeting_id if c.recorder else 0
    qtbot.waitUntil(lambda: c.screen.state == host_mod.RECORDING, timeout=10000)
    qtbot.waitUntil(lambda: len(screen.grabs) >= 1, timeout=5000)
    platform.pump(0.4)
    c.emergency_stop()
    assert closed_with == [True]                                          # o filho saiu antes de o banco fechar
    with Store.open_readonly(store.base_dir) as ro:
        assert len(ro.screen.list_chunks(mid)) >= 1
        stops = [e["data"] for e in ro.list_events(mid) if e["type"] == "screen_stop"]
    assert len(stops) == 1 and stops[0]["reason"] == "reuniao" and stops[0]["written"] >= 1


def test_cancel_kills_the_screen_child_before_the_meeting_is_deleted(qtbot, qapp, store, platform, monkeypatch):
    screen, children = MutableScreen(D1, D2), list[ThreadChild]()
    c = _controller(qapp, store, platform, monkeypatch, screen, children)
    monkeypatch.setattr("listener.ui.controller.confirm_discard", lambda *_a: True)
    order: list[str] = []
    real_delete = store.delete_meeting

    def delete(meeting_id: int) -> None:
        order.append("delete" if all(ch.killed and ch.poll() is not None for ch in children) else "delete-com-filho-vivo")
        real_delete(meeting_id)

    monkeypatch.setattr(store, "delete_meeting", delete)
    try:
        assert c.start_recording(_cfg(), {"title": "cancelar", "screen_monitor": "DISPLAY2"})
        qtbot.waitUntil(lambda: c.screen.state == host_mod.RECORDING, timeout=10000)
        c.cancel_recording()
        assert order == ["delete"] and c.screen.state == host_mod.OFF and c.state == AppState.IDLE
    finally:
        c.emergency_stop()
