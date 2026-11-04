"""F5.15 L2, item 9 (U-137): as sugestões pela voz das pendentes refeitas depois de cada decisão que muda um perfil. As
regras (a), (b) e (c) e o piso de 0,45; a mesclada, a confirmada e o L do usuário de fora; `status`, `decision_source` e
`llm_*` intactos (CP11, R2-07); a ligação no `confirm` (a pessoa nova e a anterior), no `ignore` e no `merge` sem pessoa."""
from __future__ import annotations

import logging

import numpy as np
import pytest

from listener.model import ClusterStatus
from listener.pipeline import review
from listener.pipeline.review_suggest import FLOOR, refresh_suggestions
from listener.store import Store
from listener.store.profiles import normalize
from listener.store.segments import ClusterInsert

MODEL = "wespeaker"
DIM = 16
NAMES = ("Anastácia Zimmerwald", "Bianca Quintela", "Caio Ventura")


def _voice(ana: float, bia: float) -> np.ndarray:
    """Um vetor com cosseno `ana` com o perfil da Ana (o eixo 0) e `bia` com o da Bia (o eixo 1)."""
    v = np.zeros(DIM, np.float32)
    v[0], v[1], v[DIM - 1] = ana, bia, np.sqrt(max(0.0, 1.0 - ana * ana - bia * bia))
    return normalize(v)


def _setup(store: Store) -> tuple[int, int, dict[str, int], tuple[int, int, int]]:
    """A Ana no eixo 0 e a Bia no eixo 1 (uma amostra cada); as vozes da reunião, cada uma num estado."""
    ana, bia, caio = (store.people.create_person(n) for n in NAMES)
    store.profiles.confirm(ana, MODEL, _voice(1.0, 0.0), None)
    store.profiles.confirm(bia, MODEL, _voice(0.0, 1.0), None)
    mid = store.create_meeting("r", "ms-teams", None, None)
    job = store.jobs.enqueue(mid)
    u, a = ClusterStatus.UNNAMED, ClusterStatus.AMBIGUOUS
    plan = {"a1": (_voice(0.80, 0.50), a, bia), "a2": (_voice(0.70, 0.10), u, None), "a3": (_voice(0.44, 0.10), u, None),
            "a4": (_voice(0.20, 0.30), u, bia), "a5": (_voice(0.46, 0.10), u, None), "merged": (_voice(0.90, 0.0), u, None),
            "done": (_voice(0.95, 0.0), ClusterStatus.CONFIRMED, bia)}
    ids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", k, 20_000, v, MODEL, st, person_id=p, similarity=0.2, margin=0.01)
                                          for k, (v, st, p) in plan.items()]
                                         + [ClusterInsert(mid, job, "L", "L", 20_000, _voice(0.9, 0.0), MODEL, u)])   # o L do usuário
    store.segments.update_cluster(ids["merged"], merged_into=ids["a1"])
    store.segments.set_llm_suggestion(ids["a1"], caio, None, 0.8, "[]")
    return mid, job, ids, (ana, bia, caio)


def _plain(store: Store, cid: int) -> dict:
    """A linha do cluster sem o vetor (o dicionário compara campo a campo)."""
    return {k: v for k, v in store.segments.get_cluster(cid).items() if k != "centroid"}


def _state(store: Store, cid: int) -> tuple:
    c = store.segments.get_cluster(cid)
    return (c["person_id"], None if c["similarity"] is None else round(c["similarity"], 3),
            None if c["margin"] is None else round(c["margin"], 3))


