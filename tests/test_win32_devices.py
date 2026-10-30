"""U-043 (F5.2), só no Windows: a assinatura dos endpoints pelo Core Audio, o `refresh_audio_devices` que só reinicia o
PortAudio quando ela muda e nada além dos streams de quem pede está aberto, e o `_StreamHandle` que fecha a stream que a
biblioteca encerrou (aberta, ela segurava o PortAudio)."""
from __future__ import annotations

import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32Platform só existe no Windows")


def test_stream_handle_closes_a_stream_the_library_ended():
    from listener.platform.win32.stream import _StreamHandle

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

    def restart() -> bool:
        restarts.append(1)
        return True

    monkeypatch.setattr(wp.portaudio, "restart", restart)
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
    from listener.platform.win32 import system_loopback as sl

    reading = Endpoints(render=(Endpoint("a", "Alto-falantes (Realtek(R) Audio)"), Endpoint("b", "Fone de Ouvido (WH-1000XM4)")),
                        render_default="a", render_comm="b")
    assert sl._loopback_target(reading, "b") == ("b", "Fone de Ouvido (WH-1000XM4)")
    assert sl._loopback_target(reading, "sumiu") == ("a", "Alto-falantes (Realtek(R) Audio)")
    assert sl._loopback_target(reading, None) == ("a", "Alto-falantes (Realtek(R) Audio)") and sl._loopback_target(None, "b") == ("", "")
    loopbacks = [{"index": 13, "name": "Alto-falantes (Realtek(R) Audio) [Loopback]"},
                 {"index": 14, "name": "Fone de Ouvido (WH-1000XM4) [Loopback]"}]
    pa = SimpleNamespace(get_loopback_device_info_generator=lambda: iter(loopbacks),
                         get_host_api_info_by_type=lambda _t: {"defaultOutputDevice": 10},
                         get_device_info_by_index=lambda _i: {"name": "Alto-falantes (Realtek(R) Audio)", "isLoopbackDevice": False})
    pyaudio = SimpleNamespace(paWASAPI=13)
    assert sl._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4)")["index"] == 14
    assert sl._pick_loopback(pa, pyaudio, "")["index"] == 13 and sl._pick_loopback(pa, pyaudio, "outra")["index"] == 13
    assert sl._render_id(reading, "Fone de Ouvido (WH-1000XM4)") == "b" and sl._render_id(None, "x") == ""
    similar = [{"index": 20, "name": "Fone de Ouvido (WH-1000XM4) Hands-Free [Loopback]"}, *loopbacks]   # o igual primeiro
    pa.get_loopback_device_info_generator = lambda: iter(similar)
    assert sl._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4)")["index"] == 14


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

    sig: dict[str, tuple[str, ...] | None] = {"now": ("a",)}
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
    from listener.platform.win32 import endpoints, mute as wm

    class Volume:
        def __init__(self) -> None:
            self.muted = 0

        def SetMute(self, value: int, _ctx: object) -> None:  # noqa: N802 (nome do COM)
            self.muted = value

        def GetMute(self) -> int:  # noqa: N802
            return self.muted

    volumes = {"ep-a": Volume(), "ep-b": Volume()}
    monkeypatch.setattr(endpoints, "capture_endpoints", lambda: [("Fone de Fulano", "ep-a", "ep-a"), ("Mic", "ep-b", "ep-b")])
    monkeypatch.setattr(wm, "_endpoint_volume", lambda dev: volumes[dev])
    mute = wm._Win32MicMute()
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


