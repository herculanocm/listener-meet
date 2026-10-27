"""PortAudio do sounddevice (U-043, F5.2): a biblioteca lê os dispositivos uma vez, ao iniciar, e um fone conectado com o
app aberto só aparece reiniciando-a, o que fecha todo stream aberto. Aqui ficam o registro dos streams que o app abre e o
reinício, que só acontece sem nenhum aberto e na thread da interface (onde o PortAudio nasce: o WASAPI dele usa COM).
Quem abre um stream do sounddevice segura `LOCK` durante a abertura e o registra com `track`."""
from __future__ import annotations

import threading
import weakref
from typing import Any

from ...log import get_logger

log = get_logger("platform.portaudio")

LOCK = threading.RLock()
_streams: weakref.WeakSet[Any] = weakref.WeakSet()


def track[T](stream: T) -> T:
    """Registra um stream do sounddevice aberto pelo app; fechado ou coletado, deixa de contar."""
    with LOCK:
        _streams.add(stream)
    return stream


def open_streams() -> int:
    """Streams do app ainda abertos, mais o do `sd.play` enquanto toca (o teste do warm-up)."""
    with LOCK:
        count = sum(1 for s in list(_streams) if not getattr(s, "closed", True))
    return count + (1 if _play_active() else 0)


def restart() -> bool:
    """Reinicia o PortAudio, que relê os dispositivos. False sem fazer nada fora da thread principal, com stream aberto ou
    quando a biblioteca não volta (o erro vai para o log)."""
    if threading.current_thread() is not threading.main_thread():
        log.debug("reinício do PortAudio pedido fora da thread da interface: ignorado")
        return False
    sd = _sounddevice()
    if sd is None:
        return False
    with LOCK:
        if open_streams():
            return False
        sd.stop()                                          # fecha o stream do `sd.play` que já terminou de tocar
        if getattr(sd, "_initialized", 1):                 # duas falhas antes deixaram o PortAudio parado: só iniciar (A-05)
            try:
                sd._terminate()
            except Exception as e:  # o PortAudio segue como estava: a lista fica a antiga
                log.warning("PortAudio não terminou para reiniciar: %s", e)
                return False
        try:
            sd._initialize()
        except Exception as first:  # raro: uma segunda tentativa; se falhar, o áudio só volta reabrindo o app
            log.warning("PortAudio não reiniciou (%s); tentando de novo", first)
            try:
                sd._initialize()
            except Exception:
                log.exception("PortAudio não reiniciou (o áudio volta reabrindo o app)")
                return False
    return True


def _play_active() -> bool:
    sd = _sounddevice()
    if sd is None:
        return False
    try:
        stream = sd.get_stream()
    except RuntimeError:                                   # nenhum `sd.play` ainda
        return False
    return bool(getattr(stream, "active", False)) and not getattr(stream, "closed", True)


def _sounddevice() -> Any:
    try:
        import sounddevice as sd
    except Exception as e:  # sem PortAudio na máquina
        log.debug("sounddevice indisponível: %s", e)
        return None
    return sd
