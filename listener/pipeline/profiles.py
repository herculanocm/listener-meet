"""Casamento cluster <-> perfil (F2 item 7, D-F2-8), puro: `S` (voz x pessoa), `linear_sum_assignment(maximize=True)` sem
repetir pessoa; depois, por cluster: similaridade >= limiar do perfil (ou global) -> `auto`; margem (melhor menos segundo
da linha) abaixo de `margin` -> `ambíguo`, com a lista de participantes esperados como desempate (D-041 P4; não é LLM);
menos de 10 s de fala nunca é `auto` (Q5); abaixo do limiar -> `sem_nome` com a melhor sugestão guardada para a revisão.
Centroide do cluster: média dos embeddings normalizados dos turnos limpos (>= 1,5 s, sem sobreposição), no máximo 20.
F5.15 (item 4): a nota de uma voz para uma pessoa é o maior cosseno entre a voz e as referências dela (`person_scores`),
com uma coluna por pessoa (a margem segue entre pessoas); o limiar de cada célula vem da variante (`threshold_for`) e o
desempate pelos esperados confere o limiar do vencedor (A-10). `load_profile_refs`, a única função que lê o banco, monta as
referências e o limiar das amostras ativas, na carga (A-03), para o worker, o ao vivo e a revisão."""
from __future__ import annotations

import json
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config.defaults import SPECS
from ..log import get_logger
from ..model import ClusterStatus
from ..store.profiles import (THRESHOLD_VARIANT, THRESHOLD_VARIANTS, calibrate, centroid_of, group_samples, normalize, references,
                              threshold_for, unit_rows)
from .types import ClusterData, MatchResult, Overlaps, Turn

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("pipeline.profiles")

CLEAN_TURN_MS = 1500
MAX_TURNS_PER_CLUSTER = 20
MIN_AUTO_SPEECH_MS = 10_000


@dataclass(frozen=True, slots=True)
class ProfileRef:
    profile_id: int
    person_id: int
    refs: np.ndarray                     # (k, d) com as linhas normalizadas; um vetor 1-D vira (1, d)
    threshold: float | None = None       # o da pessoa (calibrado) ou None = global
    name: str = ""
    sizes: tuple[int, ...] = ()          # amostras de cada referência; vazio = desconhecido

    def __post_init__(self) -> None:
        refs = np.asarray(self.refs, dtype=np.float32)
        refs = normalize(refs).reshape(1, -1) if refs.ndim == 1 else unit_rows(refs)
        if self.sizes and len(self.sizes) != refs.shape[0]:
            raise ValueError(f"{len(self.sizes)} tamanhos para {refs.shape[0]} referências")
        object.__setattr__(self, "refs", refs)


def person_scores(v: np.ndarray, profiles: Sequence[ProfileRef]) -> tuple[np.ndarray, np.ndarray]:
    """F5.15 item 4: a nota `(m, P)` de cada voz (`v`, linhas normalizadas) para cada pessoa é o maior cosseno entre a voz e
    as referências dela; o índice `(m, P)` diz qual referência venceu. Uma multiplicação sobre as referências empilhadas;
    cada perfil com ao menos uma referência."""
    m = len(v)
    if not profiles:
        return np.zeros((m, 0), np.float32), np.zeros((m, 0), np.intp)
    counts = [p.refs.shape[0] for p in profiles]
    starts = np.cumsum([0, *counts[:-1]])
    s = v @ np.concatenate([p.refs for p in profiles]).T                       # (m, K): uma coluna por referência
    win = np.stack([np.argmax(s[:, a:a + k], axis=1) for a, k in zip(starts.tolist(), counts, strict=True)], axis=1)
    return np.take_along_axis(s, win + starts, axis=1), win


