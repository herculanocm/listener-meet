"""Processos, portável (F2 item 2, CP15): lançar o worker solto do app, baixar a própria prioridade, medir a memória e
montar o comando e o ambiente do worker. O win32 sobrescreve `spawn_detached`, `spawn_child`, `lower_own_priority` e
`memory_rss` em `platform/win32/`; aqui fica o que serve a posix (e ao Windows sem a camada, como fallback). Nunca
`preexec_fn` (não é seguro num processo com threads): o filho ao vivo da F4 é rebaixado pelo pai logo depois de nascer."""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

NICE_STEP = 10
NICE_MAX = 19
WORKER_FLAG = "--worker"
LIGHT_FLAG = "--light"           # F3: worker leve (só jobs `llm`, sem torch)
LIVE_FLAG = "--live"             # F4: processo ao vivo (legendas), preso à gravação


def worker_command(light: bool = False) -> list[str]:
    """`python -m listener --worker [--light]` no venv; `<exe> --worker [--light]` quando congelado (PyInstaller, R-10)."""
    flags = [WORKER_FLAG, LIGHT_FLAG] if light else [WORKER_FLAG]
    if getattr(sys, "frozen", False):
        return [sys.executable, *flags]
    return [sys.executable, "-m", "listener", *flags]


def live_command(db_dir: str | Path, meeting_id: int) -> list[str]:
    """`python -m listener --live --db DIR --meeting ID` no venv; `<exe> --live ...` quando congelado (F4)."""
    flags = [LIVE_FLAG, "--db", str(db_dir), "--meeting", str(int(meeting_id))]
    if getattr(sys, "frozen", False):
        return [sys.executable, *flags]
    return [sys.executable, "-m", "listener", *flags]


def live_prepare_command(db_dir: str | Path | None = None) -> list[str]:
    """`--live --prepare [--db DIR]`: baixa `small` e `base` fora da gravação, com progresso em linhas JSON no stdout."""
    flags = [LIVE_FLAG, "--prepare", *(("--db", str(db_dir)) if db_dir else ())]
    if getattr(sys, "frozen", False):
        return [sys.executable, *flags]
    return [sys.executable, "-m", "listener", *flags]


def worker_env(torch_threads: int, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente do subprocesso: threads limitadas antes de importar torch e CTranslate2 (D-F2-3), sem avisos de symlink."""
    env = dict(os.environ if base is None else base)
    n = str(max(1, int(torch_threads)))
    env.update({"OMP_NUM_THREADS": n, "MKL_NUM_THREADS": n, "CT2_VERBOSE": "0", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                "PYTHONUTF8": "1"})
    return env


def spawn_detached(argv: list[str], env: Mapping[str, str] | None = None) -> int:
    """Lança o processo numa sessão própria (sobrevive ao fim do app, P2) sem herdar os descritores; devolve o pid."""
    p = subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.DEVNULL,  # noqa: S603
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         start_new_session=sys.platform != "win32")
    return int(p.pid)


def live_env(threads: int, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente do processo ao vivo: as threads do Whisper (`live.threads`) também limitam OpenMP e MKL antes da carga."""
    return worker_env(threads, base)


def spawn_child(argv: list[str], env: Mapping[str, str] | None = None) -> subprocess.Popen[bytes]:
    """F4: filho com stdin e stdout binários e stderr descartado, na sessão do app (morre com a gravação); no posix, `nice
    +10` aplicado pelo pai logo depois de nascer (`setpriority`, o equivalente seguro do `preexec_fn`)."""
    p = subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.PIPE,  # noqa: S603
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
    lower_priority_of(p.pid)
    return p


def disable_own_power_throttling() -> bool:
    """EcoQoS só existe no Windows (`platform/win32`); aqui não há o que desligar (False)."""
    return False


def sync_stdout_handle() -> None:
    """No posix o fd 1 já é o descritor do SO: nada a sincronizar depois do `dup2`."""


def lower_priority_of(pid: int) -> bool:
    """`nice` do processo `pid` + 10 (até 19) no posix; no Windows a prioridade vai nas flags de criação (False aqui)."""
    if sys.platform == "win32":
        return False
    try:
        current = os.getpriority(os.PRIO_PROCESS, pid)
        os.setpriority(os.PRIO_PROCESS, pid, min(NICE_MAX, current + NICE_STEP))
    except OSError:
        return False
    return True


def lower_own_priority() -> bool:
    """`nice +10` no posix. No Windows sem a camada de plataforma não há o que fazer aqui (False)."""
    if sys.platform == "win32":
        return False
    try:
        os.nice(NICE_STEP)
    except OSError:
        return False
    return True


def memory_rss() -> int | None:
    """RSS atual em bytes (Linux: `/proc/self/statm`; macOS: `ru_maxrss`, que é o pico); None quando não dá para ler."""
    if sys.platform == "win32":
        return None
    try:
        if sys.platform == "linux":
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return peak if sys.platform == "darwin" else peak * 1024
    except (OSError, ValueError, ImportError):
        return None
