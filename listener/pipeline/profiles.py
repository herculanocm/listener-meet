"""Casamento cluster <-> perfil (F2 item 7, D-F2-8), puro: `S = C @ P.T`, `linear_sum_assignment(maximize=True)` sem
repetir pessoa; depois, por cluster: similaridade >= limiar do perfil (ou global) -> `auto`; margem (melhor menos segundo
da linha) abaixo de `margin` -> `ambíguo`, com a lista de participantes esperados como desempate (D-041 P4; não é LLM);
menos de 10 s de fala nunca é `auto` (Q5); abaixo do limiar -> `sem_nome` com a melhor sugestão guardada para a revisão.
Centroide do cluster: média dos embeddings normalizados dos turnos limpos (>= 1,5 s, sem sobreposição), no máximo 20."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..model import ClusterStatus
from ..store.profiles import centroid_of, normalize
from .types import ClusterData, MatchResult, Overlaps, Turn

CLEAN_TURN_MS = 1500
MAX_TURNS_PER_CLUSTER = 20
MIN_AUTO_SPEECH_MS = 10_000


@dataclass(frozen=True, slots=True)
class ProfileRef:
    profile_id: int
    person_id: int
    centroid: np.ndarray
    threshold: float | None = None       # calibrado (Q3) ou None = global
    name: str = ""


def clean_turns(turns: Sequence[Turn], overlap: Overlaps, min_ms: int = CLEAN_TURN_MS,
                limit: int = MAX_TURNS_PER_CLUSTER) -> dict[str, list[Turn]]:
    """Por rótulo, os turnos mais longos sem sobreposição e com pelo menos `min_ms`: os que valem um embedding."""
    out: dict[str, list[Turn]] = {}
    for t in turns:
        if t.duration_ms < min_ms or any(a < t.end_ms and b > t.start_ms for a, b in overlap):
            continue
        out.setdefault(t.speaker, []).append(t)
    return {k: sorted(v, key=lambda t: -t.duration_ms)[:limit] for k, v in out.items()}


def cluster_data(turns: Sequence[Turn], embeddings: dict[str, list[np.ndarray]]) -> list[ClusterData]:
    speech: dict[str, int] = {}
    count: dict[str, int] = {}
    for t in turns:
        speech[t.speaker] = speech.get(t.speaker, 0) + t.duration_ms
        count[t.speaker] = count.get(t.speaker, 0) + 1
    return [ClusterData(label, speech[label], centroid_of(embeddings.get(label, [])), count[label])
            for label in sorted(speech, key=lambda k: -speech[k])]


def expected_names(text: str | None) -> tuple[str, ...]:
    """'Ana, Bia; Carlos' -> ('ana', 'bia', 'carlos') (F2A-14)."""
    if not text:
        return ()
    return tuple(n.strip().lower() for n in text.replace(";", ",").split(",") if n.strip())


class ProfileMatcher:
    def __init__(self, threshold: float = 0.60, margin: float = 0.05, min_speech_ms: int = MIN_AUTO_SPEECH_MS,
                 expected: Sequence[str] = ()):
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.min_speech_ms = int(min_speech_ms)
        self.expected = tuple(n.lower() for n in expected)

    def match(self, clusters: Sequence[ClusterData], profiles: Sequence[ProfileRef]) -> list[MatchResult]:
        usable = [(i, c) for i, c in enumerate(clusters) if c.centroid is not None and c.centroid.size]
        results: dict[int, MatchResult] = {}
        if usable and profiles:
            dim = usable[0][1].centroid.size  # type: ignore[union-attr]
            prof = [p for p in profiles if p.centroid.size == dim and np.any(p.centroid)]
            if prof:
                c_mat = np.stack([normalize(c.centroid) for _, c in usable])          # type: ignore[arg-type]
                p_mat = np.stack([normalize(p.centroid) for p in prof])
                s = c_mat @ p_mat.T
                rows, cols = linear_sum_assignment(s, maximize=True)
                assigned = dict(zip(rows.tolist(), cols.tolist(), strict=True))
                for r, (i, c) in enumerate(usable):
                    results[i] = self._decide(c, s[r], prof, assigned.get(r))
        return [results.get(i, MatchResult(c.label, ClusterStatus.UNNAMED)) for i, c in enumerate(clusters)]

    def _decide(self, c: ClusterData, row: np.ndarray, prof: list[ProfileRef], j: int | None) -> MatchResult:
        order = np.argsort(-row)
        best_j = int(order[0])
        best, second = float(row[best_j]), (float(row[order[1]]) if len(order) > 1 else 0.0)
        margin = best - second
        if j is None:                                            # mais clusters que perfis: este ficou sem par no LSA
            return MatchResult(c.label, ClusterStatus.UNNAMED, prof[best_j].person_id, prof[best_j].profile_id, best, margin,
                               prof[best_j].threshold or self.threshold)
        p = prof[j]
        sim = float(row[j])
        threshold = p.threshold if p.threshold is not None else self.threshold
        if sim < threshold:
            return MatchResult(c.label, ClusterStatus.UNNAMED, p.person_id, p.profile_id, sim, margin, threshold)
        if margin < self.margin:
            winner = self._tie_break(prof[best_j], prof[int(order[1])] if len(order) > 1 else None)
            if winner is None:
                return MatchResult(c.label, ClusterStatus.AMBIGUOUS, p.person_id, p.profile_id, sim, margin, threshold)
            p, sim = winner, float(row[prof.index(winner)])
        if c.speech_ms < self.min_speech_ms:                     # Q5: curto demais para nomear sozinho
            return MatchResult(c.label, ClusterStatus.AMBIGUOUS, p.person_id, p.profile_id, sim, margin, threshold)
        return MatchResult(c.label, ClusterStatus.AUTO, p.person_id, p.profile_id, sim, margin, threshold)

    def _tie_break(self, a: ProfileRef, b: ProfileRef | None) -> ProfileRef | None:
        """Participantes esperados decidem quando só um dos dois nomes está na lista (P4)."""
        if not self.expected or b is None:
            return None
        in_a = bool(a.name) and a.name.lower() in self.expected
        in_b = bool(b.name) and b.name.lower() in self.expected
        if in_a != in_b:
            return a if in_a else b
        return None
