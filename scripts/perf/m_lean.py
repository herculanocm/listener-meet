"""`lean` (F5.18 PF-04, Aceite 1): a mesma janela diarizada pelo caminho original do pyannote e pela `LeanDiarization`
(`listener/pipeline/lean_diarization.py`), alternados na mesma instância carregada (`lean.disable`/`lean.enable`) e
intercalados por rodada. De cada passagem: os segundos totais e por etapa (o `HookLog` do M-02), o lote dos embeddings
(`embedding_batch_size`: pares no original, trechos no novo), os embeddings crus `(trechos, falantes, dim)` do artefato final
`embeddings` do gancho, os `hard_clusters` capturados na saída do `clustering` (antes do `-2` dos slots inativos), os `turns`
e as sobreposições (`turns_of`/`overlaps_of`, como o runner) e os centroides (`speaker_embeddings`). Paridade (o novo contra o
original da mesma rodada): `turns`, sobreposições, rótulos e `hard_clusters` iguais; embeddings normalizados por vetor com
`np.allclose(rtol=1e-4, atol=1e-6)`, mais `max_abs_diff`, a diferença relativa máxima, o pior cosseno e os crus. Com `--runs 2`
ou mais, também original x original entre rodadas (o ruído do próprio oneDNN com threads). Só leitura no banco real."""
from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import audio
from .common import Report, best_of_medians, interleaved, rss_mb
from .m_diar import HookLog, stage_seconds

VARIANTS = ("original", "lean")
RTOL, ATOL = 1e-4, 1e-6


@dataclass(slots=True)
class Pass:
    variant: str
    run: int
    seconds: float
    stages: dict[str, float]
    batch: int
    embeddings: np.ndarray                      # (trechos, falantes, dim), cru
    hard_clusters: np.ndarray | None
    turns: tuple[Any, ...]
    overlap: tuple[Any, ...]
    labels: list[str]
    centroids: np.ndarray | None
    hook_calls: int
    rss_mb: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@contextlib.contextmanager
def capture_clusters(pipe: Any, sink: dict[str, Any]) -> Iterator[None]:
    """Espia o `__call__` da classe do `clustering` e guarda uma cópia dos `hard_clusters` devolvidos (o `apply` os altera
    em seguida, com o -2 dos slots inativos); restaura a classe ao sair."""
    cls = type(pipe.clustering)
    own = "__call__" in cls.__dict__
    original = cls.__call__

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        out = original(self, *args, **kwargs)
        sink["hard_clusters"] = np.array(out[0], copy=True)
        return out

    cls.__call__ = spy
    try:
        yield
    finally:
        if own:
            cls.__call__ = original
        else:
            del cls.__call__


def normalized(emb: np.ndarray) -> np.ndarray:
    x = np.asarray(emb, np.float64)
    norms = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norms > 0, norms, 1.0)


def compare(a: Pass, b: Pass) -> dict[str, Any]:
    """`b` contra `a`: a paridade do Aceite 1 e os números por trás dela."""
    out: dict[str, Any] = {"shape_equal": a.embeddings.shape == b.embeddings.shape, "turns_equal": a.turns == b.turns,
                           "overlap_equal": a.overlap == b.overlap, "labels_equal": a.labels == b.labels,
                           "hard_clusters_equal": (a.hard_clusters is not None and b.hard_clusters is not None
                                                   and np.array_equal(a.hard_clusters, b.hard_clusters))}
    if out["shape_equal"] and a.embeddings.size:
        na, nb = normalized(a.embeddings), normalized(b.embeddings)
        diff = np.abs(na - nb)
        scale = np.maximum(np.abs(na), np.abs(nb))
        cos = np.sum(na * nb, axis=-1)
        raw = np.abs(a.embeddings.astype(np.float64) - b.embeddings.astype(np.float64))
        out.update({"allclose_rtol1e-4_atol1e-6": bool(np.allclose(na, nb, rtol=RTOL, atol=ATOL)),
                    "allclose_rtol1e-4_atol0": bool(np.allclose(na, nb, rtol=RTOL, atol=0.0)),
                    "max_abs_diff": float(diff.max()), "max_rel_diff": float((diff / np.maximum(scale, 1e-6)).max()),
                    "min_cos": float(cos.min()), "vectors_below_cos_0_9999": int(np.sum(cos < 0.9999)),
                    "raw_max_abs_diff": float(raw.max()), "raw_max_abs": float(np.abs(a.embeddings).max()),
                    "vectors": int(np.prod(a.embeddings.shape[:-1]))})
    if a.centroids is not None and b.centroids is not None and a.centroids.shape == b.centroids.shape and a.centroids.size:
        ca, cb = normalized(a.centroids), normalized(b.centroids)
        out["centroids_min_cos"] = float(np.sum(ca * cb, axis=-1).min())
    out["parity"] = bool(out["turns_equal"] and out["overlap_equal"] and out["hard_clusters_equal"]
                         and out.get("allclose_rtol1e-4_atol1e-6", False))
    return out


