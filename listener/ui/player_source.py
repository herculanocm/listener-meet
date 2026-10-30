"""O áudio da reunião para o player (F5 item 10, R7-02 e R7-04), sem Qt e sem threads: as pausas da gravação (D-011), o
tempo de áudio que o ±10 s anda, a mistura de L e R com ganho ½, a posição pelas âncoras e os pedaços FLAC decodificados
uma vez para a alimentadora (`Chunks`). Saiu do `player.py`, que reexporta os nomes."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

from ..capture.audio import decode_flac, resample
from ..model import EventType
from ..store.core import LEGACY_AUDIO_FILE

if TYPE_CHECKING:
    from ..store import Store

GAIN = np.float32(0.5)           # L e R misturados com ganho ½ cada
CHUNKS_KEPT = 3                  # pedaços decodificados guardados por canal (o da janela e os vizinhos)
EMPTY = np.zeros(0, np.float32)
EMPTY.setflags(write=False)


def pause_intervals(events: Iterable[Mapping[str, Any]]) -> list[tuple[int, int | None]]:
    """Intervalos [pausa, retomada) da gravação a partir dos eventos `pause` e `resume` em ordem de tempo (D-011); uma pausa
    sem retomada vai até o fim (None); pausa repetida sem retomada no meio conta a primeira."""
    out: list[tuple[int, int | None]] = []
    start: int | None = None
    for e in sorted((e for e in events if e.get("t_ms") is not None), key=lambda e: int(e["t_ms"])):
        kind, t = str(e.get("type")), int(e["t_ms"])
        if kind == EventType.PAUSE and start is None:
            start = t
        elif kind == EventType.RESUME and start is not None:
            if t > start:
                out.append((start, t))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def skip_paused(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int | None:
    """Onde o som começa a partir de `t_ms`: ele mesmo fora das pausas, o fim da pausa que o contém, None numa pausa que vai
    até o fim da reunião."""
    t = int(t_ms)
    for a, b in intervals:
        if a <= t and (b is None or t < b):
            if b is None:
                return None
            t = b
    return t


def next_pause(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int | None:
    """Começo da próxima pausa depois de `t_ms` (a janela decodificada para ali)."""
    starts = [a for a, _b in intervals if a > t_ms]
    return min(starts) if starts else None


def audio_ms(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int:
    """Tempo de áudio no instante `t_ms` da reunião: ele menos o que as pausas da gravação tomaram antes dele (um instante
    dentro de uma pausa vale o começo dela)."""
    t = int(t_ms)
    paused = 0
    for a, b in sorted(intervals):
        if a >= t:
            break
        paused += (t if b is None else min(b, t)) - a
    return t - paused


def meeting_ms(u_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int:
    """O instante da reunião com `u_ms` de áudio (o inverso de `audio_ms`): sempre fora das pausas, salvo a que vai até o fim
    (não há áudio depois dela: o começo dela)."""
    t = int(u_ms)
    for a, b in sorted(intervals):
        if t < a:
            break
        if b is None:
            return a
        t += b - a
    return t


def seek_target(base_ms: int, delta_ms: int, intervals: Iterable[tuple[int, int | None]], end_ms: int) -> int:
    """O ponto do -10 s ou +10 s em tempo de áudio (R7-04): as pausas da gravação não contam nos dois sentidos. Para trás, um
    alvo dentro de [a, b) vira a - (b - alvo), e assim por diante a cada pausa atravessada; para frente, b + (alvo - a);
    piso 0 e teto no fim do áudio. Antes, o alvo dentro da pausa ia para o fim dela e o -10 s ficava preso ali."""
    spans = list(intervals)
    u = max(0, audio_ms(base_ms, spans) + int(delta_ms))
    return max(0, min(meeting_ms(u, spans), max(0, int(end_ms) - 1)))


def mix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """L e R com ganho ½ cada, no comprimento do maior (o menor completa com silêncio)."""
    n = max(len(left), len(right))
    out = np.zeros(n, np.float32)
    out[:len(left)] += np.asarray(left, np.float32) * GAIN
    out[:len(right)] += np.asarray(right, np.float32) * GAIN
    return out


def position_ms(anchors: Iterable[tuple[int, int]], frames: int, rate: int) -> int:
    """Tempo da reunião no quadro de saída `frames`: a última âncora (quadro, ms) antes dele mais o que andou desde ela."""
    best: tuple[int, int] | None = None
    for f0, t0 in anchors:
        if f0 <= frames and (best is None or f0 >= best[0]):
            best = (f0, t0)
    if best is None or rate <= 0:
        return 0
    return int(best[1] + (frames - best[0]) * 1000 / rate)


def audio_path_missing(store: Store, meeting: Mapping[str, Any]) -> str | None:
    """O arquivo de áudio do mês da reunião quando ele falta na pasta do banco (o `attach` do app criaria um vazio)."""
    name = str(meeting.get("audio_file") or LEGACY_AUDIO_FILE)
    return None if (store.base_dir / name).exists() else name


class Chunks:
    """Os pedaços FLAC de um som para a alimentadora (R7-02): cada um decodificado uma vez (`AudioRepo.get_chunk`) e posto na
    taxa gravada; as janelas saem deles pela posição no tempo, em amostras absolutas (sem deriva entre janelas), com
    silêncio no buraco entre pedaços (como o `decode_range`, F1.1 A17). Guarda os `CHUNKS_KEPT` mais recentes de cada canal;
    a lista dos pedaços é lida uma vez por som (uma gravação em curso ganha pedaços entre um som e outro)."""

    def __init__(self, store: Store, meeting_id: int, in_rate: int):
        self.store = store
        self.meeting_id = meeting_id
        self.in_rate = in_rate
        self._meta: dict[str, list[tuple[int, int, int]]] = {}       # canal -> [(idx, primeira amostra, amostras)]
        self._cache: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()

    def window(self, channel: str, s0: int, s1: int) -> np.ndarray:
        """As amostras [s0, s1) do canal, na taxa gravada."""
        out = np.zeros(max(0, s1 - s0), np.float32)
        for idx, c0, n in self._list(channel):
            if c0 >= s1 or c0 + n <= s0:
                continue
            samples = self._samples(channel, idx)
            a_out, a_in = max(0, c0 - s0), max(0, s0 - c0)
            length = min(len(samples) - a_in, len(out) - a_out)
            if length > 0:
                out[a_out:a_out + length] = samples[a_in:a_in + length]
        return out

    def _list(self, channel: str) -> list[tuple[int, int, int]]:
        meta = self._meta.get(channel)
        if meta is None:
            meta = []
            for c in self.store.list_chunks(self.meeting_id, channel):
                sr = int(c.get("sample_rate") or self.in_rate)
                meta.append((int(c["idx"]), round(int(c["t_start_ms"]) * self.in_rate / 1000), round(int(c["frames"]) * self.in_rate / sr)))
            self._meta[channel] = meta
        return meta

    def _samples(self, channel: str, idx: int) -> np.ndarray:
        key = (channel, idx)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        row = self.store.audio.get_chunk(self.meeting_id, channel, idx)
        samples = EMPTY                                         # o pedaço sumiu (reunião apagada): silêncio
        if row is not None:
            samples, sr = decode_flac(row["flac"])
            if sr != self.in_rate:
                samples = resample(samples, sr, self.in_rate)
        self._cache[key] = samples
        while len(self._cache) > CHUNKS_KEPT * max(1, len(self._meta)):
            self._cache.popitem(last=False)
        return samples


__all__ = ["CHUNKS_KEPT", "EMPTY", "GAIN", "Chunks", "audio_ms", "audio_path_missing", "meeting_ms", "mix", "next_pause",
           "pause_intervals", "position_ms", "seek_target", "skip_paused"]
