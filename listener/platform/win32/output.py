"""D-067 (o defeito de 29/10: o player seguiu no alto-falante depois de o dono trocar o padrão do Windows para o fone): o
aparelho padrão de agora no `Win32Platform`, herdado por ele (o `platform.py` não cresce).

- O PortAudio lê a lista e o padrão uma vez, ao iniciar (U-043), e o MME abre um aparelho fixo: o padrão dele fica velho.
  Aqui o padrão é sempre o do Core Audio de agora (`endpoints.default_id`, papel console, o mesmo do loopback que
  acompanha), achado na lista WASAPI da leitura atual do PortAudio.
- R-02: o índice sai do id do endpoint. Depois de cada reinício do PortAudio, na thread em que ele reiniciou (a da
  interface), `remember_wasapi_ids` guarda {índice: id} pelo `PaWasapi_GetIMMDevice` da DLL do sounddevice: no PortAudio
  19.7 a função devolve o IMMDevice que ele mesmo guarda desde o `Pa_Initialize`, sem `AddRef`, e o solta no
  `Pa_Terminate`; aqui só se chama `GetId` e se copia o texto (nunca `Release`), sob o `portaudio.LOCK`. Sem o mapa (antes
  do primeiro reinício), pelo nome amigável igual (o do WASAPI é o do Core Audio; o MME corta em 31 caracteres), e só
  quando um só aparelho tem o nome: dois iguais é `LookupError` (o player avisa, e a releitura monta o mapa).
- O player abre no WASAPI compartilhado, mono float32 na taxa do mix, com `auto_convert` e 0,2 s de buffer (o MME tinha
  ~0,18 s). Sem WASAPI, ou sem como identificar o padrão (R-11), o padrão da biblioteca, calado (`OutputUnknownError`).
- R-08: o COM é iniciado uma vez por thread (`endpoints.com_init`) e nunca desfeito; os objetos COM de cada chamada nascem
  e são soltos nela, na thread de quem chama (a escritora do player abre e lê na dela). O IMMDevice do PortAudio, criado na
  thread da interface, é usado pelo próprio PortAudio ao abrir na escritora: risco aceito (D-067).
- O microfone padrão (`default_input_device`) também vem do Core Audio de agora, pelo id na lista.
- Log só com o id do endpoint (`log_id`), nunca o nome do aparelho (A-22)."""
from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from ...log import get_logger
from ..base import AudioDevice, OutputStream, OutputUnknownError, log_id
from ..common import output, portaudio
from . import endpoints

log = get_logger("platform.win32.output")

WASAPI = "WASAPI"
GET_ID_SLOT = 5                  # IMMDevice: QueryInterface, AddRef, Release, Activate, OpenPropertyStore, GetId
ABSENT, AMBIGUOUS, UNKNOWN = "fora da lista", "com o nome repetido na lista", "sem como identificar"


def read_default(flow: int) -> str | None:
    """O endpoint padrão de agora do fluxo (papel console); None quando não há ou o Core Audio não responde."""
    try:
        return endpoints.default_id(flow) or None
    except Exception as e:  # sem pycaw ou o Core Audio fora: ninguém acompanha nesta leitura
        log.debug("padrão do Core Audio: %s", e)
        return None


def _wasapi(sd: Any) -> Any:
    return next((api for api in sd.query_hostapis() if WASAPI in api["name"]), None)


