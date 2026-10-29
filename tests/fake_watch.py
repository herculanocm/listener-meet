"""F5.7 (L3): os falsos da cola do aviso de reunião sem gravação (`ui/call_watch.py`): o relógio manual, a bandeja, a pilha dos
avisos e o controlador, e a cola montada sem a thread do sensor (as leituras vêm do teste)."""
from __future__ import annotations

import functools
from types import SimpleNamespace
from typing import Any

from listener.ui.call_sensor import CallSensor, Intervals
from tests.fake_calls import act, teams

FAST = Intervals(stopped=0.05, idle=5.0, arming=0.02, poll=0.03, slice=0.01, backoff=0.01, backoff_max=0.04)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeTray:
    def __init__(self) -> None:
        self.call = ("", "")
        self.off_label = ""
        self.handlers: tuple[Any, Any] | None = None

    def set_call_handlers(self, on_record: Any, on_off: Any) -> None:
        self.handlers = (on_record, on_off)

    def set_call(self, exe: str, label: str, off_label: str = "") -> None:
        self.call, self.off_label = (exe, label), off_label


class FakeToasts:
    def __init__(self) -> None:
        self.avoid: Any = None
        self.relayouts = 0

    def avoid_also(self, fn: Any) -> None:
        self.avoid = fn

    def relayout(self) -> None:
        self.relayouts += 1


class FakeHost:
    """O controlador da cola: `busy` (gravando, salvando ou saindo), a bandeja, a pilha dos avisos, o "Antes de gravar"
    (`on_open` simula o que o usuário faz nele) e os avisos."""

    def __init__(self) -> None:
        self.busy = False
        self.tray = FakeTray()
        self.toasts = FakeToasts()
        self.opened: list[str | None] = []
        self.notices: list[str] = []
        self.on_open: Any = None

    def open_warmup(self, source_exe: str | None = None) -> None:
        self.opened.append(source_exe)
        if self.on_open is not None:
            self.on_open(self)

    def notify(self, message: str) -> None:
        self.notices.append(message)


def glue(platform: Any, store: Any, monkeypatch: Any, card_ms: int = 300) -> SimpleNamespace:
    """A cola sem a thread (o `start` dela não roda) e com o cartão de prazo curto; quem chama faz o `close`."""
    from listener.ui import tasks
    from listener.ui.call_prompt import CallPrompt
    from listener.ui.call_watch import CallWatch

    monkeypatch.setattr(CallSensor, "start", lambda _self: None)
    clock, host = Clock(), FakeHost()
    calls = CallWatch(host, platform, store, clock=clock, intervals=FAST,
                      card_factory=functools.partial(CallPrompt, duration_ms=card_ms))
    calls.start()
    sensor = calls.sensor

    def close() -> None:
        calls.shutdown()
        tasks.live_threads.discard(sensor)

    return SimpleNamespace(calls=calls, host=host, clock=clock, sensor=sensor, close=close)


def feed(g: Any, *apps: Any, at: float | None = None) -> None:
    if at is not None:
        g.clock.t = 1000.0 + at
    g.calls._on_reading(act(*apps))


def prompted(g: Any, *apps: Any, start: float = 0) -> Any:
    """Uma chamada até o aviso (8 s com o microfone, R1) e o cartão dela."""
    feed(g, *(apps or (teams(),)), at=start)
    feed(g, *(apps or (teams(),)), at=start + 8)
    assert g.calls.card is not None
    return g.calls.card
