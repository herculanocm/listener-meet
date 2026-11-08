"""As legendas do vídeo da reunião (F5.19, itens 3 a 5), puro: sem Qt, sem banco e sem threads.

Cada fala da leitura (`Speech`, no tempo da reunião) vira uma ou mais legendas (`Cue`) de até `MAX_CHARS` caracteres,
cortadas entre palavras em partes equilibradas, com o tempo da fala dividido pelo tamanho de cada parte. Os tempos vão
para o do arquivo, que não tem as pausas da gravação (`audio_ms`, D-011). A legenda curta fica pelo menos `MIN_CUE_MS`
na tela, sem passar o começo da seguinte; terminada, fica mais `HOLD_MS` se nada começar antes.

`screens` diz o que a tela mostra em cada intervalo (`Screen`): a legenda atual (a de começo mais recente ainda na tela)
e a anterior a ela, quando terminou até `CONTEXT_MS` antes do começo da atual, com a marca de que ainda fala (falas ao
mesmo tempo, L e R). O vídeo desenha uma tela por troca e a repete entre as trocas."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import accumulate
from math import ceil

from .player_source import audio_ms

MAX_CHARS = 110          # caracteres por legenda: duas linhas da letra grande do vídeo
MIN_CUE_MS = 1200        # a legenda curta fica pelo menos isto, sem passar o começo da seguinte
HOLD_MS = 1500           # terminada, fica mais isto na tela se nada começar antes
CONTEXT_MS = 10_000      # a anterior aparece acima da atual se terminou até isto antes do começo dela


@dataclass(frozen=True, slots=True)
class Speech:
    """Uma fala da leitura, no tempo da reunião: o nome e o cargo do dia (D-016), o texto mostrado e a cor (U-081)."""
    t_start_ms: int
    t_end_ms: int
    name: str
    role: str
    text: str
    color: int = 0


@dataclass(frozen=True, slots=True)
class Cue:
    """Uma legenda no tempo do arquivo (sem as pausas da gravação)."""
    start_ms: int
    end_ms: int
    name: str
    role: str
    text: str
    color: int = 0

    @property
    def speaker(self) -> tuple[str, str, int]:
        return self.name, self.role, self.color


@dataclass(frozen=True, slots=True)
class Screen:
    """O que a tela mostra: a legenda atual e a anterior (índices na lista das legendas; None = nenhuma) e se a anterior
    ainda está sendo falada (falas ao mesmo tempo: ela não apaga)."""
    current: int | None = None
    previous: int | None = None
    previous_live: bool = False


IDLE = Screen()


def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """O texto em partes de até `max_chars` caracteres, cortadas entre palavras e equilibradas (a última não sobra curta);
    uma palavra maior que o limite fica sozinha numa parte (a tela encolhe a letra). Texto vazio: nenhuma parte."""
    words = text.split()
    if not words:
        return []
    total = len(" ".join(words))
    planned = ceil(total / max(1, max_chars))
    target = total / planned
    out: list[str] = []
    current: list[str] = []
    size = 0
    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and (size + extra > max_chars or (size >= target and len(out) < planned - 1)):
            out.append(" ".join(current))
            current, size, extra = [], 0, len(word)
        current.append(word)
        size += extra
    out.append(" ".join(current))
    return out


def _parts(speech: Speech, max_chars: int) -> list[tuple[int, int, str]]:
    """(começo, fim, texto) de cada parte da fala, no tempo da reunião, o tempo dividido pelo tamanho de cada parte."""
    parts = split_text(speech.text, max_chars)
    t0 = int(speech.t_start_ms)
    span = max(0, int(speech.t_end_ms) - t0)
    weights = list(accumulate(len(p) + 1 for p in parts))
    total = weights[-1] if weights else 1
    starts = [0, *weights[:-1]]
    return [(t0 + span * a // total, t0 + span * b // total, p) for a, b, p in zip(starts, weights, parts, strict=True)]


def build_cues(speeches: Iterable[Speech], pauses: Sequence[tuple[int, int | None]], duration_ms: int,
               max_chars: int = MAX_CHARS) -> list[Cue]:
    """As legendas no tempo do arquivo de `duration_ms`, em ordem de começo: as partes de cada fala, os tempos sem as pausas,
    o mínimo de `MIN_CUE_MS` sem passar o começo da seguinte; a que fica sem duração ou fora do arquivo sai."""
    raw: list[Cue] = []
    for s in speeches:
        for a, b, text in _parts(s, max_chars):
            start, end = audio_ms(a, pauses), min(audio_ms(b, pauses), duration_ms)
            if start < duration_ms:
                raw.append(Cue(start, max(start, end), s.name, s.role, text, s.color))
    raw.sort(key=lambda c: (c.start_ms, c.end_ms))
    out: list[Cue] = []
    for i, c in enumerate(raw):
        following = raw[i + 1].start_ms if i + 1 < len(raw) else duration_ms
        end = c.end_ms
        if following > c.start_ms:
            end = max(end, min(c.start_ms + MIN_CUE_MS, following))
        end = min(end, duration_ms)
        if end > c.start_ms:
            out.append(Cue(c.start_ms, end, c.name, c.role, c.text, c.color))
    return out


class ScreenIndex:
    """`screen_at` por busca binária (os começos e o maior fim até cada legenda, como o `playing_row` da Transcrição)."""

    def __init__(self, cues: Sequence[Cue]):
        self.cues = cues
        self.starts = [c.start_ms for c in cues]
        self.reach = list(accumulate((c.end_ms for c in cues), max))

    def screen_at(self, t_ms: int) -> Screen:
        """A tela em `t_ms`: a legenda de começo mais recente ainda na tela (até o fim mais `HOLD_MS`) e a anterior a ela."""
        j = bisect_right(self.starts, t_ms) - 1
        while j >= 0 and self.reach[j] + HOLD_MS > t_ms:
            if self.cues[j].end_ms + HOLD_MS > t_ms:
                return self._with_previous(j, t_ms)
            j -= 1
        return IDLE

    def _with_previous(self, current: int, t_ms: int) -> Screen:
        """A anterior (revisão de arquitetura, A-09): a de começo mais recente antes da atual que ainda fala em `t_ms` (falas
        ao mesmo tempo: não apaga); sem nenhuma, a que terminou por último, até `CONTEXT_MS` antes do começo da atual."""
        j = current - 1
        while j >= 0 and self.reach[j] > t_ms:
            if self.cues[j].end_ms > t_ms:
                return Screen(current, j, True)
            j -= 1
        if current == 0:
            return Screen(current)
        last = current - 1                               # todas as anteriores já terminaram: a de fim mais tarde
        while self.cues[last].end_ms != self.reach[current - 1]:
            last -= 1
        if self.cues[current].start_ms - self.cues[last].end_ms > CONTEXT_MS:
            return Screen(current)
        return Screen(current, last, False)


def screens(cues: Sequence[Cue], duration_ms: int) -> list[tuple[int, Screen]]:
    """As trocas da tela num arquivo de `duration_ms`: (instante, tela) em ordem, a primeira em 0, sem duas telas iguais
    seguidas. Só os começos, os fins e os fins mais `HOLD_MS` podem trocar a tela."""
    if duration_ms <= 0:
        return []
    index = ScreenIndex(cues)
    times = {0}
    for c in cues:
        times.update((c.start_ms, c.end_ms, c.end_ms + HOLD_MS))
    out: list[tuple[int, Screen]] = []
    for t in sorted(x for x in times if 0 <= x < duration_ms):
        screen = index.screen_at(t)
        if not out or out[-1][1] != screen:
            out.append((t, screen))
    return out


__all__ = ["CONTEXT_MS", "HOLD_MS", "IDLE", "MAX_CHARS", "MIN_CUE_MS", "Cue", "Screen", "ScreenIndex", "Speech", "build_cues",
           "screens", "split_text"]
