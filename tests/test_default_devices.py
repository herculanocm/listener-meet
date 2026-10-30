"""D-067: a escolha única do "aparelho padrão de agora" fora do player. No Windows (com um `sounddevice` de mentira e o Core
Audio trocado por funções): o fluxo do player e o "Testar 3 s" abrem no WASAPI pelo id (o mapa da leitura, R-02) ou pelo
nome amigável inteiro e único do padrão de console de agora, nunca pelo nome cortado do MME nem por prefixo; o pedido
explícito fora da lista ou ambíguo é `LookupError`, e sem como saber, `OutputUnknownError` (R-11); o padrão fora da lista cai
na saída padrão WASAPI da leitura (R-12); o "Testar 3 s" vai para a saída escolhida (R-04); o microfone padrão e o do mudo
são os do Core Audio, pelo id. O warm-up, com o `play_sound` de verdade, toca no aparelho de depois de cada troca. Portável:
o medidor do "acompanha" que ficou num microfone que já não é o padrão reabre nele."""
from __future__ import annotations

import sys
import types
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from listener.platform.base import AudioDevice, Endpoint
from tests.fake_platform import BASE_ENDPOINTS, MIC

win32 = pytest.mark.skipif(sys.platform != "win32", reason="Win32Platform só existe no Windows")
SPEAKERS_NAME = "Altofalantes (Realtek(R) Audio)"
HEADSET_NAME = "Fones de ouvido (HyperX QuadCast 2 S)"
IDS = {"ep-alto": SPEAKERS_NAME, "ep-fone": HEADSET_NAME, "ep-novo": "Fone Bluetooth novo", "ep-mic": "Microfone (Webcam)"}


def _dev(name: str, ins: int, outs: int, rate: float, api: int) -> dict[str, Any]:
    return {"name": name, "max_input_channels": ins, "max_output_channels": outs, "default_samplerate": rate, "hostapi": api}


class Stream:
    """Um `sd.OutputStream` aberto (com `weakref`, como o de verdade: o `portaudio.track` o guarda)."""

    def __init__(self, kw: dict[str, Any]) -> None:
        self.kw, self.samplerate, self.latency, self.closed, self.endpoint_id = kw, float(kw["samplerate"]), 0.2, False, ""


class FakeSD(types.ModuleType):
    """O PortAudio do Windows: o MME com o nome do fone cortado em 31 caracteres (e índice menor) e o WASAPI inteiro."""

    def __init__(self) -> None:
        super().__init__("sounddevice")
        self.devices = [_dev("Microfone (Webcam)", 1, 0, 44100.0, 0), _dev(HEADSET_NAME[:31], 0, 2, 44100.0, 0),
                        _dev(SPEAKERS_NAME, 0, 2, 44100.0, 0), _dev("Microfone (Webcam)", 1, 0, 48000.0, 1),
                        _dev(SPEAKERS_NAME, 0, 2, 48000.0, 1), _dev(HEADSET_NAME, 0, 2, 44100.0, 1)]
        self.apis = [{"name": "MME", "devices": [0, 1, 2], "default_output_device": 2, "default_input_device": 0},
                     {"name": "Windows WASAPI", "devices": [3, 4, 5], "default_output_device": 4, "default_input_device": 3}]
        self.streams: list[dict[str, Any]] = []
        self.plays: list[dict[str, Any]] = []

    def query_hostapis(self, index: int | None = None) -> Any:
        return self.apis if index is None else self.apis[index]

    def query_devices(self, device: int | None = None, kind: str | None = None) -> Any:
        return self.devices if device is None and kind is None else self.devices[device if device is not None else 2]

    def OutputStream(self, **kw: Any) -> Any:  # noqa: N802 (o nome do sounddevice)
        self.streams.append(kw)
        return Stream(kw)

    def WasapiSettings(self, **kw: Any) -> Any:  # noqa: N802
        return ("wasapi", tuple(sorted(kw.items())))

    def play(self, samples: np.ndarray, rate: int, **kw: Any) -> None:
        self.plays.append({"frames": len(samples), "rate": rate, **kw})

    def get_stream(self) -> Any:
        raise RuntimeError("nenhum play")


