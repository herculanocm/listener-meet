"""M-08 `weights` (PF-08, a sonda Q8): compara o modelo de embedding de dentro do pipeline community-1 (a instância carregada,
`pipe._embedding.model_`, um `Model` do pyannote) com o `Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")`
que o `PyannoteEmbedder` do runner usa: tensores do `state_dict` iguais (contagem e máximo de |diferença|), o cosseno entre
os embeddings dos dois num mesmo trecho de 5 s (`Inference(window="whole")`) e, numa janela diarizada, o cosseno por cluster
entre o centroide VBx (`result.speaker_embeddings[i]` <-> `result.speaker_diarization.labels()[i]`) e o centroide por turno
como o runner faz (`clean_turns` dos turnos exclusivos com as sobreposições, `embedder.embed` nos turnos >= 1,5 s, até 20,
ponderados pela duração e normalizados), calculado com o modelo avulso e também com o de dentro do pipeline (separa o efeito
dos pesos do efeito do estimador). O transporte de decisões exige 0,8 (`TRANSPORT_MIN_COS`); o PF-08 pede 0,95."""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from . import audio
from .common import Report, cosine, rss_mb


def embedding_id(pipe: Any) -> str:
    """Só `checkpoint` e `subfolder` do modelo de embedding do pipeline: o dicionário inteiro carrega o token (D-059)."""
    emb = getattr(pipe, "embedding", None)
    if isinstance(emb, dict):
        return f"{emb.get('checkpoint')}/{emb.get('subfolder')}"
    return str(emb) if isinstance(emb, str) else type(emb).__name__


def compare_state_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    import torch

    common = sorted(set(a) & set(b))
    equal, max_diff, shape_mismatch = 0, 0.0, []
    for k in common:
        x, y = a[k], b[k]
        if tuple(x.shape) != tuple(y.shape):
            shape_mismatch.append(k)
            continue
        if torch.equal(x, y):
            equal += 1
        elif x.is_floating_point():
            max_diff = max(max_diff, float((x.float() - y.float()).abs().max()))
        else:
            max_diff = max(max_diff, float((x.long() - y.long()).abs().max()))
    return {"tensors_pipeline": len(a), "tensors_standalone": len(b), "common": len(common), "equal": equal,
            "different": len(common) - equal - len(shape_mismatch), "max_abs_diff": max_diff, "shape_mismatch": shape_mismatch[:10],
            "only_pipeline": sorted(set(a) - set(b))[:10], "only_standalone": sorted(set(b) - set(a))[:10]}


def turn_centroids(window: Any, turns: Any, overlap: Any, embed: Any) -> dict[str, np.ndarray]:
    """O `_embed_window` do runner: por rótulo, os turnos limpos ponderados pela duração e normalizados."""
    from listener.pipeline.profiles import clean_turns
    from listener.store.profiles import normalize

    out: dict[str, np.ndarray] = {}
    for label, items in clean_turns(turns, overlap).items():
        acc: np.ndarray | None = None
        for t in items:
            a, b = round((t.start_ms - window.t0_ms) * window.sample_rate / 1000), round((t.end_ms - window.t0_ms) * window.sample_rate / 1000)
            v = normalize(embed(window.samples[a:b], window.sample_rate)) * t.duration_ms
            acc = v if acc is None else acc + v
        if acc is not None and np.any(acc):
            out[label] = normalize(acc)
    return out


def inference_embed(inference: Any) -> Any:
    """`embed(samples, sr)` sobre uma `Inference(window="whole")`, com o preenchimento a 1 s do `PyannoteEmbedder`."""
    import torch

    def embed(samples: np.ndarray, sr: int) -> np.ndarray:
        x = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        if x.size < sr:
            x = np.pad(x, (0, sr - x.size))
        return np.asarray(inference({"waveform": torch.from_numpy(x)[None, :], "sample_rate": int(sr)}), dtype=np.float32).reshape(-1)
    return embed


