"""Processos, portável (F2 item 2, CP15): lançar o worker solto do app, baixar a própria prioridade, medir a memória e
montar o comando e o ambiente do worker. O win32 sobrescreve `spawn_detached`, `lower_own_priority` e `memory_rss` em
`platform/win32/`; aqui fica o que serve a posix (e ao Windows sem a camada, como fallback). Nunca `preexec_fn`."""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

NICE_STEP = 10
WORKER_FLAG = "--worker"


def worker_command() -> list[str]:
    """`python -m listener --worker` no venv; `<exe> --worker` quando congelado (PyInstaller, R-10)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG]
    return [sys.executable, "-m", "listener", WORKER_FLAG]


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
