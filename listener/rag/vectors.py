"""Vetores do índice, puro (F5A-20; D-024 revista, sem sqlite-vec): normalização, assinatura binária e busca por Hamming
com reordenação exata. Medido no L0 (`scripts/rag_probe.py sig`): 200 mil vetores de 768 = 19,2 MB de assinaturas, ~17 ms por
consulta numa thread e recall@10 de 0,99 centralizando contra 0,90 sem centralizar [exp, sintético anisotrópico].

Assinatura = bits do sinal de (v - mu), empacotados (`np.packbits`, 1 bit por dimensão): `mu` é a média dos vetores do
modelo (`rag_models`), porque os embeddings de texto são anisotrópicos (cosseno médio ~0,75 entre textos quaisquer) e sem
centralizar quase todo bit repete o da direção comum. O `mu` é refeito sempre que o número de vetores do modelo dobra, até
2 mil, e então congela (`mu_due`); refazer regrava as assinaturas a partir dos float32, sem rede. A busca pega
max(200, 1 % de n) candidatos (até 2 mil) pela distância de Hamming (`np.bitwise_count`, numpy 2) e os reordena pelo
cosseno exato dos float32. O cache das assinaturas no app (`rag.search.SignatureCache`) usa `RagRepo.vector_version` como
chave: o `mu_n` muda quando o `mu` é refeito e o `created_at` do maior id, quando o SQLite reaproveita o id."""
from __future__ import annotations

import numpy as np

MU_FREEZE = 2000                 # o `mu` refeito a cada dobra do número de vetores até aqui; depois, congelado
MIN_CANDIDATES = 200
MAX_CANDIDATES = 2000
CANDIDATE_SHARE = 0.01


def normalize(vectors: np.ndarray) -> np.ndarray:
    """Linhas com norma 1 em float32 (a OpenAI não normaliza [fato]); vetor nulo fica nulo."""
    v = np.asarray(vectors, dtype=np.float32)
    if v.ndim == 1:
        v = v[None, :]
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return np.ascontiguousarray(v / np.where(norms > 0, norms, 1.0), dtype=np.float32)


def signatures(vectors: np.ndarray, mu: np.ndarray | None) -> np.ndarray:
    """Bits de sinal de (v - mu) empacotados: uint8 (n, ceil(dim / 8)). `mu` nulo = sem centralizar (antes do primeiro)."""
    v = np.asarray(vectors, dtype=np.float32)
    if v.ndim == 1:
        v = v[None, :]
    centered = v - np.asarray(mu, np.float32)[None, :] if mu is not None and np.size(mu) else v
    return np.ascontiguousarray(np.packbits(centered > 0, axis=1))


def mu_due(count: int, mu_n: int) -> bool:
    """Refazer o `mu`? Sem `mu` ainda, ou quando os vetores do modelo dobraram desde o último, enquanto não congelou."""
    if count <= 0:
        return False
    return mu_n <= 0 or (mu_n < MU_FREEZE and count >= 2 * mu_n)


def n_candidates(n: int) -> int:
    """max(200, 1 % de n), até 2 mil, nunca mais que a base."""
    return min(n, MAX_CANDIDATES, max(MIN_CANDIDATES, int(n * CANDIDATE_SHARE)))


def as_words(sigs: np.ndarray) -> np.ndarray:
    """Assinaturas uint8 (n, b) vistas como uint64 (n, ceil(b / 8)), com zeros no fim: o XOR e o `bitwise_count` andam 8
    bytes por vez."""
    s = np.ascontiguousarray(sigs, dtype=np.uint8)
    if s.ndim == 1:
        s = s[None, :]
    pad = (-s.shape[1]) % 8
    if pad:
        s = np.pad(s, ((0, 0), (0, pad)))
    return np.ascontiguousarray(s).view(np.uint64)


def hamming(words: np.ndarray, query_words: np.ndarray) -> np.ndarray:
    """Distância de Hamming de cada linha (uint64) à consulta (uint64 de uma linha)."""
    q = np.asarray(query_words, np.uint64).reshape(1, -1)
    return np.bitwise_count(words ^ q).sum(axis=1, dtype=np.uint16)


def candidates(words: np.ndarray, query_sig: np.ndarray, k: int | None = None, mask: np.ndarray | None = None) -> np.ndarray:
    """Índices (nas linhas de `words`) dos k mais próximos por Hamming, sem ordem; `mask` (bool) filtra antes (os filtros de
    pessoa, período e reunião valem antes do top-k, F5 item 4)."""
    n = words.shape[0]
    if n == 0:
        return np.zeros(0, np.int64)
    dist = hamming(words, as_words(query_sig)[0]).astype(np.int32)
    if mask is not None:
        dist = np.where(mask, dist, np.iinfo(np.int32).max)
        n = int(np.count_nonzero(mask))
    m = min(n, k if k is not None else n_candidates(n))
    if m <= 0:
        return np.zeros(0, np.int64)
    if m >= dist.shape[0]:
        return np.flatnonzero(dist < np.iinfo(np.int32).max).astype(np.int64)
    return np.argpartition(dist, m - 1)[:m].astype(np.int64)


def rerank(query: np.ndarray, vectors: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(posições, cossenos) dos k melhores em `vectors` (normalizados) pelo cosseno exato, do maior para o menor."""
    if len(vectors) == 0 or k <= 0:
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    q = normalize(query)[0]
    scores = np.asarray(vectors, np.float32) @ q
    k = min(k, len(scores))
    top = np.argpartition(-scores, k - 1)[:k]
    order = top[np.argsort(-scores[top], kind="stable")]
    return order.astype(np.int64), scores[order]
