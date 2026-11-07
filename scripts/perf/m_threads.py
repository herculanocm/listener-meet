"""M-01 `threads`: RTF da diarização (community-1, `PyannoteDiarizer.diarize`) e da transcrição (large-v3-turbo int8,
`FasterWhisperTranscriber.transcribe` com beam 5, `word_timestamps` e `vad_filter`, como o runner) na mesma janela, com
`OMP_NUM_THREADS`/`cpu_threads` em 4, 6, 8, 10, 12 e 14. Cada valor roda num processo filho com `worker_env(n)` e
`torch_threads=n`, porque o OpenMP lê o ambiente ao carregar; `--kmp0` acrescenta a variante 12 com `KMP_BLOCKTIME=0` e
`OMP_WAIT_POLICY=PASSIVE`. Variantes intercaladas; o resumo é o menor RTF por variante."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import audio
from .common import Report, best_of_medians, child_env, emit_child, interleaved, rss_mb, run_child, safe_path

KMP0 = {"KMP_BLOCKTIME": "0", "OMP_WAIT_POLICY": "PASSIVE"}


def variants_of(args: Any) -> list[tuple[str, int, dict[str, str]]]:
    threads = [int(t) for t in str(args.threads).split(",") if t.strip()]
    out = [(f"t{n}", n, {}) for n in threads]
    if args.kmp0:
        out.append(("t12_kmp0", 12, KMP0))
    return out


def run(args: Any, report: Report, source: audio.Source, db_dir: Path | None, language: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perf-threads-"))
    npy = audio.save_npy(source.mono, tmp / "window.npy")
    variants = variants_of(args)
    samples: dict[str, list[float]] = {}
    for run_idx, (label, n, extra) in interleaved(variants, args.runs):
        tail = ["threads", "--npy", str(npy), "--threads", str(n), "--what", args.what, "--language", language]
        if db_dir is not None:
            tail += ["--db", str(db_dir)]
        if args.p_cores:
            tail.append("--p-cores")
        result = run_child(tail, child_env(n, extra))
        report.add("threads_run", variant=label, run=run_idx + 1, extra_env=extra, **result)     # o filho já traz `threads`
        for stage in ("diar", "whisper"):
            rtf = (result.get(stage) or {}).get("rtf")
            if rtf is not None:
                samples.setdefault(f"{stage}:{label}", []).append(float(rtf))
    npy.unlink(missing_ok=True)
    tmp.rmdir()
    report.finish(best_rtf=best_of_medians(samples), **source.describe(), language=language)


def child(args: Any) -> None:
    """(filho) Carrega os dois modelos com `threads` e mede uma janela; uma linha JSON no fim."""
    from .common import job_config, meeting_language, open_real, prepare_environment, read_token, real_db_dir
    from .models import import_torch

    store = open_real(real_db_dir(args.db))
    config = job_config(store, args.threads)
    prepare_environment(config)
    token = read_token(store)
    language = args.language or meeting_language(store, None)
    if store is not None:
        store.close()
    torch, import_s = import_torch(args.threads)
    x = audio.load_npy(args.npy)
    window = audio.audio_window(x)
    result: dict[str, Any] = {"threads": args.threads, "torch_threads": torch.get_num_threads(), "torch_import_s": import_s,
                              "audio_s": round(window.duration_ms / 1000, 1), "models_dir": safe_path(config.models_dir or None)}
    if "diar" in args.what:
        result["diar"] = _measure_diar(token, args.threads, window)
    if "whisper" in args.what:
        result["whisper"] = _measure_whisper(config, args.threads, window, language)
    result["peak_rss_mb"] = rss_mb(peak=True)
    emit_child(**result)


def _measure_diar(token: str, threads: int, window: Any) -> dict[str, Any]:
    import time

    from .models import diarizer

    if not token:
        return {"error": "sem token do Hugging Face no banco real"}
    d, load_s = diarizer(token, threads)
    rss0 = rss_mb()
    t0 = time.perf_counter()
    dw = d.diarize(window, min_speakers=None, max_speakers=None)
    wall = time.perf_counter() - t0
    audio_s = window.duration_ms / 1000
    return {"load_s": load_s, "wall_s": round(wall, 2), "audio_s": round(audio_s, 1), "rtf": round(wall / audio_s, 4),
            "speakers": len({t.speaker for t in dw.turns}), "turns": len(dw.turns), "rss_before_mb": rss0, "rss_after_mb": rss_mb()}


def _measure_whisper(config: Any, threads: int, window: Any, language: str) -> dict[str, Any]:
    import time

    from .models import runner_prompt, transcriber

    t, load_s = transcriber(config, threads)
    rss0 = rss_mb()
    prompt = runner_prompt(with_context=True)
    t0 = time.perf_counter()
    tw = t.transcribe(window, language=language, initial_prompt=prompt)
    wall = time.perf_counter() - t0
    audio_s = window.duration_ms / 1000
    speech_s = max(0.001, tw.speech_ms / 1000)
    return {"load_s": load_s, "wall_s": round(wall, 2), "audio_s": round(audio_s, 1), "speech_s": round(speech_s, 1),
            "rtf": round(wall / audio_s, 4), "rtf_speech": round(wall / speech_s, 4), "words": len(tw.words),
            "rss_before_mb": rss0, "rss_after_mb": rss_mb()}
