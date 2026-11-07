"""M-06 `pack` e M-07 `embed`, o custo do ao vivo.
`pack`: o `small` int8 com 2 threads (o `WhisperLive` do filho `--live`: beam 1, sem timestamps, prompt do ao vivo com o
glossário de teste) em N janelas de fala real de 5 a 8 s (N = 1 a 4, somando até 25 s), transcritas separadas (N chamadas)
e juntas numa chamada só (0,3 s de zeros entre elas): segundos por N em cada modo, intercalados (PF-09).
`embed`: o `PyannoteEmbedder` (wespeaker) com torch em 1 thread numa janela de 5 s, 20 repetições por rodada: mediana e
p95 em ms, a carga do modelo e o `import torch` em segundos (PF-10)."""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from . import audio
from .common import Report, best_of_medians, interleaved, rss_mb, stats

LIVE_THREADS = 2
GAP_S = 0.3
PACK_MAX_S = 25.0
PIECE_LO_S, PIECE_HI_S = 5.0, 8.0
EMBED_WINDOW_S = 5.0
EMBED_REPEAT = 20


def pieces_for(source: audio.Source, silence_rms: float) -> list[np.ndarray]:
    """4 trechos de 5 a 8 s cujo pacote de 4 (com os 3 intervalos de 0,3 s) cabe em 25 s: cada um cortado em 6 s."""
    cap = int(min(PIECE_HI_S, (PACK_MAX_S - 3 * GAP_S) / 4) * audio.SR)
    return [p[:cap] for p in audio.speech_pieces(source.mono, 4, PIECE_LO_S, PIECE_HI_S, silence_rms)]


def joined(pieces: list[np.ndarray]) -> np.ndarray:
    gap = np.zeros(int(GAP_S * audio.SR), np.float32)
    parts: list[np.ndarray] = []
    for i, p in enumerate(pieces):
        if i:
            parts.append(gap)
        parts.append(p)
    return np.ascontiguousarray(np.concatenate(parts), dtype=np.float32)


def text_chars(whisper: Any, samples: np.ndarray, language: str, prompt: str, timestamps: bool) -> int:
    """Caracteres transcritos: pelo `WhisperLive` (sem timestamps, como o filho faz hoje) ou pelo modelo com timestamps
    (`--timestamps`): sem eles o decodificador pode parar na primeira pausa e o faster-whisper pula o resto do passe de 30 s
    (medido em 07/11: 4 janelas juntas davam o texto de uma só), o que o PF-09 precisa evitar."""
    if not timestamps:
        return sum(len(s.text) for s in whisper.transcribe(samples, language=language, prompt=prompt).segments)
    segments, _info = whisper._model.transcribe(np.ascontiguousarray(samples, dtype=np.float32), language=language, task="transcribe",
                                                beam_size=1, vad_filter=False, word_timestamps=False, condition_on_previous_text=False,
                                                without_timestamps=False, temperature=0.0, initial_prompt=prompt or None)
    return sum(len(s.text) for s in segments)


def run_pack(args: Any, report: Report, config: Any, language: str, source: audio.Source, silence_rms: float) -> None:
    from .models import live_prompt, live_whisper

    whisper, load_s = live_whisper(config, "small", LIVE_THREADS)
    pieces = pieces_for(source, silence_rms)
    prompt = live_prompt("")
    report.add("pack_setup", load_s=load_s, threads=LIVE_THREADS, piece_s=[round(len(p) / audio.SR, 2) for p in pieces],
               prompt_words=len(prompt.split()), **source.describe(), language=language, rss_mb=rss_mb())
    variants = [(n, mode) for n in range(1, 5) for mode in ("separate", "joined")]
    walls: dict[str, list[float]] = {}
    for run_idx, (n, mode) in interleaved(variants, args.runs):
        subset = pieces[:n]
        t0 = time.perf_counter()
        if mode == "separate":
            chars = sum(text_chars(whisper, p, language, prompt, args.timestamps) for p in subset)
        else:
            chars = text_chars(whisper, joined(subset), language, prompt, args.timestamps)
        wall = time.perf_counter() - t0
        label = f"n{n}_{mode}"
        walls.setdefault(label, []).append(round(wall, 3))
        report.add("pack_run", variant=label, run=run_idx + 1, n=n, mode=mode, wall_s=round(wall, 3),
                   audio_s=round(sum(len(p) for p in subset) / audio.SR + (n - 1) * GAP_S * (mode == "joined"), 2), text_chars=chars)
    best = best_of_medians(walls)
    ratio = {f"n{n}": round(best[f"n{n}_joined"] / best[f"n{n}_separate"], 3) for n in range(1, 5)
             if best.get(f"n{n}_joined") and best.get(f"n{n}_separate")}
    report.finish(best_wall_s=best, joined_over_separate=ratio, **source.describe())


def run_embed(args: Any, report: Report, token: str, source: audio.Source, silence_rms: float) -> None:
    from .models import embedder, import_torch

    if not token:
        raise SystemExit("embed precisa do token do Hugging Face do banco real")
    torch, import_s = import_torch(1)
    e, load_s = embedder(token, 1)
    window = audio.speech_window(source.mono, EMBED_WINDOW_S, silence_rms)
    report.add("embed_setup", torch_import_s=import_s, load_s=load_s, torch_threads=torch.get_num_threads(), model=e.model,
               window_s=EMBED_WINDOW_S, **source.describe(), rss_mb=rss_mb())
    medians: dict[str, list[float]] = {}
    for run_idx, _v in interleaved(["embed"], args.runs):
        runs_ms: list[float] = []
        for _ in range(EMBED_REPEAT):
            t0 = time.perf_counter()
            vec = e.embed(window, audio.SR)
            runs_ms.append((time.perf_counter() - t0) * 1000)
        medians.setdefault("embed_ms", []).append(float(np.median(runs_ms)))
        report.add("embed_run", run=run_idx + 1, repeat=EMBED_REPEAT, ms=stats(runs_ms, 1), dim=int(vec.size), peak_rss_mb=rss_mb(peak=True))
    report.finish(best_median_ms=best_of_medians(medians), load_s=load_s, torch_import_s=import_s)
