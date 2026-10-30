import sys
from types import SimpleNamespace

import numpy as np

from listener.store import playback


def test_the_portable_play_uses_the_library_default_or_the_given_device(monkeypatch):
    """D-067: o teste do warm-up portável vai para o padrão da biblioteca; com um dispositivo (o Windows, pelo endpoint),
    para ele, com o buffer do player e os ajustes da host API."""
    from listener.platform.common import output

    calls: list[tuple[object, ...]] = []
    fake = SimpleNamespace(play=lambda samples, sr, **kw: calls.append(("play", len(samples), sr, kw)))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    output.play(np.zeros(160, np.float32), 16000)
    output.play(np.zeros(80, np.float32), 16000, device=7, extra="wasapi")
    assert calls == [("play", 160, 16000, {}),
                     ("play", 80, 16000, {"device": 7, "latency": output.PLAYER_LATENCY_S, "extra_settings": "wasapi"})]


def test_decode_range_empty(store):
    mid = store.create_meeting(None, None, None, None)
    samples, sr = playback.decode_range(store, mid, "L", 0, 1000)
    assert samples.size == 0 and sr == 16000
