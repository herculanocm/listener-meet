"""O corpo da tarefa da exportação de áudio e vídeo (F5.19; revisão de arquitetura A-24), sem Qt: testável sem janela.

- `progress_tick`: o `Tick` da tarefa. Manda (etapa, %) só quando o inteiro muda e, com uma gravação em curso, espera
  parada até ela acabar (A-07: a exportação não disputa a CPU com a legenda ao vivo), conferindo o cancelamento.
- `run_job`: confere de novo o arquivo de áudio dentro da tarefa (A-08: o anexo do papel APP criaria um vazio), roda a
  exportação e devolve o caminho ou um `JobFailure`, sem relançar: a `StreamTask` registraria o rastro com a mensagem do
  PyAV ou do soundfile, que traz o caminho do temporário, com o título (A-01). O log leva a reunião, o formato, a pasta, as
  contagens e o tipo do erro; o rastro do erro inesperado vai sem a mensagem (só arquivo, linha e código).
- Antes de começar, na thread da interface: `space_problem` (pouco espaço livre para o tamanho estimado, A-02) e
  `held_by_another` (o destino que já existe aberto num player: o aviso do U-030 antes de gerar, A-19)."""
from __future__ import annotations

import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from .media_audio import ExportCancelledError, Tick

if TYPE_CHECKING:
    from .media_audio import AudioPlan

log = get_logger("ui.media")

WAIT = "wait"                    # a etapa da espera por uma gravação em curso
POLL_S = 0.25                    # a espera confere a gravação e o cancelamento a cada isto
MP3_BPS = 64_000                 # as estimativas de tamanho, com folga (medido: ~41 kbps no MP3 e ~130 kbps no MP4, 07/11)
VIDEO_BPS = 200_000
SPACE_MARGIN = 1.25
SPACE_SLACK = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class JobFailure:
    """O fim sem arquivo: `notice` vai para a faixa da reunião; `dialog` abre o aviso (gravação que falhou, erro inesperado)."""
    notice: str = ""
    dialog: str = ""


CANCELLED = JobFailure(notice="Exportação cancelada.")


def progress_tick(cancelled: Callable[[], bool], report: Callable[[Any], None], recording: Callable[[], bool],
                  sleep: Callable[[float], None] = time.sleep) -> Tick:
    """O `Tick` da tarefa (ver o módulo); `report` recebe (etapa, %), e a espera manda (`WAIT`, %) uma vez."""
    last: list[tuple[str, int]] = [("", -1)]

    def send(item: tuple[str, int]) -> None:
        if item != last[0]:
            last[0] = item
            report(item)

    def tick(stage: str, fraction: float) -> bool:
        if cancelled():                           # R-02: depois do "Cancelar", nada de progresso por cima do "Cancelando…"
            return True
        percent = min(100, int(fraction * 100))
        if recording():
            send((WAIT, percent))
            while recording() and not cancelled():
                sleep(POLL_S)
            if cancelled():
                return True
        send((stage, percent))
        return cancelled()

    return tick


def media_error_text(path: str | Path, error: OSError) -> str:
    """O aviso de um áudio ou vídeo que não salvou (U-030 sem o "Word, Excel…", A-02): o destino aberto num player ou a
    pasta sem gravação; senão o motivo do sistema, sem o caminho do temporário."""
    if isinstance(error, PermissionError):
        return (f"Não consegui salvar o arquivo:\n{path}\n\nEle está aberto em outro programa (um player de áudio ou de vídeo) ou "
                "a pasta não aceita gravação. Feche o arquivo e tente de novo, ou salve com outro nome.")
    reason = error.strerror or "a gravação falhou"
    return f"Não consegui salvar o arquivo:\n{path}\n\n{reason}."


def estimate_bytes(video: bool, fmt: str, plan: AudioPlan) -> int:
    """O tamanho esperado, com folga: o WAV é exato; o FLAC fica abaixo do WAV; o MP3 e o MP4 pela taxa de bits."""
    seconds = plan.duration_ms / 1000
    if video:
        return int(seconds * VIDEO_BPS / 8)
    if fmt in ("wav", "flac"):
        return plan.frames * 2 + 44
    return int(seconds * MP3_BPS / 8)


def space_problem(target: Path, need: int) -> str | None:
    """O aviso de pouco espaço livre na pasta de destino para `need` bytes (com margem); None com espaço ou sem saber."""
    try:
        free = shutil.disk_usage(target.parent).free
    except OSError:
        return None
    if free >= need * SPACE_MARGIN + SPACE_SLACK:
        return None
    return (f"Pouco espaço livre em {target.parent}: o arquivo deve ter cerca de {need / 1e6:.0f} MB e há {free / 1e6:.0f} MB "
            "livres. Libere espaço ou salve em outra pasta.")


def held_by_another(target: Path) -> bool:
    """O destino que já existe e não abre para gravar (aberto num player, no Windows): a troca no fim falharia (A-19)."""
    if not target.exists():
        return False
    try:
        with target.open("r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def run_job(kind: str, fmt: str, meeting_id: int, target: Path, make: Callable[[Tick], tuple[Path, int, int]],
            check: Callable[[], str | None], tick: Tick, seconds: float) -> Path | JobFailure:
    """Na tarefa: confere o áudio (`check` devolve o aviso), exporta (`make` devolve o caminho, as legendas e os quadros) e
    devolve o caminho ou o `JobFailure`; nunca relança (A-01). `seconds` é a duração do áudio, para o log."""
    started = time.perf_counter()
    problem = check()
    if problem is not None:
        log.warning("reunião %s: exportação de %s sem o arquivo de áudio na pasta do banco", meeting_id, kind)
        return JobFailure(notice=problem)
    try:
        out, cues, frames = make(tick)
    except ExportCancelledError:
        log.info("reunião %s: exportação de %s cancelada em %.1f s", meeting_id, kind, time.perf_counter() - started)
        return CANCELLED
    except OSError as e:                          # o texto do erro traz o caminho (o título): só a pasta e o tipo
        log.warning("reunião %s: exportar %s em %s falhou: %s", meeting_id, fmt, target.parent, type(e).__name__)
        return JobFailure(dialog=media_error_text(target, e))
    except Exception as e:                        # o rastro sem a mensagem, que traz o caminho (A-01): não `log.exception`
        log.error("reunião %s: gerar %s para %s falhou: %s\n%s", meeting_id, fmt, target.parent, type(e).__name__,  # noqa: TRY400
                  "".join(traceback.format_tb(e.__traceback__)))
        what = "o vídeo" if kind == "video" else "o áudio"
        return JobFailure(dialog=f"Não consegui gerar {what}:\n{target}\n\n{type(e).__name__}: {e}")
    try:
        size = out.stat().st_size
    except OSError:
        size = -1
    log.info("reunião %s: %s salvo em %s (%d bytes, %.1f s de áudio, %d legendas, %d quadros, %.1f s)", meeting_id, fmt,
             out.parent, size, seconds, cues, frames, time.perf_counter() - started)
    return out


__all__ = ["CANCELLED", "WAIT", "JobFailure", "estimate_bytes", "held_by_another", "media_error_text", "progress_tick", "run_job",
           "space_problem"]
