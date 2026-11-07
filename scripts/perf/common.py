"""Infra das sondas do lote 0 (F5.18, D-096): saída JSON (uma linha por resultado e um arquivo por rodada), ambiente da
máquina (máscara P/E, threads do OpenMP, versões), o banco real só em `mode=ro` (token nunca impresso), a conferência de
que ninguém grava, os filhos com classe de prioridade e as estatísticas (variantes intercaladas, o menor das medianas).
D-059: nada pessoal sai daqui: caminhos saem com a pasta do usuário trocada por `~` e as reuniões só pelo id."""
from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]          # scripts/perf/common.py -> raiz do checkout
SCRIPTS = ROOT / "scripts"
for _p in (str(ROOT), str(SCRIPTS)):                 # o pacote deste checkout e os scripts vizinhos (probe_ui, rag_probe)
    if _p not in sys.path:
        sys.path.insert(0, _p)

HF_PROVIDER = "huggingface"
DISTS = ("torch", "ctranslate2", "faster-whisper", "pyannote.audio", "onnxruntime")
OMP_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "KMP_BLOCKTIME", "OMP_WAIT_POLICY")
PRIORITY_CLASSES = {"idle": 0x40, "below_normal": 0x4000, "normal": 0x20}     # creationflags do Windows
SR = 16000
STALE_S = 60.0                                       # heartbeat mais novo que isto = worker vivo (store/jobs.py)
CHILD_TIMEOUT_S = 4 * 3600


def out(stage: str, **kw: Any) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def safe_path(path: str | Path | None) -> str | None:
    """O caminho sem a pasta do usuário (D-059): `C:/Users/fulano/x` vira `~/x`."""
    if path is None:
        return None
    text = str(path).replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")
    if text.lower().startswith(home.lower()):
        return "~" + text[len(home):]
    return text


def safe_text(text: str) -> str:
    """Um texto (o fim de um stderr) sem a pasta do usuário, nas duas grafias de barra."""
    home = str(Path.home())
    return text.replace(home, "~").replace(home.replace("\\", "/"), "~")


def default_out_dir() -> Path:
    from listener.platform.common import paths

    return paths.data_dir_suggestion() / "perf-out"


def versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for dist in DISTS:
        try:
            found[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            found[dist] = "ausente"
    return found


def cpu_mask() -> int:
    """A máscara dos núcleos P (`probe_ui.performance_mask`); 0 fora do Windows ou numa CPU homogênea."""
    if sys.platform != "win32":
        return 0
    from probe_ui import performance_mask

    try:
        return performance_mask()
    except Exception:
        return 0


def pin_p_cores() -> int:
    """Prende o processo (e os filhos que nascerem depois) aos núcleos P; devolve a máscara usada (0 = não prendeu)."""
    if sys.platform != "win32":
        return 0
    from probe_ui import pin_to_performance_cores

    try:
        return pin_to_performance_cores()
    except Exception:
        return 0


def env_info() -> dict[str, Any]:
    affinity = None
    if hasattr(os, "sched_getaffinity"):
        with suppress(OSError):
            affinity = len(os.sched_getaffinity(0))
    return {"platform": sys.platform, "machine": platform.machine(), "cpu_count": os.cpu_count(), "python": sys.version.split()[0],
            "p_core_mask": f"{cpu_mask():#x}", "process_mask": f"{process_mask():#x}", "affinity_cpus": affinity,
            "omp": {k: os.environ.get(k) for k in OMP_VARS}, "versions": versions()}


def process_mask() -> int:
    """A máscara de afinidade atual do processo (Windows); 0 quando não dá para ler."""
    if sys.platform != "win32":
        return 0
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t))
        proc, system = ctypes.c_size_t(0), ctypes.c_size_t(0)
        if k32.GetProcessAffinityMask(k32.GetCurrentProcess(), ctypes.byref(proc), ctypes.byref(system)):
            return int(proc.value)
    except Exception:
        return 0
    return 0


