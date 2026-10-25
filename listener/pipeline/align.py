"""Alinhamento palavra <-> turno e montagem de segmentos (F2 item 8, D-F2-7), puro.

Cada palavra entra no turno exclusivo que contém o seu centro; fora de qualquer turno, vai para o mais próximo se a
distância for de até 500 ms, senão fica sem cluster. Segmento = palavras consecutivas do mesmo rótulo, cortado em pausa
acima de 1 s, em 30 s, ou após `.?!` seguido de pausa acima de 400 ms. `merge_words` junta janelas do Whisper
descartando duplicatas da sobreposição pelo centro da palavra (D-F2-5)."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from .types import TranscriptWindow, Turn, Word

SENTENCE_END = (".", "?", "!")


@dataclass(frozen=True, slots=True)
class SegmentDraft:
    label: str | None
    words: tuple[Word, ...]

    @property
    def start_ms(self) -> int:
        return self.words[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.words[-1].end_ms

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words if w.text.strip())

    @property
    def confidence(self) -> float:
        return float(sum(w.prob for w in self.words) / len(self.words)) if self.words else 0.0


def merge_words(windows: Sequence[TranscriptWindow]) -> tuple[Word, ...]:
    """Palavras de todas as janelas em ordem; numa sobreposição, cada palavra fica na metade que lhe pertence."""
    out: list[Word] = []
    ordered = sorted(windows, key=lambda w: w.idx)
    for i, win in enumerate(ordered):
        lo = win.t0_ms + win.overlap_ms // 2 if win.overlap_ms else -1
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        hi = nxt.t0_ms + nxt.overlap_ms // 2 if nxt is not None and nxt.overlap_ms else None
        for w in win.words:
            c = w.center_ms
            if c < lo or (hi is not None and c >= hi):
                continue
            out.append(w)
    out.sort(key=lambda w: (w.start_ms, w.end_ms))
    return tuple(out)


class Segmenter:
    def __init__(self, max_orphan_ms: int = 500, pause_split_ms: int = 1000, max_segment_ms: int = 30_000,
                 sentence_pause_ms: int = 400):
        self.max_orphan_ms = max_orphan_ms
        self.pause_split_ms = pause_split_ms
        self.max_segment_ms = max_segment_ms
        self.sentence_pause_ms = sentence_pause_ms

    def assign(self, words: Sequence[Word], turns: Sequence[Turn]) -> list[tuple[Word, str | None]]:
        """Rótulo do turno para cada palavra (None = fora de qualquer turno a mais de `max_orphan_ms`)."""
        if not turns:
            return [(w, None) for w in words]
        ts = sorted(turns, key=lambda t: (t.start_ms, t.end_ms))
        starts = [t.start_ms for t in ts]
        out: list[tuple[Word, str | None]] = []
        for w in words:
            c = w.center_ms
            k = bisect_right(starts, c) - 1
            label: str | None = None
            if k >= 0 and ts[k].start_ms <= c < ts[k].end_ms:
                label = ts[k].speaker
            else:
                best: tuple[int, str] | None = None
                if k >= 0:
                    best = (c - ts[k].end_ms, ts[k].speaker)
                if k + 1 < len(ts):
                    d = ts[k + 1].start_ms - c
                    if best is None or d < best[0]:
                        best = (d, ts[k + 1].speaker)
                if best is not None and best[0] <= self.max_orphan_ms:
                    label = best[1]
            out.append((w, label))
        return out

    def segments(self, labelled: Sequence[tuple[Word, str | None]]) -> list[SegmentDraft]:
        out: list[SegmentDraft] = []
        cur: list[Word] = []
        cur_label: str | None = None
        for w, label in labelled:
            if cur:
                prev = cur[-1]
                pause = w.start_ms - prev.end_ms
                cut = (label != cur_label or pause > self.pause_split_ms or w.end_ms - cur[0].start_ms > self.max_segment_ms
                       or (prev.text.rstrip().endswith(SENTENCE_END) and pause > self.sentence_pause_ms))
                if cut:
                    out.append(SegmentDraft(cur_label, tuple(cur)))
                    cur = []
            if not cur:
                cur_label = label
            cur.append(w)
        if cur:
            out.append(SegmentDraft(cur_label, tuple(cur)))
        return out

    def run(self, words: Sequence[Word], turns: Sequence[Turn]) -> list[SegmentDraft]:
        return self.segments(self.assign(words, turns))
