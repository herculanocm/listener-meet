"""Perfis de voz persistentes (CP5, F2 item 7): `voice_profiles` (um por pessoa e modelo) e `voice_embeddings`.

Centroide = média dos vetores L2-normalizados com `source in (cadastro, confirmacao)`, renormalizada; os `automatico`
ficam gravados sem entrar (D-041 P3). Autocalibração (Q3): Welford de `sim_mean` e `sim_std` com a similaridade medida
antes da atualização; `threshold = clip(mean - 1,5·std, 0,45, 0,75)` só com `n_samples >= 5` (D-008). Vetores em BLOB
float32 little-endian; `dim` e `model` por perfil (D-009).
"""
from __future__ import annotations

import math
from typing import Any, TypedDict, cast

import numpy as np

from ..log import get_logger
from ..model import EmbeddingSource
from .core import Core, _now

log = get_logger("store.profiles")

CENTROID_SOURCES = (str(EmbeddingSource.ENROLL), str(EmbeddingSource.CONFIRM))
THRESHOLD_LOW, THRESHOLD_HIGH, THRESHOLD_K, MIN_CALIBRATION_SAMPLES = 0.45, 0.75, 1.5, 5


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

    def embeddings(self, profile_id: int, sources: tuple[str, ...] | None = None) -> list[tuple[int, np.ndarray, str]]:
        if sources:
            marks = ",".join("?" * len(sources))
            rows = self.core.q(f"SELECT id, vector, source FROM voice_embeddings WHERE profile_id = ? AND source IN ({marks}) ORDER BY id",  # noqa: S608
                               (profile_id, *sources))
        else:
            rows = self.core.q("SELECT id, vector, source FROM voice_embeddings WHERE profile_id = ? ORDER BY id", (profile_id,))
        return [(int(r["id"]), from_blob(r["vector"]), str(r["source"])) for r in rows]

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

