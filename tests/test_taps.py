"""F5.9 (item 7): o fanout do desvio ao vivo entrega o mesmo bloco a todos os consumidores, na ordem, e troca a lista sem
parar o gravador; o gravador segue com `set_live_tap` e ganha `set_tap` para a vigia de eco."""
from __future__ import annotations

import numpy as np

from listener.capture.taps import TapFanout


def test_the_fanout_delivers_to_every_consumer_and_swaps_the_list_at_once():
    fan = TapFanout()
    got: list[tuple[str, str, int]] = []
    assert fan.empty
    fan.set("live", lambda ch, start, _rate, _block, f: got.append(("live", ch, start)))
    fan.set("echo", lambda ch, start, _rate, _block, f: got.append(("echo", ch, start)))
    assert not fan.empty
    fan("L", 0, 16000, np.zeros(10, np.float32), 0)
    assert got == [("live", "L", 0), ("echo", "L", 0)]
    fan.set("live", None)
    fan("R", 10, 16000, np.zeros(10, np.float32), 0)
    assert got[-1] == ("echo", "R", 10) and len(got) == 3
    fan.set("echo", None)
    assert fan.empty
    fan("L", 20, 16000, np.zeros(10, np.float32), 0)
    assert len(got) == 3


def test_the_recorder_feeds_the_live_tap_and_a_named_tap_through_the_fanout(platform, store, recorder_factory):
    rec = recorder_factory()
    live: list[str] = []
    echo: list[str] = []
    rec.set_live_tap(lambda ch, *_: live.append(ch))
    rec.set_tap("echo", lambda ch, *_: echo.append(ch))
    rec.start()
    platform.pump(0.5)
    assert live and echo and len(live) == len(echo)
    rec.set_live_tap(None)
    n = len(echo)
    platform.pump(0.3)
    assert len(live) < len(echo) and len(echo) > n           # o ao vivo saiu; a vigia segue
    rec.set_tap("echo", None)
    n = len(echo)
    platform.pump(0.3)
    assert len(echo) == n and rec.tap_errors == 0
    rec.stop()