def wasapi_ids(sd: Any) -> dict[int, str]:
    """(sob `portaudio.LOCK`, na thread em que o PortAudio iniciou) {índice: id do endpoint} de todo dispositivo WASAPI
    desta leitura; vazio sem WASAPI ou sem a função na DLL."""
    if sys.platform != "win32":
        return {}
    api = _wasapi(sd)
    if api is None:
        return {}
    try:
        fn = ctypes.CDLL(sd._libname).PaWasapi_GetIMMDevice
    except (AttributeError, OSError) as e:
        log.debug("PaWasapi_GetIMMDevice indisponível: %s", e)
        return {}
    fn.argtypes, fn.restype = (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)), ctypes.c_int
    get_id = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    free = ctypes.windll.ole32.CoTaskMemFree
    out: dict[int, str] = {}
    for idx in api["devices"]:
        dev = ctypes.c_void_p()
        if fn(int(idx), ctypes.byref(dev)) != 0 or not dev.value:
            continue
        vtable = ctypes.cast(dev.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        text = ctypes.c_void_p()
        if get_id(vtable[GET_ID_SLOT])(dev.value, ctypes.byref(text)) == 0 and text.value:
            out[int(idx)] = ctypes.wstring_at(text.value)
            free(text)
    return out


def wasapi_matches(sd: Any, name: str, *, output_side: bool = True) -> list[int]:
    """(sob `portaudio.LOCK`) Os índices WASAPI desta leitura com o nome igual (R-02: o prefixo era para o MME)."""
    api = _wasapi(sd) if name else None
    if api is None:
        return []
    key, low = ("max_output_channels" if output_side else "max_input_channels"), name.strip().lower()
    return [int(i) for i in api["devices"] if (info := sd.query_devices(i))[key] > 0 and str(info["name"]).strip().lower() == low]


def wasapi_default_output(sd: Any) -> int | None:
    """(sob `portaudio.LOCK`) A saída padrão WASAPI da leitura do PortAudio (a reserva: a de quando ele leu)."""
    api = _wasapi(sd)
    idx = int(api["default_output_device"]) if api is not None else -1
    return idx if idx >= 0 else None


class Win32Output:
    """D-067: o padrão de agora no `Win32Platform`. `default_output_id`, `default_input_id`, `open_output`, `play_sound` e
    `default_input_device` em qualquer thread; `remember_wasapi_ids` só na da interface, logo depois do reinício."""

    list_input_devices: Callable[[], list[AudioDevice]]   # do `Win32Platform`
    _wasapi_map: tuple[int, dict[int, str]] | None = None  # (leitura do PortAudio, {índice: id})

    def default_output_id(self) -> str | None:
        return read_default(endpoints.FLOW_RENDER)

    def default_input_id(self) -> str | None:
        return read_default(endpoints.FLOW_CAPTURE)

    def remember_wasapi_ids(self) -> None:
        """R-02: o mapa {índice: id} desta leitura, na thread em que o PortAudio acabou de reiniciar."""
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            import sounddevice as sd

            with portaudio.LOCK:
                self._wasapi_map = (portaudio.generation(), wasapi_ids(sd))
        except Exception as e:  # sem o mapa vale o nome
            log.debug("ids dos dispositivos WASAPI: %s", e)

    def wasapi_ids_now(self) -> dict[int, str] | None:
        """O mapa {índice: id} se é desta leitura do PortAudio; None quando não há (aí vale o nome)."""
        known = self._wasapi_map
        return known[1] if known is not None and known[0] == portaudio.generation() and known[1] else None

    def _locate(self, sd: Any, endpoint_id: str) -> tuple[int | None, str]:
        """(sob `portaudio.LOCK`) (índice WASAPI desta leitura, "") ou (None, o motivo: fora da lista, ambíguo, sem como)."""
        if not endpoint_id or _wasapi(sd) is None:
            return None, UNKNOWN
        ids = self.wasapi_ids_now()
        if ids is not None:
            idx = next((i for i, eid in ids.items() if eid == endpoint_id), None)
            return idx, "" if idx is not None else ABSENT
        name = endpoints.name_of(endpoint_id)
        if not name:
            return None, UNKNOWN
        found = wasapi_matches(sd, name)
        return (found[0], "") if len(found) == 1 else (None, AMBIGUOUS if found else ABSENT)

    def open_output(self, endpoint_id: str | None = None) -> OutputStream:
        """O fluxo do player: o endpoint pedido (fora da lista ou ambíguo, `LookupError`; sem como saber, `OutputUnknownError`)
        ou, sem pedido, o padrão de agora; fora da lista, a saída padrão WASAPI da leitura, com o `endpoint_id` vazio (o
        player sabe que não está no padrão)."""
        import sounddevice as sd

        wanted = endpoint_id or read_default(endpoints.FLOW_RENDER) or ""
        with portaudio.LOCK:                               # U-043: o reinício do PortAudio não cruza a abertura
            idx, problem = self._locate(sd, wanted)
            if idx is None and endpoint_id:
                error = OutputUnknownError if problem == UNKNOWN else LookupError
                raise error(f"a saída {log_id(endpoint_id)} está {problem} do sistema de áudio")
            device = idx if idx is not None else wasapi_default_output(sd)
            extra = sd.WasapiSettings(auto_convert=True) if device is not None else None
            stream = output.open_default(device=device, extra=extra)
        stream.endpoint_id = wanted if idx is not None else ""     # o sounddevice não sabe do endpoint: vem daqui
        if idx is not None:
            log.info("player: saída aberta no endpoint %s (%s), %d Hz", log_id(wanted), "pedido" if endpoint_id else "padrão",
                     round(float(stream.samplerate)))
        else:
            log.warning("player: o padrão %s está %s; o som sai na saída padrão da última leitura", log_id(wanted), problem)
        return stream

    def play_sound(self, samples: np.ndarray, sample_rate: int, endpoint_id: str | None = None) -> None:
        """O teste de 3 s do warm-up (sem esperar) na saída pedida (R-04: a escolha explícita do usuário) ou no padrão de
        agora; fora da lista, na saída padrão WASAPI da leitura (R-12), como o player."""
        import sounddevice as sd

        default = read_default(endpoints.FLOW_RENDER) or ""
        with portaudio.LOCK:
            idx, chosen = (self._locate(sd, endpoint_id)[0], endpoint_id) if endpoint_id else (None, "")
            if idx is None:
                idx, chosen = self._locate(sd, default)[0], default
            device = idx if idx is not None else wasapi_default_output(sd)
            output.play(samples, sample_rate, device=device, extra=sd.WasapiSettings(auto_convert=True) if device is not None else None)
        log.info("teste do warm-up tocando no endpoint %s", log_id(chosen) if idx is not None else "padrão da última leitura")

    def default_input_device(self) -> AudioDevice | None:
        """O microfone padrão do Windows agora (o Core Audio, pelo id na lista: o padrão do PortAudio é o da última leitura
        dele); fora da lista, o padrão WASAPI da leitura; sem ele, o primeiro."""
        devs = self.list_input_devices()
        if not devs:
            return None
        now = read_default(endpoints.FLOW_CAPTURE)
        found = next((d for d in devs if now and d.id == now), None)
        return found or _portaudio_default_input(devs) or devs[0]


def _portaudio_default_input(devs: list[AudioDevice]) -> AudioDevice | None:
    import sounddevice as sd

    try:
        with portaudio.LOCK:
            api = _wasapi(sd)
            idx = int(api["default_input_device"]) if api is not None else -1
    except Exception as e:
        log.debug("dispositivo padrão WASAPI: %s", e)
        return None
    return next((d for d in devs if d.index == idx), None)


__all__ = ["Win32Output", "read_default", "wasapi_default_output", "wasapi_ids", "wasapi_matches"]