def load_profile_refs(store: Store, model: str, *, ref_cos: float, max_refs: int | None,
                      person_ids: Collection[int] | None = None) -> list[ProfileRef]:
    """F5.15 (item 3, Comportamento 1): os perfis do modelo com as referências e o limiar calculados das amostras ativas de
    agora, numa consulta (`active_samples`): cada pessoa tem as amostras agrupadas (`group_samples`), uma referência por
    grupo (`references`) e o limiar de `calibrate`, nunca o da coluna (A-03). O perfil sem amostra utilizável fica fora. O
    nome vai só para o desempate pelos esperados; o log leva contagens e ms, nunca o nome (D-053)."""
    t0 = time.perf_counter()
    names = {int(p["id"]): str(p.get("name") or "") for p in store.people.list_people()}
    found = store.profiles.active_samples(model, person_ids)
    out: list[ProfileRef] = []
    for ps in found:
        if not len(ps.vectors):
            continue
        labels = group_samples(ps.vectors, ref_cos, max_refs)
        refs, sizes = references(ps.vectors, labels)
        out.append(ProfileRef(ps.profile_id, ps.person_id, refs, calibrate(ps.vectors, labels).threshold,
                              names.get(ps.person_id, ""), sizes))
    log.debug("perfis de voz (%s): %d pessoa(s), %d referência(s), %d amostra(s) ignorada(s), %.1f ms", model, len(out),
              sum(p.refs.shape[0] for p in out), sum(ps.skipped for ps in found), 1000 * (time.perf_counter() - t0))
    return out


def voice_settings(store: Store) -> tuple[float, int]:
    """`pipeline.ref_cos` e `pipeline.max_refs` de agora (item 8: a revisão lê a cada ação)."""
    return float(store.get(SPECS["pipeline.ref_cos"])), int(store.get(SPECS["pipeline.max_refs"]))


def person_similarity(store: Store, model: str, vector: np.ndarray, person_id: int, ref_cos: float,
                      max_refs: int | None) -> float | None:
    """A nota de uma voz para uma pessoa: o maior cosseno com as referências dela (item 4); None sem perfil utilizável na
    dimensão da voz."""
    v = normalize(vector)
    found = [p for p in load_profile_refs(store, model, ref_cos=ref_cos, max_refs=max_refs, person_ids=[person_id])
             if p.refs.shape[1] == v.size]
    if not found or not np.any(v):
        return None
    return float(person_scores(v.reshape(1, -1), found[:1])[0][0, 0])


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


def expected_person_ids(raw: str | None) -> frozenset[int]:
    """`meetings.expected_people_json` (F3 item 4: participantes casados com `people` no warm-up) -> ids."""
    if not raw:
        return frozenset()
    try:
        data = json.loads(raw)
    except ValueError:
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    return frozenset(int(x) for x in data if isinstance(x, int | float | str) and str(x).lstrip("-").isdigit())


