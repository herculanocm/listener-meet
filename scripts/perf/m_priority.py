"""M-04 `priority` (só desta máquina, CP15: classes de prioridade do Windows): um filho de CARGA (N threads de numpy em laço,
BLAS em uma thread cada, classe `below_normal` ou `idle`, e dentro dele uma thread que dorme 1 s em laço e registra o maior
intervalo entre despertares, o "heartbeat esfomeado" do PF-02) e um filho MEDIDOR (`small` int8 com 2 threads, como o
filho `--live`, transcrevendo uma janela de 10 s de fala real `--repeat` vezes, classe `below_normal` ou `normal`).
Combinações: sem carga; carga BELOW_NORMAL x medidor BELOW_NORMAL (hoje); carga BELOW_NORMAL x medidor NORMAL; carga
IDLE x medidor NORMAL; e, com `--p-cores`, carga BELOW_NORMAL x medidor NORMAL preso aos núcleos P (PF-12). A classe vai
nas `creationflags` do `Popen` (IDLE 0x40, BELOW_NORMAL 0x4000, NORMAL 0x20). O filho de carga para quando o stdin fecha e
só então imprime o heartbeat. Saída: segundos por janela (mediana) por combinação e o maior intervalo do heartbeat."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import audio
from .common import Report, best_of_medians, child_env, emit_child, interleaved, parse_child, rss_mb, start_child, stats

WINDOW_S = 10.0
LIVE_THREADS = 2
COMBOS: tuple[tuple[str, str | None, str, bool], ...] = (     # (rótulo, classe da carga, classe do medidor, medidor preso aos P)
    ("sem_carga_medidor_below_normal", None, "below_normal", False),
    ("carga_below_normal_medidor_below_normal", "below_normal", "below_normal", False),
    ("carga_below_normal_medidor_normal", "below_normal", "normal", False),
    ("carga_idle_medidor_normal", "idle", "normal", False),
)
COMBO_P = ("carga_below_normal_medidor_normal_p_cores", "below_normal", "normal", True)


def run(args: Any, report: Report, source: audio.Source, db_dir: Path | None, language: str, silence_rms: float) -> None:
    if sys.platform != "win32":
        raise SystemExit("priority: as classes de prioridade são do Windows (CP15); esta sonda só roda lá")
    tmp = Path(tempfile.mkdtemp(prefix="perf-priority-"))
    npy = audio.save_npy(audio.speech_window(source.mono, WINDOW_S, silence_rms), tmp / "window.npy")
    combos = [*COMBOS, COMBO_P] if args.p_cores else list(COMBOS)
    medians: dict[str, list[float]] = {}
    for run_idx, (label, load_class, probe_class, pinned) in interleaved(combos, args.runs):
        load = _start_load(args, load_class) if load_class else None
        time.sleep(1.0 if load else 0.0)                         # a carga sobe antes do medidor nascer
        tail = ["probe", "--npy", str(npy), "--threads", str(LIVE_THREADS), "--repeat", str(args.repeat), "--seconds", str(args.seconds),
                "--language", language, *(["--db", str(db_dir)] if db_dir else []), *(["--p-cores"] if pinned else [])]
        probe = parse_child(*_communicate(start_child(tail, child_env(LIVE_THREADS), probe_class)))
        heartbeat = _stop_load(load) if load is not None else None
        row = report.add("priority_run", combo=label, run=run_idx + 1, load_class=load_class, probe_class=probe_class,
                         probe_pinned=pinned, probe=probe, load=heartbeat)
        if "window_s" in probe:
            medians.setdefault(label, []).append(float(probe["window_s"]["median"]))
        if heartbeat and heartbeat.get("max_gap_s") is not None:
            medians.setdefault(f"heartbeat_gap:{label}", []).append(float(heartbeat["max_gap_s"]))
        del row
    npy.unlink(missing_ok=True)
    tmp.rmdir()
    report.finish(best_median_window_s=best_of_medians(medians), window_s=WINDOW_S, repeat=args.repeat, **source.describe())


def _communicate(proc: subprocess.Popen[str], timeout: float = 3600.0) -> tuple[str, str, int | None]:
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    return stdout, stderr, proc.returncode


def _start_load(args: Any, priority: str) -> subprocess.Popen[str]:
    env = child_env(1, {"OPENBLAS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
    tail = ["load", "--threads", str(args.load_threads), "--fraction", str(args.load_fraction)]
    return start_child(tail, env, priority, stdin=subprocess.PIPE)


def _stop_load(load: subprocess.Popen[str]) -> dict[str, Any]:
    """Fecha o stdin da carga (ela para e imprime o heartbeat) e lê a linha."""
    if load.stdin is not None:
        load.stdin.close()
    stdout, stderr, code = _communicate(load, timeout=30.0)
    return parse_child(stdout, stderr, code)


# ---------------------------------------------------------------- filhos
def load_child(args: Any) -> None:
    """(filho de carga) `threads` threads com produto de matrizes em laço (BLAS em uma thread cada, pelo ambiente), com ciclo
    de trabalho `fraction` (1,0 = sem pausa; 0,85 = 85 % de cada 100 ms); uma thread dorme 1 s em laço e mede o maior
    intervalo entre despertares. Para no EOF do stdin e imprime o heartbeat."""
    import numpy as np

    stop = threading.Event()
    gaps: list[float] = []

    def work() -> None:
        rng = np.random.default_rng()
        a, b = rng.standard_normal((192, 192), dtype=np.float32), rng.standard_normal((192, 192), dtype=np.float32)
        while not stop.is_set():
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.1 * args.fraction and not stop.is_set():
                a = np.tanh(a @ b)
            if args.fraction < 1.0:
                time.sleep(0.1 * (1.0 - args.fraction))

    def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(1.0)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    threads = [threading.Thread(target=work, daemon=True, name=f"load-{i}") for i in range(max(1, args.threads))]
    threads.append(threading.Thread(target=heartbeat, daemon=True, name="heartbeat"))
    started = time.perf_counter()
    for t in threads:
        t.start()
    sys.stdin.buffer.read()                                  # EOF = o pai mandou parar
    stop.set()
    for t in threads:
        t.join(timeout=2.0)
    emit_child(threads=args.threads, fraction=args.fraction, ran_s=round(time.perf_counter() - started, 1),
               max_gap_s=round(max(gaps), 3) if gaps else None, gaps=stats(gaps, 3), peak_rss_mb=rss_mb(peak=True))


def probe_child(args: Any) -> None:
    """(filho medidor) O `small` int8 com 2 threads (como o filho `--live`) numa janela de 10 s, `repeat` vezes ou até
    `seconds`; beam 1, sem timestamps, prompt do ao vivo com o glossário de teste."""
    from .common import job_config, open_real, pin_p_cores, prepare_environment, real_db_dir
    from .models import live_prompt, live_whisper

    mask = pin_p_cores() if args.p_cores else 0
    store = open_real(real_db_dir(args.db))
    config = job_config(store, args.threads)
    prepare_environment(config)
    if store is not None:
        store.close()
    x = audio.load_npy(args.npy)
    whisper, load_s = live_whisper(config, "small", args.threads)
    prompt = live_prompt("")
    runs: list[float] = []
    chars = 0
    started = time.perf_counter()
    for _ in range(max(1, args.repeat)):
        t0 = time.perf_counter()
        result = whisper.transcribe(x, language=args.language, prompt=prompt)
        runs.append(time.perf_counter() - t0)
        chars = sum(len(s.text) for s in result.segments)
        if args.seconds and time.perf_counter() - started > args.seconds:
            break
    emit_child(load_s=load_s, threads=args.threads, pinned_mask=f"{mask:#x}", repeat=len(runs), window_s=stats(runs, 3),
               text_chars=chars, peak_rss_mb=rss_mb(peak=True))
