"""F5.3 L4 (U-049 a U-051), o gravador e as decisões sem Qt: `switch_mic` conhecido sem reiniciar o PortAudio,
desconhecido com o player solto antes e o reinício, falha que reabre o anterior, depois de parado sem efeito (A-17); o
buraco vira silêncio e chega ao ao vivo como `GAP`; o microfone que acompanha relê quando o padrão muda (A-20); toda
reabertura do R fecha o loopback anterior antes (A-15, a contagem do fake); a saída escolhida que some cai na padrão e volta
(A-16); as decisões da vigia e a prioridade da linha de alerta (A-21); eventos e log sem o nome do aparelho (A-22)."""
from __future__ import annotations

import json
import logging

import pytest

from listener.capture.chunker import TapFlag
from listener.capture.recorder import REREAD_REFUSED, STOPPED_TEXT, SourceSpec
from listener.model import DeviceReason, EventType, Health
from listener.platform.base import AudioDevice, Endpoint, Endpoints
from tests.conftest import make_config
from tests.fake_platform import BASE_ENDPOINTS, MIC, SPEAKERS

pytest.importorskip("PySide6")

PERSON = "Fulano"                                            # o nome de pessoa que o nome do aparelho traz (A-22)
BT = AudioDevice("bt-1", f"AirPods de {PERSON} Hands-Free", "fake", 1, 16000, index=1)
BT_MIC = Endpoint(BT.id, BT.name, "{BT-BOX}")
BT_OUT = Endpoint("bt-out", f"AirPods de {PERSON} Stereo", "{BT-BOX}")
BT_HFP = Endpoint("bt-hfp", f"AirPods de {PERSON} Hands-Free", "{BT-BOX}")
SYSTEM = SourceSpec("system", label="Sistema inteiro")


def with_bt(mic: str = MIC.id, out: str = SPEAKERS.id, comm: str = SPEAKERS.id) -> Endpoints:
    return Endpoints(capture=(*BASE_ENDPOINTS.capture, BT_MIC), render=(SPEAKERS, BT_OUT, BT_HFP), capture_default=mic,
                     capture_comm=mic, render_default=out, render_comm=comm)


def _changes(store, mid: int) -> list[dict]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == EventType.DEVICE_CHANGE]


def _live_mics(platform) -> list:
    return [s for s in platform.mic_streams if s.started and not s.stopped]


def test_a_microphone_portaudio_knows_opens_without_restarting(platform, store, recorder_factory):
    """A-17: o escolhido que já está na leitura do PortAudio abre direto; o anterior fecha antes (A-15)."""
    platform.devices = [MIC, BT]
    platform.endpoints = with_bt()
    platform.settle()
    rec = recorder_factory()
    rec.start()
    platform.pump(0.5)
    old = _live_mics(platform)[0]
    result = rec.switch_mic(BT)
    assert result.ok and result.endpoint_id == BT.id and platform.refreshes == 0
    assert old.stop_calls == 1 and [s.device_id for s in _live_mics(platform)] == [BT.id]
    assert platform.mics_at_open == [0, 0]                                # o anterior fechou antes de o novo abrir
    platform.pump(0.5)
    rec.stop()
    assert _changes(store, rec.meeting_id) == [{"channel": "L", "endpoint_id": BT.id, "follows": False, "reason": "user"}]
    assert store.continuity(rec.meeting_id, "L") == []


def test_a_new_microphone_releases_the_player_rereads_and_opens(platform, store, recorder_factory):
    """A-17: o fone que chegou depois da leitura do PortAudio pede o reinício; o gancho solta o player antes e o nosso
    microfone fecha logo antes do reinício. Sem o player soltar, nada fecha: a gravação segue no anterior, sem buraco."""
    player = {"open": 1}
    platform.other_streams = lambda: player["open"]
    calls: list[str] = []
    rec = recorder_factory(before_reread=lambda: calls.append("player"))
    rec.start()
    platform.pump(0.4)
    old = _live_mics(platform)[0]
    platform.next_endpoints, platform.next_devices = with_bt(), [MIC, BT]
    assert platform.audio_endpoints() is not None                     # a vigia leu o Core Audio: o fone está lá
    refused = rec.switch_mic(BT)
    assert not refused.ok and refused.message == REREAD_REFUSED and calls == ["player"]
    assert old.stop_calls == 0 and _live_mics(platform) == [old] and platform.refreshes == 0
    platform.pump(0.3)
    calls.clear()
    def release_output() -> None:                                               # agora o player solta a saída
        calls.append("player")
        player.update(open=0)

    rec.before_reread = release_output
    result = rec.switch_mic(BT)
    assert result.ok and calls == ["player"] and platform.refreshes == 1 and old.stop_calls == 1
    assert [s.device_id for s in _live_mics(platform)] == [BT.id]
    platform.pump(0.3)
    rec.stop()
    assert [c["endpoint_id"] for c in _changes(store, rec.meeting_id)] == [BT.id]   # a recusada não gerou evento