@pytest.fixture
def win(monkeypatch):
    """O `Win32Output` com o PortAudio de mentira e o padrão do Core Audio num dicionário (o papel lido fica anotado)."""
    from listener.platform.common import portaudio
    from listener.platform.win32 import endpoints, output, platform as wp

    sd = FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(portaudio, "_streams", portaudio.weakref.WeakSet())
    now = {endpoints.FLOW_RENDER: "ep-alto", endpoints.FLOW_CAPTURE: "ep-mic"}
    roles: list[int] = []

    def default_id(flow: int, role: int = endpoints.ROLE_CONSOLE, enumerator: Any = None) -> str:
        roles.append(role)
        return now[flow]

    monkeypatch.setattr(endpoints, "default_id", default_id)
    monkeypatch.setattr(endpoints, "name_of", lambda eid: IDS.get(eid, ""))
    plat = wp.Win32Platform()
    return types.SimpleNamespace(sd=sd, now=now, roles=roles, plat=plat, flows=endpoints, output=output)


@win32
def test_the_player_opens_the_default_of_now_on_wasapi_by_the_full_name(win):
    s = win.plat.open_output()
    assert s.endpoint_id == "ep-alto" and win.sd.streams[-1]["device"] == 4
    win.now[win.flows.FLOW_RENDER] = "ep-fone"                           # o dono troca o padrão: o PortAudio não releu
    s = win.plat.open_output()
    kw = win.sd.streams[-1]
    assert s.endpoint_id == "ep-fone" and kw["device"] == 5 and kw["samplerate"] == 44100   # o WASAPI, não o MME cortado (1)
    assert kw["latency"] == win.output.output.PLAYER_LATENCY_S and kw["extra_settings"] == ("wasapi", (("auto_convert", True),))
    assert (kw["channels"], kw["dtype"]) == (1, "float32") and set(win.roles) == {win.flows.ROLE_CONSOLE}   # não o de comunicação
    assert win.plat.open_output("ep-fone").endpoint_id == "ep-fone"
    with pytest.raises(LookupError):
        win.plat.open_output("ep-novo")                                   # chegou depois da leitura: o player fica onde está


@win32
def test_a_default_outside_the_reading_opens_the_reading_default_and_says_so(win, caplog):
    win.now[win.flows.FLOW_RENDER] = "ep-novo"
    with caplog.at_level("WARNING", logger="listener.platform.win32.output"):
        s = win.plat.open_output()
    assert s.endpoint_id == "" and win.sd.streams[-1]["device"] == 4 and "ep-novo" in caplog.text
    assert "Fone Bluetooth novo" not in caplog.text                     # A-22: o id, nunca o nome
    win.sd.apis = win.sd.apis[:1]                                        # sem WASAPI: o padrão da biblioteca (o de antes)
    s = win.plat.open_output()
    assert s.endpoint_id == "" and win.sd.streams[-1]["device"] is None and win.sd.streams[-1]["extra_settings"] is None


@win32
def test_the_warmup_test_plays_on_the_chosen_output_or_the_default_of_now(win):
    win.now[win.flows.FLOW_RENDER] = "ep-fone"
    win.plat.play_sound(np.zeros(160, np.float32), 16000)
    win.now[win.flows.FLOW_RENDER] = "ep-novo"
    win.plat.play_sound(np.zeros(80, np.float32), 16000)                 # fora da lista: a saída padrão WASAPI (R-12)
    win.plat.play_sound(np.zeros(40, np.float32), 16000, "ep-fone")      # R-04: a saída escolhida no "Antes de gravar"
    win.now[win.flows.FLOW_RENDER] = "ep-alto"
    win.plat.play_sound(np.zeros(20, np.float32), 16000, "ep-novo")      # a escolhida fora da lista: o padrão
    first, second, chosen, gone = win.sd.plays
    assert (first["device"], first["rate"], first["extra_settings"]) == (5, 16000, ("wasapi", (("auto_convert", True),)))
    assert (second["device"], chosen["device"], gone["device"]) == (4, 5, 4)
    assert second["extra_settings"] == ("wasapi", (("auto_convert", True),))


