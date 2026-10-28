"""U-043 (F5.2), só no Windows: a assinatura dos endpoints pelo Core Audio, o `refresh_audio_devices` que só reinicia o
PortAudio quando ela muda e nada além dos streams de quem pede está aberto, e o `_StreamHandle` que fecha a stream que a
biblioteca encerrou (aberta, ela segurava o PortAudio)."""
from __future__ import annotations

import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32Platform só existe no Windows")


def test_stream_handle_closes_a_stream_the_library_ended():
    from listener.platform.win32.platform import _StreamHandle

    calls: list[str] = []
    errors: list[str] = []
    h = _StreamHandle(lambda: calls.append("start"), lambda: calls.append("stop"), 16000, 1, errors.append)
    h.start()
    h.finished()
    h.finished()
    assert errors == ["stream encerrada pelo sistema (dispositivo removido?)"]       # um aviso só
    h.stop()
    h.stop()
    h.start()
    assert calls == ["start", "stop"]                                                # fechada uma vez, sem reabrir
    ours = _StreamHandle(lambda: None, lambda: calls.append("ours"), 16000, 1, errors.append)
    ours.start()
    ours.stop()
    ours.finished()                                                                  # o fim de uma parada nossa
    assert errors == ["stream encerrada pelo sistema (dispositivo removido?)"] and calls[-1] == "ours"
    # A-05: a encerrada pela biblioteca só fecha (`close` já aborta); a parada nossa usa o `stop` de sempre
    mic = _StreamHandle(lambda: None, lambda: calls.append("stop+close"), 16000, 1, errors.append,
                        close_fn=lambda: calls.append("close"))
    mic.start()
    mic.finished()
    mic.stop()
    assert calls[-1] == "close"


def test_an_index_from_an_older_reading_is_resolved_again_by_id(monkeypatch):
    """A-02: o reinício do PortAudio renumera e o MME repete nomes: só vale o índice que a leitura atual tem para o id."""
    from listener.platform.base import AudioDevice
    from listener.platform.win32 import platform as wp

    plat = wp.Win32Platform()
    plat._indices = {"ep-a": 3}
    listed: list[int] = []

    def fresh() -> list[AudioDevice]:
        listed.append(1)
        return [AudioDevice("ep-a", "Fone", "WASAPI", 1, 16000, index=7)]

    monkeypatch.setattr(plat, "list_input_devices", fresh)
    assert plat._resolve_index(AudioDevice("ep-a", "Fone", "WASAPI", 1, 16000, index=3)) == 3 and listed == []
    assert plat._resolve_index(AudioDevice("ep-a", "Fone", "WASAPI", 1, 16000, index=5)) == 7 and listed == [1]


def test_refresh_rereads_only_when_the_endpoints_change_and_nothing_else_is_open(monkeypatch):
    from listener.platform.win32 import platform as wp

    sigs = iter([("a",), ("a",), ("b",), ("b",)])
    monkeypatch.setattr(wp, "_endpoint_signature", lambda: next(sigs))
    restarts: list[int] = []
    monkeypatch.setattr(wp.portaudio, "restart", lambda: restarts.append(1) or True)
    opened = {"n": 0}
    monkeypatch.setattr(wp.portaudio, "open_streams", lambda: opened["n"])
    plat = wp.Win32Platform()
    assert plat.refresh_audio_devices() is True and restarts == [1]        # a primeira relê sempre
    assert plat.refresh_audio_devices() is False and restarts == [1]       # mesma assinatura
    opened["n"] = 1
    released: list[int] = []
    assert plat.refresh_audio_devices(0, lambda: released.append(1)) is False and released == []   # outro stream aberto
    assert plat.refresh_audio_devices(1, lambda: released.append(1)) is True and released == [1] and restarts == [1, 1]
    out: list[bool] = []
    t = threading.Thread(target=lambda: out.append(plat.refresh_audio_devices()))
    t.start()
    t.join(5)
    assert out == [False]                                                  # fora da thread da interface: nada


def test_endpoint_signature_reads_this_machine():
    from listener.platform.win32 import platform as wp

    sig = wp._endpoint_signature()
    assert isinstance(sig, tuple) and len(sig) == 6 and all(isinstance(x, (tuple, str)) for x in sig)


