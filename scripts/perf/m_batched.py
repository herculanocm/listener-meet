"""M-03 `batched`: na mesma janela, o sequencial de hoje (`FasterWhisperTranscriber.transcribe`, prompt do runner com o
glossário de teste e 60 palavras de contexto: variante `a`) contra `BatchedInferencePipeline(model).transcribe(...)` com
`batch_size` em `--batch` (4 e 8), beam 5, `word_timestamps`, `vad_filter` e `min_silence_duration_ms=500`, em duas
variantes de prompt: `b` com o prompt inteiro e `c` só com o glossário (em lote o prompt vale para todo trecho de 30 s e
o contexto sairia fora de ordem). O lote força `hallucination_silence_threshold=None` e só a primeira temperatura
(`transcribe.py`). Saída por variante: `wall_s`, RTF sobre a fala, nº de palavras, RSS de pico e, contra a `a` da mesma
rodada, a razão de palavras, o `SequenceMatcher.ratio()` sobre as palavras normalizadas e os deltas dos tempos de início
das palavras casadas (mediana, p95, máximo, em ms). Variantes intercaladas; o resumo é o menor `wall_s` por variante."""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from . import audio
from .common import Report, best_of_medians, interleaved, rss_mb
from .models import compare_words, runner_prompt, segment_words, window_words

MIN_SILENCE_MS = 500


def variants_of(args: Any) -> list[tuple[str, int, bool]]:
    """(rótulo, lote (0 = sequencial), prompt com contexto)."""
    batches = [int(b) for b in str(args.batch).split(",") if b.strip()]
    out: list[tuple[str, int, bool]] = [("a_seq_full", 0, True)]
    out += [(f"b_batch{b}_full", b, True) for b in batches]
    out += [(f"c_batch{b}_glossary", b, False) for b in batches]
    return out


def transcribe_batched(pipeline: Any, samples: np.ndarray, language: str, batch: int, prompt: str) -> tuple[list[tuple[int, int, str]], int]:
    segments, _info = pipeline.transcribe(samples, language=language, task="transcribe", beam_size=5, word_timestamps=True,
                                          vad_filter=True, vad_parameters={"min_silence_duration_ms": MIN_SILENCE_MS},
                                          initial_prompt=prompt or None, batch_size=batch)
    return segment_words(list(segments))                        # o gerador só roda aqui


def run(args: Any, report: Report, config: Any, language: str, source: audio.Source) -> None:
    from faster_whisper import BatchedInferencePipeline

    from .models import import_torch, transcriber

    _torch, import_s = import_torch(args.torch_threads)         # torch antes do CTranslate2, como o worker
    t, load_s = transcriber(config, args.torch_threads)
    pipeline = BatchedInferencePipeline(t._load())
    window = audio.audio_window(source.mono)
    samples = np.ascontiguousarray(window.samples, dtype=np.float32)
    prompts = {True: runner_prompt(True), False: runner_prompt(False)}
    report.add("batched_setup", torch_import_s=import_s, load_s=load_s, model=t.name, **source.describe(), language=language,
               prompt_words={"full": len(prompts[True].split()), "glossary": len(prompts[False].split())}, rss_mb=rss_mb())
    walls: dict[str, list[float]] = {}
    base: dict[int, list[tuple[int, int, str]]] = {}
    for run_idx, (label, batch, with_context) in interleaved(variants_of(args), args.runs):
        t0 = time.perf_counter()
        if batch == 0:
            tw = t.transcribe(window, language=language, initial_prompt=prompts[with_context])
            words, speech_ms = window_words(tw), int(tw.speech_ms)
        else:
            words, speech_ms = transcribe_batched(pipeline, samples, language, batch, prompts[with_context])
        wall = time.perf_counter() - t0
        walls.setdefault(label, []).append(round(wall, 3))
        row: dict[str, Any] = {"variant": label, "run": run_idx + 1, "batch": batch, "prompt": "full" if with_context else "glossary",
                               "wall_s": round(wall, 2), "speech_s": round(speech_ms / 1000, 1), "words": len(words),
                               "rtf_speech": round(wall / max(0.001, speech_ms / 1000), 4),
                               "rtf_audio": round(wall / max(0.001, window.duration_ms / 1000), 4), "peak_rss_mb": rss_mb(peak=True)}
        if batch == 0:
            base[run_idx] = words
        elif run_idx in base:
            row["vs_a"] = compare_words(base[run_idx], words)
        report.add("batched_run", **row)
    report.finish(best_wall_s=best_of_medians(walls), audio_s=round(window.duration_ms / 1000, 1), language=language)