class ProfileMatcher:
    def __init__(self, threshold: float = 0.60, margin: float = 0.05, min_speech_ms: int = MIN_AUTO_SPEECH_MS,
                 expected: Sequence[str] = (), expected_ids: frozenset[int] = frozenset(), variant: str = THRESHOLD_VARIANT):
        if variant not in THRESHOLD_VARIANTS:
            raise ValueError(f"variante de limiar desconhecida: {variant!r} (A, P ou C)")
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.min_speech_ms = int(min_speech_ms)
        self.expected = tuple(n.lower() for n in expected)
        self.expected_ids = frozenset(expected_ids)       # F3 item 4: ids casados no warm-up valem como nome na lista (D-041 P4)
        self.variant = variant                            # F5.15 item 5: A, P ou C

    def match(self, clusters: Sequence[ClusterData], profiles: Sequence[ProfileRef]) -> list[MatchResult]:
        usable = [(i, c) for i, c in enumerate(clusters) if c.centroid is not None and c.centroid.size]
        results: dict[int, MatchResult] = {}
        if usable and profiles:
            dim = usable[0][1].centroid.size  # type: ignore[union-attr]
            prof = [p for p in profiles if p.refs.shape[1] == dim and np.any(p.refs)]
            if prof:
                c_mat = np.stack([normalize(c.centroid) for _, c in usable])          # type: ignore[arg-type]
                s, win = person_scores(c_mat, prof)
                t = self._thresholds(prof, win)
                rows, cols = linear_sum_assignment(s, maximize=True)
                assigned = dict(zip(rows.tolist(), cols.tolist(), strict=True))
                for r, (i, c) in enumerate(usable):
                    taken = {j for rr, j in assigned.items() if rr != r}
                    results[i] = self._decide(c, s[r], t[r], prof, assigned.get(r), taken)
        return [results.get(i, MatchResult(c.label, ClusterStatus.UNNAMED)) for i, c in enumerate(clusters)]

    def _thresholds(self, prof: list[ProfileRef], win: np.ndarray) -> np.ndarray:
        """`T (m, P)`: o limiar de cada voz para cada pessoa, pela variante e pelo tamanho da referência vencedora."""
        t = np.empty(win.shape, np.float64)
        for (r, j), w in np.ndenumerate(win):
            p = prof[j]
            t[r, j] = threshold_for(self.variant, p.threshold, p.sizes[w] if p.sizes else None, self.threshold)
        return t

    def _decide(self, c: ClusterData, row: np.ndarray, t_row: np.ndarray, prof: list[ProfileRef], j: int | None,
                taken: set[int]) -> MatchResult:
        order = np.argsort(-row)
        best_j = int(order[0])
        best, second = float(row[best_j]), (float(row[order[1]]) if len(order) > 1 else 0.0)
        margin = best - second
        if j is None:                                            # mais clusters que perfis: este ficou sem par no LSA
            return MatchResult(c.label, ClusterStatus.UNNAMED, prof[best_j].person_id, prof[best_j].profile_id, best, margin,
                               float(t_row[best_j]))
        p = prof[j]
        sim = float(row[j])
        threshold = float(t_row[j])
        if sim < threshold:
            return MatchResult(c.label, ClusterStatus.UNNAMED, p.person_id, p.profile_id, sim, margin, threshold)
        if j != best_j:                                          # o LSA deu o 2º melhor (o melhor foi para outra voz): revisar (R2-16)
            return MatchResult(c.label, ClusterStatus.AMBIGUOUS, p.person_id, p.profile_id, sim, margin, threshold)
        if margin < self.margin:
            w = self._tie_winner(prof, order, row, t_row, j, taken)
            if w is None:
                return MatchResult(c.label, ClusterStatus.AMBIGUOUS, p.person_id, p.profile_id, sim, margin, threshold)
            p, sim, threshold = prof[w], float(row[w]), float(t_row[w])
        if c.speech_ms < self.min_speech_ms:                     # Q5: curto demais para nomear sozinho
            return MatchResult(c.label, ClusterStatus.AMBIGUOUS, p.person_id, p.profile_id, sim, margin, threshold)
        return MatchResult(c.label, ClusterStatus.AUTO, p.person_id, p.profile_id, sim, margin, threshold)

    def _tie_winner(self, prof: list[ProfileRef], order: np.ndarray, row: np.ndarray, t_row: np.ndarray, j: int,
                    taken: set[int]) -> int | None:
        """A coluna que vence o desempate pelos esperados, ou None (fica ambíguo): nenhum ou os dois na lista, a pessoa já
        dada a outra voz (nunca repetir pessoa) ou o vencedor abaixo do próprio limiar (A-10, como no ao vivo)."""
        winner = self._tie_break(prof[int(order[0])], prof[int(order[1])] if len(order) > 1 else None)
        if winner is None:
            return None
        w = prof.index(winner)
        if (w != j and w in taken) or row[w] < t_row[w]:
            return None
        return w

    def _tie_break(self, a: ProfileRef, b: ProfileRef | None) -> ProfileRef | None:
        """Participantes esperados decidem quando só um dos dois está na lista (P4): pelo id casado no warm-up ou pelo nome."""
        if (not self.expected and not self.expected_ids) or b is None:
            return None
        in_a = a.person_id in self.expected_ids or (bool(a.name) and a.name.lower() in self.expected)
        in_b = b.person_id in self.expected_ids or (bool(b.name) and b.name.lower() in self.expected)
        if in_a != in_b:
            return a if in_a else b
        return None
