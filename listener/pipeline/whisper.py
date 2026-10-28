"""`FasterWhisperTranscriber` (F2 item 5, D-028): faster-whisper em CPU int8 com `word_timestamps`, VAD, `beam_size=5`,
`condition_on_previous_text=False` e `hallucination_silence_threshold=2.0`; segmentos com `no_speech_prob > 0,6` ou
`avg_logprob < -1,0` são descartados; o idioma vem da reunião (F5.3 A-01: o runner nunca pede detecção). Opção "alta
precisão" realinha as palavras com wav2vec2 pelo WhisperX quando instalado (`pipeline-hq`), caindo nos tempos do próprio
Whisper em qualquer falha. Idioma sem modelo de alinhamento (o inglês, A-04): os tempos do Whisper, com uma linha no log
por job e sem desligar a opção no processo (antes, o `en` caía na pasta de modelos, falhava e desligava a alta precisão
das reuniões seguintes do mesmo worker). Importações preguiçosas: o módulo carrega sem torch; só o worker instancia.
Escrito às cegas no WSL seguindo `scripts/medir.py`; verificado no Windows com os modelos (B4)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..log import get_logger
from .base import PermanentError
from .types import AudioWindow, TranscriptWindow, Word

log = get_logger("pipeline.whisper")

BEAM_SIZE = 5
MIN_SILENCE_MS = 500
NO_SPEECH_MAX = 0.6
LOGPROB_MIN = -1.0
HALLUCINATION_SILENCE_S = 2.0
ALIGN_MODELS = {"pt": "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"}      # mesmo do F0 (`scripts/medir.py`)


class FasterWhisperTranscriber:
    def __init__(self, model_dir: Path, model_name: str, *, cpu_threads: int = 4, hq: bool = False, compute_type: str = "int8"):
        self.model_dir = Path(model_dir)
        self.model_name = model_name
        self.cpu_threads = max(1, int(cpu_threads))
        self.hq = hq
        self.compute_type = compute_type
        self.name = f"faster-whisper:{model_name}:{compute_type}" + (":hq" if hq else "")
        self._model: Any = None
        self._align: dict[str, tuple[Any, Any]] = {}
        self._align_failed = False
        self._unaligned: set[str] = set()                # A-04: idiomas sem wav2vec2 já avisados nesta passada
        self._last_r_t0 = -1

    # ---------------------------------------------------------------- carga
    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            from .models import INSTALL_HINT

            raise PermanentError(f"{INSTALL_HINT} ({e})") from e
        source = str(self.model_dir) if (self.model_dir / "model.bin").exists() else self.model_name
        try:
            self._model = WhisperModel(source, device="cpu", compute_type=self.compute_type, cpu_threads=self.cpu_threads, num_workers=1)
        except Exception as e:
            from .models import classify_error

            err = classify_error(e, f"faster-whisper {self.model_name}")
            if err is not None:
                raise err from e
            raise
        log.info("faster-whisper %s carregado de %s (%s, %d threads)", self.model_name, source, self.compute_type, self.cpu_threads)
        return self._model

    # ---------------------------------------------------------------- transcrição
    def transcribe(self, window: AudioWindow, *, language: str | None, initial_prompt: str) -> TranscriptWindow:
        self._new_pass(window)
        model = self._load()
        samples = np.ascontiguousarray(window.samples, dtype=np.float32)
        segments, info = model.transcribe(
            samples, language=language, task="transcribe", beam_size=BEAM_SIZE, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": MIN_SILENCE_MS}, condition_on_previous_text=False,
            initial_prompt=initial_prompt or None, hallucination_silence_threshold=HALLUCINATION_SILENCE_S)
        kept: list[Any] = []
        speech_ms = 0
        for seg in segments:                                   # o gerador só roda aqui
            if float(getattr(seg, "no_speech_prob", 0.0)) > NO_SPEECH_MAX or float(getattr(seg, "avg_logprob", 0.0)) < LOGPROB_MIN:
                continue
            kept.append(seg)
            speech_ms += max(0, round((float(seg.end) - float(seg.start)) * 1000))
        words = self._words(kept, window)
        if self.hq and words:
            words = self._align_hq(kept, words, samples, window, getattr(info, "language", None))
        lang = getattr(info, "language", None)
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return TranscriptWindow(window.idx, window.t0_ms, window.t1_ms, words, lang, prob, int(speech_ms), window.overlap_ms)

    @staticmethod
    def _words(segments: list[Any], window: AudioWindow) -> tuple[Word, ...]:
        out: list[Word] = []
        for seg in segments:
            for w in getattr(seg, "words", None) or ():
                text = str(w.word).strip()
                if not text:
                    continue
                start = window.t0_ms + round(float(w.start) * 1000)
                end = window.t0_ms + round(float(w.end) * 1000)
                out.append(Word(start, max(end, start + 1), text, float(getattr(w, "probability", 1.0) or 1.0)))
        return tuple(out)

    # ---------------------------------------------------------------- alta precisão (opcional)
    def _new_pass(self, window: AudioWindow) -> None:
        """O transcritor vive no worker de um job para o outro; o runner transcreve o R primeiro, em janelas de início
        crescente: uma janela do R que não começa depois da anterior abre outro job (ou outra tentativa) e os avisos do
        A-04 voltam a valer (uma linha por job, não uma por janela)."""
        if window.channel != "R":
            return
        if window.t0_ms <= self._last_r_t0:
            self._unaligned.clear()
        self._last_r_t0 = window.t0_ms

    def _align_hq(self, segments: list[Any], words: tuple[Word, ...], samples: np.ndarray, window: AudioWindow,
                  language: str | None) -> tuple[Word, ...]:
        """Realinha com wav2vec2 (WhisperX). Idioma sem modelo de alinhamento: as palavras do Whisper, sem desligar a opção
        (A-04). Qualquer falha devolve as palavras do Whisper e desliga a opção no processo."""
        if self._align_failed or not language:
            return words
        if language not in ALIGN_MODELS:
            if language not in self._unaligned:
                self._unaligned.add(language)
                log.info("alta precisão: sem modelo de alinhamento para %s; ficam os tempos do Whisper (a opção segue ligada)",
                         language)
            return words
        try:
            import whisperx

            if language not in self._align:
                local = self.model_dir.parent / ALIGN_MODELS[language].rsplit("/", 1)[-1]
                kw = {"model_name": str(local)} if local.exists() else {}
                self._align[language] = whisperx.load_align_model(language_code=language, device="cpu", **kw)
            model, meta = self._align[language]
            segs = [{"start": float(s.start), "end": float(s.end), "text": str(s.text)} for s in segments]
            aligned = whisperx.align(segs, model, meta, samples, "cpu", return_char_alignments=False)
            out: list[Word] = []
            for seg in aligned.get("segments", []):
                for w in seg.get("words", []):
                    if "start" not in w or "end" not in w:
                        continue
                    start = window.t0_ms + round(float(w["start"]) * 1000)
                    end = window.t0_ms + round(float(w["end"]) * 1000)
                    out.append(Word(start, max(end, start + 1), str(w.get("word", "")).strip(), float(w.get("score", 1.0) or 1.0)))
            return tuple(out) if out else words
        except Exception as e:
            self._align_failed = True
            log.warning("alta precisão (wav2vec2) indisponível: %s; seguindo com os tempos do Whisper", e)
            return words
