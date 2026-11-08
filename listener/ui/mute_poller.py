"""O leitor do mudo do sistema da gravação (F1: o mudo global; D-072: a saída o abandona preso no COM), saído do
`ui/controller.py` na F5.20 para o controlador não crescer (A-12): uma `QThread` sem pai que lê o mudo a cada 2 s fora da
thread do Qt, porque o COM pode engasgar no dock e no undock do notebook."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QThread, Signal

from ..log import get_logger

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.controller")
MUTE_POLL_S = 2.0


class MutePoller(QThread):
    """Lê o mudo do sistema a cada 2 s fora da thread do Qt (COM pode engasgar no dock/undock)."""
    changed = Signal(object)

    def __init__(self, platform: Platform):
        super().__init__(None)
        self.platform = platform
        self._wake = threading.Event()

    def interrupt(self) -> None:
        self.requestInterruption()
        self._wake.set()

    @override
    def run(self) -> None:
        mute = self.platform.mic_mute()
        while not self.isInterruptionRequested():
            try:
                self.changed.emit(mute.get())
            except Exception as e:
                log.debug("ler mudo do sistema: %s", e)
            self._wake.wait(MUTE_POLL_S)


__all__ = ["MUTE_POLL_S", "MutePoller"]