# ---------------------------------------------------------------- memória
class _Counters(ctypes.Structure):                   # PROCESS_MEMORY_COUNTERS do psapi
    _fields_ = (("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t))


def _win_counters() -> _Counters | None:
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)      # instância própria, com restype e argtypes (maquina.md)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = ()
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD)
        c = _Counters()
        c.cb = ctypes.sizeof(c)
        return c if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb) else None
    except Exception:
        return None


def rss_mb(peak: bool = False) -> float | None:
    """RSS do próprio processo em MB (o pico com `peak=True`): psapi no Windows (psutil leva 4 s com o EDR); a camada de
    plataforma (só o atual) fora dele."""
    if sys.platform == "win32":
        c = _win_counters()
        if c is None:
            return None
        return round((c.PeakWorkingSetSize if peak else c.WorkingSetSize) / 1e6, 1)
    from listener.platform.common.process import memory_rss

    value = memory_rss()
    return round(value / 1e6, 1) if value else None


# ---------------------------------------------------------------- estatística e intercalação
def pct(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def stats(values: Sequence[float], digits: int = 1) -> dict[str, Any]:
    """Média, mediana, p95, máximo e mínimo de uma lista (vazia: só `n`)."""
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": round(statistics.fmean(values), digits), "median": round(statistics.median(values), digits),
            "p95": round(pct(values, 0.95), digits), "max": round(max(values), digits), "min": round(min(values), digits)}


def interleaved[T](variants: Sequence[T], runs: int) -> Iterator[tuple[int, T]]:
    """A, B, C, A, B, C...: o que deriva no tempo (térmica, EDR) pesa igual em todas as variantes."""
    for run in range(max(1, runs)):
        for v in variants:
            yield run, v


def best_of_medians(samples: dict[str, list[float]]) -> dict[str, float | None]:
    """Por variante, o menor valor entre as rodadas (cada rodada já é a mediana das repetições dela): o ruído da máquina só
    soma tempo, então o menor é o mais perto do custo real."""
    return {k: (round(min(v), 3) if v else None) for k, v in samples.items()}


def cosine(a: Any, b: Any) -> float:
    import numpy as np

    x, y = np.asarray(a, np.float64).reshape(-1), np.asarray(b, np.float64).reshape(-1)
    n = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(np.dot(x, y) / n) if n > 0 else 0.0


# ---------------------------------------------------------------- relatório (um arquivo por rodada)
@dataclass(slots=True)
class Report:
    probe: str
    params: dict[str, Any]
    out_dir: Path
    started: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    results: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, stage: str, **kw: Any) -> dict[str, Any]:
        row = {"stage": stage, **kw}
        self.results.append(row)
        out(stage, **kw)
        return row

    def finish(self, **summary: Any) -> Path:
        self.summary = summary
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{self.probe}-{stamp}.json"
        data = {"probe": self.probe, "started": self.started, "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
                "params": self.params, "env": env_info(), "results": self.results, "summary": summary}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        out(f"{self.probe}_summary", **summary, json=safe_path(path))
        return path


