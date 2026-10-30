"""F5.9 (item 4, pergunta 1): o app que não abre cai no sistema inteiro com `source_change{fallback, fallback_exe}`, e a
gravação volta sozinha ao app quando a raiz nova dele aparece (`source_change{back}`), em até 5 s e fechando o loopback
do sistema antes (A-15); a raiz que já falhou não é tentada de novo (cada tentativa fecharia o sistema: um buraco a cada
5 s); a troca manual apaga `fallback_exe` e encerra a volta (D-067); a linha de alerta mostra o caso; o R perdido durante
o fallback tenta o app antes do sistema."""
from __future__ import annotations

from listener.capture.recorder import Recorder, SourceSpec
from listener.model import DeviceReason, EventType, Health
from listener.ui.recording_alerts import FALLBACK_TEXT, alert_line
from tests.conftest import make_config
from tests.fake_platform import FakePlatform

SYSTEM = SourceSpec("system", label="Sistema inteiro")
EXE = "meeting.exe"


def _source_changes(store, mid: int) -> list[dict]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == EventType.SOURCE_CHANGE]


def _types(store, mid: int) -> list[str]:
    return [e["type"] for e in store.list_events(mid)]


def _recorder(platform: FakePlatform, store, source: SourceSpec | None = None) -> Recorder:
    cfg = make_config(source=source) if source is not None else make_config()
    return Recorder(platform, store, store.create_meeting(None, None, None, None), cfg, clock=platform.clock, inline_worker=True)


def test_the_app_that_does_not_open_falls_back_with_the_event_and_the_alert(tmp_path, store):
    """O fallback no `start()`: um `source_change{fallback, fallback_exe}` só; o alerta "app sem loopback"; a raiz que
    falhou segue lá e não é tentada de novo; `reopen_output` preserva o `fallback_exe`."""
    platform = FakePlatform(tmp_path, app_fail=True)
    rec = _recorder(platform, store)
    rec.start()
    platform.pump(0.3)
    assert rec.source.kind == "system" and rec.source.fallback_exe == EXE and rec.health()["R"] == Health.OK
    changes = _source_changes(store, rec.meeting_id)
    assert len(changes) == 1 and (changes[0]["reason"], changes[0]["fallback_exe"], changes[0]["kind"]) == ("fallback", EXE, "system")
    line = alert_line(rec, rec.health(), None, None)
    assert line is not None and line.text == FALLBACK_TEXT and line.button == "Dispositivos…"
    rec.check_devices()
    platform.pump(5.1)
    rec.check_devices()                                        # a raiz que falhou (4242) segue lá: nada a tentar
    assert rec.source.kind == "system" and len(_source_changes(store, rec.meeting_id)) == 1
    assert rec.reopen_output(DeviceReason.DEFAULT) and rec.source.fallback_exe == EXE
    rec.stop()
    assert store.continuity(rec.meeting_id, "R") == []


def test_the_source_goes_back_to_the_app_when_its_new_root_appears(tmp_path, store):
    """Aceite 7: `root_pid` com a raiz nova, a fonte volta ao app com `source_change{back}` em até 5 s e até 1,5 s de
    buraco no R; o loopback do sistema fechou antes; `fallback_exe` some e o alerta também."""
    platform = FakePlatform(tmp_path, app_fail=True)
    rec = _recorder(platform, store)
    rec.start()
    platform.pump(0.5)
    platform.app_fail = False
    platform.root_pids[EXE] = None                             # o app fechou
    platform.pump(5.0)
    rec.check_devices()
    assert rec.source.kind == "system"
    platform.root_pids[EXE] = 4243                             # reabriu: a raiz nova
    platform.pump(5.0)
    rec.check_devices()
    platform.pump(0.5)
    assert (rec.source.kind, rec.source.pid, rec.source.fallback_exe) == ("app", 4243, "") and rec.health()["R"] == Health.OK
    assert alert_line(rec, rec.health(), None, None) is None
    changes = _source_changes(store, rec.meeting_id)
    assert [c["reason"] for c in changes] == ["fallback", "back"] and (changes[1]["pid"], changes[1]["fallback_exe"]) == (4243, "")
    assert platform.loopbacks[-1].stop_calls == 1              # o loopback do sistema fechou antes (A-15)
    stats = rec.stop()
    assert store.continuity(rec.meeting_id, "R") == [] and stats["drift"]["R"]["gap_frames"] <= 16000 * 1.5
    assert EventType.DEVICE_CHANGE not in _types(store, rec.meeting_id)


def test_a_manual_switch_ends_the_return_and_a_manual_app_that_fails_is_a_fallback_too(tmp_path, store):
    """D-067: a escolha explícita do sistema inteiro apaga `fallback_exe` (nada volta); o app pedido à mão que cai no
    sistema sai como `fallback` com `fallback_exe`, e volta quando a raiz nova aparece."""
    platform = FakePlatform(tmp_path, app_fail=True)
    rec = _recorder(platform, store)
    rec.start()
    platform.pump(0.3)
    assert rec.switch_source(SYSTEM) and rec.source.fallback_exe == ""
    platform.app_fail = False
    platform.root_pids[EXE] = 4243
    platform.pump(5.1)
    rec.check_devices()
    platform.pump(0.3)
    assert rec.source.kind == "system" and [c["reason"] for c in _source_changes(store, rec.meeting_id)] == ["fallback"]
    assert alert_line(rec, rec.health(), None, None) is None
    rec.stop()
    platform.app_fail = True
    other = _recorder(platform, store, SYSTEM)
    other.start()
    platform.pump(0.3)
    assert other.switch_source(SourceSpec("app", pid=4243, exe=EXE, label="Reunião"))   # cai no sistema
    assert other.source.kind == "system" and other.source.fallback_exe == EXE
    changes = _source_changes(store, other.meeting_id)
    assert [c["reason"] for c in changes] == ["fallback"] and changes[0]["fallback_exe"] == EXE
    platform.app_fail = False
    platform.root_pids[EXE] = 4244
    platform.pump(5.1)
    other.check_devices()
    platform.pump(0.3)
    assert other.source.kind == "app" and other.source.pid == 4244
    assert [c["reason"] for c in _source_changes(store, other.meeting_id)] == ["fallback", "back"]
    other.stop()


def test_the_lost_r_during_the_fallback_tries_the_app_first(tmp_path, store):
    """O loopback do sistema morre antes da volta de 5 s com o app já de volta: a reabertura tenta o app primeiro, com
    `device_back` e `source_change{back}`, sem `device_change`."""
    platform = FakePlatform(tmp_path, app_fail=True)
    rec = _recorder(platform, store)
    rec.start()
    platform.pump(0.5)
    platform.app_fail = False
    platform.root_pids[EXE] = 4243
    platform.kill_r()
    platform.pump(1.1)
    rec.check_devices()
    platform.pump(0.5)
    assert (rec.source.kind, rec.source.pid) == ("app", 4243) and rec.health()["R"] == Health.OK
    types = _types(store, rec.meeting_id)
    assert types.count("device_lost") == 1 and types.count("device_back") == 1 and EventType.DEVICE_CHANGE not in types
    assert [c["reason"] for c in _source_changes(store, rec.meeting_id)] == ["fallback", "back"]
    rec.stop()
