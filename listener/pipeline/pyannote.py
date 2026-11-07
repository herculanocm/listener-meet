"""`PyannoteDiarizer` e `PyannoteEmbedder` (F2 itens 6 e 7, CP5): pyannote community-1 em CPU com o waveform em memória
(`{"waveform": tensor (1, N), "sample_rate": 16000}`, D-025: nunca torchcodec nem FFmpeg), saída exclusiva para alinhar
mais a lista de trechos sobrepostos, `min_speakers`/`max_speakers` (nunca `num_speakers`), sonda Q8 (`speaker_embeddings`
do resultado e o id do modelo de embedding do pipeline, logados na primeira execução); embeddings por turno com
wespeaker (`Inference(window="whole")`). Importações preguiçosas: o módulo carrega sem torch. Escrito às cegas no WSL
seguindo `scripts/medir.py`; verificado no Windows com o token (B4).
F5.18: `on_progress` vira o `hook` do pipeline (PF-01: o runner confere o cancelamento e a lease a cada lote, na thread de
cálculo); o lote dos embeddings sai de `pipeline.embedding_batch` (PF-03), em trechos de verdade com o caminho novo do PF-04
(`lean_diarization.py`: o tronco do wespeaker uma vez por trecho) e em pares (trechos x falantes locais) no caminho original;
PF-08: o id do modelo de embedding do pipeline resolve para `checkpoint/subfolder` (`_embedding_id`) e a sonda Q8 lê os rótulos
da anotação cheia."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ..config.defaults import EMBEDDING_BATCH
from ..log import get_logger
from ..store.profiles import normalize
from . import lean_diarization as lean
from .base import PermanentError, Progress
from .types import AudioWindow, DiarizationWindow, Overlaps, Turn

log = get_logger("pipeline.pyannote")

DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"
MIN_EMBED_SAMPLES = 16000          # wespeaker precisa de ~1 s: trechos menores são preenchidos com zeros
LOCAL_SPEAKERS = 3                 # F5.18 (PF-03): falantes locais da segmentação do community-1, quando a instância não os expõe


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


def _embedding_id(emb: Any) -> str | None:
    """PF-08: o id do modelo de embedding do pipeline para a sonda Q8. No community-1 o atributo `embedding` é o dicionário
    `{"checkpoint", "subfolder"}` (saía "dict"): vira `checkpoint/subfolder`, e NUNCA o alias `EMBEDDING_REPO`, embora os
    pesos sejam os mesmos (M-08, 07/11: os 218 tensores iguais, cosseno 1,0 num trecho): o centroide VBx que o pipeline
    devolve deu cosseno 0,88 contra o centroide por turno numa voz (0,98 e 0,99 nas outras duas), abaixo dos 0,95 da
    condição da pergunta 7; com o alias, o ramo Q8 de `_embed_window` usaria esses centroides e mudaria a identificação.
    Só as duas chaves saem daqui: o dicionário inteiro pode carregar o token (D-059)."""
    if isinstance(emb, str):
        return emb
    if isinstance(emb, dict):
        checkpoint, sub = emb.get("checkpoint"), emb.get("subfolder")
        if not checkpoint:
            return None
        return f"{checkpoint}/{sub}" if sub else str(checkpoint)
    return getattr(emb, "name_or_path", None) or (type(emb).__name__ if emb else None)


def _hook(on_progress: Progress) -> Callable[..., None]:
    """F5.18 (PF-01): o `hook` do pyannote, chamado de forma síncrona na thread de cálculo a cada lote da segmentação e dos
    embeddings como `hook(etapa, artefato, file=..., total=..., completed=...)` (`speaker_diarization.py`, `apply` e
    `get_embeddings`). Os argumentos são ignorados; a exceção levantada aqui (o `JobCancelledError`) atravessa o pipeline, que
    não a engole (`core/pipeline.py`, `__call__` e `apply` sem `try/except` em volta do gancho)."""
    def hook(*_args: Any, **_kwargs: Any) -> None:
        on_progress()
    return hook


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
    def __init__(self, token: str, *, torch_threads: int = 4, repo: str = DIARIZATION_REPO, embedding_batch: int = EMBEDDING_BATCH):
        self.token = token
        self.torch_threads = torch_threads
        self.repo = repo
        self.embedding_batch = embedding_batch         # F5.18 (PF-03): trechos por lote dos embeddings (`pipeline.embedding_batch`)
        self.name = f"pyannote:{repo.rsplit('/', 1)[-1]}"
        self._pipeline: Any = None
        self.embedding_model: str | None = None        # id do modelo de embedding do pipeline (sonda Q8)
        self.lean = False                              # F5.18 (PF-04): o tronco do wespeaker uma vez por trecho está ativo
        self._probed = False

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        if not self.token:
            raise PermanentError("token do Hugging Face ausente: informe em Configurações, Chaves de acesso")
        pipe = self._pretrained(self.repo, "Pipeline")
        if pipe is None:
            raise PermanentError(f"não consegui carregar {self.repo}: aceite os termos do modelo no Hugging Face e confira o token")
        self.embedding_model = _embedding_id(getattr(pipe, "embedding", None))
        self.lean = lean.enable(pipe)                                      # PF-04: senão o caminho original, com o motivo no log
        log.info("pyannote %s carregado em CPU (%d threads); modelo de embedding do pipeline: %s; embeddings: %s", self.repo,
                 self.torch_threads, self.embedding_model, "tronco uma vez por trecho" if self.lean else "caminho original")
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

    def _set_embedding_batch(self, pipe: Any) -> None:
        """F5.18 (PF-03): o `embedding_batch_size` do pyannote original conta pares (trecho, falante) e vinha dos 32 do
        `config.yaml` (ativações do ResNet34 de centenas de MB por lote); aqui vale `pipeline.embedding_batch` em trechos vezes
        os falantes locais do modelo de segmentação (3 no community-1). Com o caminho novo do PF-04 (`LeanDiarization`) o lote
        já conta trechos: o valor entra como está. Por chamada, para a configuração valer em uso."""
        if not hasattr(pipe, "embedding_batch_size"):
            log.debug("pipeline sem embedding_batch_size: lote dos embeddings não ajustado")
            return
        trechos = max(1, int(self.embedding_batch))
        if lean.is_lean(pipe):
            batch, unit = trechos, "trechos (tronco uma vez por trecho)"
        else:
            try:
                local = len(pipe._segmentation.model.specifications.classes)
            except Exception:
                local = LOCAL_SPEAKERS
            batch, unit = trechos * max(1, local), f"pares ({trechos} trechos x {max(1, local)} falantes locais)"
        if pipe.embedding_batch_size != batch:
            pipe.embedding_batch_size = batch
            log.info("pyannote: lote dos embeddings %d %s", batch, unit)

    def diarize(self, window: AudioWindow, *, min_speakers: int | None, max_speakers: int | None,
                on_progress: Progress | None = None) -> DiarizationWindow:
        pipe = self._load()
        self._set_embedding_batch(pipe)
        kwargs: dict[str, Any] = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers is not None:
            kwargs["max_speakers"] = int(max_speakers)
        if on_progress is not None:
            kwargs["hook"] = _hook(on_progress)                    # PF-01: `pipe(file, hook=...)` chega ao `apply` como está
        result = pipe({"waveform": _waveform(window.samples), "sample_rate": window.sample_rate}, **kwargs)
        full = getattr(result, "speaker_diarization", None)
        exclusive = getattr(result, "exclusive_speaker_diarization", None)
        if full is None and exclusive is None:
            full = result                                          # versões antigas devolvem a Annotation direto
        base = exclusive if exclusive is not None else full
        turns = turns_of(base, window.t0_ms)
        overlap = overlaps_of(full, window.t0_ms) if full is not None else ()
        embeddings = self._probe(result, full if full is not None else base)     # PF-08: os rótulos da anotação cheia
        return DiarizationWindow(window.idx, window.t0_ms, window.t1_ms, turns, overlap, embeddings, self.embedding_model)

    def _probe(self, result: Any, annotation: Any) -> dict[str, np.ndarray] | None:
        """Q8: `speaker_embeddings[i]` corresponde a `speaker_diarization.labels()[i]`, a anotação CHEIA (PF-08: um falante só em
        sobreposição some da exclusiva e a forma deixaria de bater); o runner só os usa se o modelo for o do perfil, o que o
        `_embedding_id` hoje impede de propósito (M-08)."""
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
            raise PermanentError("token do Hugging Face ausente: informe em Configurações, Chaves de acesso")
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
