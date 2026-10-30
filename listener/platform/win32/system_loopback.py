"""F5.9 (L1a): o loopback do sistema inteiro pelo PyAudioWPatch, fora de `platform.py`. F5.3 (A-21): a saída escolhida é
casada pelo nome na lista de loopback dele ("<nome> [Loopback]"); ausente, a padrão de console; sem o Core Audio, a padrão
do PyAudioWPatch. Um `PyAudio()` novo por abertura, terminado ao parar ou na falha: com outro vivo a lista seria a antiga
(A-15, quem reabre fecha antes). Log com o id do endpoint, nunca o nome do aparelho (A-22)."""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from ...log import get_logger
from ..base import AudioCallback, Endpoints, ErrorCallback, log_id
from .endpoints import by_name as _by_name, names_match as _names_match
from .stream import _StreamHandle

log = get_logger("platform.win32.system_loopback")


def _loopback_target(reading: Endpoints | None, output_id: str | None) -> tuple[str, str]:
    """F5.3 (A-21): (id, nome) da saída cujo som o loopback grava: a escolhida, se está ativa, senão a padrão de console;
    vazios sem o Core Audio (aí vale a padrão do PyAudioWPatch)."""
    if reading is None:
        return "", ""
    ep = reading.output(output_id) if output_id else None
    if ep is None and output_id:
        log.warning("saída %s ausente; o loopback vai na saída padrão", log_id(output_id))
    ep = ep or reading.output(reading.render_default)
    return (ep.id, ep.name) if ep is not None else ("", "")


def _loopback_base(dev: Mapping[str, Any]) -> str:
    return str(dev["name"]).removesuffix(" [Loopback]")


def _pick_loopback(pa: Any, pyaudio: Any, name: str) -> Mapping[str, Any]:
    """O loopback da saída `name` na lista do PyAudioWPatch ("<nome> [Loopback]"), pelo nome igual; sem ela, ou com o nome
    repetido na lista (F5.9, item 5: ambíguo, como o AMBIGUOUS da R-02; no log só a contagem, A-22), o da saída padrão."""
    loopbacks = list(pa.get_loopback_device_info_generator())
    if name:
        found = [lb for lb in loopbacks if _names_match(_loopback_base(lb), name)]
        if len(found) == 1:
            return found[0]
        if found:
            log.warning("a saída escolhida tem o nome repetido na lista de loopback (%d vezes); o loopback vai na saída padrão",
                        len(found))
    return _default_loopback(pa, pyaudio, loopbacks)


def _default_loopback(pa: Any, pyaudio: Any, loopbacks: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """O loopback da saída padrão do PyAudioWPatch, pelo nome igual (revisão do L3: a substring abria o irmão "Hands-Free"
    que viesse antes na lista, e o "acompanha" é o caso mais comum); só sem o igual, por substring (o PyAudio trunca
    nomes), o mais curto."""
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if speakers.get("isLoopbackDevice"):
        return speakers
    name = str(speakers["name"])
    exact = [lb for lb in loopbacks if _names_match(_loopback_base(lb), name)]
    partial = sorted((lb for lb in loopbacks if name in str(lb["name"])), key=lambda lb: len(str(lb["name"])))
    dev = exact[0] if exact else (partial[0] if partial else None)
    if dev is None:
        raise RuntimeError("nenhum dispositivo de loopback WASAPI encontrado")
    return dev


def _render_id(reading: Endpoints | None, name: str) -> str:
    """O id do Core Audio da saída com este nome ("" quando não se sabe)."""
    if reading is None:
        return ""
    found = _by_name([e for e in reading.render if e.name], name, lambda e: e.name)
    return found.id if found is not None else ""


def open_system_loopback(reading: Endpoints | None, on_audio: AudioCallback, on_error: ErrorCallback | None = None,
                         output_id: str | None = None) -> _StreamHandle:
    """O som que toca numa saída (o sistema inteiro). F5.3 (A-21): `output_id` é a saída escolhida, casada pelo nome na
    lista de loopback do PyAudioWPatch; None, ou a escolhida que sumiu, é a padrão de console. `reading` é a leitura do
    Core Audio de quem chama (a thread da interface). Um `PyAudio()` novo por abertura, terminado ao parar ou na falha: com
    outro vivo a lista seria a antiga (A-15, quem reabre fecha antes)."""
    import pyaudiowpatch as pyaudio

    target = _loopback_target(reading, output_id)
    pa = pyaudio.PyAudio()
    try:
        dev = _pick_loopback(pa, pyaudio, target[1])
        matched = bool(target[1]) and _names_match(_loopback_base(dev), target[1])
        opened_id = target[0] if matched else _render_id(reading, _loopback_base(dev))
        sr, ch = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
        stream = _loopback_stream(pa, pyaudio, dev, sr, ch, on_audio)
    except Exception:
        pa.terminate()
        raise

    def start() -> None:
        stream.start_stream()
        log.info("loopback do sistema aberto: saída %s (%s), %d Hz, %d canais", log_id(opened_id),
                 "escolhida" if output_id and opened_id == output_id else "padrão", sr, ch)

    def stop() -> None:
        try:
            stream.stop_stream()
            stream.close()
        finally:
            pa.terminate()

    def close() -> None:
        """F5.9 (item 1): a stream que morreu só fecha: o `stop_stream` numa stream já parada levanta e pulava o `close`."""
        try:
            stream.close()
        finally:
            pa.terminate()

    return _StreamHandle(start, stop, sr, ch, on_error, close_fn=close, device_id=opened_id, alive_fn=stream.is_active)


def _loopback_stream(pa: Any, pyaudio: Any, dev: Mapping[str, Any], sr: int, ch: int, on_audio: AudioCallback) -> Any:
    """O stream do loopback, parado, com o callback que só copia e entrega (o carimbo vem do ADC quando há)."""

    def cb(in_data: bytes, frame_count: int, time_info: dict[str, float], _status: int) -> tuple[None, int]:
        now = time.perf_counter()
        adc, cur = time_info.get("input_buffer_adc_time", 0.0), time_info.get("current_time", 0.0)
        lag = (cur - adc) if (adc > 0 and cur >= adc) else frame_count / sr
        arr = np.frombuffer(in_data, dtype=np.float32).reshape(-1, ch).copy()    # float32 nativo do WASAPI (M-11)
        on_audio(arr, now - lag)
        return (None, pyaudio.paContinue)

    return pa.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True, input_device_index=dev["index"],
                   frames_per_buffer=sr // 10, stream_callback=cb, start=False)
