"""F5.27 (item 11): os tempos da tela gravada no vídeo exportado, puro (sem Qt, sem PyAV e sem banco: a leitura e a
decodificação dos pedaços chegam como funções).

O vídeo segue o áudio sem as pausas (`media_audio.sound_spans`, D-011): `FileClock` leva um instante da sessão ao tempo do
arquivo; um instante dentro de uma pausa vale a retomada (o que estava na tela na pausa aparece na volta, se nada o trocou
antes), e do fim do áudio em diante não há vídeo. `shots` percorre os pedaços em ordem, cada um decodificado uma vez,
quadro a quadro, e diz as trocas da tela no tempo do arquivo: o quadro gravado (`Shot`) vale do instante dele até o
seguinte, e o último do pedaço até o fim dele (`Segment`, F5.27 lote 1); antes do primeiro pedaço, nos buracos entre eles
e depois do último, a tela vale None (a tela escura da F5.19). Um quadro igual ao anterior não é troca. `moments` junta
essas trocas às das legendas (`media_cues.screens`): um quadro do vídeo em cada troca de uma ou de outra (as do mesmo ms
viram uma, a última valendo) e um a cada `REFRESH_MS` entre elas, como a F5.19."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import accumulate
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .media_cues import Screen

REFRESH_MS = 1000                # um quadro por segundo entre as trocas (o mesmo da F5.19)


@dataclass(frozen=True, slots=True, eq=False)
class Shot:
    """Um quadro da tela gravada: `key` é (índice do pedaço, número do quadro nele); `rgb`, a imagem H x W x 3."""
    key: tuple[int, int]
    rgb: np.ndarray = field(repr=False)


type ScreenState = Shot | None
type Read = Callable[[int], bytes | None]
type Decode = Callable[[bytes], Iterator[tuple[int, np.ndarray]]]


class FileClock:
    """Instante da sessão (ms) -> tempo do arquivo (ms), pelos trechos com som (`AudioPlan.spans`, em ordem)."""

    def __init__(self, spans: Sequence[tuple[int, int]]):
        self.spans = spans
        self.ends = [b for _a, b in spans]
        self.offsets = [0, *accumulate(b - a for a, b in spans)]

    @property
    def duration_ms(self) -> int:
        return self.offsets[-1]

    def at(self, t_ms: int) -> int | None:
        """O tempo do arquivo de `t_ms`: dentro de uma pausa (ou antes do primeiro trecho), o começo do trecho seguinte;
        None do fim do último trecho em diante."""
        j = bisect_right(self.ends, t_ms)
        if j >= len(self.spans):
            return None
        a = self.spans[j][0]
        return self.offsets[j] + max(0, t_ms - a)


def _same(a: ScreenState, rgb: np.ndarray) -> bool:
    return a is not None and a.rgb.shape == rgb.shape and bool(np.array_equal(a.rgb, rgb))


def shots(chunks: Sequence[Mapping[str, Any]], read: Read, decode: Decode, clock: FileClock) -> Iterator[tuple[int, ScreenState]]:
    """As trocas da tela no tempo do arquivo, em ordem (tempos que nunca voltam), começando em 0 sem tela; `chunks` são os de
    `store.screen.list_chunks`, em ordem. Um pedaço que já não existe (`read` devolve None) vale um buraco. Para no fim do
    áudio, sem decodificar o resto."""
    last: ScreenState = None
    yield 0, None
    for i, c in enumerate(chunks):
        t0, t1, idx = int(c["t_start_ms"]), int(c["t_end_ms"]), int(c["idx"])
        data = read(idx)
        if data is None:
            f = clock.at(t0)
            if f is None:
                return
            last = None
            yield f, None
            continue
        for n, (rel, rgb) in enumerate(decode(data)):
            f = clock.at(t0 + rel)
            if f is None:
                return
            if not _same(last, rgb):
                last = Shot((idx, n), rgb)
                yield f, last
        following = int(chunks[i + 1]["t_start_ms"]) if i + 1 < len(chunks) else None
        if following is None or following > t1:              # um buraco depois deste pedaço (ou o fim da tela)
            f = clock.at(t1)
            if f is None:
                return
            last = None
            yield f, None


def moments(captions: Sequence[tuple[int, Screen]], screen: Iterator[tuple[int, ScreenState]], duration_ms: int,
            refresh_ms: int = REFRESH_MS) -> Iterator[tuple[int, int, ScreenState]]:
    """(tempo do arquivo, índice da legenda em `captions`, tela) de cada quadro do vídeo, em tempos que só crescem: um em cada
    troca de legenda ou de tela (as do mesmo ms juntas) e um a cada `refresh_ms` depois do último, até `duration_ms`.
    `captions` começa em 0 (`media_cues.screens`)."""
    if not captions:
        return
    ci = 0
    state: ScreenState = None
    pending = next(screen, None)
    last: int | None = None
    while True:
        t_caption = captions[ci + 1][0] if ci + 1 < len(captions) else duration_ms
        t_screen = pending[0] if pending is not None else duration_ms
        t = min(t_caption, t_screen, last + refresh_ms) if last is not None else 0
        if t >= duration_ms:
            return
        while ci + 1 < len(captions) and captions[ci + 1][0] <= t:
            ci += 1
        while pending is not None and pending[0] <= t:
            state = pending[1]
            pending = next(screen, None)
        yield t, ci, state
        last = t


__all__ = ["REFRESH_MS", "FileClock", "ScreenState", "Shot", "moments", "shots"]
