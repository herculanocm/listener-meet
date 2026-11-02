"""F5.13: o listener-meet na barra de tarefas (`Win32Taskbar`, herdada pelo `Win32Platform`; o `platform.py` não cresce).

L1 (U-129): a trava de instância única é um mutex `Local\\<nome>` (da sessão do usuário; o nome vem de quem chama, com o
resumo da pasta de configuração), segurado pela vida do processo: o Windows o solta quando o processo sai, mesmo numa queda.
`ERROR_ALREADY_EXISTS` é outra instância; `ERROR_ACCESS_DENIED` também (a de outro processo com outros direitos, como uma
elevada). Qualquer outra falha abre o app sem a trava, como antes dela, com uma linha de log. `AllowSetForegroundWindow`
(`ASFW_ANY`): o processo novo, a quem o clique do usuário deu o direito de trazer janela à frente, o passa adiante antes do
pedido; o que roda usa o `bring_to_front` de sempre. Instâncias próprias do kernel32 e do user32 (`WinDLL`), com as
assinaturas daqui, sem mexer nas de outros módulos. Nada daqui roda fora do Windows."""
from __future__ import annotations

import ctypes
import functools
import sys
from typing import Any

from ...log import get_logger

log = get_logger("platform.win32.taskbar")

ERROR_ACCESS_DENIED, ERROR_ALREADY_EXISTS = 5, 183
ASFW_ANY = 0xFFFFFFFF                                  # (DWORD)-1: qualquer processo pode trazer janela à frente
_HELD: list[int] = []                                  # o handle do mutex vive o processo inteiro (fechar soltaria a trava)


@functools.cache
def _kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("kernel32 só existe no Windows")
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


@functools.cache
def _user32() -> Any:
    if sys.platform != "win32":
        raise OSError("user32 só existe no Windows")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL
    return user32


class Win32Taskbar:
    """F5.13: a barra de tarefas no `Win32Platform`. `claim_instance` e `hand_over_foreground` na thread da interface, na
    abertura, antes do tema e do banco."""

    def claim_instance(self, name: str) -> bool:
        if sys.platform != "win32":
            return True
        try:
            k32 = _kernel32()
            ctypes.set_last_error(0)
            handle = k32.CreateMutexW(None, False, f"Local\\{name}")
            error = ctypes.get_last_error()
        except Exception as e:
            log.warning("trava de instância indisponível (%s); o app abre sem ela", e)
            return True
        if not handle:
            if error == ERROR_ACCESS_DENIED:
                log.info("trava de instância segurada por um processo com outros direitos (erro %s)", error)
                return False
            log.warning("trava de instância indisponível (erro %s); o app abre sem ela", error)
            return True
        if error == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        _HELD.append(int(handle))
        return True

    def hand_over_foreground(self) -> None:
        if sys.platform != "win32":
            return
        try:
            if not _user32().AllowSetForegroundWindow(ASFW_ANY):
                log.debug("AllowSetForegroundWindow recusou (erro %s)", ctypes.get_last_error())
        except Exception as e:
            log.debug("AllowSetForegroundWindow: %s", e)
