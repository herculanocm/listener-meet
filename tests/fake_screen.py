"""F5.27 (lote 2): a tela de mentira do filho `--screen`. `FakeScreenSource` é a do processo de verdade com
`LISTENER_SCREEN=fake` (um monitor pequeno e um gradiente que muda a cada quadro); `MutableScreen` é a dos testes em processo,
com os monitores que o teste liga e desliga e os prints contados."""
from __future__ import annotations

import threading

import numpy as np

from listener.platform.screen_types import Frame, Monitor, ScreenRect

SMALL = Monitor("FAKE1", "Monitor 1 · 64 x 48 · principal", ScreenRect(0, 0, 64, 48), True)
WIDE = Monitor("FAKE2", "Monitor 2 · 96 x 54", ScreenRect(64, 0, 96, 54), False)


def gradient(rect: ScreenRect, seed: int) -> Frame:
    h, w = rect.height, rect.width
    img = np.empty((h, w, 4), dtype=np.uint8)
    img[..., 0] = (np.arange(w, dtype=np.uint32) * 255 // max(1, w - 1)).astype(np.uint8)[None, :]
    img[..., 1] = (np.arange(h, dtype=np.uint32) * 255 // max(1, h - 1)).astype(np.uint8)[:, None]
    img[..., 2] = (seed * 37) % 256
    img[..., 3] = 255
    return Frame(img.tobytes(), w, h, w * 4)


class MutableScreen:
    """Os monitores (o teste troca `present`) e o print, que falha com `fail`; thread-safe para leitura."""

    def __init__(self, *monitors: Monitor):
        self.all = monitors or (SMALL, WIDE)
        self.present = {m.key for m in self.all}
        self.fail = False
        self.grabs: list[ScreenRect] = []
        self._lock = threading.Lock()

    def monitors(self) -> tuple[Monitor, ...]:
        with self._lock:
            return tuple(m for m in self.all if m.key in self.present)

    def unplug(self, key: str) -> None:
        with self._lock:
            self.present.discard(key)

    def plug(self, key: str) -> None:
        with self._lock:
            self.present.add(key)

    def grab_screen(self, rect: ScreenRect) -> Frame | None:
        self.grabs.append(rect)
        return None if self.fail else gradient(rect, len(self.grabs))


class FakeScreenSource(MutableScreen):
    """O que o `main` do filho pede à plataforma, sem BitBlt nem prioridade."""

    def __init__(self) -> None:
        super().__init__(SMALL)

    def lower_own_priority(self) -> bool:
        return True

    def use_physical_pixels(self) -> bool:
        return True

    def sync_stdout_handle(self) -> None:
        pass