def set_variant(d: Any, pipe: Any, variant: str) -> bool:
    """Liga ou desliga o caminho novo na instância e reaplica o lote (`_set_embedding_batch` sabe em que unidade)."""
    from listener.pipeline import lean_diarization as lean

    if variant == "lean":
        active = lean.enable(pipe)
    else:
        lean.disable(pipe)
        active = False
    d._set_embedding_batch(pipe)
    return active


def diarize_once(pipe: Any, file: dict[str, Any], window: Any, variant: str, run: int) -> Pass:
    from listener.pipeline.pyannote import overlaps_of, turns_of

    log = HookLog(time.perf_counter())
    sink: dict[str, Any] = {}
    with capture_clusters(pipe, sink):
        result = pipe(file, hook=log)
    end = time.perf_counter()
    full, exclusive = result.speaker_diarization, result.exclusive_speaker_diarization
    emb = np.asarray(log.embeddings, dtype=np.float32) if log.embeddings is not None else np.zeros((0, 0, 0), np.float32)
    centroids = getattr(result, "speaker_embeddings", None)
    return Pass(variant, run, round(end - log.t0, 2), stage_seconds(log, end), int(pipe.embedding_batch_size), emb,
                sink.get("hard_clusters"), turns_of(exclusive, window.t0_ms), overlaps_of(full, window.t0_ms),
                [str(x) for x in full.labels()], None if centroids is None else np.asarray(centroids, dtype=np.float32),
                len(log.calls), rss_mb())


def describe(p: Pass) -> dict[str, Any]:
    return {"variant": p.variant, "run": p.run, "seconds": p.seconds, "stages": p.stages, "embedding_batch_size": p.batch,
            "embeddings_shape": list(p.embeddings.shape), "speakers": len(p.labels), "turns": len(p.turns), "overlaps": len(p.overlap),
            "hook_calls": p.hook_calls, "hard_clusters_shape": None if p.hard_clusters is None else list(p.hard_clusters.shape),
            "rss_mb": p.rss_mb}


def run(args: Any, report: Report, token: str, language: str, source: audio.Source) -> None:
    from listener.pipeline import lean_diarization as lean

    from .models import diarizer, import_torch

    if not token:
        raise SystemExit("lean precisa do token do Hugging Face do banco real")
    torch, import_s = import_torch(args.torch_threads)
    d, load_s = diarizer(token, args.torch_threads)
    pipe = d._load()
    lean.disable(pipe)
    reason = lean.unsupported_reason(pipe)
    window = audio.audio_window(source.mono)
    file = {"waveform": torch.from_numpy(np.ascontiguousarray(window.samples))[None, :], "sample_rate": window.sample_rate}
    report.add("lean_setup", torch_import_s=import_s, load_s=load_s, torch_threads=torch.get_num_threads(), lean_supported=reason is None,
               unsupported_reason=reason, pyannote_version=lean.pyannote_version(), embedding_batch_chunks=d.embedding_batch,
               exclude_overlap=bool(getattr(pipe, "embedding_exclude_overlap", False)), rss_mb=rss_mb(), **source.describe(),
               language=language)
    if reason is not None:
        raise SystemExit(f"o caminho novo não entra nesta instalação: {reason}")
    passes: dict[str, list[Pass]] = {v: [] for v in VARIANTS}
    totals: dict[str, list[float]] = {}
    for run_idx, variant in interleaved(VARIANTS, args.runs):
        active = set_variant(d, pipe, variant)
        p = diarize_once(pipe, file, window, variant, run_idx + 1)
        passes[variant].append(p)
        totals.setdefault(f"{variant}_total_s", []).append(p.seconds)
        totals.setdefault(f"{variant}_embeddings_s", []).append(float(p.stages.get("embeddings", 0.0)))
        report.add("lean_run", lean_active=active, rtf=round(p.seconds / max(0.001, window.duration_ms / 1000), 4), **describe(p))
        if variant == "lean":
            report.add("lean_parity", run=run_idx + 1, **compare(passes["original"][-1], p))
        elif len(passes["original"]) >= 2:
            report.add("lean_noise", run=run_idx + 1, what="original x original (rodada anterior)",
                       **compare(passes["original"][-2], p))
    best = best_of_medians(totals)
    orig, new = best.get("original_total_s"), best.get("lean_total_s")
    parity = [r for r in report.results if r["stage"] == "lean_parity"]
    report.finish(best_seconds=best, speedup=round(orig / new, 3) if orig and new else None,
                  diarize_gain_pct=round(100 * (1 - new / orig), 1) if orig and new else None,
                  parity_all=all(r.get("parity") for r in parity) if parity else None,
                  worst_max_abs_diff=max((r.get("max_abs_diff", 0.0) for r in parity), default=None),
                  worst_min_cos=min((r.get("min_cos", 1.0) for r in parity), default=None),
                  audio_s=round(window.duration_ms / 1000, 1), peak_rss_mb=rss_mb(peak=True))
