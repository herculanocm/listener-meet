"""F5.9 (item 7): um desvio ao vivo que entrega o mesmo bloco a vários consumidores (o cliente ao vivo da F4 e a vigia de
eco), O(1) por bloco: a tupla de consumidores é lida por referência na thread de trabalho e trocada inteira pela thread
da interface. Cada consumidor é O(1); uma falha sobe ao gravador, que conta e loga a primeira (a gravação segue)."""
from __future__ import annotations

import threading

import numpy as np

from .chunker import Tap


class TapFanout:
    def __init__(self) -> None:
        self._by_name: dict[str, Tap] = {}
        self._taps: tuple[Tap, ...] = ()
        self._lock = threading.Lock()

    def set(self, name: str, fn: Tap | None) -> None:
        """Liga (ou desliga, com None) o consumidor `name`; vale a partir do próximo bloco."""
        with self._lock:
            if fn is None:
                self._by_name.pop(name, None)
            else:
                self._by_name[name] = fn
            self._taps = tuple(self._by_name.values())

    @property
    def empty(self) -> bool:
        return not self._taps

    def __call__(self, channel: str, start_frame: int, rate: int, block: np.ndarray, flags: int) -> None:
        for fn in self._taps:
            fn(channel, start_frame, rate, block, flags)


__all__ = ["TapFanout"]
