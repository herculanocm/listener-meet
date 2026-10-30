"""D-067: a saída de áudio portável (sounddevice) para as plataformas sem a escolha pelo endpoint: o fluxo do player e o
teste do warm-up no dispositivo padrão da biblioteca, que é o da última leitura dela (U-043). O Windows abre pelo endpoint
padrão de agora (`platform/win32/output.py`). Quem abre segura `portaudio.LOCK` e registra o stream (`track`)."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..base import OutputStream, OutputUnknownError
from . import portaudio

PLAYER_LATENCY_S = 0.2       # o buffer do fluxo do player: perto dos ~0,18 s do MME medidos sem buraco com carga (R7-02)


def open_default(endpoint_id: str | None = None, device: int | None = None, extra: Any = None) -> OutputStream:
    """O fluxo mono float32 do player em `device` (None = o padrão da biblioteca), na taxa padrão dele. Um `endpoint_id`
    pedido aqui não tem como ser honrado (a biblioteca não sabe dos endpoints): `OutputUnknownError`, e o player segue onde está.
    Sem PortAudio, a importação levanta `OSError` (o player avisa "sem dispositivo de saída")."""
    if endpoint_id:
        raise OutputUnknownError("esta plataforma não abre a saída por endpoint")
    import sounddevice as sd

    with portaudio.LOCK:                                   # U-043: o reinício do PortAudio não cruza a abertura
        info = sd.query_devices(device, kind="output") if device is not None else sd.query_devices(kind="output")
        rate = round(float(info["default_samplerate"]))
        stream = sd.OutputStream(device=device, samplerate=rate, channels=1, dtype="float32",
                                 latency=PLAYER_LATENCY_S if device is not None else "high", extra_settings=extra)
        portaudio.track(stream)
    stream.endpoint_id = ""                                # quem abriu pelo endpoint troca (o Windows)
    return stream


def play(samples: np.ndarray, sample_rate: int, device: int | None = None, extra: Any = None) -> None:
    """O teste de 3 s do warm-up (`sd.play`, sem esperar) em `device` (None = o padrão da biblioteca)."""
    import sounddevice as sd

    kwargs: dict[str, Any] = {} if device is None else {"device": device, "latency": PLAYER_LATENCY_S, "extra_settings": extra}
    with portaudio.LOCK:                                   # U-043: o reinício do PortAudio espera o `sd.play` tocar
        sd.play(samples, sample_rate, **kwargs)