@win32
def test_the_default_mic_is_the_core_audio_one_by_id(win, monkeypatch):
    mics = [AudioDevice("ep-outro", "Outro", "WASAPI", 1, 48000, index=6), AudioDevice("ep-mic", "Microfone (Webcam)", "WASAPI", 1,
                                                                                       48000, index=3)]
    monkeypatch.setattr(win.plat, "list_input_devices", lambda: list(mics))
    assert win.plat.default_input_device() is mics[1]
    win.now[win.flows.FLOW_CAPTURE] = "ep-outro"                         # trocou: o PortAudio ainda diz o 3
    assert win.plat.default_input_device() is mics[0]
    win.now[win.flows.FLOW_CAPTURE] = "ep-sumiu"                         # fora da lista: o padrão da leitura
    assert win.plat.default_input_device() is mics[1]


@win32
def test_the_central_default_read_is_console_and_tolerates_core_audio_failing(monkeypatch):
    from listener.platform.win32 import endpoints, output

    seen: list[tuple[int, int]] = []

    class Enumerator:
        def GetDefaultAudioEndpoint(self, flow: int, role: int) -> Any:  # noqa: N802
            seen.append((flow, role))
            return types.SimpleNamespace(GetId=lambda: f"ep-{flow}-{role}")

    assert endpoints.default_id(endpoints.FLOW_RENDER, enumerator=Enumerator()) == "ep-0-0" and seen == [(0, 0)]

    def down(*_args: Any, **_kw: Any) -> str:
        raise OSError("Core Audio fora")

    monkeypatch.setattr(endpoints, "default_id", down)
    assert output.read_default(endpoints.FLOW_RENDER) is None


@win32
def test_two_outputs_with_the_same_name_open_the_one_by_id_or_none_at_all(win, monkeypatch):
    """R-02: com o mapa {índice: id} desta leitura, o id exato; sem ele, nome igual repetido não escolhe ninguém (o padrão
    abre a reserva sem declarar o id; o pedido é `LookupError`); prefixo não casa no WASAPI."""
    from listener.platform.common import portaudio

    same = "Fones de ouvido (USB Headset)"
    win.sd.devices[4] = {**win.sd.devices[4], "name": same}
    win.sd.devices[5] = {**win.sd.devices[5], "name": same}
    ids_same = {"ep-usb-1": same, "ep-usb-2": same, "ep-curto": "Fones de ouvido (USB"}
    monkeypatch.setattr(win.flows, "name_of", lambda eid: ids_same.get(eid, ""))
    win.now[win.flows.FLOW_RENDER] = "ep-usb-2"                          # o dono escolheu o SEGUNDO
    s = win.plat.open_output()
    assert (win.sd.streams[-1]["device"], s.endpoint_id) == (4, "")      # a reserva, sem dizer que é o pedido
    for wanted in ("ep-usb-2", "ep-curto"):
        with pytest.raises(LookupError):
            win.plat.open_output(wanted)
    win.plat._wasapi_map = (portaudio.generation(), {3: "ep-mic", 4: "ep-usb-1", 5: "ep-usb-2"})
    s = win.plat.open_output()
    assert (win.sd.streams[-1]["device"], s.endpoint_id) == (5, "ep-usb-2")
    assert win.plat.open_output("ep-usb-1").endpoint_id == "ep-usb-1" and win.sd.streams[-1]["device"] == 4
    win.plat._wasapi_map = (portaudio.generation() - 1, {4: "ep-usb-2"})   # de outra leitura: não vale
    assert win.plat.wasapi_ids_now() is None