# ---------------------------------------------------------------- F5.9 L3: o casamento sem prefixo (Aceite 5)
def test_two_devices_with_a_common_prefix_never_match_each_other(monkeypatch, caplog):
    """Item 5: dois fones da mesma família só diferem no fim do nome. O salvo ausente não cai no irmão (`pick_device`
    pelo id e pelo nome exato, `None` senão); `_resolve_input` estrito recusa; o loopback com o nome repetido na lista do
    PyAudioWPatch vai na saída padrão com uma linha de log sem o nome do aparelho; o id estável do microfone pelo nome
    só quando o nome é um só no Core Audio."""
    import logging
    from types import SimpleNamespace

    from listener.platform.base import AudioDevice, pick_device
    from listener.platform.win32 import endpoints, system_loopback as sl
    from listener.platform.win32 import platform as wp

    stereo = AudioDevice("ep-stereo", "Fone de Ouvido (WH-1000XM4)", "WASAPI", 1, 16000, index=4)
    hands_free = AudioDevice("ep-hfp", "Fone de Ouvido (WH-1000XM4) Hands-Free", "WASAPI", 1, 16000, index=5)
    assert pick_device([stereo, hands_free], "ep-sumiu", stereo.name) is stereo
    assert pick_device([hands_free], "ep-sumiu", stereo.name) is None                   # o irmão não serve
    assert pick_device([stereo], "ep-sumiu", hands_free.name) is None
    plat = wp.Win32Platform()
    monkeypatch.setattr(plat, "list_input_devices", lambda: [hands_free])
    monkeypatch.setattr(plat, "default_input_device", lambda: hands_free)
    with pytest.raises(LookupError, match="ep-stereo"):
        plat._resolve_input(stereo, strict=True)
    assert plat._resolve_input(stereo) == hands_free                                     # sem exigir: o padrão, não o irmão
    assert endpoints.names_match("Fone de Ouvido (WH-1000XM4)", "fone de ouvido (wh-1000xm4) ")
    assert not endpoints.names_match(stereo.name, hands_free.name)
    twins = [("Microfone USB", "ep-1", None), ("Microfone USB", "ep-2", None), ("Webcam", "ep-3", None)]
    assert endpoints.by_name(twins, "Webcam", lambda c: c[0]) == twins[2]
    assert endpoints.by_name(twins, "Microfone USB", lambda c: c[0]) is None             # repetido: nenhum dos dois
    loopbacks = [{"index": 13, "name": "Alto-falantes (Realtek(R) Audio) [Loopback]"},
                 {"index": 14, "name": "Fone de Ouvido (WH-1000XM4) [Loopback]"},
                 {"index": 15, "name": "Fone de Ouvido (WH-1000XM4) [Loopback]"}]
    pa = SimpleNamespace(get_loopback_device_info_generator=lambda: iter(loopbacks),
                         get_host_api_info_by_type=lambda _t: {"defaultOutputDevice": 10},
                         get_device_info_by_index=lambda _i: {"name": "Alto-falantes (Realtek(R) Audio)", "isLoopbackDevice": False})
    pyaudio = SimpleNamespace(paWASAPI=13)
    with caplog.at_level(logging.WARNING, logger="listener"):
        assert sl._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4)")["index"] == 13   # repetido: o padrão
        assert sl._pick_loopback(pa, pyaudio, "Fone de Ouvido (WH-1000XM4) Hands-Free")["index"] == 13   # ausente: o padrão, calado
    assert sum("nome repetido" in r.getMessage() for r in caplog.records) == 1 and "WH-1000XM4" not in caplog.text


# ---------------------------------------------------------------- F5.9 L1b: a prova de vida dos handles
def test_the_stream_handle_answers_alive_only_between_start_and_stop():
    """F5.9 (item 1): `alive()` é None sem `alive_fn`, antes do `start()`, depois do nosso `stop()` ou do fim avisado, e quando
    a pergunta falha; False é definitivo: o `stop()` seguinte só fecha (`close_fn`) e um `finished` tardio não avisa."""
    from listener.platform.win32.stream import _StreamHandle

    answer: list[bool | None] = [True]
    calls: list[str] = []
    errors: list[str] = []
    h = _StreamHandle(lambda: None, lambda: calls.append("stop"), 16000, 1, errors.append,
                      close_fn=lambda: calls.append("close"), alive_fn=lambda: answer[0])
    assert h.alive() is None                                                      # antes do start
    h.start()
    assert h.alive() is True
    answer[0] = None
    assert h.alive() is None
    answer[0] = False
    assert h.alive() is False and h.alive() is None                               # definitivo: depois, não se sabe
    h.finished()
    h.stop()
    assert errors == [] and calls == ["close"]                                    # só fecha, como a encerrada pela biblioteca
    assert _StreamHandle(lambda: None, lambda: None, 16000, 1).alive() is None    # sem `alive_fn`
    ours = _StreamHandle(lambda: None, lambda: None, 16000, 1, alive_fn=lambda: True)
    ours.start()
    ours.stop()
    assert ours.alive() is None
    def boom() -> bool | None:
        raise RuntimeError("a biblioteca não respondeu")

    broken = _StreamHandle(lambda: None, lambda: None, 16000, 1, alive_fn=boom)
    broken.start()
    assert broken.alive() is None                                                 # a pergunta que falha não derruba a vigia


