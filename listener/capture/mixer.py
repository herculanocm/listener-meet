"""Soma de capturas do mesmo motor de áudio (D-055). O loopback de processo do Windows pega o processo alvo e os filhos
diretos, não os netos (medido em 26/10): o app cujo som sai de um neto (o WebView2 da chamada no Teams novo, neto do
ms-teams.exe raiz) precisa da captura da raiz somada à do neto. As fontes têm a mesma taxa e os mesmos canais e entregam
quadros contínuos, zeros quando o processo está mudo. A primeira (a raiz) dita o ritmo: cada bloco dela sai somado ao que
as outras já entregaram, na ordem, com o horário dela; uma fonte que se adianta guarda no máximo `max_lag_s` (o excesso,
o mais antigo, cai). Sem Windows nem biblioteca de áudio: só numpy."""
from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

type AudioCallback = Callable[[np.ndarray, float], None]


class FrameMixer:
    def __init__(self, sources: int, sample_rate: int, channels: int, on_audio: AudioCallback, max_lag_s: float = 0.5):
        self.sample_rate = sample_rate
        self.channels = channels
        self._on_audio = on_audio
        self._max_lag = max(1, int(sample_rate * max_lag_s))
        self._pending = [np.zeros((0, channels), np.float32) for _ in range(max(0, sources - 1))]
        self._lock = threading.Lock()

    def feed(self, source: int, block: np.ndarray, t: float) -> None:
        """Um bloco da fonte `source` (0 = a principal), começando em `t` (perf_counter)."""
        frames = np.asarray(block, dtype=np.float32).reshape(-1, self.channels)
        with self._lock:
            if source > 0:
                pending = np.concatenate([self._pending[source - 1], frames])
                self._pending[source - 1] = pending[-self._max_lag:]
                return
            out = frames.copy()
            for i, pending in enumerate(self._pending):
                k = min(len(out), len(pending))
                if k:
                    out[:k] += pending[:k]
                    self._pending[i] = pending[k:]
            np.clip(out, -1.0, 1.0, out=out)
            self._on_audio(out, t)


__all__ = ["FrameMixer"]
