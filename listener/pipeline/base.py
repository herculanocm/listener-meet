"""Contratos do pipeline (F2 item 1): `Transcriber`, `Diarizer` e `Embedder` são Protocols; `PipelineDeps` entrega as
implementações por fábricas preguiçosas, uma instância por processo (D-F2-3), e `release()` as solta quando o worker fica
ocioso. A F3 implementa `Transcriber` com um provedor de LLM (`name` começa por `llm:`) sem tocar no resto (item 18).
`PermanentError` marca falhas que não contam tentativa (token ausente, termos não aceitos, extra ausente, sem rede)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

from .types import AudioWindow, DiarizationWindow, TranscriptWindow

type ProgressFn = Callable[[str, float], None]     # (mensagem, fração 0..1) para "baixando modelo…"


class PermanentError(RuntimeError):
    """Erro que uma nova tentativa automática não resolve (item 11): mensagem própria para o usuário."""


class Transcriber(Protocol):
    name: str                                        # ex.: faster-whisper:large-v3-turbo:int8

    def transcribe(self, window: AudioWindow, *, language: str | None, initial_prompt: str) -> TranscriptWindow: ...


class Diarizer(Protocol):
    name: str

    def diarize(self, window: AudioWindow, *, min_speakers: int | None, max_speakers: int | None) -> DiarizationWindow: ...


class Embedder(Protocol):
    model: str                                       # id real do modelo (D-009): `voice_profiles.model`
    dim: int

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray: ...   # float32, norma L2 = 1


class PipelineDeps:
    """Fábricas preguiçosas: nada pesado é importado antes da etapa que precisa; `prepare` baixa modelos com progresso."""

    def __init__(self, transcriber: Callable[[], Transcriber], diarizer: Callable[[], Diarizer], embedder: Callable[[], Embedder],
                 prepare: Callable[[ProgressFn], None] | None = None, name: str = "fake"):
        self._transcriber_factory = transcriber
        self._diarizer_factory = diarizer
        self._embedder_factory = embedder
        self._prepare = prepare
        self.name = name
        self._transcriber: Transcriber | None = None
        self._diarizer: Diarizer | None = None
        self._embedder: Embedder | None = None
        self.prepared = False

    def prepare(self, progress: ProgressFn) -> None:
        if not self.prepared and self._prepare is not None:
            self._prepare(progress)
        self.prepared = True

    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            self._transcriber = self._transcriber_factory()
        return self._transcriber

    def diarizer(self) -> Diarizer:
        if self._diarizer is None:
            self._diarizer = self._diarizer_factory()
        return self._diarizer

    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        return self._embedder

    def release(self) -> None:
        """Solta os modelos (1 a 2 GB) quando o worker fica ocioso; a próxima etapa recarrega."""
        self._transcriber = self._diarizer = self._embedder = None