def cluster_cosines(pipe: Any, window: Any, standalone: Any, inner: Any) -> dict[str, Any]:
    """Diariza a janela e compara, por rótulo, o centroide VBx com os centroides por turno dos dois modelos."""
    import torch

    from listener.pipeline.pyannote import overlaps_of, turns_of

    t0 = time.perf_counter()
    result = pipe({"waveform": torch.from_numpy(np.ascontiguousarray(window.samples))[None, :], "sample_rate": window.sample_rate})
    diar_s = round(time.perf_counter() - t0, 1)
    full, exclusive = result.speaker_diarization, result.exclusive_speaker_diarization
    labels = [str(x) for x in full.labels()]
    vbx = {label: np.asarray(result.speaker_embeddings[i], dtype=np.float32) for i, label in enumerate(labels)
           if i < len(result.speaker_embeddings)}
    turns, overlap = turns_of(exclusive, window.t0_ms), overlaps_of(full, window.t0_ms)
    by_standalone = turn_centroids(window, turns, overlap, standalone)
    by_inner = turn_centroids(window, turns, overlap, inner)
    per_label = {}
    for label in labels:
        row: dict[str, Any] = {"speech_ms": sum(t.duration_ms for t in turns if t.speaker == label),
                               "clean_turns": label in by_standalone, "vbx_finite": bool(np.all(np.isfinite(vbx.get(label, np.nan))))}
        if label in by_standalone and label in vbx and row["vbx_finite"]:
            row["cos_vbx_vs_standalone_turns"] = round(cosine(vbx[label], by_standalone[label]), 4)
            row["cos_vbx_vs_pipeline_model_turns"] = round(cosine(vbx[label], by_inner[label]), 4)
            row["cos_standalone_vs_pipeline_model_turns"] = round(cosine(by_standalone[label], by_inner[label]), 4)
        per_label[label] = row
    return {"diar_s": diar_s, "labels_full": labels, "labels_exclusive": [str(x) for x in exclusive.labels()],
            "embeddings_shape": list(np.asarray(result.speaker_embeddings).shape), "turns": len(turns), "by_label": per_label}


def run(args: Any, report: Report, token: str, source: audio.Source, silence_rms: float) -> None:
    from pyannote.audio import Inference

    from .models import diarizer, embedder, import_torch

    if not token:
        raise SystemExit("weights precisa do token do Hugging Face do banco real")
    _torch, import_s = import_torch(args.torch_threads)
    d, load_pipe_s = diarizer(token, args.torch_threads)
    pipe = d._load()
    e, load_emb_s = embedder(token, args.torch_threads)
    inner_model = pipe._embedding.model_
    standalone_model = e._inference.model
    report.add("weights_setup", torch_import_s=import_s, load_pipeline_s=load_pipe_s, load_standalone_s=load_emb_s,
               pipeline_embedding=embedding_id(pipe), standalone=e.model, inner_class=type(inner_model).__name__,
               state_dict=compare_state_dicts(inner_model.state_dict(), standalone_model.state_dict()), rss_mb=rss_mb())
    inner = inference_embed(Inference(inner_model, window="whole"))
    standalone = e.embed
    chunk = audio.speech_window(source.mono, 5.0, silence_rms)
    report.add("weights_chunk", seconds=5.0, cos_pipeline_model_vs_standalone=round(cosine(inner(chunk, audio.SR), standalone(chunk, audio.SR)), 6))
    window = audio.audio_window(source.mono)
    report.add("weights_clusters", **cluster_cosines(pipe, window, standalone, inner), audio_s=round(window.duration_ms / 1000, 1),
               peak_rss_mb=rss_mb(peak=True))
    cos = [r["cos_vbx_vs_standalone_turns"] for r in report.results[-1]["by_label"].values() if "cos_vbx_vs_standalone_turns" in r]
    report.finish(min_cos_vbx_vs_standalone=min(cos) if cos else None, labels=len(cos), **source.describe())
