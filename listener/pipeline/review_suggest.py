"""A revisão pela voz depois das referências (F5.15 L2), fora do `review.py`, que está no teto de linhas: a nota de uma voz
para uma pessoa (`similarity_to`), as sugestões das pendentes refeitas a cada decisão que muda um perfil (item 9, U-137), a
ordem pela voz do "Outra pessoa…" (`voice_ranking`, U-136) e a previsão da linha do U-101 (`learn_hints`, U-138). Tudo pelas
referências e com as configurações de agora (`voice_settings`); sem Qt.

Sugestões refeitas (`refresh_suggestions`), na transação de quem chama: só as pendentes da reunião (`ambíguo` e `sem_nome`,
não mescladas, fora o L do usuário, com vetor e modelo). b = a pessoa de maior nota, sem o um para um (pergunta 2); a margem
é a nota de b menos a da segunda pessoa. (a) Se a sugestão de agora é uma pessoa que mudou: com nota de b de 0,45 ou mais
(`FLOOR`, pergunta 4), vira b; abaixo disso, fica a mesma pessoa, com a nota nova dela e a margem contra a melhor outra (sem
perfil utilizável, nota e margem nulas). (b) Senão, se b é uma pessoa que mudou e tem nota de 0,45 ou mais, vira b. (c) Senão,
nada muda. Grava só `person_id`, `similarity` e `margin`; nunca `status`, `decision_source` nem `llm_*` (CP11, R2-07)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ..log import get_logger
from ..model import ClusterStatus
from ..store.profiles import THRESHOLD_LOW, group_samples, normalize
from .profiles import ProfileRef, load_profile_refs, person_scores, person_similarity, voice_settings
from .runner import USER_CLUSTER

if TYPE_CHECKING:
    from ..store import Store
    from ..store.segments import ClusterRow
    from .review import ReviewItem

log = get_logger("pipeline.review_suggest")

FLOOR = THRESHOLD_LOW                 # pergunta 4: abaixo disso, a sugestão refeita não troca de pessoa
PENDING = (str(ClusterStatus.AMBIGUOUS), str(ClusterStatus.UNNAMED))
type Suggestion = tuple[int, float | None, float | None]     # (pessoa, nota, margem)


@dataclass(frozen=True, slots=True)
class LearnHint:
    """U-138: o que a confirmação faz com o perfil da pessoa. `new_ref`: a voz fica sozinha num grupo (uma referência nova);
    `total`: as referências depois do teto (na voz confirmada que já ensinou o perfil, as de agora)."""
    new_ref: bool
    total: int


def similarity_to(store: Store, c: ClusterRow, person_id: int) -> float | None:
    """Item 4: a nota da voz pelas referências da pessoa, com as configurações de agora; None sem perfil utilizável."""
    if not c["centroid"].size or not c.get("model"):
        return None
    return person_similarity(store, str(c["model"]), c["centroid"], person_id, *voice_settings(store))


def _scores(c: ClusterRow, refs: Sequence[ProfileRef]) -> dict[int, float]:
    """A nota da voz para cada pessoa com perfil na dimensão dela (o maior cosseno com as referências), na ordem do perfil."""
    v = normalize(c["centroid"])
    prof = [p for p in refs if p.refs.shape[1] == v.size]
    if not prof or not np.any(v) or not np.all(np.isfinite(v)):
        return {}
    return {p.person_id: float(s) for p, s in zip(prof, person_scores(v.reshape(1, -1), prof)[0][0], strict=True)}


def _margin(scores: dict[int, float], person: int) -> float:
    return scores[person] - max((s for q, s in scores.items() if q != person), default=0.0)


def _suggestion(c: ClusterRow, refs: Sequence[ProfileRef], changed: set[int]) -> Suggestion | None:
    """A sugestão refeita pelas regras (a), (b) e (c) do módulo, ou None quando nada muda."""
    scores = _scores(c, refs)
    best = max(scores, key=scores.__getitem__) if scores else None
    raw = c.get("person_id")
    current = int(raw) if raw is not None else None
    if best is not None and scores[best] >= FLOOR and (best in changed or current in changed):
        return best, scores[best], _margin(scores, best)
    if current is not None and current in changed:                                  # (a) abaixo do piso: a mesma pessoa
        return (current, scores[current], _margin(scores, current)) if current in scores else (current, None, None)
    return None


def _reachable(c: ClusterRow) -> bool:
    user = c["channel"] == "L" and c["cluster"] == USER_CLUSTER
    return str(c["status"]) in PENDING and c.get("merged_into") is None and not user and bool(c["centroid"].size and c.get("model"))


def _differs(c: ClusterRow, new: Suggestion) -> bool:
    def rounded(x: float | None) -> float | None:
        return None if x is None else round(float(x), 6)
    return c.get("person_id") != new[0] or (rounded(c.get("similarity")), rounded(c.get("margin"))) != (rounded(new[1]), rounded(new[2]))


def refresh_suggestions(store: Store, meeting_id: int, job_id: int, changed: set[int]) -> int:
    """Item 9: refaz as sugestões pela voz das pendentes do job depois de mudar o perfil das pessoas de `changed` (vazio:
    nada); uma carga das referências por modelo. Devolve quantas vozes mudaram; o log leva ids e contagens (D-053)."""
    if not changed:
        return 0
    voices = [c for c in store.segments.list_clusters(meeting_id, job_id) if _reachable(c)]
    if not voices:
        return 0
    ref_cos, max_refs = voice_settings(store)
    n = 0
    for model in sorted({str(c["model"]) for c in voices}):
        refs = load_profile_refs(store, model, ref_cos=ref_cos, max_refs=max_refs)
        for c in (c for c in voices if c["model"] == model):
            new = _suggestion(c, refs, changed)
            if new is not None and _differs(c, new):
                store.segments.update_cluster(int(c["id"]), person_id=new[0], similarity=new[1], margin=new[2])
                n += 1
    if n:
        log.info("reunião %s: %d sugestões refeitas (perfis mudados: pessoas %s)", meeting_id, n, sorted(changed))
    return n


def voice_ranking(store: Store, cluster_id: int) -> list[tuple[int, float]]:
    """U-136: as pessoas com perfil no modelo da voz, da maior nota para a menor; vazia sem vetor ou sem perfil."""
    c = store.segments.get_cluster(cluster_id)
    if c is None or not c["centroid"].size or not c.get("model"):
        return []
    ref_cos, max_refs = voice_settings(store)
    scores = _scores(c, load_profile_refs(store, str(c["model"]), ref_cos=ref_cos, max_refs=max_refs))
    return sorted(scores.items(), key=lambda kv: -kv[1])


def _teaches(it: ReviewItem) -> bool:
    """A voz cuja linha do U-101 fala do perfil: com vetor e pessoa, não mesclada; confirmada, só a que já ensinou."""
    return (it.person_id is not None and it.has_centroid and it.merged_into is None
            and (it.learned or it.status != ClusterStatus.CONFIRMED))


def _hint(it: ReviewItem, centroid: np.ndarray, vectors: np.ndarray | None, ref_cos: float, max_refs: int) -> LearnHint | None:
    """A previsão exata (o agrupamento com o vetor que a confirmação grava, o último) ou o total de agora (confirmada)."""
    if vectors is None or not len(vectors):                                         # pessoa sem amostra: a linha de hoje
        return None
    if it.status == ClusterStatus.CONFIRMED:
        return LearnHint(False, int(group_samples(vectors, ref_cos, max_refs).max()) + 1)
    v = normalize(centroid)
    if v.size != vectors.shape[1] or not np.any(v):
        return None
    labels = group_samples(np.vstack([vectors, v.reshape(1, -1)]), ref_cos, max_refs)
    return LearnHint(int(np.count_nonzero(labels == labels[-1])) == 1, int(labels.max()) + 1)


def learn_hints(store: Store, meeting_id: int, items: Sequence[ReviewItem]) -> dict[int, LearnHint]:
    """U-138, só para as pessoas dos cards: voz -> a previsão da linha do U-101. Uma consulta das amostras por modelo
    (`active_samples` com as pessoas); sem previsão, a linha de hoje."""
    wanted = [it for it in items if _teaches(it)]
    job = store.segments.current_job_id(meeting_id) if wanted else None
    rows = {int(c["id"]): c for c in store.segments.list_clusters(meeting_id, job)} if job is not None else {}
    wanted = [it for it in wanted if it.cluster_id in rows]
    if not wanted:
        return {}
    ref_cos, max_refs = voice_settings(store)
    out: dict[int, LearnHint] = {}
    for model in sorted({str(rows[it.cluster_id]["model"]) for it in wanted}):
        group = [it for it in wanted if rows[it.cluster_id]["model"] == model]
        people = {it.person_id for it in group if it.person_id is not None}
        samples = {ps.person_id: ps.vectors for ps in store.profiles.active_samples(model, people)}
        for it in group:
            if it.person_id is not None and (hint := _hint(it, rows[it.cluster_id]["centroid"], samples.get(it.person_id), ref_cos,
                                                           max_refs)) is not None:
                out[it.cluster_id] = hint
    return out


__all__ = ["FLOOR", "LearnHint", "learn_hints", "refresh_suggestions", "similarity_to", "voice_ranking"]