# ---------------------------------------------------------------- F5.3 L4
def test_the_endpoint_reader_reads_names_by_key_only_when_the_signature_changes():
    """A-19: a cada leitura só a assinatura; nome e ContainerId por chave, em cache por id, relidos com `refresh_names`. A
    leitura tem a mesma assinatura da U-043 (o reinício do PortAudio compara as duas)."""
    from listener.platform.win32 import endpoints as ep
    from listener.platform.win32 import platform as wp

    reader = ep.EndpointReader()
    first = reader.read()
    if first is None:
        pytest.skip("o Core Audio não respondeu nesta máquina")
    assert first.signature == wp._endpoint_signature()
    assert all(e.id and e.name for e in (*first.capture, *first.render)) and reader.props_read == len(first.ids())
    assert all(e.container != ep.LOCAL_CONTAINER for e in (*first.capture, *first.render))   # o do computador vira vazio
    assert reader.read() is first and reader.props_read == len(first.ids())                  # nada mudou: nada relido
    again = reader.read(refresh_names=True)
    assert again == first and reader.props_read == 2 * len(first.ids())
    plat = wp.Win32Platform()
    assert plat.audio_endpoints() == first
    out: list[object] = []
    t = threading.Thread(target=lambda: out.append(plat.audio_endpoints()))
    t.start()
    t.join(5)
    assert out == [None]                                                          # COM só na thread da interface


def test_the_loopback_opens_on_the_chosen_output_matched_by_name():
    """A-21: a escolhida (ativa) pelo nome na lista do PyAudioWPatch; ausente, a padrão; sem o Core Audio, a padrão dele."""
    from types import SimpleNamespace

    from listener.platform.base import Endpoint, Endpoints
    from listener.platform.win32 import platform as wp

    reading = Endpoints(render=(Endpoint("a", "Alto-falantes (Realtek(R) Audio)"), Endpoint("b", "Fone de Ouvido (WH-1000XM4)")),
                        render_default="a", render_comm="b")
    assert wp._loopback_target(reading, "b") == ("b", "Fone de Ouvido (WH-1000XM4)")
    assert wp._loopback_target(reading, "sumiu") == ("a", "Alto-falantes (Realtek(R) Audio)")
    assert wp._loopback_target(reading, None) == ("a", "Alto-falantes (Realtek(R) Audio)") and wp._loopback_target(None, "b") == ("", "")
    loopbacks = [{"index": 13, "name": "Alto-falantes (Realtek(R) Audio) [Loopback]"},
                 {"index": 14, "name": "Fone de Ouvido (WH-1000XM4) [Loopback]"}]
    pa = SimpleNamespace(get_loopback_device_info_generator=lambda: iter(loopbacks),
                         get_host_api_info_by_type=lambda _t: {"defaultOutputDevice": 10},
                         get_device_info_by_index=lambda _i: {"name": "Alto-falantes (Realtek(R) Audio)", "isLoopbackDevice": False})
    pyaudio = SimpleNamespace(paWASAPI=13)
    assert wp._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4)")["index"] == 14
    assert wp._pick_loopback(pa, pyaudio, "")["index"] == 13 and wp._pick_loopback(pa, pyaudio, "outra")["index"] == 13
    assert wp._render_id(reading, "Fone de Ouvido (WH-1000XM4)") == "b" and wp._render_id(None, "x") == ""
    similar = [{"index": 20, "name": "Fone de Ouvido (WH-1000XM4) Hands-Free [Loopback]"}, *loopbacks]   # o igual primeiro
    pa.get_loopback_device_info_generator = lambda: iter(similar)
    assert wp._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4)")["index"] == 14