def test_a_failed_switch_reopens_the_previous_microphone(platform, store, recorder_factory):
    platform.devices = [MIC, BT]
    platform.fail_ids = {BT.id}
    rec = recorder_factory()
    rec.start()
    platform.pump(0.4)
    result = rec.switch_mic(BT)
    assert not result.ok and "recusou" in result.message and rec.mic_device is None and rec.mic_open_id == MIC.id
    assert [s.device_id for s in _live_mics(platform)] == [MIC.id] and len(platform.mic_streams) == 2   # o anterior de volta
    platform.pump(0.4)
    rec.stop()
    assert _changes(store, rec.meeting_id) == [] and store.continuity(rec.meeting_id, "L") == []


def test_a_switch_after_the_recording_stopped_does_nothing(platform, store, recorder_factory):
    platform.devices = [MIC, BT]
    rec = recorder_factory()
    rec.start()
    platform.pump(0.3)
    rec.stop()
    opened = len(platform.mic_streams)
    result = rec.switch_mic(BT)
    assert not result.ok and result.message == STOPPED_TEXT and len(platform.mic_streams) == opened
    assert rec.switch_source(SYSTEM) is False and rec.reopen_output(DeviceReason.DEFAULT) is False


def test_the_switch_gap_is_silence_and_reaches_the_live_tap_as_gap(platform, store, recorder_factory):
    """O Bluetooth leva de 1 a 3 s para entregar o primeiro bloco no perfil de chamada (A-23): o buraco vira silêncio no
    canal pelo chunker e o desvio ao vivo recebe a marca `GAP`, como na troca de fonte."""
    platform.devices = [MIC, BT]
    rec = recorder_factory()
    flags: list[tuple[str, int]] = []
    rec.set_live_tap(lambda ch, _start, _rate, _block, f: flags.append((ch, f)))
    rec.start()
    platform.pump(0.6)
    assert rec.switch_mic(BT).ok
    now = platform.clock.rel()
    _live_mics(platform)[0].silent_windows = [(now, now + 1.5)]       # o fone só entrega depois de 1,5 s
    platform.pump(2.0)
    stats = rec.stop()
    assert ("L", TapFlag.GAP) in flags
    assert stats["drift"]["L"]["fills"] == 1 and stats["drift"]["L"]["gap_frames"] >= 16000 * 1.4
    assert store.continuity(rec.meeting_id, "L") == []


def test_the_microphone_that_follows_rereads_when_the_windows_default_changes(platform, store, recorder_factory):
    """A-20: "acompanha" segue o padrão de console; o padrão do PortAudio é o da última leitura, então a troca relê."""
    rec = recorder_factory()
    rec.start()
    assert rec.mic_device is None and rec.mic_open_id == MIC.id
    platform.pump(0.3)
    platform.next_endpoints, platform.next_devices = with_bt(mic=BT.id), [MIC, BT]
    platform.audio_endpoints()
    result = rec.switch_mic(None, reason=DeviceReason.DEFAULT, strict=False)
    assert result.ok and result.endpoint_id == BT.id and platform.refreshes == 1
    platform.pump(0.3)
    rec.stop()
    assert _changes(store, rec.meeting_id) == [{"channel": "L", "endpoint_id": BT.id, "follows": True, "reason": "default"}]


def test_every_r_reopening_closes_the_previous_loopback_first(platform, store, recorder_factory):
    """A-15: com o `PyAudio()` anterior vivo a lista seria a antiga (o fake resolve contra a do primeiro aberto): a troca do
    padrão e a reabertura do que caiu fecham antes de abrir, e o loopback novo cai na saída nova."""
    rec = recorder_factory(make_config(source=SYSTEM))
    rec.start()
    platform.pump(0.4)
    assert rec.output_open_id == SPEAKERS.id
    platform.next_endpoints = with_bt(out=BT_OUT.id)
    platform.audio_endpoints()
    assert rec.reopen_output(DeviceReason.DEFAULT) and rec.output_open_id == BT_OUT.id
    platform.pump(0.3)
    platform.loopbacks[-1].fail("dispositivo removido")
    platform.pump(0.2)
    rec.check_devices()                                                    # a reabertura do que caiu (M-09)
    platform.pump(0.3)
    rec.stop()
    assert platform.loopbacks_at_open == [0, 0, 0] and rec.output_open_id == BT_OUT.id
    events = store.list_events(rec.meeting_id)
    assert [e["data"]["endpoint_id"] for e in events if e["type"] == EventType.DEVICE_BACK] == [BT_OUT.id]
    assert _changes(store, rec.meeting_id) == [{"channel": "R", "endpoint_id": BT_OUT.id, "follows": True, "reason": "default"}]
    assert store.continuity(rec.meeting_id, "R") == []