@win32
def test_a_name_prefix_does_not_match_on_wasapi(win, monkeypatch):
    """R-02: o prefixo de 20 caracteres era para o nome cortado do MME; no WASAPI, outro aparelho que começa igual não é o
    pedido (o alvo fora da lista é `LookupError`, e o padrão fora da lista abre a reserva sem declarar o id)."""
    win.sd.devices[5] = {**win.sd.devices[5], "name": "Alto-falantes (Dispositivo USB de Audio) 2"}
    monkeypatch.setattr(win.flows, "name_of", lambda eid: {"ep-novo": "Alto-falantes (Dispositivo USB de Audio)"}.get(eid, ""))
    with pytest.raises(LookupError):
        win.plat.open_output("ep-novo")
    win.now[win.flows.FLOW_RENDER] = "ep-novo"
    assert win.plat.open_output().endpoint_id == "" and win.sd.streams[-1]["device"] == 4


@win32
def test_without_wasapi_or_a_name_the_output_is_unknown_and_quiet(win, monkeypatch):
    """R-11: sem WASAPI, ou sem o nome do padrão e sem o mapa, o pedido é `OutputUnknownError` (o player fica calado) e o
    padrão abre o da biblioteca."""
    from listener.platform.base import OutputUnknownError

    monkeypatch.setattr(win.flows, "name_of", lambda eid: "")
    with pytest.raises(OutputUnknownError):
        win.plat.open_output("ep-fone")
    win.sd.apis = win.sd.apis[:1]
    with pytest.raises(OutputUnknownError):
        win.plat.open_output("ep-alto")
    assert win.plat.open_output().endpoint_id == "" and win.sd.streams[-1]["device"] is None


@win32
def test_the_id_map_is_kept_only_on_the_ui_thread_for_this_reading(win, monkeypatch):
    import threading

    from listener.platform.common import portaudio

    monkeypatch.setattr(win.output, "wasapi_ids", lambda sd: {4: "ep-alto", 5: "ep-fone"})
    worker = threading.Thread(target=win.plat.remember_wasapi_ids)
    worker.start()
    worker.join(5)
    assert win.plat.wasapi_ids_now() is None                             # fora da thread do PortAudio: nada
    win.plat.remember_wasapi_ids()
    assert win.plat.wasapi_ids_now() == {4: "ep-alto", 5: "ep-fone"}
    monkeypatch.setattr(portaudio, "_generation", [portaudio.generation() + 1])
    assert win.plat.wasapi_ids_now() is None                             # o PortAudio reiniciou: outra leitura


@win32
def test_the_mic_list_takes_the_id_from_the_map_and_the_mute_follows_the_central_default(win, monkeypatch):
    from listener.platform.common import portaudio
    from listener.platform.win32 import mute as wm

    monkeypatch.setattr(win.plat, "refresh_audio_devices", lambda *a, **k: False)
    monkeypatch.setattr(win.flows, "capture_endpoints", list)         # o Core Audio sem nome: só o mapa sabe
    win.plat._wasapi_map = (portaudio.generation(), {3: "ep-mic"})
    assert [d.id for d in win.plat.list_input_devices()] == ["ep-mic"]
    asked: list[str] = []

    def device(eid: str) -> str:
        asked.append(eid)
        return f"dev:{eid}"

    monkeypatch.setattr(win.flows, "device", device)
    monkeypatch.setattr(wm, "_endpoint_volume", lambda dev: ("volume", dev))
    win.now[win.flows.FLOW_CAPTURE] = "ep-outro"
    assert wm._Win32MicMute()._default() == ("volume", "dev:ep-outro") and asked == ["ep-outro"]
    assert set(win.roles) == {win.flows.ROLE_CONSOLE}                  # o console, como o gravador (não o multimídia)


