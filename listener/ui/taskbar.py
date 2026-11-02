"""F5.13: o app na barra de tarefas, a parte decidida fora da plataforma.

- L2 (U-131): as quatro tarefas do Jump List, na ordem da spec (Gravar…, Histórico, Fila de processamento e Configurações),
  cada uma com o comando de agora (`open_command`: o `.exe` atual e o `--open`; refeito a cada abertura, porque a pasta pode
  ter mudado de lugar). Só no `.exe`: no venv, o comando abriria um console. A plataforma publica (`set_taskbar_tasks`; no
  Windows, `platform/win32/jumplist.py`); falhar é uma linha de log e o app segue.
- L3 (U-132): a dica de fixar o ícone, na última página do assistente, também só no `.exe`: no venv, o ícone fixado seria o
  do `python.exe`, que abriria um console."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ..model import OpenTarget
from ..platform.base import TaskbarTask
from ..platform.common.process import open_command

if TYPE_CHECKING:
    from ..platform.base import Platform

TASKS: tuple[tuple[OpenTarget, str], ...] = ((OpenTarget.RECORD, "Gravar…"), (OpenTarget.HISTORY, "Histórico"),
                                            (OpenTarget.QUEUE, "Fila de processamento"), (OpenTarget.SETTINGS, "Configurações"))
PUBLISH_DELAY_MS = 3000          # depois da abertura e do Histórico: o COM a frio custa ~0,25 s na thread da interface


def packaged() -> bool:
    """O `.exe` do PyInstaller (F5.8), que liga `sys.frozen`."""
    return bool(getattr(sys, "frozen", False))


def taskbar_tasks() -> list[TaskbarTask]:
    """As tarefas do Jump List com o comando de agora."""
    out: list[TaskbarTask] = []
    for target, title in TASKS:
        program, *arguments = open_command(target)
        out.append(TaskbarTask(title, program, tuple(arguments)))
    return out


def publish_tasks(platform: Platform) -> bool:
    """U-131: o Jump List refeito na abertura, depois do controlador; True quando a plataforma o aceitou."""
    return packaged() and platform.set_taskbar_tasks(taskbar_tasks())


def pin_hint(platform: Platform) -> str | None:
    """U-132: a linha da dica, ou None (fora do `.exe`, ou a plataforma sem dica)."""
    return platform.pin_hint() if packaged() else None