def test_strict_refuses_a_microphone_that_is_not_in_the_list(monkeypatch):
    """A-16: na troca pelo painel o escolhido fora da lista é erro; na reabertura, o padrão no lugar."""
    from listener.platform.base import AudioDevice
    from listener.platform.win32 import platform as wp

    plat = wp.Win32Platform()
    present = AudioDevice("ep-a", "Fone", "WASAPI", 1, 16000, index=7)
    monkeypatch.setattr(plat, "list_input_devices", lambda: [present])
    monkeypatch.setattr(plat, "default_input_device", lambda: present)
    missing = AudioDevice("ep-b", "Outro", "WASAPI", 1, 16000)
    with pytest.raises(LookupError, match="ep-b"):
        plat._resolve_input(missing, strict=True)
    assert plat._resolve_input(missing) == present and plat._resolve_input(None, strict=True) == present


def test_audio_devices_changed_compares_with_the_last_portaudio_reading(monkeypatch):
    from listener.platform.win32 import platform as wp

    sig = {"now": ("a",)}
    monkeypatch.setattr(wp, "_endpoint_signature", lambda: sig["now"])
    plat = wp.Win32Platform()
    plat._audio_signature = ("a",)
    assert plat.audio_devices_changed() is False
    sig["now"] = ("b",)
    assert plat.audio_devices_changed() is True
    sig["now"] = None                                                             # sem o Core Audio: nada a reler
    assert plat.audio_devices_changed() is False


def test_the_mute_moves_to_the_new_endpoint_at_once_under_the_lock(monkeypatch):
    """A-18: com a gravação muda, a troca tira o mudo do antigo e põe no novo dentro da trava que `get` também usa (o poller
    lê em outra thread); `resolved` é o id do endpoint, nunca o nome (A-22)."""
    from listener.platform.base import AudioDevice
    from listener.platform.win32 import platform as wp

    class Volume:
        def __init__(self) -> None:
            self.muted = 0

        def SetMute(self, value: int, _ctx: object) -> None:  # noqa: N802 (nome do COM)
            self.muted = value

        def GetMute(self) -> int:  # noqa: N802
            return self.muted

    volumes = {"ep-a": Volume(), "ep-b": Volume()}
    monkeypatch.setattr(wp, "_capture_endpoints", lambda: [("Fone de Fulano", "ep-a", "ep-a"), ("Mic", "ep-b", "ep-b")])
    monkeypatch.setattr(wp, "_endpoint_volume", lambda dev: volumes[dev])
    mute = wp._Win32MicMute()
    assert mute.set_device(AudioDevice("ep-a", "Fone de Fulano", "WASAPI", 1, 16000)) and mute.set(True)
    assert volumes["ep-a"].muted == 1 and mute.resolved == "ep-a"
    assert mute.set_device(AudioDevice("ep-b", "Mic", "WASAPI", 1, 16000), muted=True)
    assert (volumes["ep-a"].muted, volumes["ep-b"].muted) == (0, 1) and mute.get() is True and mute.resolved == "ep-b"
    got: list[object] = []
    with mute._lock:                                                              # a troca em curso segura o poller
        t = threading.Thread(target=lambda: got.append(mute.get()))
        t.start()
        t.join(0.2)
        assert t.is_alive() and got == []
    t.join(5)
    assert got == [True]


def test_a_name_id_is_summarized_in_the_messages_and_the_log(monkeypatch, caplog):
    """R-05 (A-22): o id `name:<nome>` (sem id do Core Audio) vai resumido na mensagem do modo estrito, que chega à tela e ao
    log, e no aviso de cair no padrão."""
    import logging

    from listener.platform.base import AudioDevice, log_id
    from listener.platform.win32 import platform as wp

    plat = wp.Win32Platform()
    present = AudioDevice("ep-a", "Microfone", "WASAPI", 1, 16000, index=7)
    monkeypatch.setattr(plat, "list_input_devices", lambda: [present])
    monkeypatch.setattr(plat, "default_input_device", lambda: present)
    named = AudioDevice("name:AirPods de Fulano", "AirPods de Fulano", "WASAPI", 1, 16000)
    with pytest.raises(LookupError) as err:
        plat._resolve_input(named, strict=True)
    assert "Fulano" not in str(err.value) and log_id(named.id) in str(err.value)
    with caplog.at_level(logging.DEBUG, logger="listener"):
        assert plat._resolve_input(named) == present
    assert "Fulano" not in caplog.text and log_id(named.id) in caplog.text
