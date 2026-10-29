"""F5.7 (L1): o sensor no protocolo `Platform` (CP15). Sem implementação, a plataforma não tem sensor (None ou vazio); o
`FakePlatform` o simula, controlado pelo teste. Roda em qualquer sistema."""
from __future__ import annotations

import threading

import pytest

from listener.platform.base import KIND_BROWSER, KIND_MEETING, AppAudio, AudioActivity, Platform
from tests.fake_activity import FakeMicWatch
from tests.fake_platform import FakePlatform


def test_a_platform_without_the_sensor_gives_none_or_empty():
    class Bare(Platform):
        name = "bare"

    bare = Bare()  # type: ignore[abstract]
    assert bare.audio_activity() is None and bare.audio_activity(frozenset({"chrome.exe"}), degraded=True) is None
    bare.close_audio_activity()                                               # não faz nada (e não levanta)
    assert bare.mic_users() is None
    assert bare.watched_apps() == () and bare.mic_watch() is None


def test_the_fake_platform_sensor_is_driven_by_the_test(tmp_path):
    fake = FakePlatform(tmp_path)
    assert fake.audio_activity() == AudioActivity(())
    teams = AudioActivity((AppAudio(4242, "ms-teams.exe", True, 0.4),))
    fake.activity = teams
    assert fake.audio_activity(frozenset({"chrome.exe"})) is teams
    assert fake.audio_activity(degraded=True) == AudioActivity((), degraded=True)
    fake.activity_error = OSError("COM")
    with pytest.raises(OSError, match="COM"):
        fake.audio_activity()
    fake.close_audio_activity()
    assert fake.activity_reads == [(frozenset(), False), (frozenset({"chrome.exe"}), False), (frozenset(), True),
                                   (frozenset(), False)]
    assert fake.activity_closed == 1 and fake.mic_users() == frozenset()
    kinds = {w.exe: w.kind for w in fake.watched_apps()}
    assert kinds["ms-teams.exe"] == KIND_MEETING and kinds["chrome.exe"] == KIND_BROWSER and "discord.exe" not in kinds


def test_the_fake_mic_watch_wakes_once_per_change_and_on_cancel():
    watch = FakeMicWatch()
    assert watch.wait(0.01) is False
    watch.change()
    watch.change()
    assert watch.wait(0.01) is True and watch.wait(0.01) is False             # o sistema junta as mudanças
    got: list[bool] = []
    t = threading.Thread(target=lambda: got.append(watch.wait(10)))
    t.start()
    watch.change()
    t.join(2)
    assert got == [True]
    t = threading.Thread(target=lambda: got.append(watch.wait(10)))
    t.start()
    watch.cancel()
    t.join(2)
    assert got == [True, False] and watch.wait(10) is False
    watch.close()
    assert watch.closed and watch.waits == 6
