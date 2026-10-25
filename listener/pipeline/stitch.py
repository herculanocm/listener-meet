"""Costura de falantes entre janelas de diarização (F2 item 6, D-F2-4): cada janela nasce com rótulos próprios; entre
janelas consecutivas, cosseno entre centroides e `linear_sum_assignment` maximizando; par aceito com cosseno de `min_cos`
ou mais, ou quando os dois rótulos ocupam o mesmo trecho da sobreposição (o tempo manda); rótulo sem par ganha id global
novo (`SPK_n`). Na sobreposição, cada janela fica com a sua metade e turnos contíguos do mesmo falante são emendados."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..store.profiles import normalize
from .types import DiarizationWindow, Overlaps, Turn

MIN_COS = 0.5
TIME_SHARE = 0.5        # fração do tempo (do rótulo menor) partilhada na sobreposição para "mesmo trecho"
JOIN_GAP_MS = 20


def _shared_time(a: Sequence[Turn], b: Sequence[Turn], lo: int, hi: int) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for ta in a:
        s0, e0 = max(ta.start_ms, lo), min(ta.end_ms, hi)
        if e0 <= s0:
            continue
        for tb in b:
            s, e = max(s0, tb.start_ms), min(e0, tb.end_ms)
            if e > s:
                out[ta.speaker, tb.speaker] = out.get((ta.speaker, tb.speaker), 0) + (e - s)
    return out


def _time_in(turns: Sequence[Turn], lo: int, hi: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in turns:
        d = min(t.end_ms, hi) - max(t.start_ms, lo)
        if d > 0:
            out[t.speaker] = out.get(t.speaker, 0) + d
    return out


def pair_labels(prev: DiarizationWindow, nxt: DiarizationWindow, min_cos: float = MIN_COS) -> dict[str, str]:
    """Rótulo da janela seguinte -> rótulo da anterior, para os pares aceitos."""
    la = sorted({t.speaker for t in prev.turns})
    lb = sorted({t.speaker for t in nxt.turns})
    if not la or not lb:
        return {}
    lo, hi = nxt.t0_ms, prev.t1_ms
    shared = _shared_time(prev.turns, nxt.turns, lo, hi)
    ta, tb = _time_in(prev.turns, lo, hi), _time_in(nxt.turns, lo, hi)
    time_ok = np.zeros((len(la), len(lb)), bool)
    for i, a in enumerate(la):
        for j, b in enumerate(lb):
            s = shared.get((a, b), 0)
            smaller = min(ta.get(a, 0), tb.get(b, 0))
            time_ok[i, j] = s > 0 and s >= TIME_SHARE * smaller
    cos = np.zeros((len(la), len(lb)), np.float32)
    ea, eb = prev.embeddings or {}, nxt.embeddings or {}
    for i, a in enumerate(la):
        for j, b in enumerate(lb):
            if a in ea and b in eb and ea[a].size == eb[b].size and ea[a].size:
                cos[i, j] = float(np.dot(normalize(ea[a]), normalize(eb[b])))
    score = cos + time_ok.astype(np.float32) * 2.0            # o tempo domina o cosseno
    rows, cols = linear_sum_assignment(score, maximize=True)
    out: dict[str, str] = {}
    for i, j in zip(rows.tolist(), cols.tolist(), strict=True):
        if time_ok[i, j] or cos[i, j] >= min_cos:
            out[lb[j]] = la[i]
    return out


def stitch(windows: Sequence[DiarizationWindow], min_cos: float = MIN_COS) -> tuple[tuple[Turn, ...], Overlaps, dict[tuple[int, str], str]]:
    """Turnos globais, sobreposições globais e o mapa (idx da janela, rótulo local) -> `SPK_n`."""
    ordered = sorted(windows, key=lambda w: w.idx)
    mapping: dict[tuple[int, str], str] = {}
    counter = 0
    turns: list[Turn] = []
    overlaps: list[tuple[int, int]] = []
    prev: DiarizationWindow | None = None
    for w in ordered:
        pairs = pair_labels(prev, w, min_cos) if prev is not None else {}
        for label in sorted({t.speaker for t in w.turns}):
            if label in pairs and prev is not None:
                mapping[w.idx, label] = mapping[prev.idx, pairs[label]]
            else:
                mapping[w.idx, label] = f"SPK_{counter}"
                counter += 1
        lo = w.t0_ms if prev is None else (w.t0_ms + prev.t1_ms) // 2       # metade da sobreposição é desta janela
        if prev is not None:
            turns = [t for t in (Turn(x.start_ms, min(x.end_ms, lo), x.speaker) for x in turns) if t.end_ms > t.start_ms]
            overlaps = [(a, min(b, lo)) for a, b in overlaps if min(b, lo) > a]
        for t in w.turns:
            s, e = max(t.start_ms, lo), t.end_ms
            if e > s:
                turns.append(Turn(s, e, mapping[w.idx, t.speaker]))
        overlaps += [(max(a, lo), b) for a, b in w.overlap if b > max(a, lo)]
        prev = w
    return _join(turns), tuple(sorted(set(overlaps))), mapping


def _join(turns: list[Turn]) -> tuple[Turn, ...]:
    """Emenda turnos contíguos do mesmo falante (a costura corta no meio da sobreposição)."""
    out: list[Turn] = []
    for t in sorted(turns, key=lambda t: (t.start_ms, t.end_ms)):
        if out and out[-1].speaker == t.speaker and t.start_ms - out[-1].end_ms <= JOIN_GAP_MS:
            out[-1] = Turn(out[-1].start_ms, max(out[-1].end_ms, t.end_ms), t.speaker)
        else:
            out.append(t)
    return tuple(out)
