"""F5.9 (itens 1 e 2): a vida dos canais L e R. O fake imita o `alive()` do protocolo (`FakeStream.die()` e
`FakePlatform.kill_r()`: a stream morre calada, sem `on_error`, e para de entregar); a vigia de 1 s do gravador lê o
`alive()` e marca o canal perdido com o motivo (Aceite 1); o que não abriu no começo fica perdido, não desligado, e é
tentado de novo a cada 5 s (Aceite 4). A primeira reabertura vem logo depois da marca (como na queda que o driver avisa);
os testes a fazem falhar para ver `Health.LOST` no meio e `Health.OK` na reabertura seguinte, 5 s depois."""
from __future__ import annotations

from listener.capture.recorder import Recorder, SourceSpec
from listener.model import EventType, Health, LostReason
from tests.conftest import make_config
from tests.fake_platform import MIC, FakePlatform

SYSTEM = SourceSpec("system", label="Sistema inteiro")


def _lost_events(store, mid: int) -> list[dict]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == EventType.DEVICE_LOST]


def _types(store, mid: int) -> list[str]:
    return [e["type"] for e in store.list_events(mid)]


def _live_mics(platform) -> list:
    return [s for s in platform.mic_streams if s.started and not s.stopped]


def test_the_fake_stream_dies_quietly_and_kill_r_picks_the_r_stream(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.pump(0.3)
    assert all(s.alive() is None for s in platform.live_streams())             # o driver não diz nada
    r = platform.kill_r()
    assert r.freq != 440.0 and r.alive() is False and not r.stopped and r in platform.live_streams()
    assert all(s.alive() is None for s in platform.mic_streams) and r.on_error is not None
    delivered = r.delivered
    platform.pump(0.3)
    assert r.delivered == delivered and "R" not in rec.snapshot.lost            # calada: sem `on_error`, ninguém marcou
    rec.stop()
    assert r.stop_calls == 1


def test_the_r_that_dies_without_on_error_is_lost_in_two_seconds_and_comes_back(platform, store, recorder_factory):
    """Aceite 1: o app saiu e o driver não avisa: `device_lost` com `process_exit` em até 2 s e `Health.LOST` enquanto a
    reabertura não dá; `device_back` e `Health.OK` na reabertura de 5 s."""
    rec = recorder_factory()
    rec.start()
    platform.pump(0.5)
    dead = platform.kill_r()
    platform.app_fail = platform.system_fail = True                            # o app ainda não voltou: a primeira falha
    platform.pump(1.0)
    rec.check_devices()
    assert dead.stop_calls == 1 and rec.health()["R"] == Health.LOST and rec.R.lost_reason == LostReason.PROCESS_EXIT
    events = _lost_events(store, rec.meeting_id)
    assert [e["reason"] for e in events] == ["process_exit"] and "parou de responder" in events[0]["message"]
    platform.pump(2.0)
    rec.check_devices()                                                        # antes dos 5 s: nada muda
    assert rec.health()["R"] == Health.LOST and len(_lost_events(store, rec.meeting_id)) == 1
    platform.app_fail = platform.system_fail = False
    platform.pump(3.1)                                                         # 5 s depois da primeira tentativa
    rec.check_devices()
    platform.pump(0.5)
    assert rec.health()["R"] == Health.OK and rec.R.lost_reason == "" and rec.source.kind == "app"
    rec.stop()
    types = _types(store, rec.meeting_id)
    assert types.index("device_lost") < types.index("device_back") and types.count("device_back") == 1
    assert store.continuity(rec.meeting_id, "R") == []                         # o buraco virou silêncio


def test_the_microphone_that_dies_without_on_error_is_lost_and_comes_back(platform, store, recorder_factory):
    """Aceite 1, no L: a stream do microfone parou sem o `finished_callback`: `stream_stopped`, perdido, e de volta."""
    rec = recorder_factory()
    rec.start()
    platform.pump(0.5)
    mic = _live_mics(platform)[0]
    mic.die()
    platform.fail_ids = {MIC.id}                                               # ainda não responde: a primeira falha
    platform.pump(1.0)
    rec.check_devices()
    assert mic.stop_calls == 1 and rec.health()["L"] == Health.LOST and rec.L.lost_reason == LostReason.STREAM_STOPPED
    assert [e["reason"] for e in _lost_events(store, rec.meeting_id)] == ["stream_stopped"]
    platform.fail_ids = set()
    platform.pump(5.1)
    rec.check_devices()
    platform.pump(0.5)
    assert rec.health()["L"] == Health.OK and len(_live_mics(platform)) == 1 and rec.L.lost_reason == ""
    rec.stop()
    types = _types(store, rec.meeting_id)
    assert types.index("device_lost") < types.index("device_back") and types.count("device_back") == 1
    assert store.continuity(rec.meeting_id, "L") == []


def test_the_microphone_that_refuses_at_start_is_lost_not_off_and_opens_later(tmp_path, store):
    """Aceite 4 (item 2): o microfone que não abre no `start()` deixa o L perdido (não desligado) com `device_lost` dizendo
    "não iniciou" (`never_opened`); a reabertura de 5 s o abre quando ele responde."""
    platform = FakePlatform(tmp_path, mic_fail=True)
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    assert rec.health()["L"] == Health.LOST and rec.L.lost_reason == LostReason.NEVER_OPENED
    platform.pump(0.3)
    rec.check_devices()                                                        # o evento e a primeira tentativa, que falha
    events = _lost_events(store, mid)
    assert [e["reason"] for e in events] == ["never_opened"] and "não iniciou" in events[0]["message"]
    assert rec.health()["L"] == Health.LOST and _live_mics(platform) == []
    platform.mic_fail = False
    platform.pump(5.1)
    rec.check_devices()
    platform.pump(0.5)
    assert rec.health()["L"] == Health.OK and rec.L.lost_reason == "" and len(_live_mics(platform)) == 1
    rec.stop()
    assert _types(store, mid).count("device_back") == 1 and _types(store, mid).count("device_lost") == 1


def test_the_output_that_refuses_at_start_is_lost_not_off_and_opens_later(tmp_path, store):
    """Aceite 4, no R: o sistema inteiro que não abre no começo, e o app com o sistema falhando atrás dele, deixam o R
    perdido com "não iniciou"; a reabertura de 5 s abre a fonte pedida quando ela responde."""
    platform = FakePlatform(tmp_path)
    platform.system_fail = True
    mid = store.create_meeting(None, None, None, None)
    rec = Recorder(platform, store, mid, make_config(source=SYSTEM), clock=platform.clock, inline_worker=True)
    rec.start()
    assert rec.health()["R"] == Health.LOST and rec.R.lost_reason == LostReason.NEVER_OPENED and not rec.output_open
    platform.pump(0.3)
    rec.check_devices()
    events = _lost_events(store, mid)
    assert [e["reason"] for e in events] == ["never_opened"] and "não iniciou" in events[0]["message"]
    assert rec.health()["R"] == Health.LOST
    platform.system_fail = False
    platform.pump(5.1)
    rec.check_devices()
    platform.pump(0.5)
    assert rec.health()["R"] == Health.OK and rec.output_open and rec.source.kind == "system"
    rec.stop()
    assert _types(store, mid).count("device_back") == 1
    platform.app_fail = platform.system_fail = True
    app = Recorder(platform, store, store.create_meeting(None, None, None, None), make_config(), clock=platform.clock,
                   inline_worker=True)
    app.start()
    assert app.health()["R"] == Health.LOST and app.R.lost_reason == LostReason.NEVER_OPENED
    platform.app_fail = platform.system_fail = False
    platform.pump(0.3)
    app.check_devices()                                                        # a primeira tentativa já abre o app pedido
    platform.pump(0.5)
    assert app.health()["R"] == Health.OK and app.source.kind == "app" and app.R.detail == "meeting.exe (pid 4242)"
    app.stop()
    assert [e["reason"] for e in _lost_events(store, app.meeting_id)] == ["never_opened"]