def test_the_chosen_output_that_leaves_falls_back_to_the_default_and_comes_back(platform, store, recorder_factory):
    """A-16 e pergunta 10, na saída: a escolhida que some abre a padrão (`missing`, com a pedida junto) e volta sozinha."""
    platform.endpoints = with_bt()
    rec = recorder_factory(make_config(source=SourceSpec("system", label="Sistema inteiro: AirPods", output_id=BT_OUT.id)))
    rec.start()
    assert rec.output_open_id == BT_OUT.id and not rec.source.follows
    platform.pump(0.3)
    platform.endpoints = BASE_ENDPOINTS                                    # o fone saiu
    assert rec.reopen_output(DeviceReason.MISSING) and rec.output_open_id == SPEAKERS.id
    platform.pump(0.3)
    platform.endpoints = with_bt()                                         # voltou
    assert rec.reopen_output(DeviceReason.BACK) and rec.output_open_id == BT_OUT.id
    rec.stop()
    assert _changes(store, rec.meeting_id) == [
        {"channel": "R", "endpoint_id": SPEAKERS.id, "follows": False, "reason": "missing", "wanted_id": BT_OUT.id},
        {"channel": "R", "endpoint_id": BT_OUT.id, "follows": False, "reason": "back"}]


def test_events_and_log_never_carry_the_device_name(platform, store, recorder_factory, caplog):
    """A-22: o nome do aparelho pode trazer o nome de uma pessoa ("AirPods de Fulano"): eventos, detalhes dos canais e log
    levam o id do endpoint; o nome fica na tela."""
    platform.devices = [MIC, BT]
    platform.endpoints = with_bt()
    platform.settle()
    with caplog.at_level(logging.DEBUG, logger="listener"):
        rec = recorder_factory(make_config(mic_device=BT, source=SourceSpec("system", label=f"Sistema inteiro: {BT_OUT.name}",
                                                                             output_id=BT_OUT.id)))
        rec.start()
        platform.pump(0.4)
        _live_mics(platform)[0].fail("dispositivo removido")
        platform.pump(0.2)
        rec.check_devices()
        platform.pump(0.2)
        assert rec.switch_mic(MIC).ok and rec.switch_source(SourceSpec("system", label="Sistema inteiro"))
        platform.pump(0.2)
        rec.stop()
    assert PERSON not in rec.L.detail and PERSON not in rec.R.detail and BT.id in caplog.text
    dumped = json.dumps([e["data"] for e in store.list_events(rec.meeting_id)], ensure_ascii=False)
    assert "device_lost" in {e["type"] for e in store.list_events(rec.meeting_id)}
    assert PERSON not in dumped and PERSON not in caplog.text


# ---------------------------------------------------------------- decisões da vigia e a linha de alerta (sem Qt)
def test_the_watch_decides_from_the_reading_alone():
    from listener.ui.recording_devices import comm_hint, mic_action, output_action

    both = with_bt()
    assert mic_action(both, None, MIC.id) is None                                       # acompanha e nada mudou
    assert mic_action(with_bt(mic=BT.id), None, MIC.id) == (DeviceReason.DEFAULT, False)
    assert mic_action(both, BT, MIC.id) == (DeviceReason.BACK, True)                     # o escolhido voltou
    assert mic_action(both, BT, BT.id) is None
    assert mic_action(BASE_ENDPOINTS, BT, BT.id) == (DeviceReason.MISSING, False)       # sumiu: o padrão no lugar
    assert mic_action(BASE_ENDPOINTS, BT, MIC.id) is None                               # já no padrão
    app = SourceSpec("app", pid=1, exe="teams.exe")
    assert output_action(with_bt(out=BT_OUT.id), app, "") is None                       # o app segue o app
    assert output_action(with_bt(out=BT_OUT.id), SYSTEM, SPEAKERS.id) == DeviceReason.DEFAULT
    chosen = SourceSpec("system", output_id=BT_OUT.id)
    assert output_action(both, chosen, SPEAKERS.id) == DeviceReason.BACK and output_action(both, chosen, BT_OUT.id) is None
    assert output_action(BASE_ENDPOINTS, chosen, BT_OUT.id) == DeviceReason.MISSING
    assert comm_hint(with_bt(comm=BT_HFP.id), SYSTEM, SPEAKERS.id) == BT_HFP                   # A-20: a de comunicação difere
    assert comm_hint(with_bt(comm=BT_HFP.id), SYSTEM, BT_HFP.id) is None and comm_hint(both, SYSTEM, SPEAKERS.id) is None


