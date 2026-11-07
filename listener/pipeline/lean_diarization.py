"""F5.18 (PF-04): o tronco do wespeaker uma vez por trecho na diarização do pyannote community-1.

O `get_embeddings` original (`pyannote/audio/pipelines/speaker_diarization.py`, 4.0.7) monta um par (trecho, falante local)
por slot e chama `self._embedding(waveform, masks=mask)` por par: a fbank e o ResNet34 rodam três vezes por trecho de 10 s,
inclusive nos slots sem fala, e só a pooling por máscara muda (M-02: os embeddings são 93 % da etapa). Aqui, por lote de
TRECHOS, `model.forward_frames(waveforms)` roda uma vez (fbank e tronco) e `model.forward_embedding(frames, weights=masks)`
com `masks` `(lote, falantes, quadros)` devolve `(lote, falantes, dim)`: o `StatsPool` aceita a dimensão dos falantes e
reamostra os pesos aos quadros do tronco com o mesmo `interpolate(mode="nearest")` do caminho 2-D, e `seg_1` é um `Linear`
(com `two_emb_layer=False` o `seg_bn_1` e o `seg_2` são `Identity`, então o 3-D passa). O resto é copiado do original: a
escolha por falante entre a máscara limpa (sem sobreposição) e a cheia pelo `min_num_frames` derivado de `min_num_samples`
(`embedding_exclude_overlap: true` no `config.yaml`), o `nan_to_num` das máscaras, o `crop(mode="pad")` do mesmo `Audio`, a
ordem (trecho, falante) da saída e o gancho a cada lote. Nenhum slot vira NaN: o vazio sai com a pooling de pesos zero,
como no original, e `filter_embeddings` o descarta pela segmentação.

A subclasse `LeanDiarization` nasce da classe da instância (`lean_class(type(pipe))`) e a instância troca de classe em
`enable` (`object.__setattr__`, que o `Pipeline.__setattr__` do pyannote usa para o que não é parâmetro): assim este módulo
e os testes da suíte não importam pyannote nem torch; o torch entra só em `_tensor`, `_numpy` e `_quiet`, trocáveis nos
testes. Guardas (`unsupported_reason`): pyannote.audio 4.x, a classe `SpeakerDiarization`, o `WeSpeakerResNet34` com
`forward_frames`/`forward_embedding` e `two_emb_layer=False` (o `BatchNorm1d` do caminho de duas camadas não aceita a
dimensão dos falantes); fora disso o método original fica e uma linha INFO diz por quê. Com o caminho novo,
`pipeline.embedding_batch` vale em trechos de verdade (`PyannoteDiarizer._set_embedding_batch`). Paridade conferida na
sonda `scripts/probe_perf.py lean` (Aceite 1): `turns` e `hard_clusters` iguais, embeddings normalizados com `rtol 1e-4`."""
from __future__ import annotations

import contextlib
import functools
import itertools
import math
import warnings
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np

from ..log import get_logger

log = get_logger("pipeline.lean")

LEAN_CLASS_NAME = "LeanDiarization"
EXPECTED_PIPELINE = "SpeakerDiarization"
EXPECTED_MODEL = "WeSpeakerResNet34"


# ---------------------------------------------------------------- o torch, isolado (os testes trocam estas três funções)
def _tensor(array: np.ndarray, device: Any) -> Any:
    import torch

    t = torch.from_numpy(np.ascontiguousarray(array))
    return t.to(device) if device is not None else t


