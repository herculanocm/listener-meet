"""`LLMAudioTranscriber` (D-020, F3 item 9): implementa `Transcriber` com um provedor de LLM que aceita áudio nativo.
Uma janela por `asyncio.run()` com cliente próprio; FLAC 16 kHz mono (Gemini aceita até 20 MB em linha; 10 min ≈ 8 a 10 MB
[exp]); provedores sem FLAC recebem WAV em janelas de até 5 min. A saída (falas com tempos relativos) vira `Word`s
interpoladas: o `Segmenter` decide o falante pelo centro de cada palavra (uma fala de 15 s com dois falantes iria inteira
para um só). `name = llm:<provedor>:<modelo>`; `language_prob = 0,9` fixa [chute]."""
from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from ..capture.audio import encode_flac, to_int16
from ..llm import prompts
from ..llm.base import LLMProvider, ProviderUnavailableError
from ..llm.types import AudioPart, Capability, LlmTask, ProviderKind, Request
from ..log import get_logger
from .base import PermanentError
from .types import AudioWindow, TranscriptWindow, Word

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("pipeline.llm_audio")

FLAC_PROVIDERS = frozenset({ProviderKind.GEMINI})
WAV_WINDOW_S = 300
LANGUAGE_PROB = 0.9
MAX_INLINE_BYTES = 20_000_000


def encode_wav(mono: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    with sf.SoundFile(buf, mode="w", samplerate=int(sample_rate), channels=1, format="WAV", subtype="PCM_16") as f:
        f.write(to_int16(mono))
    return buf.getvalue()


def interpolate_words(utterances: list[dict[str, Any]], t0_ms: int, t1_ms: int) -> tuple[tuple[Word, ...], int]:
    """Palavras com tempos espaçados igualmente dentro de cada fala (F3A-40); devolve também os ms de fala."""
    words: list[Word] = []
    speech = 0
    for u in utterances:
        try:
            a, b = int(float(u["start_s"]) * 1000), int(float(u["end_s"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        text = str(u.get("text") or "").strip()
        if not text or b <= a:
            continue
        a, b = max(0, a), min(b, t1_ms - t0_ms)
        if b <= a:
            continue
        speech += b - a
        toks = text.split()
        step = (b - a) / len(toks)
        for i, tok in enumerate(toks):
            ws = t0_ms + round(a + i * step)
            we = t0_ms + round(a + (i + 1) * step)
            words.append(Word(ws, max(ws + 1, we - 1), tok, LANGUAGE_PROB))
    return tuple(words), speech


class LLMAudioTranscriber:
    """`Transcriber` sobre `LLMProvider.complete` com `AudioPart`; `provider_factory` cria o provedor dentro do event loop."""

    def __init__(self, provider_factory: Callable[[], LLMProvider], kind: ProviderKind, model: str, provider_id: str = ""):
        self.provider_factory = provider_factory
        self.kind = kind
        self.model = model
        self.name = f"llm:{provider_id or kind}:{model}"
        self.calls: list[tuple[int, int, str]] = []          # (idx, bytes de áudio, mime)

    def _encode(self, window: AudioWindow) -> list[tuple[AudioPart, int, int]]:
        """[(parte, t0 relativo ms, t1 relativo ms)]: FLAC inteiro, ou WAV em fatias de 5 min para quem não aceita FLAC."""
        samples = np.asarray(window.samples, dtype=np.float32).reshape(-1)
        if self.kind in FLAC_PROVIDERS:
            data = encode_flac(samples, window.sample_rate)
            if len(data) <= MAX_INLINE_BYTES:
                return [(AudioPart(data, "audio/flac", "janela"), 0, window.duration_ms)]
        out: list[tuple[AudioPart, int, int]] = []
        step = WAV_WINDOW_S * window.sample_rate
        for i in range(0, max(1, len(samples)), step):
            chunk = samples[i:i + step]
            if not len(chunk):
                break
            t0 = int(i * 1000 / window.sample_rate)
            out.append((AudioPart(encode_wav(chunk, window.sample_rate), "audio/wav", f"janela parte {len(out) + 1}"), t0,
                        t0 + int(len(chunk) * 1000 / window.sample_rate)))
        return out

    def transcribe(self, window: AudioWindow, *, language: str | None, initial_prompt: str) -> TranscriptWindow:
        parts = self._encode(window)

        async def go() -> list[tuple[dict[str, Any], int, int]]:
            provider = self.provider_factory()
            if Capability.AUDIO_IN not in provider.capabilities:
                raise ProviderUnavailableError(f"{provider.id} não aceita áudio: escolha outro provedor ou volte ao Whisper")
            results: list[tuple[dict[str, Any], int, int]] = []
            try:
                for part, t0, t1 in parts:
                    ctx = prompts.transcribe.TranscribeCtx(part, language, initial_prompt)
                    req = Request(LlmTask.TRANSCRIBE, self.model, prompts.transcribe.build(ctx), prompts.transcribe.SCHEMA,
                                  max_output_tokens=8192, timeout_s=300.0)
                    comp = await provider.complete(req)
                    self.calls.append((window.idx, len(part.data), part.mime))
                    results.append((dict(comp.data or {}), t0, t1))
            finally:
                await provider.aclose()
            return results

        try:
            results = asyncio.run(go())
        except ProviderUnavailableError as e:
            raise PermanentError(str(e)) from e
        words: list[Word] = []
        speech = 0
        lang: str | None = None
        for data, t0, t1 in results:
            w, s = interpolate_words(list(data.get("utterances") or []), window.t0_ms + t0, window.t0_ms + t1)
            words += w
            speech += s
            lang = lang or (str(data.get("language") or "").strip()[:5] or None)
        return TranscriptWindow(window.idx, window.t0_ms, window.t1_ms, tuple(words), lang or language, LANGUAGE_PROB if lang else 0.0,
                                speech, window.overlap_ms)


def llm_transcriber(store: Store) -> LLMAudioTranscriber:
    """Fábrica para `real_deps` com `pipeline.transcriber = llm`: provedor ativo e modelo rápido lidos do banco."""
    from ..llm.jobconfig import LlmJobConfig
    from ..llm.registry import build_provider

    cfg = LlmJobConfig.from_store(store)
    if not cfg.ready:
        raise PermanentError("transcrição pela LLM: nenhum provedor ativo com modelo nas Configurações (LLM)")

    def factory() -> LLMProvider:
        return build_provider(store, cfg.provider_id)[0]

    return LLMAudioTranscriber(factory, ProviderKind(cfg.provider_kind), cfg.fast_model, cfg.provider_id)
