"""F5.9 (L1a): o mudo do microfone pelo Core Audio (pycaw, COM), fora de `platform.py`. `_Win32MicMute` lembra a busca do
endpoint e refaz o ponteiro quando a thread muda (M-07); a troca de endpoint é atômica sob trava (F5.3, A-18); o padrão é o
da escolha central (D-067); log com o id do endpoint, nunca o nome do aparelho (A-22)."""
from __future__ import annotations

import threading
import time
from typing import Any, override

from ...log import get_logger
from ..base import AudioDevice, MicMute, log_id
from . import endpoints
from .endpoints import com_init as _com_init, names_match as _names_match

log = get_logger("platform.win32.mute")

MUTE_RETRY_S = 30.0            # após falhar a busca do endpoint do mudo, só tenta de novo depois disto


def _endpoint_volume(dev: Any) -> Any:
    ep = getattr(dev, "EndpointVolume", None)
    if ep is not None:
        return ep
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    imm = getattr(dev, "_dev", dev)
    iface = imm.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    try:
        return iface.QueryInterface(IAudioEndpointVolume)
    except Exception:
        from ctypes import POINTER, cast

        return cast(iface, POINTER(IAudioEndpointVolume))


class _Win32MicMute(MicMute):
    """Muta o endpoint de captura escolhido no warm-up (F1.1 A2); sem ele, o microfone padrão do Windows.
    Lembra o resultado da busca (não re-enumera a cada `get()`), espera 30 s após uma falha e refaz o ponteiro COM
    quando é usado por outra thread (M-07). F5.3 (A-18): uma trava cobre a troca de endpoint, `get` e `set` (o poller lê
    em outra thread e nunca vê a troca pela metade); `resolved` é o id do endpoint achado, nunca o nome (A-22)."""

    def __init__(self) -> None:
        self._device: AudioDevice | None = None
        self._ep: Any = None
        self._ep_thread: int | None = None
        self._lookup_done = False
        self._retry_after = 0.0
        self._lock = threading.RLock()
        self.resolved: str | None = None

    @override
    def set_device(self, device: AudioDevice | None, muted: bool = False) -> bool:
        """Troca o endpoint de uma vez: com `muted` (a gravação está muda), o antigo perde o mudo e o novo ganha (A-18)."""
        with self._lock:
            if muted:
                self.set(False)                            # o antigo: o endpoint que o mudo usa agora
            self._device, self._ep, self._ep_thread, self.resolved = device, None, None, None
            self._lookup_done, self._retry_after = False, 0.0
            found = device is None or self._lookup() is not None
            if muted:
                self.set(True)
            return found

    def _lookup(self) -> Any:
        """Procura o endpoint do dispositivo escolhido uma vez (ou após o backoff); guarda o resultado."""
        if self._device is None:
            return None
        if self._lookup_done and (self._ep is not None or time.monotonic() < self._retry_after):
            return self._ep
        self._lookup_done = True
        for name, dev_id, dev in endpoints.capture_endpoints():
            if dev_id == self._device.id or _names_match(name, self._device.name):
                try:
                    ep = _endpoint_volume(dev)
                except Exception as e:
                    log.debug("EndpointVolume de %s: %s", log_id(dev_id), e)
                else:
                    self._ep, self.resolved, self._ep_thread = ep, dev_id, threading.get_ident()
                    return ep
        self._retry_after = time.monotonic() + MUTE_RETRY_S
        log.warning("endpoint de captura %s não encontrado; o mudo global vai no microfone padrão", log_id(self._device.id))
        return None

    def _default(self) -> Any:
        """D-067: o microfone padrão de agora pela escolha central (papel console, como o gravador; o `GetMicrophone` do
        pycaw é o multimídia)."""
        mic = endpoints.device(endpoints.default_id(endpoints.FLOW_CAPTURE))
        return _endpoint_volume(mic) if mic is not None else None

    def _endpoint(self) -> Any:
        if self._ep is not None and self._ep_thread != threading.get_ident():
            _com_init()
            self._ep, self._lookup_done = None, False      # ponteiro COM de outra thread: refaz nesta
        ep = self._lookup() if self._device is not None else None
        return ep if ep is not None else self._default()

    @override
    def get(self) -> bool | None:
        with self._lock:
            try:
                ep = self._endpoint()
                return bool(ep.GetMute()) if ep is not None else None
            except Exception as e:
                log.debug("ler mudo: %s", e)
                self._ep, self._retry_after = None, time.monotonic() + MUTE_RETRY_S
                return None

    @override
    def set(self, muted: bool) -> bool:
        with self._lock:
            try:
                ep = self._endpoint()
                if ep is None:
                    return False
                ep.SetMute(1 if muted else 0, None)
            except Exception as e:
                log.warning("mutar microfone: %s", e)
                self._ep, self._retry_after = None, time.monotonic() + MUTE_RETRY_S
                return False
            else:
                return True