@pytest.mark.lento                                                                # subprocesso
def test_the_process_handle_says_when_the_process_left():
    """F5.9 (item 1, D-070): `OpenProcess(SYNCHRONIZE)` e `WaitForSingleObject(h, 0)`: vivo (False), saiu (True); sem
    processo, sem handle."""
    import os
    import subprocess

    from listener.platform.win32.processes import close_handle, has_exited, open_process_handle

    own = open_process_handle(os.getpid())
    assert own is not None and has_exited(own) is False
    close_handle(own)
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    handle = open_process_handle(child.pid)
    assert handle is not None
    child.wait(30)
    assert has_exited(handle) is True
    close_handle(handle)
    assert open_process_handle(0) is None


def test_the_system_loopback_handle_reads_is_active_and_a_dead_stream_only_closes(monkeypatch):
    """F5.9 (item 1): `alive()` pelo `is_active()` do PyAudioWPatch; a stream morta só fecha e termina o PyAudio no `stop()`
    (o `stop_stream` numa stream já parada levantava e pulava o `close`)."""
    from types import SimpleNamespace

    from listener.platform.base import Endpoint, Endpoints
    from listener.platform.win32 import system_loopback as sl

    calls: list[str] = []
    state = {"active": True}
    stream = SimpleNamespace(start_stream=lambda: calls.append("start"), stop_stream=lambda: calls.append("stop_stream"),
                             close=lambda: calls.append("close"), is_active=lambda: state["active"])
    loopbacks = [{"index": 13, "name": "Alto-falantes [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2}]
    pa = SimpleNamespace(get_loopback_device_info_generator=lambda: iter(loopbacks),
                         get_host_api_info_by_type=lambda _t: {"defaultOutputDevice": 10},
                         get_device_info_by_index=lambda _i: {"name": "Alto-falantes", "isLoopbackDevice": False},
                         open=lambda **_kw: stream, terminate=lambda: calls.append("terminate"))
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", SimpleNamespace(paWASAPI=13, paFloat32=1, paContinue=0, PyAudio=lambda: pa))
    reading = Endpoints(render=(Endpoint("a", "Alto-falantes"),), render_default="a")
    h = sl.open_system_loopback(reading, lambda block, t: None)
    assert h.device_id == "a" and (h.sample_rate, h.channels) == (48000, 2) and h.alive() is None
    h.start()
    assert h.alive() is True and calls == ["start"]
    state["active"] = False                                                       # o endpoint foi invalidado: o driver parou
    assert h.alive() is False
    h.stop()
    assert calls == ["start", "close", "terminate"]                               # sem o `stop_stream`
    live = sl.open_system_loopback(reading, lambda block, t: None)
    live.start()
    live.stop()
    assert calls[3:] == ["start", "stop_stream", "close", "terminate"]


def test_the_app_loopback_handle_follows_the_process_handle_and_the_composite_answers_for_the_root(monkeypatch):
    """F5.9 (item 1, D-070): a prova de vida do loopback do app é o handle do processo, aberto na abertura e fechado no
    `stop()`; sem handle, só o fim da thread do tap conta; o handle composto responde pela raiz (o sub-alvo morto é do L2)."""
    from types import SimpleNamespace

    from listener.platform.win32 import app_loopback, processes
    from listener.platform.win32.processes import ProcInfo

    class Tap:
        def __init__(self, pid: int, on_data: object) -> None:
            self.pid, self.running = pid, False
            taps.append(self)

        def get_format(self) -> dict[str, object]:
            return {"sample_rate": 48000, "channels": 2, "sample_format": "float32"}

        def start(self) -> None:
            self.running = True

        def stop(self) -> None:
            self.running = False

        def close(self) -> None:
            pass

        @property
        def is_running(self) -> bool:
            return self.running

    taps: list[Tap] = []
    monkeypatch.setitem(sys.modules, "proctap", SimpleNamespace(ProcessAudioCapture=Tap))
    exited: dict[int, bool | None] = {}
    closed: list[int] = []
    monkeypatch.setattr(processes, "open_process_handle", lambda pid: None if pid == 7 else 100 + pid)
    monkeypatch.setattr(processes, "has_exited", exited.get)
    monkeypatch.setattr(processes, "close_handle", closed.append)
    plat = app_loopback.Win32AppLoopback()
    monkeypatch.setattr(plat, "_processes", lambda: ({}, {}), raising=False)
    h = plat.open_app_loopback(5, lambda block, t: None)
    h.start()
    assert h.alive() is None                                                      # a espera falhou: não se sabe
    exited[105] = False
    assert h.alive() is True
    exited[105] = True
    assert h.alive() is False                                                     # o processo saiu
    h.stop()
    assert closed == [105]
    none = plat.open_app_loopback(7, lambda block, t: None)                      # o Windows recusou o handle
    none.start()
    assert none.alive() is None
    taps[-1].running = False                                                      # a thread do tap acabou
    assert none.alive() is False
    none.stop()
    assert closed == [105]
    tree = {p.pid: p for p in [ProcInfo(1, "explorer.exe", 0), ProcInfo(20, "ms-teams.exe", 1), ProcInfo(21, "ms-teams.exe", 20),
                               ProcInfo(22, "msedgewebview2.exe", 21)]}
    monkeypatch.setattr(plat, "_processes", lambda: (tree, {}))
    monkeypatch.setattr(plat, "_session_pids", lambda: {21, 22})
    both = plat.open_app_loopback(20, lambda block, t: None)
    both.start()
    exited[120], exited[122] = False, True                                        # o sub-alvo morreu: o canal segue vivo
    assert both.alive() is True
    exited[120] = True
    assert both.alive() is False
    both.stop()
    assert closed == [105, 120, 122]


def test_the_microphone_handle_reads_active_from_sounddevice(monkeypatch):
    """F5.9 (item 1): `alive()` do microfone é o `active` do sounddevice, com o guarda do handle (a stream fechada também
    diz False: só vale iniciada e não parada por nós)."""
    import types

    from listener.platform.base import AudioDevice
    from listener.platform.win32 import platform as wp

    class Stream:
        def __init__(self, **kw: object) -> None:
            self.kw, self.active = kw, False
            self.calls: list[str] = []

        def start(self) -> None:
            self.active = True
            self.calls.append("start")

        def stop(self) -> None:
            self.active = False
            self.calls.append("stop")

        def close(self) -> None:
            self.active = False
            self.calls.append("close")

    streams: list[Stream] = []

    def input_stream(**kw: object) -> Stream:
        streams.append(Stream(**kw))
        return streams[-1]

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=input_stream, WasapiSettings=lambda **kw: kw))
    plat = wp.Win32Platform()
    present = AudioDevice("ep-a", "Fone", "WASAPI", 1, 16000, index=7)
    monkeypatch.setattr(plat, "_resolve_input", lambda device, strict=False: present)
    h = plat.open_mic(present, 16000, lambda block, t: None)
    assert h.device_id == "ep-a" and h.alive() is None
    h.start()
    assert h.alive() is True
    streams[-1].active = False                                                    # o driver parou sem o `finished_callback`
    assert h.alive() is False
    h.stop()
    assert streams[-1].calls == ["start", "close"]                                # só fecha (A-05)
