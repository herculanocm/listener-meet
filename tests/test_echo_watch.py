"""F5.9 (item 7, Aceite 8): o eco depois da troca de microfone. A vigia sozinha, com blocos sintéticos (o L igual ao R
atrasado 60 ms acha a correlação e o atraso; sem correlação, não; mudo, pausa e buraco zeram a janela; sem áudio em 60 s
desiste calada; uma rodada por troca); e com o gravador e o fake (`mic_echo_of_r_ms=60`): o aviso com "Marcar como sala"
e o evento `echo_warning` em até 8 s depois do `device_change`; sem eco, nenhum aviso."""
from __future__ import annotations

import time

import numpy as np
import pytest

from listener.capture.chunker import TapFlag
from listener.capture.echo_watch import EchoWatch
from listener.model import EventType
from tests.fake_platform import MIC, FakePlatform

SR = 16000


def _wait_round(watch: EchoWatch, n: int = 1) -> None:
    deadline = time.monotonic() + 5.0
    while watch.rounds < n and time.monotonic() < deadline:
        time.sleep(0.01)
    assert watch.rounds == n


def _feed(watch: EchoWatch, l: np.ndarray, r: np.ndarray, block: int = 1600, flags: int = 0) -> None:
    """Entrega L e R em blocos de 100 ms, intercalados, como o chunker faz."""
    for start in range(0, len(l), block):
        watch.tap("L", start, SR, l[start:start + block], flags)
        watch.tap("R", start, SR, r[start:start + block], flags)


def test_the_watch_finds_the_echo_and_its_delay_once_per_round():
    rng = np.random.default_rng(7)
    r = rng.standard_normal(SR * 6).astype(np.float32) * 0.3
    l = np.concatenate([np.zeros(960, np.float32), r[:-960]])          # o L ouve o R 60 ms depois
    now = [0.0]
    watch = EchoWatch(clock=lambda: now[0])
    _feed(watch, l[:SR * 2], r[:SR * 2])
    assert watch.poll() is None and watch.rounds == 0                   # sem `arm()`, nada acumula
    watch.arm()
    _feed(watch, l, r)
    _wait_round(watch)
    res = watch.poll()
    assert res is not None and res.correlation > 0.9 and abs(res.offset_ms + 60) < 2   # o R está 60 ms antes do L
    assert watch.poll() is None and not watch.armed                     # lido uma vez; a rodada acabou
    _feed(watch, l, r)
    assert watch.rounds == 1                                            # só outra troca arma de novo
    other = rng.standard_normal(SR * 6).astype(np.float32) * 0.3
    watch.arm()
    _feed(watch, other, r)
    _wait_round(watch, 2)
    res = watch.poll()
    assert res is not None and res.correlation < 0.1                    # sem eco: a interface compara com o limiar


def test_silenced_blocks_restart_the_window_and_the_round_gives_up_quietly_after_a_minute():
    rng = np.random.default_rng(3)
    r = rng.standard_normal(SR * 4).astype(np.float32) * 0.3
    now = [0.0]
    watch = EchoWatch(clock=lambda: now[0])
    watch.arm()
    _feed(watch, r, r)                                                  # 4 s acumulados...
    watch.tap("L", SR * 4, SR, np.zeros(1600, np.float32), TapFlag.SILENCED)   # ...o mudo zera a janela
    _feed(watch, r[:SR * 4], r[:SR * 4])                                # 4 s de novo: ainda não chega
    assert watch.rounds == 0 and watch.armed
    _feed(watch, r[:SR * 2], r[:SR * 2])
    _wait_round(watch)
    assert watch.poll() is not None
    watch.arm()
    now[0] = 61.0                                                       # um minuto sem 5 s de áudio
    assert watch.poll() is None and not watch.armed
    _feed(watch, r, r)
    _feed(watch, r, r)
    assert watch.rounds == 1                                            # desistiu calada


@pytest.mark.lento                                                      # a thread da correlação (tempo real curto)
def test_the_echo_warning_comes_within_eight_seconds_of_the_mic_switch_and_offers_the_room(qapp, tmp_path, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import Recorder
    from listener.ui.recording_alerts import ACTION_ROOM, ECHO_TEXT
    from listener.ui.recording_devices import RecordingDevices
    from tests.conftest import make_config

    platform = FakePlatform(tmp_path)
    platform.mic_echo_of_r_ms = 60
    mid = store.create_meeting("t", "meeting", None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    watch = RecordingDevices(platform, rec, rooms=store.meetings)
    watch.start()
    platform.pump(1.0)
    assert watch.switch_mic(MIC).ok and watch.echo.armed                 # o `device_change` arma a vigia
    platform.pump(6.0)                                                  # 5 s de L e R depois da troca
    _wait_round(watch.echo)
    watch.poll()
    alert = watch.notice()
    assert alert is not None and alert.text == ECHO_TEXT and alert.action == ACTION_ROOM and alert.kind == "warning"
    events = [e for e in store.list_events(mid) if e["type"] == EventType.ECHO_WARNING]
    assert len(events) == 1 and events[0]["data"]["correlation"] >= rec.cfg.echo_corr_min and events[0]["t_ms"] <= 8000
    assert store.get_meeting(mid)["is_in_room"] == 0
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    watch.on_alert(ACTION_ROOM)
    assert store.get_meeting(mid)["is_in_room"] == 0 and watch.notice() is not None        # recusou: só o aviso
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    watch.on_alert(ACTION_ROOM)
    assert store.get_meeting(mid)["is_in_room"] == 1 and watch.notice() is None
    watch.stop()
    rec.stop()


@pytest.mark.lento
def test_without_echo_there_is_no_warning(qapp, tmp_path, store):
    from listener.capture.recorder import Recorder
    from listener.ui.recording_devices import RecordingDevices
    from tests.conftest import make_config

    platform = FakePlatform(tmp_path)                                   # o microfone (440 Hz) não ouve o R (220 Hz)
    mid = store.create_meeting("t", "meeting", None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    watch = RecordingDevices(platform, rec, rooms=store.meetings)
    watch.start()
    platform.pump(1.0)
    assert watch.switch_mic(MIC).ok
    platform.pump(6.0)
    _wait_round(watch.echo)
    watch.poll()
    assert watch.notice() is None and not [e for e in store.list_events(mid) if e["type"] == EventType.ECHO_WARNING]
    watch.stop()
    rec.stop()
