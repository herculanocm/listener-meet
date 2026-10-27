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
