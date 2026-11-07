"""M-02 `diar`: uma diarização da janela com `hook=` medindo `perf_counter` por etapa (`segmentation`, `speaker_counting`,
`embeddings`, `discrete_diarization` e a cauda sem gancho até o retorno), os instantes de cada chamada do gancho (a cadência
que um `checkpoint()` do PF-01 teria: diferenças entre chamadas consecutivas, mediana e máximo, por etapa) e, do artefato
`segmentation` (um `SlidingWindowFeature` de forma (trechos, quadros, falantes)), a fração de pares (trecho, falante) sem
nenhum quadro ativo: os slots em que o tronco do wespeaker roda à toa hoje (PF-04). Binarização: com o modelo de
segmentação em `powerset` o artefato já é 0/1 (a conversão do `Inference`) e a sonda usa `> 0,5`; senão, `binarize` com o
`onset` do pipeline (`pipe.segmentation.threshold`), como o `apply` faz. O JSON diz qual valeu."""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import audio
from .common import Report, best_of_medians, interleaved, rss_mb, stats

STEPS = ("segmentation", "speaker_counting", "embeddings", "discrete_diarization")


@dataclass(slots=True)
class HookLog:
    t0: float
    calls: list[tuple[float, str, bool]] = field(default_factory=list)      # (instante, etapa, é o artefato final da etapa?)
    segmentation: Any = None
    embeddings: Any = None

    def __call__(self, step: str, artifact: Any = None, *, file: Any = None, total: int | None = None,  # noqa: ARG002
                 completed: int | None = None) -> None:
        """A assinatura do pyannote: `hook(etapa, artefato, file=..., total=..., completed=...)`; sem `total` é o artefato final."""
        final = completed is None and total is None
        self.calls.append((time.perf_counter(), str(step), final))
        if final and step == "segmentation":
            self.segmentation = artifact
        if final and step == "embeddings":
            self.embeddings = artifact


def stage_seconds(log: HookLog, end: float) -> dict[str, float]:
    """Segundos até o artefato final de cada etapa (o trabalho antes da chamada é da etapa chamada) e a cauda."""
    out: dict[str, float] = {}
    last = log.t0
    for t, step, final in log.calls:
        if final and step in STEPS:
            out[step] = round(t - last, 2)
            last = t
    out["tail_after_last_hook"] = round(end - last, 2)
    out["total"] = round(end - log.t0, 2)
    return out


def cadence(log: HookLog, end: float) -> dict[str, Any]:
    """Diferenças entre chamadas consecutivas do gancho (todas e por etapa), em segundos; e a maior espera sem gancho."""
    times = [log.t0, *(t for t, _s, _f in log.calls), end]
    gaps = [round(b - a, 3) for a, b in itertools.pairwise(times)]
    per_step: dict[str, list[float]] = {}
    prev = log.t0
    for t, step, _final in log.calls:
        per_step.setdefault(step, []).append(round(t - prev, 3))
        prev = t
    return {"calls": len(log.calls), "all": stats(gaps, 3), "by_step": {s: stats(v, 3) for s, v in per_step.items()},
            "progress_calls": {s: sum(1 for _t, st, f in log.calls if st == s and not f) for s in STEPS}}


def empty_pairs(pipe: Any, seg: Any) -> dict[str, Any]:
    """Fração de pares (trecho, falante) sem quadro ativo na segmentação binarizada."""
    if seg is None or not hasattr(seg, "data"):
        return {"error": "sem artefato de segmentação"}
    data = np.nan_to_num(np.asarray(seg.data, dtype=np.float32), nan=0.0)
    powerset = bool(getattr(pipe._segmentation.model.specifications, "powerset", False))
    if powerset:
        binary, how = data > 0.5, "powerset: artefato já binário (> 0,5)"
    else:
        from pyannote.audio.utils.signal import binarize

        onset = float(pipe.segmentation.threshold)
        binary, how = np.asarray(binarize(seg, onset=onset, initial_state=False).data) > 0.5, f"binarize(onset={onset:g})"
    chunks, frames, speakers = binary.shape
    active_frames = binary.sum(axis=1)                        # (trechos, falantes)
    empty = int(np.sum(active_frames == 0))
    pairs = chunks * speakers
    batch = int(getattr(pipe, "embedding_batch_size", 0) or 0)
    return {"binarization": how, "chunks": int(chunks), "frames_per_chunk": int(frames), "local_speakers": int(speakers), "pairs": pairs,
            "empty_pairs": empty, "empty_fraction": round(empty / pairs, 4) if pairs else None,
            "chunks_without_speech": int(np.sum(active_frames.sum(axis=1) == 0)),
            "embedding_batch_size": batch, "embedding_batches": int(np.ceil(pairs / batch)) if batch else None,
            "segmentation_batch_size": int(getattr(pipe, "segmentation_batch_size", 0) or 0),
            "chunk_duration_s": round(float(seg.sliding_window.duration), 2), "chunk_step_s": round(float(seg.sliding_window.step), 2)}


def run(args: Any, report: Report, token: str, language: str, source: audio.Source) -> None:
    from .models import diarizer, import_torch

    if not token:
        raise SystemExit("sem token do Hugging Face no banco real: o pyannote não carrega")
    torch, import_s = import_torch(args.torch_threads)
    d, load_s = diarizer(token, args.torch_threads)
    pipe = d._load()
    window = audio.audio_window(source.mono)
    file = {"waveform": torch.from_numpy(np.ascontiguousarray(window.samples))[None, :], "sample_rate": window.sample_rate}
    report.add("diar_setup", torch_import_s=import_s, load_s=load_s, torch_threads=torch.get_num_threads(), **source.describe(),
               embedding_batch_size=int(pipe.embedding_batch_size), segmentation_batch_size=int(pipe.segmentation_batch_size),
               exclude_overlap=bool(getattr(pipe, "embedding_exclude_overlap", False)), rss_mb=rss_mb())
    totals: dict[str, list[float]] = {}
    for run_idx, _v in interleaved(["diar"], args.runs):
        log = HookLog(time.perf_counter())
        result = pipe(file, hook=log)
        end = time.perf_counter()
        seconds = stage_seconds(log, end)
        for k, v in seconds.items():
            totals.setdefault(k, []).append(v)
        ann = getattr(result, "speaker_diarization", result)
        report.add("diar_run", run=run_idx + 1, seconds=seconds, rtf=round(seconds["total"] / max(0.001, window.duration_ms / 1000), 4),
                   speakers=len(ann.labels()), cadence=cadence(log, end), pairs=empty_pairs(pipe, log.segmentation),
                   embeddings_shape=list(getattr(log.embeddings, "shape", ()) or ()), peak_rss_mb=rss_mb(peak=True))
    report.finish(best_seconds=best_of_medians(totals), audio_s=round(window.duration_ms / 1000, 1), language=language)
