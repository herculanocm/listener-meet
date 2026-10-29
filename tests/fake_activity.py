"""F5.7 (L1): o lado do sensor no `FakePlatform` (fora do `fake_platform.py`, que já é grande): a lista de fábrica de
mentira e o `MicWatch` controlável pelo teste, sem registro nem ctypes (roda em qualquer sistema)."""
from __future__ import annotations

import threading

from listener.platform.base import KIND_BROWSER, KIND_MEETING, WatchedApp

FAKE_WATCHED: tuple[WatchedApp, ...] = (
    WatchedApp("ms-teams.exe", "Teams", KIND_MEETING), WatchedApp("zoom.exe", "Zoom", KIND_MEETING),
    WatchedApp("slack.exe", "Slack", KIND_MEETING),
    WatchedApp("chrome.exe", "Chrome", KIND_BROWSER), WatchedApp("msedge.exe", "Edge", KIND_BROWSER),
)


class FakeMicWatch:
    """`change()` simula o indicador mudando: o `wait` seguinte (ou o que está esperando) devolve True uma vez, como o
    aviso do sistema, que junta as mudanças até o rearme. `cancel` acorda quem espera e vale até o fim."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._changes = 0
        self.cancelled = False
        self.closed = False
        self.waits = 0

    def change(self) -> None:
        with self._cond:
            self._changes += 1
            self._cond.notify_all()

    def _ready(self) -> bool:
        return self.cancelled or self.closed or self._changes > 0

    def wait(self, timeout_s: float) -> bool:
        with self._cond:
            self.waits += 1
            self._cond.wait_for(self._ready, timeout_s)
            if self.cancelled or self.closed or not self._changes:
                return False
            self._changes = 0
            return True

    def cancel(self) -> None:
        with self._cond:
            self.cancelled = True
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()