def test_the_alert_line_puts_the_channels_first(platform, store, recorder_factory):
    from listener.ui.recording_devices import ACTION_DEVICES, ACTION_USE, Alert, alert_line

    rec = recorder_factory()
    rec.start()
    platform.pump(0.2)
    novo = Alert("novo: AirPods", "Usar", ACTION_USE)
    ok = {"L": Health.OK, "R": Health.OK, "db": Health.OK}
    assert alert_line(rec, ok, None, None) is None and alert_line(rec, ok, novo, None) == novo
    lost = alert_line(rec, {**ok, "L": Health.LOST, "db": Health.ALERT}, novo, None)
    assert lost is not None and lost.text.startswith("microfone desconectado")                # o canal antes do banco
    assert alert_line(rec, {**ok, "db": Health.ALERT}, novo, None).text.startswith("banco não responde")  # type: ignore[union-attr]
    assert alert_line(rec, {**ok, "db": Health.WARN}, novo, None) == novo                       # o aviso antes do banco lento
    quiet = alert_line(rec, {**ok, "R": Health.ALERT}, novo, BT_HFP)
    assert quiet is not None and BT_HFP.name in quiet.text and "(comunicação)" in quiet.text
    assert (quiet.button, quiet.action) == ("Dispositivos…", ACTION_DEVICES)
    rec.stop()


def test_without_core_audio_the_panel_lists_portaudio_without_new():
    """Comportamento da spec: sem pycaw ou com o Core Audio falhando, os microfones são os do PortAudio, sem "novo"; a saída
    fica no "acompanha" e no "Outro app…", e o escolhido que saiu segue marcado na lista."""
    from listener.ui.devices_panel import NO_CORE_AUDIO, OTHER_APP, OUT_FOLLOW, mic_rows, output_rows, panel_notes

    mics = mic_rows(None, [MIC, BT], None, frozenset())
    assert [r.text for r in mics] == ["Padrão do Windows (acompanha)", MIC.name, BT.name] and mics[0].current
    assert all("novo" not in r.text for r in mics) and NO_CORE_AUDIO in panel_notes(None, SYSTEM, mics)
    assert [r.key for r in output_rows(None, SYSTEM, frozenset())] == [OUT_FOLLOW, OTHER_APP]
    gone = mic_rows(BASE_ENDPOINTS, [], BT, BASE_ENDPOINTS.ids())
    assert gone[-1].current and gone[-1].text == f"{BT.name} (saiu: gravando no padrão)"


# ---------------------------------------------------------------- revisão do L4
def test_a_failed_back_switch_restores_the_microphone_that_was_recording(platform, store, recorder_factory):
    """R-01: o escolhido saiu e a gravação seguiu no padrão; ele volta mas não abre (o perfil de chamada falhou): a troca
    devolve o padrão que GRAVAVA, não o pedido (o mesmo que acabou de falhar), e o canal não fica perdido nem mente."""
    platform.devices = [MIC, BT]
    platform.endpoints = with_bt()
    platform.settle()
    rec = recorder_factory(make_config(mic_device=BT))
    rec.start()
    platform.pump(0.3)
    bt = _live_mics(platform)[0]
    platform.next_endpoints, platform.next_devices, platform.fail_ids = BASE_ENDPOINTS, [MIC], {BT.id}   # o fone saiu
    platform.audio_endpoints()
    bt.fail("dispositivo removido")
    platform.pump(0.2)
    assert rec.switch_mic(BT, reason=DeviceReason.MISSING, strict=False).ok and rec.mic_open_id == MIC.id
    platform.pump(0.3)
    platform.next_endpoints, platform.next_devices = with_bt(), [MIC, BT]           # voltou, mas não abre
    platform.audio_endpoints()
    result = rec.switch_mic(BT, reason=DeviceReason.BACK)
    assert not result.ok and rec.mic_device == BT and result.endpoint_id == MIC.id
    assert rec.mic_open_id == MIC.id and [s.device_id for s in _live_mics(platform)] == [MIC.id]
    platform.pump(0.3)
    assert "L" not in rec.snapshot.lost and rec.health()["L"] != Health.LOST
    rec.stop()