@win32
@pytest.mark.lento
def test_the_warmup_test_follows_each_switch_through_the_real_play_sound(qtbot, store, platform, win, monkeypatch):
    """R-13 (b): o "Testar 3 s" do warm-up pelo `Win32Output.play_sound` de verdade (só o PortAudio e o Core Audio são de
    mentira): toca no aparelho de depois de cada troca, ida e volta, e na saída escolhida quando há uma."""
    from listener.capture.recorder import SourceSpec
    from listener.model import SourceKind
    from listener.ui.warmup import WarmupDialog

    monkeypatch.setattr(platform, "play_sound", win.plat.play_sound)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    try:
        for endpoint in ("ep-alto", "ep-fone", "ep-alto"):
            win.now[win.flows.FLOW_RENDER] = endpoint
            dlg._testing, dlg._test_buffer = True, [np.zeros(16000, np.float32)]
            dlg._finish_test()
        monkeypatch.setattr(dlg.sources, "selected_spec", lambda: SourceSpec(SourceKind.SYSTEM, output_id="ep-fone"))
        dlg._testing, dlg._test_buffer = True, [np.zeros(16000, np.float32)]
        dlg._finish_test()
        assert [p["device"] for p in win.sd.plays] == [4, 5, 4, 5] and {p["frames"] for p in win.sd.plays} == {16000}
    finally:
        dlg.reject()


# ---------------------------------------------------------------- portável (a plataforma falsa)
MIC2 = AudioDevice("fake-mic-2", "Mic Fone", "fake", 1, 16000, index=1)


@pytest.mark.lento
def test_the_follow_meter_left_on_an_old_mic_reopens_on_the_default_of_now(qtbot, platform):
    """A lista não pôde ser relida (o player segura o PortAudio) e o padrão mudou para um microfone já conhecido: o medidor
    do "acompanha" fecha e reabre nele; no escolhido, ou já no padrão, nada muda."""
    from PySide6.QtWidgets import QComboBox

    from listener.ui.devices import fill_mic_combo, refresh_mics

    platform.devices = [MIC, MIC2]
    platform.settle()
    platform.other_streams = lambda: 1                                   # o player toca: o PortAudio não relê
    combo = QComboBox()
    qtbot.addWidget(combo)
    fill_mic_combo(combo, platform.devices, "", None, follow=True)
    assert combo.itemText(0) == "Padrão do Windows (acompanha)"            # sem o padrão: só o "acompanha"
    meter = platform.open_mic(None, 16000, lambda *_: None)
    closed: list[int] = []
    kwargs: dict[str, Any] = {"meter_open": True, "close_meter": lambda: closed.append(1), "follow": True}
    assert meter.device_id == MIC.id and refresh_mics(platform, combo, platform.devices, meter=meter, **kwargs) is None
    assert combo.itemText(0) == f"Padrão do Windows (acompanha): agora {MIC.name}"   # F5.9 (item 8): o "agora X"
    platform.endpoints = replace(BASE_ENDPOINTS, capture=(Endpoint(MIC.id, MIC.name), Endpoint(MIC2.id, MIC2.name)),
                                 capture_default=MIC2.id, capture_comm=MIC2.id)
    result = refresh_mics(platform, combo, platform.devices, meter=meter, **kwargs)
    assert result is not None and result.reopen and closed == [1] and platform.refreshes == 0
    assert combo.itemText(0) == f"Padrão do Windows (acompanha): agora {MIC2.name}" and combo.toolTip() == combo.itemText(0)
    assert platform.open_mic(None, 16000, lambda *_: None).device_id == MIC2.id    # a reabertura vai para o padrão de agora
    combo.setCurrentIndex(combo.findData(MIC.id))                        # o escolhido: fica
    assert refresh_mics(platform, combo, platform.devices, meter=meter, **kwargs) is None and closed == [1]