def test_rule_b_and_the_floor_with_who_changed_and_the_rest_untouched(store: Store, caplog: pytest.LogCaptureFixture):
    mid, job, ids, (ana, bia, caio) = _setup(store)
    before = {k: _plain(store, cid) for k, cid in ids.items()}
    assert refresh_suggestions(store, mid, job, set()) == 0 and refresh_suggestions(store, mid, job, {caio}) == 0   # (c)
    with caplog.at_level(logging.INFO, logger="listener.pipeline.review_suggest"):
        assert refresh_suggestions(store, mid, job, {ana}) == 3
    assert _state(store, ids["a1"]) == (ana, 0.8, 0.3)                       # (b): a mais parecida é a Ana, que mudou
    assert _state(store, ids["a2"]) == (ana, 0.7, 0.6) and _state(store, ids["a5"]) == (ana, 0.46, 0.36)   # 0,46: passa do piso
    assert _state(store, ids["a3"]) == (None, 0.2, 0.01)                      # 0,44: abaixo do piso, fica como estava
    assert _state(store, ids["a4"]) == (bia, 0.2, 0.01)                       # (c): a Bia não mudou e é a mais parecida
    for key in ("merged", "done", "L"):                                       # a mesclada, a confirmada e o L do usuário
        assert _plain(store, ids[key]) == before[key]
    after = _plain(store, ids["a1"])
    fixed = ("status", "decision_source", "llm_person_id", "llm_name", "llm_confidence", "llm_evidence_json", "merged_into")
    assert all(after[k] == before["a1"][k] for k in fixed) and after["llm_person_id"] == caio     # CP11, R2-07
    assert f"reunião {mid}: 3 sugestões refeitas" in caplog.text and not any(n.split()[0] in caplog.text for n in NAMES)
    assert FLOOR == 0.45


def test_rule_a_the_suggested_person_changed(store: Store):
    mid, job, ids, (ana, bia, _caio) = _setup(store)
    assert refresh_suggestions(store, mid, job, {bia}) == 2
    assert _state(store, ids["a1"]) == (ana, 0.8, 0.3)                       # (a): a Bia mudou; a mais parecida é a Ana
    assert _state(store, ids["a4"]) == (bia, 0.3, 0.1)                       # (a) abaixo do piso: a Bia, com a nota nova
    assert _state(store, ids["a2"]) == (None, 0.2, 0.01)                      # (c): a Ana não mudou
    store._x("UPDATE voice_embeddings SET source = 'invalidado' WHERE profile_id = ?",
             (store.profiles.profile_for(bia, MODEL)["id"],))                 # a Bia sem amostra: sem nota
    store.segments.update_cluster(ids["a4"], person_id=bia, similarity=0.3, margin=0.1)
    store.segments.update_cluster(ids["a1"], person_id=bia)
    assert refresh_suggestions(store, mid, job, {bia}) == 2
    assert _state(store, ids["a4"]) == (bia, None, None) and _state(store, ids["a1"])[0] == ana


def test_confirm_ignore_and_merge_refresh_with_who_changed(store: Store, monkeypatch: pytest.MonkeyPatch):
    calls: list[set[int]] = []
    real = review.refresh_suggestions

    def spy(st: Store, meeting_id: int, job_id: int, changed: set[int]) -> int:
        calls.append(set(changed))
        return real(st, meeting_id, job_id, changed)

    monkeypatch.setattr(review, "refresh_suggestions", spy)
    ana, bia = store.people.create_person("Ana"), store.people.create_person("Bia")
    mid = store.create_meeting("r", None, None, None)
    job = store.jobs.enqueue(mid)
    u = ClusterStatus.UNNAMED
    ids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", "x", 20_000, _voice(1.0, 0.0), MODEL, u),
                                          ClusterInsert(mid, job, "R", "y", 20_000, _voice(0.7, 0.1), MODEL, u),
                                          ClusterInsert(mid, job, "R", "z", 20_000, None, None, u),
                                          ClusterInsert(mid, job, "R", "w", 20_000, _voice(0.0, 1.0), MODEL, u),
                                          ClusterInsert(mid, job, "R", "t", 20_000, _voice(0.1, 0.1), MODEL, u)])
    review.confirm(store, ids["x"], ana)
    assert calls[-1] == {ana} and _state(store, ids["y"]) == (ana, 0.7, 0.7)  # a pendente passa a sugerir a Ana
    review.confirm(store, ids["x"], bia)
    assert calls[-1] == {ana, bia}                                           # a correção: a pessoa nova e a anterior
    review.confirm(store, ids["z"], ana)
    assert calls[-1] == set()                                                # voz sem vetor: nenhum perfil mudou
    review.ignore(store, ids["x"])
    assert calls[-1] == {bia}
    review.confirm(store, ids["w"], bia)
    review.merge(store, ids["w"], ids["t"])                                  # numa voz sem pessoa: o reforço sai
    assert calls[-2:] == [{bia}, {bia}]
    assert store.segments.get_cluster(ids["y"])["status"] == ClusterStatus.UNNAMED   # o estado da pendente nunca muda
