"""Nome ao vivo (F4 item 5; CP5 e CP11), puro: o embedding de uma janela contra os perfis do mesmo modelo, com o limiar e
a margem da F2 (`pipeline.threshold`, `pipeline.margin`, o limiar calibrado do perfil quando existe) e o desempate pelos
participantes esperados do warm-up (`meetings.expected_people_json`). O `ProfileMatcher` da F2 casa clusters inteiros e
exige 10 s de fala para `auto`; aqui é uma janela de 0,5 a 10 s, então a decisão é por janela e a suavização por canal
segura o nome quando a janela nova não decide mas soa como a anterior. Só exibição: nada aqui grava clusters, segmentos
nem perfis (CP11)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..pipeline.profiles import ProfileRef
from ..store.profiles import normalize

SMOOTH_COS = 0.80                 # janelas seguidas com cosseno >= isto são a mesma voz
PARTICIPANT = "Participante"      # abaixo do limiar (ou sem decisão)


@dataclass(frozen=True, slots=True)
class NameMatch:
    person_id: int | None                         # decidido: acima do limiar, com margem ou desempate
    similarity: float | None = None               # cosseno do escolhido (ou do melhor, sem decisão)
    margin: float | None = None                   # melhor menos o segundo
    best_person_id: int | None = None             # o melhor candidato, mesmo abaixo do limiar (diagnóstico)
    sims: dict[int, float] = field(default_factory=dict)   # pessoa -> cosseno, para a suavização

    @property
    def decided(self) -> bool:
        return self.person_id is not None


def _threshold(p: ProfileRef, default: float) -> float:
    return p.threshold if p.threshold is not None else default


def match_one(vec: np.ndarray, profiles: Sequence[ProfileRef], threshold: float, margin: float,
              expected_ids: frozenset[int] = frozenset()) -> NameMatch:
    """O melhor perfil decide se passa do seu limiar (calibrado ou o global) com margem >= `margin` para o segundo; com
    margem menor, decide quem estiver entre os participantes esperados quando só um dos dois está (e passa do próprio
    limiar); senão não decide. Perfis de outra dimensão ou vazios não contam."""
    v = normalize(vec)
    usable = [p for p in profiles if p.centroid.size == v.size and np.any(p.centroid)]
    if not usable or not np.any(v):
        return NameMatch(None)
    sims = np.stack([normalize(p.centroid) for p in usable]) @ v
    order = np.argsort(-sims)
    by_person = {p.person_id: float(s) for p, s in zip(usable, sims, strict=True)}
    best, best_sim = usable[int(order[0])], float(sims[order[0]])
    second_sim = float(sims[order[1]]) if len(order) > 1 else 0.0
    m = best_sim - second_sim
    if best_sim < _threshold(best, threshold):
        return NameMatch(None, best_sim, m, best.person_id, by_person)
    if m >= margin:
        return NameMatch(best.person_id, best_sim, m, best.person_id, by_person)
    runner = usable[int(order[1])]
    in_best, in_runner = best.person_id in expected_ids, runner.person_id in expected_ids
    if in_best != in_runner:
        winner, wsim = (best, best_sim) if in_best else (runner, second_sim)
        if wsim >= _threshold(winner, threshold):
            return NameMatch(winner.person_id, wsim, m, best.person_id, by_person)
    return NameMatch(None, best_sim, m, best.person_id, by_person)


@dataclass(slots=True)
class ChannelNamer:
    """Suavização de um canal: guarda a pessoa e o vetor da janela anterior; se o casamento novo não decide e a janela
    soa como a anterior (cosseno >= 0,80), a pessoa anterior continua."""
    smooth_cos: float = SMOOTH_COS
    person_id: int | None = None
    vec: np.ndarray | None = None
    kept: int = 0                                 # janelas nomeadas pela suavização (diagnóstico)

    def decide(self, vec: np.ndarray, match: NameMatch) -> tuple[int | None, float | None]:
        """(pessoa, cosseno com o perfil dela) ou (None, cosseno do melhor) quando não há nome."""
        v = normalize(vec)
        prev_vec, prev_person = self.vec, self.person_id
        self.vec = v
        if match.decided:
            self.person_id = match.person_id
            return match.person_id, match.similarity
        if (prev_person is not None and prev_vec is not None and prev_vec.size == v.size
                and float(prev_vec @ v) >= self.smooth_cos):
            self.kept += 1
            return prev_person, match.sims.get(prev_person, match.similarity)
        self.person_id = None
        return None, match.similarity


def person_label(person: dict[str, Any] | None) -> str | None:
    """"Ana — CFO" (sem cargo, só o nome); None sem pessoa."""
    if not person:
        return None
    name = str(person.get("name") or "").strip()
    role = str(person.get("role") or "").strip()
    if not name:
        return None
    return f"{name} — {role}" if role else name
