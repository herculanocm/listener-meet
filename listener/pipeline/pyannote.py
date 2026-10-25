"""`PyannoteDiarizer` e `PyannoteEmbedder` (F2 itens 6 e 7, CP5): pyannote community-1 em CPU com o waveform em memória
(`{"waveform": tensor (1, N), "sample_rate": 16000}`, D-025: nunca torchcodec nem FFmpeg), saída exclusiva para alinhar
mais a lista de trechos sobrepostos, `min_speakers`/`max_speakers` (nunca `num_speakers`), sonda Q8 (`speaker_embeddings`
do resultado e o id do modelo de embedding do pipeline, logados na primeira execução); embeddings por turno com
wespeaker (`Inference(window="whole")`). Importações preguiçosas: o módulo carrega sem torch. Escrito às cegas no WSL
seguindo `scripts/medir.py`; verificado no Windows com o token (B4)."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..log import get_logger
from ..store.profiles import normalize
from .base import PermanentError
from .types import AudioWindow, DiarizationWindow, Overlaps, Turn

log = get_logger("pipeline.pyannote")

DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"
MIN_EMBED_SAMPLES = 16000          # wespeaker precisa de ~1 s: trechos menores são preenchidos com zeros


def _permanent(e: BaseException, repo: str) -> PermanentError | None:
    from .models import INSTALL_HINT, classify_error

    if isinstance(e, ImportError):
        return PermanentError(f"{INSTALL_HINT} ({e})")
    return classify_error(e, repo)


def _from_pretrained(factory: Any, repo: str, token: str) -> Any:
    """`token=` (pyannote.audio 3.1+/4) com fallback para `use_auth_token=` (versões anteriores), como em `medir.py`."""
    try:
        return factory(repo, token=token or None)
    except TypeError:
        return factory(repo, use_auth_token=token or None)


def _set_threads(n: int) -> None:
    import torch

    torch.set_num_threads(max(1, int(n)))


def _waveform(samples: np.ndarray) -> Any:
    import torch

    x = np.ascontiguousarray(samples, dtype=np.float32)
    return torch.from_numpy(x)[None, :]


def overlaps_of(annotation: Any, t0_ms: int) -> Overlaps:
    """Trechos em que mais de um falante fala, a partir da saída não exclusiva (`Annotation.get_overlap()`)."""
    try:
        timeline = annotation.get_overlap()
    except Exception as e:
        log.debug("sobreposições indisponíveis: %s", e)
        return ()
    return tuple((t0_ms + round(float(s.start) * 1000), t0_ms + round(float(s.end) * 1000)) for s in timeline)


def turns_of(annotation: Any, t0_ms: int) -> tuple[Turn, ...]:
    turns = [Turn(t0_ms + round(float(seg.start) * 1000), t0_ms + round(float(seg.end) * 1000), str(label))
             for seg, _, label in annotation.itertracks(yield_label=True)]
    return tuple(t for t in sorted(turns, key=lambda t: (t.start_ms, t.end_ms)) if t.end_ms > t.start_ms)


class PyannoteDiarizer:
    def __init__(self, token: str, *, torch_threads: int = 4, repo: str = DIARIZATION_REPO):
        self.token = token
        self.torch_threads = torch_threads
        self.repo = repo
        self.name = f"pyannote:{repo.rsplit('/', 1)[-1]}"
        self._pipeline: Any = None
        self.embedding_model: str | None = None        # id do modelo de embedding do pipeline (sonda Q8)
        self._probed = False

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        if not self.token:
            raise PermanentError("token do Hugging Face ausente: informe nas Configurações (Processamento)")
        pipe = self._pretrained(self.repo, "Pipeline")
        if pipe is None:
            raise PermanentError(f"não consegui carregar {self.repo}: aceite os termos do modelo no Hugging Face e confira o token")
        emb = getattr(pipe, "embedding", None)
        self.embedding_model = emb if isinstance(emb, str) else getattr(emb, "name_or_path", None) or (type(emb).__name__ if emb else None)
        log.info("pyannote %s carregado em CPU (%d threads); modelo de embedding do pipeline: %s", self.repo, self.torch_threads,
                 self.embedding_model)
        self._pipeline = pipe
        return pipe

    def _pretrained(self, repo: str, kind: str) -> Any:
        try:
            _set_threads(self.torch_threads)
            from pyannote.audio import Model, Pipeline

            factory = Pipeline.from_pretrained if kind == "Pipeline" else Model.from_pretrained
            loaded = _from_pretrained(factory, repo, self.token)
        except Exception as e:
            err = _permanent(e, repo)
            if err is not None:
                raise err from e
            raise
        return loaded

    def diarize(self, window: AudioWindow, *, min_speakers: int | None, max_speakers: int | None) -> DiarizationWindow:
        pipe = self._load()
        kwargs: dict[str, int] = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers is not None:
            kwargs["max_speakers"] = int(max_speakers)
        result = pipe({"waveform": _waveform(window.samples), "sample_rate": window.sample_rate}, **kwargs)
        full = getattr(result, "speaker_diarization", None)
        exclusive = getattr(result, "exclusive_speaker_diarization", None)
        if full is None and exclusive is None:
            full = result                                          # versões antigas devolvem a Annotation direto
        base = exclusive if exclusive is not None else full
        turns = turns_of(base, window.t0_ms)
        overlap = overlaps_of(full, window.t0_ms) if full is not None else ()
        embeddings = self._probe(result, base)
        return DiarizationWindow(window.idx, window.t0_ms, window.t1_ms, turns, overlap, embeddings, self.embedding_model)

    def _probe(self, result: Any, annotation: Any) -> dict[str, np.ndarray] | None:
        """Q8: `speaker_embeddings[i]` corresponde a `labels()[i]`; o runner só os usa se o modelo for o do perfil."""
        emb = getattr(result, "speaker_embeddings", None)
        if not self._probed:
            self._probed = True
            log.info("sonda Q8: speaker_embeddings=%s, resultado=%s, modelo=%s", getattr(emb, "shape", None),
                     type(result).__name__, self.embedding_model)
        if emb is None:
            return None
        try:
            labels = list(annotation.labels())
            arr = np.asarray(emb, dtype=np.float32)
        except Exception as e:
            log.debug("speaker_embeddings ilegíveis: %s", e)
            return None
        if arr.ndim != 2 or arr.shape[0] != len(labels):
            return None
        out = {str(label): normalize(arr[i]) for i, label in enumerate(labels) if np.all(np.isfinite(arr[i])) and np.any(arr[i])}
        return out or None


class PyannoteEmbedder:
    def __init__(self, token: str, *, torch_threads: int = 4, repo: str = EMBEDDING_REPO):
        self.token = token
        self.torch_threads = torch_threads
        self.model = repo
        self.dim = 256
        self._inference: Any = None

    def _load(self) -> Any:
        if self._inference is not None:
            return self._inference
        if not self.token:
            raise PermanentError("token do Hugging Face ausente: informe nas Configurações (Processamento)")
        model = PyannoteDiarizer._pretrained(self, self.model, "Model")  # type: ignore[arg-type]
        if model is None:
            raise PermanentError(f"não consegui carregar {self.model}: aceite os termos do modelo no Hugging Face")
        from pyannote.audio import Inference

        self._inference = Inference(model, window="whole")
        log.info("embedding %s carregado em CPU", self.model)
        return self._inference

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        inference = self._load()
        x = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        if x.size < MIN_EMBED_SAMPLES:
            x = np.pad(x, (0, MIN_EMBED_SAMPLES - x.size))
        vec = inference({"waveform": _waveform(x), "sample_rate": int(sample_rate)})
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        self.dim = int(v.size)
        return normalize(v)
