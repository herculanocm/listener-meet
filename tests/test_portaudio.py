"""U-043 (F5.2): o registro dos streams do sounddevice e o reinício do PortAudio, com um sounddevice de mentira (portável:
roda sem PortAudio). O reinício fecharia todo stream aberto, então só acontece sem nenhum e na thread principal."""
from __future__ import annotations

import gc
import sys
import threading
import types

import pytest

from listener.platform.common import portaudio


class Stream:
    def __init__(self, active: bool = True):
        self.closed = False
        self.active = active

    def close(self) -> None:
        self.closed, self.active = True, False


class FakeSD(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sounddevice")
        self.calls: list[str] = []
        self.play_stream: Stream | None = None
        self.init_failures = 0
        self._initialized = 1

    def stop(self) -> None:
        self.calls.append("stop")
        if self.play_stream is not None:
            self.play_stream.close()

    def get_stream(self) -> Stream:
        if self.play_stream is None:
            raise RuntimeError("play()/rec()/playrec() was not called yet")
        return self.play_stream

    def _terminate(self) -> None:
        self.calls.append("terminate")

    def _initialize(self) -> None:
        self.calls.append("initialize")
        if self.init_failures:
            self.init_failures -= 1
            raise RuntimeError("Error initializing PortAudio")


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    monkeypatch.setattr(portaudio, "_streams", portaudio.weakref.WeakSet())
    return fake


def test_restart_only_without_open_streams(sd):
    assert portaudio.open_streams() == 0 and portaudio.restart() is True
    assert sd.calls == ["stop", "terminate", "initialize"]
    mic = portaudio.track(Stream())
    assert portaudio.open_streams() == 1 and portaudio.restart() is False and sd.calls.count("terminate") == 1
    mic.close()
    assert portaudio.open_streams() == 0 and portaudio.restart() is True


def test_a_collected_stream_does_not_hold_portaudio(sd):
    portaudio.track(Stream())
    gc.collect()
    assert portaudio.open_streams() == 0


def test_the_play_stream_counts_while_it_plays_and_is_closed_before_the_restart(sd):
    sd.play_stream = Stream(active=True)                      # o teste do warm-up tocando
    assert portaudio.open_streams() == 1 and portaudio.restart() is False
    sd.play_stream.active = False                              # terminou de tocar, mas segue aberto
    assert portaudio.open_streams() == 0 and portaudio.restart() is True
    assert sd.play_stream.closed and sd.calls[:2] == ["stop", "terminate"]


def test_restart_outside_the_main_thread_does_nothing(sd):
    out: list[bool] = []
    t = threading.Thread(target=lambda: out.append(portaudio.restart()))
    t.start()
    t.join(5)
    assert out == [False] and sd.calls == []


def test_initialize_is_retried_once_and_a_second_failure_is_logged(sd, caplog):
    sd.init_failures = 1
    assert portaudio.restart() is True and sd.calls == ["stop", "terminate", "initialize", "initialize"]
    sd.calls.clear()
    sd.init_failures = 2
    with caplog.at_level("WARNING", logger="listener.platform.portaudio"):
        assert portaudio.restart() is False
    assert "reabrindo o app" in caplog.text
    sd.calls.clear()
    sd._initialized = 0                                       # A-05: as duas falhas deixaram o PortAudio parado
    assert portaudio.restart() is True and sd.calls == ["stop", "initialize"]           # só inicia, sem terminar