# ---------------------------------------------------------------- banco real (só leitura)
def real_db_dir(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    from listener.config import bootstrap
    from listener.platform.common import paths

    return bootstrap.read_db_dir(paths.config_dir())


def open_real(db_dir: Path | None) -> Any:
    """`Store.open_readonly` (`mode=ro`, nada é migrado nem escrito); None sem banco."""
    if db_dir is None or not (db_dir / "listener.db").exists():
        return None
    from listener.store import Store

    return Store.open_readonly(db_dir)


def busy_reason(store: Any) -> str | None:
    """Por que não medir agora: uma reunião `gravando` ou um job `rodando` com heartbeat de menos de 60 s."""
    from listener.model import JobStatus, MeetingStatus

    recording = store._q("SELECT COUNT(*) AS n FROM meetings WHERE status = ?", (str(MeetingStatus.RECORDING),))[0]["n"]
    if recording:
        return f"{recording} reunião(ões) gravando agora"
    running = [j for j in store.jobs.held_fresh(stale_s=STALE_S) if str(j["status"]) == str(JobStatus.RUNNING)]
    if running:
        return f"job {running[0]['id']} ({running[0]['kind']}) rodando com heartbeat recente"
    return None


def read_token(store: Any) -> str:
    """O token do Hugging Face do banco real; nunca impresso."""
    return str(store.settings.provider_key(HF_PROVIDER)) if store is not None else ""


def job_config(store: Any, threads: int, whisper_model: str | None = None) -> Any:
    """O `JobConfig` do worker com a pasta de modelos do banco (vazio = a padrão do sistema)."""
    from listener.pipeline.types import JobConfig

    models_dir = str(store.get_setting("pipeline.models_dir")) if store is not None else ""
    model = whisper_model or (str(store.get_setting("pipeline.whisper_model")) if store is not None else "large-v3-turbo")
    return JobConfig(models_dir=models_dir, torch_threads=max(1, int(threads)), whisper_model=model)


def prepare_environment(config: Any) -> dict[str, str]:
    """Antes de importar torch: `apply_environment` (HF_HOME na pasta de modelos, OMP e MKL, `HF_HUB_OFFLINE` com tudo
    baixado), os symlinks do Hugging Face desligados e a telemetria do pyannote fora, como o worker faz."""
    from listener.pipeline.models import apply_environment, disable_hf_symlinks

    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    values = apply_environment(config)
    disable_hf_symlinks()
    return values


def meeting_language(store: Any, meeting_id: int | None) -> str:
    """O código do Whisper da reunião (ou o padrão do app); `pt` sem banco."""
    if store is None:
        return "pt"
    from listener.config.language import meeting_language as of_meeting, whisper_code

    meeting = store.get_meeting(int(meeting_id)) if meeting_id is not None else None
    return whisper_code(of_meeting(store, meeting))


# ---------------------------------------------------------------- filhos
def child_env(threads: int, extra: dict[str, str] | None = None) -> dict[str, str]:
    """O ambiente do worker (`worker_env`: OMP e MKL antes de importar torch e CTranslate2) mais o que a variante pedir."""
    from listener.platform.common.process import worker_env

    env = worker_env(threads)
    env.update(extra or {})
    return env


def start_child(argv_tail: Sequence[str], env: dict[str, str] | None = None, priority: str | None = None,
                stdin: Any = subprocess.DEVNULL) -> subprocess.Popen[str]:
    """`python scripts/probe_perf.py _child ...` com a classe de prioridade nas `creationflags` (só no Windows)."""
    argv = [sys.executable, str(SCRIPTS / "probe_perf.py"), "_child", *map(str, argv_tail)]
    flags = PRIORITY_CLASSES[priority] if (priority and sys.platform == "win32") else 0
    return subprocess.Popen(argv, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # noqa: S603
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)


def run_child(argv_tail: Sequence[str], env: dict[str, str] | None = None, priority: str | None = None,
              timeout: float = CHILD_TIMEOUT_S) -> dict[str, Any]:
    """Roda o filho até o fim e devolve a última linha JSON dele (ou `error` com o fim do stderr)."""
    proc = start_child(argv_tail, env, priority)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return {"error": f"filho sem resposta em {timeout:.0f} s", "stderr": safe_text(stderr[-2000:])}
    return parse_child(stdout, stderr, proc.returncode)


def parse_child(stdout: str, stderr: str, code: int | None) -> dict[str, Any]:
    lines = [ln for ln in stdout.splitlines() if ln.startswith("{")]
    if code != 0 or not lines:
        return {"error": f"saída {code}", "stderr": safe_text(stderr[-2000:])}
    try:
        return dict(json.loads(lines[-1]))
    except ValueError:
        return {"error": "linha ilegível do filho", "stderr": safe_text(stderr[-2000:])}


def emit_child(**kw: Any) -> None:
    """(filho) A linha JSON que o pai lê: a última da saída padrão."""
    print(json.dumps(kw, ensure_ascii=False, default=str), flush=True)


def wait_until(predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()


def chunked[T](items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
