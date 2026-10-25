"""Pipeline simulado (F2, D-F2-16): roda o `JobRunner` de ponta a ponta sem torch sobre os tons da `FakePlatform`
(440 Hz no microfone, 220 Hz no app, 330 Hz no sistema). `LISTENER_PIPELINE=fake` faz o worker real usar isto."""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

from listener.pipeline.base import PipelineDeps, ProgressFn
from listener.pipeline.types import AudioWindow, DiarizationWindow, TranscriptWindow, Turn, Word

type Script = Sequence[tuple[float, float, str]]      # (t0 s, t1 s, texto) na linha do tempo da sessão
type Scripts = Script | dict[str, Script]             # um roteiro para todos os canais ou um por canal


def dominant_freq(x: np.ndarray, sample_rate: int) -> float:
    """Frequência de maior energia (Hz); 0 quando não há sinal."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size < 16 or float(np.dot(x, x)) / x.size < 1e-6:
        return 0.0
    spec = np.abs(np.fft.rfft(x))
    spec[0] = 0.0
    k = int(np.argmax(spec))
    return float(k * sample_rate / x.size)


class ScriptedTranscriber:
    """Devolve as palavras do roteiro que caem na janela, com tempos espaçados igualmente dentro de cada fala.
    `fail_once_at` = {(canal, idx)}: a primeira chamada dessa janela levanta (simula uma queda para o teste de retomada)."""

    def __init__(self, script: Scripts, name: str = "fake:script", language: str = "pt",
                 fail_once_at: set[tuple[str, int]] | None = None):
        self.scripts: dict[str, list[tuple[float, float, str]]] = (
            {k: list(v) for k, v in script.items()} if isinstance(script, dict) else {"*": list(script)})
        self.name = name
        self.language = language
        self.fail_once_at = set(fail_once_at or ())
        self.calls: list[tuple[str, int, str | None, str]] = []     # (canal, idx, idioma pedido, initial_prompt)

    def transcribe(self, window: AudioWindow, *, language: str | None, initial_prompt: str) -> TranscriptWindow:
        self.calls.append((window.channel, window.idx, language, initial_prompt))
        if (window.channel, window.idx) in self.fail_once_at:
            self.fail_once_at.discard((window.channel, window.idx))
            raise RuntimeError(f"queda simulada na janela {window.channel}{window.idx}")
        words: list[Word] = []
        speech = 0
        for t0, t1, text in self.scripts.get(window.channel, self.scripts.get("*", [])):
            a, b = int(t0 * 1000), int(t1 * 1000)
            if b <= window.t0_ms or a >= window.t1_ms:
                continue
            speech += min(b, window.t1_ms) - max(a, window.t0_ms)
            toks = text.split()
            step = (b - a) / max(1, len(toks))
            for i, tok in enumerate(toks):
                ws, we = round(a + i * step), round(a + (i + 1) * step) - 10
                if window.t0_ms <= (ws + we) // 2 < window.t1_ms:
                    words.append(Word(ws, max(ws + 1, we), tok, 0.9))
        return TranscriptWindow(window.idx, window.t0_ms, window.t1_ms, tuple(words), self.language, 0.99, speech, window.overlap_ms)


class ToneDiarizer:
    """Rótulo por frequência dominante a cada 500 ms: cada tom é um falante; quadros silenciosos não viram turno."""

    def __init__(self, frame_ms: int = 500, silence_rms: float = 0.01, name: str = "fake:tone"):
        self.frame_ms = frame_ms
        self.silence_rms = silence_rms
        self.name = name
        self.calls: list[tuple[int, int | None, int | None]] = []

    def diarize(self, window: AudioWindow, *, min_speakers: int | None, max_speakers: int | None) -> DiarizationWindow:
        self.calls.append((window.idx, min_speakers, max_speakers))
        n = int(window.sample_rate * self.frame_ms / 1000)
        labels: list[str | None] = []
        for i in range(0, len(window.samples) - n + 1, n):
            f = window.samples[i:i + n]
            r = math.sqrt(float(np.dot(f, f)) / n)
            labels.append(None if r < self.silence_rms else f"T{round(dominant_freq(f, window.sample_rate) / 10) * 10}")
        turns: list[Turn] = []
        for i, lab in enumerate(labels):
            if lab is None:
                continue
            s, e = window.t0_ms + i * self.frame_ms, window.t0_ms + (i + 1) * self.frame_ms
            if turns and turns[-1].speaker == lab and turns[-1].end_ms == s:
                turns[-1] = Turn(turns[-1].start_ms, e, lab)
            else:
                turns.append(Turn(s, e, lab))
        # rótulos locais da janela, na ordem de aparição (como o pyannote: SPEAKER_00 não é o mesmo entre janelas)
        local: dict[str, str] = {}
        for t in turns:
            local.setdefault(t.speaker, f"SPEAKER_{len(local):02d}")
        return DiarizationWindow(window.idx, window.t0_ms, window.t1_ms, tuple(Turn(t.start_ms, t.end_ms, local[t.speaker]) for t in turns))


class ToneEmbedder:
    """Vetor determinístico de 256 dimensões: one-hot suavizado na posição da frequência dominante."""

    def __init__(self, dim: int = 256, model: str = "fake-tone-256"):
        self.dim = dim
        self.model = model
        self.calls = 0

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        self.calls += 1
        f = dominant_freq(samples, sample_rate)
        center = (f / 4.0) % self.dim                           # 220 -> 55, 330 -> 82, 440 -> 110
        i = np.arange(self.dim, dtype=np.float32)
        v = np.exp(-0.5 * ((i - center) / 1.5) ** 2).astype(np.float32)
        return v / float(np.linalg.norm(v))


class FakeDeps(PipelineDeps):
    def __init__(self, script: Scripts = (), prepare: Callable[[ProgressFn], None] | None = None,
                 fail_once_at: set[tuple[str, int]] | None = None):
        self.scripted = ScriptedTranscriber(script, fail_once_at=fail_once_at)
        self.tones = ToneDiarizer()
        self.tone_embedder = ToneEmbedder()
        super().__init__(lambda: self.scripted, lambda: self.tones, lambda: self.tone_embedder, prepare, name="fake")
