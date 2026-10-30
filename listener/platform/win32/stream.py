"""F5.9 (L1a): o `CaptureHandle` do Windows, à parte para que `open_mic` (`platform.py`), o loopback do app
(`app_loopback.py`) e o do sistema (`system_loopback.py`) o importem sem ciclo. `alive_fn` (item 1) é a pergunta "a stream
segue viva?" de quem abriu; sem ele, `alive()` não sabe (None)."""
from __future__ import annotations

from collections.abc import Callable

from ...log import get_logger
from ..base import ErrorCallback

log = get_logger("platform.win32.stream")

type AliveFn = Callable[[], bool | None]


class _StreamHandle:
    """`CaptureHandle`: começa parada; `stop()` é idempotente e silencia o `on_error` de um encerramento nosso. A stream
    que a biblioteca encerrou (dispositivo removido) segue fechável: o `stop()` seguinte a fecha (U-043: aberta, ela
    segurava o PortAudio e a lista de dispositivos não era relida)."""

    def __init__(self, start_fn: Callable[[], None], stop_fn: Callable[[], None], sample_rate: int, channels: int,
                 on_error: ErrorCallback | None = None, close_fn: Callable[[], None] | None = None, device_id: str = "",
                 alive_fn: AliveFn | None = None):
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._close_fn = close_fn                          # a encerrada pela biblioteca só fecha (A-05: `close` já aborta)
        self._alive_fn = alive_fn                          # F5.9 (item 1): quem abriu diz se a stream segue viva
        self.sample_rate = sample_rate
        self.channels = channels
        self.device_id = device_id                         # F5.3 (A-16): o endpoint aberto ("" = não se sabe; app)
        self.on_error = on_error
        self._started = False
        self._stopped = False                              # nós paramos: o fim da stream não é erro
        self._ended = False                                # a biblioteca encerrou a stream (o `on_error` já avisou)

    def start(self) -> None:
        if not self._started and not self._stopped and not self._ended:
            self._started = True
            self._start_fn()

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            try:
                (self._close_fn if self._ended and self._close_fn is not None else self._stop_fn)()
            except Exception as e:
                log.debug("parar stream: %s", e)

    def alive(self) -> bool | None:
        """F5.9 (item 1): a stream segue viva, pelo `alive_fn` de quem abriu? None = não se sabe: sem `alive_fn`, antes do
        `start()`, depois do nosso `stop()`, do fim que a biblioteca já avisou (`finished`) ou quando a pergunta falha. False
        é definitivo: a stream morreu sem aviso, o `stop()` seguinte só fecha (`close_fn`, como a encerrada pela biblioteca)
        e um `finished` tardio não avisa de novo. Na thread da interface, sem bloquear."""
        if not self._started or self._stopped or self._ended or self._alive_fn is None:
            return None
        try:
            answer = self._alive_fn()
        except Exception as e:
            log.debug("prova de vida da stream: %s", e)
            return None
        if answer is False:
            self._ended = True
        return answer

    def finished(self) -> None:
        """Chamado pela biblioteca quando a stream termina: só é erro se não fomos nós que paramos."""
        if not self._stopped and not self._ended and self.on_error is not None:
            self._ended = True
            try:
                self.on_error("stream encerrada pelo sistema (dispositivo removido?)")
            except Exception as e:
                log.debug("on_error: %s", e)
