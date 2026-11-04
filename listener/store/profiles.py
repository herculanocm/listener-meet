"""Perfis de voz persistentes (CP5, F2 item 7): `voice_profiles` (um por pessoa e modelo) e `voice_embeddings`.

Centroide = média dos vetores L2-normalizados com `source in (cadastro, confirmacao)`, renormalizada; os `automatico`
ficam gravados sem entrar (D-041 P3). Autocalibração (Q3): Welford de `sim_mean` e `sim_std` com a similaridade medida
antes da atualização; `threshold = clip(mean - 1,5·std, 0,45, 0,75)` só com `n_samples >= 5` (D-008). Vetores em BLOB
float32 little-endian; `dim` e `model` por perfil (D-009).

F5.15 L0: as funções puras das referências de voz, que a sonda `scripts/probe_f515.py` mede antes de o app usá-las (L1).
As amostras ativas de um perfil (`active_samples`, uma consulta) se agrupam por ligação média de cosseno
(`group_samples`); cada grupo vira uma referência (`references`); o limiar da pessoa sai das amostras (`calibrate`: a nota
de cada uma contra o resto do grupo) e `threshold_for` escolhe o limiar de uma voz pela variante (A, P ou C).
"""
from __future__ import annotations

import json
import math
from collections.abc import Collection
from dataclasses import dataclass
from itertools import groupby
from typing import Any, TypedDict, cast

import numpy as np

from ..log import get_logger
from ..model import EmbeddingSource
from .core import Core, _now

log = get_logger("store.profiles")

CENTROID_SOURCES = (str(EmbeddingSource.ENROLL), str(EmbeddingSource.CONFIRM))
THRESHOLD_LOW, THRESHOLD_HIGH, THRESHOLD_K, MIN_CALIBRATION_SAMPLES = 0.45, 0.75, 1.5, 5
REF_COS, MAX_REFS = 0.80, 5            # F5.15: os padrões de `pipeline.ref_cos` e `pipeline.max_refs` (as configurações, no L1)
THRESHOLD_VARIANTS = ("A", "P", "C")   # item 5: o da pessoa; o da pessoa, salvo referência de uma amostra; só o global
THRESHOLD_VARIANT = "P"                # a variante que o L0 confirma ou troca
_ACTIVE_SQL = ("SELECT p.id AS profile_id, p.person_id, p.dim, e.id, e.meeting_id, e.platform, e.vector FROM voice_profiles p "
               "JOIN voice_embeddings e ON e.profile_id = p.id WHERE p.model = ? AND p.person_id IS NOT NULL AND e.source IN (?, ?) "
               "AND (? IS NULL OR p.person_id IN (SELECT value FROM json_each(?))) ORDER BY p.id, e.id")


@dataclass(frozen=True, slots=True)
class ProfileSamples:
    """As amostras ativas (`cadastro` e `confirmacao`) de um perfil, na ordem de `voice_embeddings.id`. `vectors` só tem as
    da dimensão do perfil, com as linhas normalizadas; as de outra dimensão ficam fora e contam em `skipped`."""
    profile_id: int
    person_id: int
    dim: int
    ids: tuple[int, ...]
    meetings: tuple[int | None, ...]      # `meeting_id` de cada amostra (nulo no cadastro sem reunião)
    platforms: tuple[str | None, ...]
    vectors: np.ndarray                   # (n, dim) float32
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class Calibration:
    """O limiar da pessoa calculado das amostras (F5.15 item 5, variante A) e as notas que o deram."""
    threshold: float | None               # None = o global (menos de `MIN_CALIBRATION_SAMPLES` notas)
    n: int
    mean: float | None
    std: float | None


class ProfileRow(TypedDict, total=False):
    id: int
    person_id: int
    model: str
    dim: int
    centroid: np.ndarray
    n_samples: int
    threshold: float | None
    sim_mean: float | None
    sim_std: float | None
    calib_n: int | None
    updated_at: str | None