def test_a_reopening_whose_start_fails_stays_lost_and_is_tried_again(platform, store, recorder_factory):
    """R-02: a reabertura cujo `start()` falha já mandou o formato (o `_Open` tira o canal de perdido): o canal volta a
    perdido, à vista, e a reabertura seguinte, 5 s depois, o devolve."""
    rec = recorder_factory()
    rec.start()
    platform.pump(0.3)
    platform.start_fail_ids = {MIC.id}
    _live_mics(platform)[0].fail("dispositivo removido")
    platform.pump(0.2)
    rec.check_devices()                                                    # abre, mas o `start()` falha
    assert "L" in rec.snapshot.lost and rec.health()["L"] == Health.LOST and _live_mics(platform) == []
    platform.start_fail_ids = set()
    platform.pump(5.1)
    rec.check_devices()
    platform.pump(0.5)
    assert "L" not in rec.snapshot.lost and rec.health()["L"] != Health.LOST and len(_live_mics(platform)) == 1
    rec.stop()
    assert [e["type"] for e in store.list_events(rec.meeting_id)].count(EventType.DEVICE_BACK) == 1


def test_an_output_switch_that_fails_brings_back_the_previous_source_or_stays_lost(platform, store, recorder_factory):
    """R-03: da fonte de um app para o sistema inteiro que não abre, o app volta (não fica sem R); sem nenhuma, o canal fica
    perdido, à vista, e a reabertura de 5 s o devolve; o `start()` que falha na reabertura automática não sobe."""
    rec = recorder_factory()                                               # a fonte é o app meeting.exe
    rec.start()
    platform.pump(0.3)
    platform.system_fail = True
    assert rec.switch_source(SourceSpec("system", output_id=SPEAKERS.id)) is False
    assert rec.source.kind == "app" and rec.output_open and rec.output_open_id == ""
    platform.pump(0.3)
    assert rec.health()["R"] == Health.OK
    platform.app_fail = True                                               # nem o app nem o sistema abrem
    assert rec.switch_source(SourceSpec("system")) is False
    assert not rec.output_open and "R" in rec.snapshot.lost and rec.health()["R"] == Health.LOST
    platform.app_fail = platform.system_fail = False
    platform.pump(0.2)
    rec.check_devices()
    platform.pump(0.3)
    assert rec.output_open and rec.source.kind == "app" and rec.health()["R"] != Health.LOST
    rec.stop()
    system = recorder_factory(make_config(source=SYSTEM))
    system.start()
    platform.pump(0.2)
    platform.system_start_fail = True
    assert system.reopen_output(DeviceReason.DEFAULT) is False             # sem exceção: a vigia segue viva
    assert not system.output_open and "R" in system.snapshot.lost
    system.stop()


def test_a_name_id_never_reaches_the_events_or_the_log(platform, store, recorder_factory, caplog):
    """R-05 (A-22): sem id do sistema, o aparelho ganha o id `name:<nome>`, com o nome de quem pode ser dono dele; nos
    eventos, no detalhe do canal e no log ele vai resumido, e o nome fica só na tela."""
    from listener.platform.base import log_id

    named = AudioDevice(f"name:AirPods de {PERSON}", f"AirPods de {PERSON}", "fake", 1, 16000, index=1)
    platform.devices = [MIC, named]
    with caplog.at_level(logging.DEBUG, logger="listener"):
        rec = recorder_factory(make_config(mic_device=named))
        rec.start()
        platform.pump(0.3)
        _live_mics(platform)[0].fail("dispositivo removido")
        platform.pump(0.2)
        rec.check_devices()
        platform.pump(0.2)
        assert rec.switch_mic(MIC).ok and rec.switch_mic(named).ok
        rec.stop()
        store.set_setting("warmup.last_mic_device_name", named.name)      # o log das configurações também (DEBUG)
        store.set_setting("warmup.last_mic_device_id", named.id)
    dumped = json.dumps([e["data"] for e in store.list_events(rec.meeting_id)], ensure_ascii=False)
    assert PERSON not in dumped and PERSON not in caplog.text and PERSON not in rec.L.detail
    assert log_id(named.id) in dumped and log_id(named.id).startswith("name#") and log_id(MIC.id) == MIC.id
    assert "configuração warmup.last_mic_device_name" in caplog.text and "(nome de aparelho)" in caplog.text
