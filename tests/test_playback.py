import sys
from types import SimpleNamespace

import numpy as np

from listener.store import playback


def test_play_and_stop_use_sounddevice(monkeypatch):
    calls: list[tuple[object, ...]] = []
    fake = SimpleNamespace(play=lambda samples, sr: calls.append(("play", len(samples), sr)), stop=lambda: calls.append(("stop",)))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    playback.play(np.zeros(160, np.float32), 16000)
    playback.stop()
    assert calls == [("play", 160, 16000), ("stop",)]


def test_decode_range_empty(store):
    mid = store.create_meeting(None, None, None, None)
    samples, sr = playback.decode_range(store, mid, "L", 0, 1000)
    assert samples.size == 0 and sr == 16000