def to_blob(vector: np.ndarray) -> bytes:
    return np.ascontiguousarray(vector, dtype="<f4").tobytes()


def from_blob(blob: bytes | memoryview | None) -> np.ndarray:
    return np.frombuffer(bytes(blob), dtype="<f4").astype(np.float32, copy=True) if blob else np.zeros(0, np.float32)


def normalize(vector: np.ndarray) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def centroid_of(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    return normalize(np.mean([normalize(v) for v in vectors], axis=0))


def welford_update(n: int, mean: float, std: float, x: float) -> tuple[int, float, float]:
    """Média e desvio-padrão populacionais atualizados com mais uma amostra (M2 reconstruído de `std`)."""
    m2 = std * std * n
    n += 1
    delta = x - mean
    mean += delta / n
    m2 += delta * (x - mean)
    return n, mean, math.sqrt(max(m2, 0.0) / n)


def calibrated_threshold(n: int, mean: float, std: float, low: float = THRESHOLD_LOW, high: float = THRESHOLD_HIGH,
                         k: float = THRESHOLD_K) -> float | None:
    """Limiar do perfil, ou None (usa o global) enquanto há menos de `MIN_CALIBRATION_SAMPLES` confirmações."""
    if n < MIN_CALIBRATION_SAMPLES:
        return None
    return float(min(high, max(low, mean - k * std)))


# ---------------------------------------------------------------- referências de voz (F5.15, puras)
def unit_rows(matrix: np.ndarray) -> np.ndarray:
    """Cada linha L2-normalizada, em float32; a linha de norma zero (ou não finita) fica como veio, como em `normalize`."""
    m = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return np.divide(m, norms, out=m.copy(), where=norms > 0)


def _first_seen(labels: np.ndarray) -> np.ndarray:
    """Rótulos 0..k-1 na ordem em que cada grupo aparece pela primeira vez (o do menor índice primeiro)."""
    _, first, inverse = np.unique(labels, return_index=True, return_inverse=True)
    rank = np.empty(first.size, np.intp)
    rank[np.argsort(first)] = np.arange(first.size)
    return rank[inverse.reshape(-1)]


def group_samples(vectors: np.ndarray, ref_cos: float, max_refs: int | None) -> np.ndarray:
    """F5.15 item 3: o grupo de cada amostra (`(n, d)`, na ordem de `voice_embeddings.id`) pela ligação média de cosseno
    com corte em `ref_cos`; com mais de `max_refs` grupos, os mais próximos se juntam até caber (`None` = sem teto).
    `ref_cos >= 1` dá uma referência por amostra (o máximo sobre as amostras). Rótulos 0..k-1 pela ordem do menor índice de
    cada grupo: a mesma entrada dá sempre os mesmos grupos. Vetores finitos e não nulos, como os gravados (o `linkage`
    recusa distância NaN)."""
    n = len(vectors)
    if n <= 1:
        return np.zeros(n, np.intp)
    if ref_cos >= 1.0 and (max_refs is None or n <= max_refs):
        return np.arange(n, dtype=np.intp)
    from scipy.cluster.hierarchy import fcluster, linkage   # ~0,5 s na primeira vez: fora da abertura do banco

    z = linkage(unit_rows(vectors), method="average", metric="cosine")
    labels = np.arange(n) if ref_cos >= 1.0 else fcluster(z, t=1.0 - ref_cos, criterion="distance")
    if max_refs is not None and np.unique(labels).size > max_refs:
        labels = fcluster(z, t=max_refs, criterion="maxclust")
    return _first_seen(labels)


def references(vectors: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, tuple[int, ...]]:
    """As referências `(k, d)` de um agrupamento, cada uma a média renormalizada do grupo (`centroid_of`), na ordem dos
    rótulos, e o número de amostras de cada uma."""
    v = np.asarray(vectors, dtype=np.float32)
    lab = np.asarray(labels).reshape(-1)
    groups = [v[lab == g] for g in np.unique(lab)]
    refs = [cast(np.ndarray, centroid_of(list(g))) for g in groups]
    if not refs:
        return np.zeros((0, v.shape[-1] if v.ndim == 2 else 0), np.float32), ()
    return np.stack(refs), tuple(len(g) for g in groups)


def calibrate(vectors: np.ndarray, labels: np.ndarray) -> Calibration:
    """F5.15 item 5 (variante A): a nota de cada amostra de um grupo de 2 ou mais é o cosseno dela com a média normalizada
    das outras do grupo (a fundadora sozinha não tem nota). Com `MIN_CALIBRATION_SAMPLES` notas ou mais, o limiar é
    `clip(média - 1,5·desvio populacional, 0,45, 0,75)` (D-008); com menos, None (o global)."""
    v = unit_rows(vectors)
    lab = np.asarray(labels).reshape(-1)
    notes: list[float] = []
    for g in np.unique(lab):
        members = v[lab == g].astype(np.float64)
        if len(members) < 2:
            continue
        total = members.sum(axis=0)
        notes += [float(normalize(total - x) @ x) for x in members]
    if not notes:
        return Calibration(None, 0, None, None)
    mean, std = float(np.mean(notes)), float(np.std(notes))
    return Calibration(calibrated_threshold(len(notes), mean, std), len(notes), mean, std)


def threshold_for(variant: str, person_threshold: float | None, ref_size: int | None, global_threshold: float) -> float:
    """O limiar de uma voz contra uma pessoa (F5.15 item 5). A: o da pessoa, ou o global sem ele. P: como a A, mas o global
    quando a referência vencedora tem uma amostra só (`ref_size` None = desconhecido: como a A). C: sempre o global."""
    if variant not in THRESHOLD_VARIANTS:
        raise ValueError(f"variante de limiar desconhecida: {variant!r} (A, P ou C)")
    if variant == "C" or person_threshold is None or (variant == "P" and ref_size == 1):
        return float(global_threshold)
    return float(person_threshold)


class ProfilesRepo:
    def __init__(self, core: Core):
        self.core = core

    @staticmethod
    def _row(r: Any) -> ProfileRow:
        d = dict(r)
        d["centroid"] = from_blob(d.get("centroid"))
        return cast(ProfileRow, d)

    # ---------------------------------------------------------------- perfis
    def list_profiles(self, model: str) -> list[ProfileRow]:
        return [self._row(r) for r in self.core.q("SELECT * FROM voice_profiles WHERE model = ? ORDER BY id", (model,))]

    def get_profile(self, profile_id: int) -> ProfileRow | None:
        rows = self.core.q("SELECT * FROM voice_profiles WHERE id = ?", (profile_id,))
        return self._row(rows[0]) if rows else None

    def profile_for(self, person_id: int, model: str) -> ProfileRow | None:
        rows = self.core.q("SELECT * FROM voice_profiles WHERE person_id = ? AND model = ?", (person_id, model))
        return self._row(rows[0]) if rows else None

    def ensure_profile(self, person_id: int, model: str, dim: int) -> int:
        """Perfil da pessoa neste modelo, criado vazio se faltar (único por `(person_id, model)`)."""
        existing = self.profile_for(person_id, model)
        if existing is not None:
            return int(existing["id"])
        return self.core.insert("INSERT INTO voice_profiles(person_id, model, dim, centroid, n_samples, updated_at) "
                                "VALUES (?, ?, ?, ?, 0, ?)", (person_id, model, int(dim), to_blob(np.zeros(int(dim), np.float32)), _now()))

    # ---------------------------------------------------------------- embeddings
    def add_embedding(self, profile_id: int, vector: np.ndarray, source: EmbeddingSource, meeting_id: int | None = None,
                      segment_id: int | None = None, platform: str | None = None, cluster_id: int | None = None) -> int:
        return self.core.insert("INSERT INTO voice_embeddings(profile_id, meeting_id, segment_id, vector, source, platform, cluster_id, "
                                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (profile_id, meeting_id, segment_id, to_blob(normalize(vector)), str(source), platform, cluster_id, _now()))

    def upsert_auto_embedding(self, person_id: int, model: str, vector: np.ndarray, meeting_id: int, platform: str | None) -> int:
        """Um só vetor `automatico` por (perfil, reunião): retomada, nova tentativa e reprocessamento substituem (R2-06)."""
        v = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self.core.transaction():
            pid = self.ensure_profile(person_id, model, int(v.size))
            n = self.core.execute("UPDATE voice_embeddings SET vector = ?, platform = ?, created_at = ? "
                                  "WHERE profile_id = ? AND meeting_id = ? AND source = ?",
                                  (to_blob(normalize(v)), platform, _now(), pid, meeting_id, EmbeddingSource.AUTO))
            if n == 0:
                self.add_embedding(pid, v, EmbeddingSource.AUTO, meeting_id, None, platform)
        return pid

    def invalidate_cluster_embeddings(self, profile_id: int, cluster_id: int) -> int:
        """Confirmação corrigida: os vetores daquela voz saem do centroide (fonte `invalidado`), sem apagar (CP3)."""
        n = self.core.execute("UPDATE voice_embeddings SET source = ? WHERE profile_id = ? AND cluster_id = ? AND source IN (?, ?)",
                              (EmbeddingSource.INVALID, profile_id, cluster_id, EmbeddingSource.CONFIRM, EmbeddingSource.ENROLL))
        if n:
            self.recompute_centroid(profile_id)
        return n

    def unlink_meeting(self, meeting_id: int) -> int:
        """F5.6 (A-34): a reunião excluída deixa as amostras dela nos perfis (o vetor, a fonte, o perfil e o `meeting_id`, que o
        piso nunca reaproveita), sem o vínculo de voz e de fala: os ids de `clusters` e `segments` voltam em outras reuniões, e
        a invalidação de uma voz nova (`invalidate_cluster_embeddings`) não pode tirar do centroide uma amostra antiga. Nada
        aqui toca `voice_profiles`: centroide e `n_samples` ficam iguais."""
        return self.core.execute("UPDATE voice_embeddings SET cluster_id = NULL, segment_id = NULL WHERE meeting_id = ? "
                                 "AND (cluster_id IS NOT NULL OR segment_id IS NOT NULL)", (meeting_id,))

    def learned_clusters(self, meeting_id: int) -> set[int]:
        """As vozes da reunião com vetor de confirmação ativo num perfil (F5.11, U-101: a revisão só promete desfazer o que
        acha; a voz confirmada que veio de um reprocessamento tem o vetor na voz antiga)."""
        rows = self.core.q("SELECT DISTINCT cluster_id FROM voice_embeddings WHERE meeting_id = ? AND source = ? "
                           "AND cluster_id IS NOT NULL", (meeting_id, EmbeddingSource.CONFIRM))
        return {int(r[0]) for r in rows}

    def embeddings(self, profile_id: int, sources: tuple[str, ...] | None = None) -> list[tuple[int, np.ndarray, str]]:
        if sources:
            marks = ",".join("?" * len(sources))
            rows = self.core.q(f"SELECT id, vector, source FROM voice_embeddings WHERE profile_id = ? AND source IN ({marks}) ORDER BY id",  # noqa: S608
                               (profile_id, *sources))
        else:
            rows = self.core.q("SELECT id, vector, source FROM voice_embeddings WHERE profile_id = ? ORDER BY id", (profile_id,))
        return [(int(r["id"]), from_blob(r["vector"]), str(r["source"])) for r in rows]

    def active_samples(self, model: str, person_ids: Collection[int] | None = None) -> list[ProfileSamples]:
        """F5.15: as amostras ativas (`cadastro` e `confirmacao`) dos perfis do modelo numa consulta só, na ordem do perfil e
        de `voice_embeddings.id`; `person_ids` filtra (vazio: nenhum). Perfil sem amostra ativa não volta; o que só tem
        amostras de outra dimensão volta com a matriz vazia e o `skipped`."""
        if person_ids is not None and not person_ids:
            return []
        wanted = None if person_ids is None else json.dumps(sorted({int(x) for x in person_ids}))
        rows = self.core.q(_ACTIVE_SQL, (model, *CENTROID_SOURCES, wanted, wanted))
        return [self._samples(list(group)) for _, group in groupby(rows, key=lambda r: int(r["profile_id"]))]

    @staticmethod
    def _samples(rows: list[Any]) -> ProfileSamples:
        dim = int(rows[0]["dim"])
        kept = [(r, v) for r in rows if (v := from_blob(r["vector"])).size == dim]
        vectors = unit_rows(np.stack([v for _, v in kept])) if kept else np.zeros((0, dim), np.float32)
        return ProfileSamples(int(rows[0]["profile_id"]), int(rows[0]["person_id"]), dim, tuple(int(r["id"]) for r, _ in kept),
                              tuple(None if r["meeting_id"] is None else int(r["meeting_id"]) for r, _ in kept),
                              tuple(None if r["platform"] is None else str(r["platform"]) for r, _ in kept), vectors,
                              len(rows) - len(kept))

    def recompute_centroid(self, profile_id: int) -> np.ndarray | None:
        """Centroide só dos vetores de cadastro e confirmação; `n_samples` acompanha. Sem nenhum, o perfil fica vazio
        (centroide zero, `n_samples = 0`: o casamento o ignora) e devolve None."""
        vectors = [v for _, v, _ in self.embeddings(profile_id, CENTROID_SOURCES)]
        c = centroid_of(vectors)
        if c is None:
            p = self.get_profile(profile_id)
            dim = int(p["dim"]) if p else 0
            self.core.execute("UPDATE voice_profiles SET centroid = ?, n_samples = 0, updated_at = ? WHERE id = ?",
                              (to_blob(np.zeros(dim, np.float32)), _now(), profile_id))
            return None
        self.core.execute("UPDATE voice_profiles SET centroid = ?, dim = ?, n_samples = ?, updated_at = ? WHERE id = ?",
                          (to_blob(c), int(c.size), len(vectors), _now(), profile_id))
        return c

    def update_calibration(self, profile_id: int, similarity: float) -> float | None:
        """Welford com a similaridade medida antes da confirmação; devolve o limiar calibrado (ou None = global)."""
        p = self.get_profile(profile_id)
        if p is None:
            return None
        n = int(p.get("calib_n") or 0)                     # só amostras de similaridade medidas, não vetores (R2-18)
        n, mean, std = welford_update(n, float(p.get("sim_mean") or 0.0), float(p.get("sim_std") or 0.0), float(similarity))
        threshold = calibrated_threshold(n, mean, std)
        self.core.execute("UPDATE voice_profiles SET sim_mean = ?, sim_std = ?, calib_n = ?, threshold = ?, updated_at = ? WHERE id = ?",
                          (mean, std, n, threshold, _now(), profile_id))
        return threshold

    def confirm(self, person_id: int, model: str, vector: np.ndarray, similarity: float | None, meeting_id: int | None,
                segment_id: int | None = None, platform: str | None = None,
                source: EmbeddingSource = EmbeddingSource.CONFIRM, cluster_id: int | None = None) -> int:
        """Uma confirmação humana: calibra (antes), grava o vetor e recalcula o centroide; devolve o id do perfil."""
        v = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self.core.transaction():
            pid = self.ensure_profile(person_id, model, int(v.size))
            if similarity is not None and source == EmbeddingSource.CONFIRM:
                self.update_calibration(pid, similarity)
            self.add_embedding(pid, v, source, meeting_id, segment_id, platform, cluster_id)
            if source in (EmbeddingSource.CONFIRM, EmbeddingSource.ENROLL):
                self.recompute_centroid(pid)
        return pid