def _numpy(tensor: Any) -> np.ndarray:
    return np.asarray(tensor.detach().cpu().numpy()) if hasattr(tensor, "detach") else np.asarray(tensor)


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """`inference_mode` e os avisos silenciados, como o `PyannoteAudioPretrainedSpeakerEmbedding.__call__` faz."""
    import torch

    with torch.inference_mode(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


# ---------------------------------------------------------------- guardas
def pyannote_version() -> str:
    from importlib import metadata

    try:
        return metadata.version("pyannote.audio")
    except metadata.PackageNotFoundError:
        return ""


def unsupported_reason(pipe: Any, version: str | None = None) -> str | None:
    """None quando o caminho novo pode entrar; senão o motivo (vai para o log). Nada aqui roda o modelo: a conferência do
    `min_num_samples` olha a classe (é uma `cached_property` que faz uma busca binária com o modelo ao ser lida)."""
    version = pyannote_version() if version is None else version
    if not version.startswith("4."):
        return f"pyannote.audio {version or 'desconhecido'} (o caminho novo foi conferido no 4.x)"
    if type(pipe).__name__ != EXPECTED_PIPELINE:
        return f"pipeline {type(pipe).__name__} (esperava {EXPECTED_PIPELINE})"
    missing = [a for a in ("_embedding", "_audio", "embedding_batch_size", "embedding_exclude_overlap") if not hasattr(pipe, a)]
    if missing:
        return f"pipeline sem {', '.join(missing)}"
    inference = pipe._embedding
    model = getattr(inference, "model_", None)
    if model is None or not hasattr(type(inference), "min_num_samples") or not hasattr(type(inference), "sample_rate"):
        return f"embedding {type(inference).__name__} sem model_ ou min_num_samples"
    if type(model).__name__ != EXPECTED_MODEL:
        return f"modelo de embedding {type(model).__name__} (esperava {EXPECTED_MODEL})"
    if not (hasattr(model, "forward_frames") and hasattr(model, "forward_embedding")):
        return "modelo sem forward_frames/forward_embedding"
    resnet = getattr(model, "resnet", None)
    if resnet is None:
        return "modelo sem o resnet esperado"
    if getattr(resnet, "two_emb_layer", True):
        return "two_emb_layer ligado (o BatchNorm1d não aceita a dimensão dos falantes)"
    return None


# ---------------------------------------------------------------- o caminho novo
def embed_batch(inference: Any, waveforms: np.ndarray, masks: np.ndarray) -> np.ndarray:
    """Um lote: `forward_frames` uma vez (fbank e ResNet34 de `(lote, 1, amostras)`) e `forward_embedding` com os pesos de
    todos os falantes `(lote, falantes, quadros)`; devolve `(lote, falantes, dim)`."""
    model = inference.model_
    device = getattr(inference, "device", None)
    with _quiet():
        frames = model.forward_frames(_tensor(waveforms, device))
        embeddings = model.forward_embedding(frames, weights=_tensor(masks, device))
    return _numpy(embeddings)


def _chunks(pipe: Any, file: Any, segmentations: Any, clean: np.ndarray, min_num_frames: int) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Por trecho, o waveform `(1, amostras)` e as máscaras usadas `(falantes, quadros)`: por falante, a limpa quando ela tem
    mais de `min_num_frames` quadros, senão a cheia (a escolha do original, slot a slot)."""
    for c, (chunk, masks) in enumerate(segmentations):              # um SlidingWindowFeature itera (Segment, data[i])
        waveform, _ = pipe._audio.crop(file, chunk, mode="pad")
        full = np.nan_to_num(masks, nan=0.0).astype(np.float32)      # (quadros, falantes); NaN na costura parcial
        clean_masks = np.nan_to_num(clean[c], nan=0.0).astype(np.float32)
        used = np.where(np.sum(clean_masks, axis=0, keepdims=True) > min_num_frames, clean_masks, full)
        yield _numpy(waveform), np.ascontiguousarray(used.T, dtype=np.float32)


def lean_embeddings(pipe: Any, file: Any, segmentations: Any, exclude_overlap: bool = False,
                    hook: Callable[..., None] | None = None) -> np.ndarray:
    """O `get_embeddings` do PF-04: `(trechos, falantes, dim)` como o original, com o tronco uma vez por trecho e o lote
    (`embedding_batch_size`) em trechos. O gancho recebe o mesmo que no original (`total`, `completed` por lote)."""
    inference = pipe._embedding
    data = segmentations.data
    num_chunks, num_frames, num_speakers = data.shape
    if exclude_overlap:
        num_samples = float(segmentations.sliding_window.duration) * inference.sample_rate
        min_num_frames = math.ceil(num_frames * int(inference.min_num_samples) / num_samples)
        clean = data * (1.0 * (np.sum(data, axis=2, keepdims=True) < 2))    # os quadros com um só falante
    else:
        min_num_frames, clean = -1, data
    batch_size = max(1, int(pipe.embedding_batch_size))
    total = math.ceil(num_chunks / batch_size)
    if hook is not None:
        hook("embeddings", None, total=total, completed=0)
    out: list[np.ndarray] = []
    for i, batch in enumerate(itertools.batched(_chunks(pipe, file, segmentations, clean, min_num_frames), batch_size), 1):
        waveforms = np.stack([w for w, _m in batch])                 # (lote, 1, amostras)
        masks = np.stack([m for _w, m in batch])                     # (lote, falantes, quadros)
        embeddings = embed_batch(inference, waveforms, masks)        # (lote, falantes, dim)
        out.append(embeddings)
        if hook is not None:
            hook("embeddings", embeddings, total=total, completed=i)
    if not out:
        return np.zeros((0, num_speakers, int(inference.dimension)), dtype=np.float32)
    return np.concatenate(out, axis=0)


_CLASSES: dict[type[Any], type[Any]] = {}            # uma `LeanDiarization` por classe-base (a `SpeakerDiarization` instalada)


def lean_class(base: type[Any]) -> type[Any]:
    """A subclasse `LeanDiarization` da classe dada, criada uma vez por base: só o `get_embeddings` muda; em treino
    (`training`, o cache dos embeddings da otimização de hiperparâmetros) o original fica."""
    if base in _CLASSES:
        return _CLASSES[base]

    def get_embeddings(self: Any, file: Any, binary_segmentations: Any, exclude_overlap: bool = False,
                       hook: Callable[..., None] | None = None) -> np.ndarray:
        if getattr(self, "training", False):
            return base.get_embeddings(self, file, binary_segmentations, exclude_overlap=exclude_overlap, hook=hook)
        return lean_embeddings(self, file, binary_segmentations, exclude_overlap, hook)

    doc = "F5.18 (PF-04): a diarização do pyannote com o tronco do wespeaker uma vez por trecho (`pipeline/lean_diarization.py`)."
    cls = type(LEAN_CLASS_NAME, (base,), {"get_embeddings": get_embeddings, "LEAN": True, "__module__": __name__, "__doc__": doc})
    _CLASSES[base] = cls
    return cls


def is_lean(pipe: Any) -> bool:
    """A instância está na `LeanDiarization` (o marcador `LEAN` da classe)."""
    return bool(getattr(type(pipe), "LEAN", False))


@functools.cache
def _announce() -> None:
    """Uma linha INFO por processo quando o caminho novo entra."""
    log.info("pyannote: diarização com o tronco do wespeaker uma vez por trecho (PF-04); o lote dos embeddings conta trechos")


def enable(pipe: Any, version: str | None = None) -> bool:
    """Troca a instância para `LeanDiarization` quando as guardas passam; devolve se o caminho novo está ativo. Fora disso o
    método original fica e o log diz por quê (uma linha INFO)."""
    if is_lean(pipe):
        return True
    reason = unsupported_reason(pipe, version)
    if reason is None:
        try:
            pipe.__class__ = lean_class(type(pipe))
        except TypeError as e:                                   # layout incompatível: nunca visto; fica o original
            reason = f"não troquei a classe ({e})"
    if reason is not None:
        log.info("pyannote: diarização pelo caminho original: %s", reason)
        return False
    _announce()
    return True


def disable(pipe: Any) -> None:
    """Devolve a instância à classe original (a sonda de paridade alterna os dois caminhos na mesma instância)."""
    if is_lean(pipe):
        pipe.__class__ = type(pipe).__mro__[1]


__all__ = ["EXPECTED_MODEL", "EXPECTED_PIPELINE", "LEAN_CLASS_NAME", "disable", "embed_batch", "enable", "is_lean", "lean_class",
           "lean_embeddings", "pyannote_version", "unsupported_reason"]
