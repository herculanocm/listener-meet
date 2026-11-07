"""Vigia de travamentos da suíte (F5.18, pedido do dono em 07/11: duas rodadas travaram nesse dia, uma às 14:32 com os quatro
workers vivos até 15:40 e outra às 16:13 com 4,7 s de CPU em quase duas horas). Só stdlib: o `faulthandler` arma um prazo
por fase (a coleta, cada teste, o fim da sessão); vencido, grava a pilha de TODAS as threads do processo em
`%TEMP%/listener-tests-hang.log` e derruba o processo (`exit=True`): um worker do xdist morto vira falha reportada, não
uma suíte parada por horas. O teste em curso fica num marcador por processo (`listener-tests-current-<pid>.txt`), porque
o despejo do faulthandler não traz o nome do teste nem o pid; um processo derrubado deixa o marcador para trás e o resumo
da rodada (`pytest_terminal_summary` do conftest) o lê, o imprime e o apaga. Prazos pelo ambiente: `LISTENER_TEST_HANG_S`
(padrão 180 s por teste; a coleta e o fim ganham o dobro; 0 desliga) e `LISTENER_TEST_HANG_LOG` (o arquivo do despejo)."""
from __future__ import annotations

import atexit
import contextlib
import faulthandler
import os
import tempfile
import time
from pathlib import Path
from typing import IO

HANG_S = float(os.environ.get("LISTENER_TEST_HANG_S", "180"))
LOG = Path(os.environ.get("LISTENER_TEST_HANG_LOG") or Path(tempfile.gettempdir()) / "listener-tests-hang.log")
LOG_MAX_BYTES = 1_000_000
MARKER_PREFIX = "listener-tests-current-"
MARKER = Path(tempfile.gettempdir()) / f"{MARKER_PREFIX}{os.getpid()}.txt"
_log: IO[str] | None = None


def _file() -> IO[str]:
    global _log  # noqa: PLW0603
    if _log is None:
        with contextlib.suppress(OSError):                       # um log que passou de 1 MB roda para `.1` (se ninguém o segura)
            if LOG.stat().st_size > LOG_MAX_BYTES:
                LOG.replace(LOG.with_suffix(".1.log"))
        _log = LOG.open("a", encoding="utf-8", buffering=1)
    return _log


def arm(seconds: float, what: str, *, marker: bool = True) -> None:
    """Rearma o vigia para `what` (o nome do teste ou a fase): `seconds` sem `disarm()` e o faulthandler despeja as pilhas no
    log e sai. Um prazo de 0 ou menos desliga. O marcador leva a hora, o pid e o `what`, sem nada do conteúdo dos testes;
    `marker=False` serve ao fim da sessão, que não tem `disarm` (o processo acaba) e deixaria um marcador por rodada."""
    if seconds <= 0:
        return
    if marker:
        with contextlib.suppress(OSError):
            MARKER.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}: {what}\n", encoding="utf-8")
    faulthandler.dump_traceback_later(seconds, repeat=False, file=_file(), exit=True)


def disarm() -> None:
    faulthandler.cancel_dump_traceback_later()
    _clear_marker()


def _clear_marker() -> None:
    with contextlib.suppress(OSError):
        MARKER.unlink(missing_ok=True)


def note(text: str) -> None:
    """Uma linha de contexto no log (por fase, não por teste: o log guarda só o que importa quando algo trava)."""
    f = _file()
    f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}: {text}\n")
    f.flush()


def leftover_markers() -> list[str]:
    """Os marcadores que outros processos deixaram para trás (um processo derrubado pelo vigia não chega ao `disarm` nem ao
    `atexit`), já apagados: cada linha diz a hora, o pid e o teste ou a fase em curso quando o prazo venceu. Só o resumo
    da rodada deve chamar isto: um teste que o chamasse apagaria o marcador vivo dos outros workers."""
    found: list[str] = []
    for path in sorted(MARKER.parent.glob(f"{MARKER_PREFIX}*.txt")):
        if path == MARKER:
            continue
        with contextlib.suppress(OSError):
            found.append(path.read_text(encoding="utf-8").strip())
            path.unlink()
    return found


atexit.register(_clear_marker)      # a saída normal apaga o marcador; a travada na saída fica no log, não num marcador
