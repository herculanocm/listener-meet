"""F5.7 (L1): o indicador de microfone do Windows (`ConsentStore` de microphone, em HKCU), só leitura.

Cada app que já usou o microfone tem uma entrada: os empacotados (MSIX, como o Teams novo) pela família do pacote; os
outros em `NonPackaged`, pelo caminho do executável com "#" no lugar da barra invertida. O caminho tem a pasta do
usuário: nunca vai para o log nem sai daqui; para fora vão só um id opaco (`user_ids`) ou o nome do executável
(`entry_exe`). Em uso: `LastUsedTimeStart` > 0 e `LastUsedTimeStop` == 0 [fato, 29/10: 20 entradas, de 0,8 a 1,9 ms].
O registro diz quem segura o microfone mesmo com o Core Audio falhando (o modo só pelo registro, `degraded_activity`), e
`RegNotifyChangeKeyValue` avisa a mudança sem consulta nem callback (`Win32MicWatch`)."""
from __future__ import annotations

import ctypes
import functools
import hashlib
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...log import get_logger
from ..base import AppAudio, AudioActivity

log = get_logger("platform.win32.consent")

CONSENT_MIC = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
NON_PACKAGED = "NonPackaged"
# a família do pacote dos apps empacotados que o modo só pelo registro reconhece (só os de reunião importam: R1)
PACKAGES = {"msteams_8wekyb3d8bbwe": "ms-teams.exe"}
REG_NOTIFY_CHANGE_NAME, REG_NOTIFY_CHANGE_LAST_SET = 0x00000001, 0x00000004
REG_NOTIFY_THREAD_AGNOSTIC = 0x10000000           # Windows 8 ou mais novo: o aviso não depende da thread que armou
NOTIFY_FILTER = REG_NOTIFY_CHANGE_NAME | REG_NOTIFY_CHANGE_LAST_SET | REG_NOTIFY_THREAD_AGNOSTIC
WAIT_OBJECT_0, WAIT_TIMEOUT = 0x0, 0x102
MAX_WAIT_MS = 0xFFFFFFFE                          # um a menos que INFINITE


class Registry(Protocol):
    """O pedaço do registro que o indicador usa (o `WinRegistry` lê HKCU; os testes passam um falso)."""
    def subkeys(self, path: str) -> list[str]: ...              # OSError quando a chave não existe ou não abre
    def times(self, path: str) -> tuple[int, int]: ...          # (LastUsedTimeStart, LastUsedTimeStop); 0 sem o valor


class WinRegistry:
    """HKCU pelo `winreg`, só leitura."""

    def subkeys(self, path: str) -> list[str]:
        if sys.platform != "win32":
            raise OSError("o registro só existe no Windows")
        import winreg

        names: list[str] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            while True:
                try:
                    names.append(winreg.EnumKey(key, len(names)))
                except OSError:                        # acabaram as subchaves
                    return names

    def times(self, path: str) -> tuple[int, int]:
        if sys.platform != "win32":
            raise OSError("o registro só existe no Windows")
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return _qword(key, "LastUsedTimeStart"), _qword(key, "LastUsedTimeStop")


def _qword(key: Any, name: str) -> int:
    if sys.platform != "win32":
        return 0
    import winreg

    try:
        value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return 0
    return value if isinstance(value, int) else 0


@dataclass(frozen=True, slots=True)
class ConsentEntry:
    """Uma entrada do indicador. `name` é a família do pacote ou o caminho com "#" (com a pasta do usuário: nunca no log
    nem no `repr`, R-12)."""
    name: str = field(repr=False)
    packaged: bool
    start: int                                         # FILETIME do último começo de uso (0 = nunca)
    stop: int                                          # FILETIME do último fim (0 com `start` = em uso)

    @property
    def in_use(self) -> bool:
        return self.start > 0 and self.stop == 0


def mic_entries(reg: Registry | None = None) -> list[ConsentEntry] | None:
    """Todas as entradas do microfone, empacotadas e `NonPackaged`; a que não abre fica de fora. None quando a chave do
    microfone não existe ou não pode ser lida."""
    reg = reg if reg is not None else WinRegistry()
    try:
        names = reg.subkeys(CONSENT_MIC)
    except OSError as e:
        log.debug("indicador de microfone ilegível: código %s", getattr(e, "winerror", None) or e.errno)
        return None
    out: list[ConsentEntry] = []
    for name in names:
        if name == NON_PACKAGED:
            out += _non_packaged(reg)
        elif (entry := _entry(reg, rf"{CONSENT_MIC}\{name}", name, packaged=True)) is not None:
            out.append(entry)
    return out


def _non_packaged(reg: Registry) -> list[ConsentEntry]:
    base = rf"{CONSENT_MIC}\{NON_PACKAGED}"
    try:
        names = reg.subkeys(base)
    except OSError:
        return []
    return [e for name in names if (e := _entry(reg, rf"{base}\{name}", name, packaged=False)) is not None]


def _entry(reg: Registry, path: str, name: str, packaged: bool) -> ConsentEntry | None:
    try:
        start, stop = reg.times(path)
    except OSError:                                    # a entrada sumiu entre a lista e a leitura
        return None
    return ConsentEntry(name, packaged, start, stop)


def own_entry() -> str:
    """O nome da entrada do próprio app: o interpretador de verdade (sob o venv, o `python.exe` base, filho do lançador)."""
    return str(getattr(sys, "_base_executable", "") or sys.executable).replace("\\", "#")


def user_ids(entries: Iterable[ConsentEntry], own: str | None = None) -> frozenset[str]:
    """Ids opacos (hash do nome) das entradas em uso, menos a do próprio app: servem só para ver se o conjunto mudou."""
    own_l = (own if own is not None else own_entry()).lower()
    return frozenset(_opaque(e.name) for e in entries if e.in_use and e.name.lower() != own_l)


def _opaque(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def entry_exe(entry: ConsentEntry) -> str:
    """O executável de uma entrada, em minúsculas, só o nome do arquivo: o pacote conhecido pela tabela (o Teams novo,
    `MSTeams_8wekyb3d8bbwe`, vira `ms-teams.exe`), o caminho pelo último pedaço. Vazio quando não se sabe."""
    if entry.packaged:
        return PACKAGES.get(entry.name.lower(), "")
    exe = entry.name.rsplit("#", 1)[-1].lower()
    return exe if exe.endswith(".exe") else ""


def degraded_activity(entries: Iterable[ConsentEntry], own: str | None = None) -> AudioActivity:
    """O modo só pelo registro: um app por executável em uso (menos o próprio app), com microfone, sem pid nem pico."""
    own_l = (own if own is not None else own_entry()).lower()
    exes = {exe for e in entries if e.in_use and e.name.lower() != own_l and (exe := entry_exe(e))}
    return AudioActivity(tuple(AppAudio(0, exe, True, 0.0) for exe in sorted(exes)), degraded=True)


@functools.cache
def _dlls() -> tuple[Any, Any]:
    """kernel32 e advapi32 em instâncias próprias (`WinDLL`), com as assinaturas daqui, sem mexer nas de outros módulos."""
    if sys.platform != "win32":
        raise OSError("o aviso do registro só existe no Windows")
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    k32.SetEvent.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.WaitForMultipleObjects.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.RegNotifyChangeKeyValue.restype = wintypes.LONG
    adv.RegNotifyChangeKeyValue.argtypes = (wintypes.HKEY, wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE, wintypes.BOOL)
    return k32, adv


def _last_error() -> int:
    if sys.platform != "win32":
        return 0
    return ctypes.get_last_error()


class Win32MicWatch:
    """`MicWatch` no Windows: `RegNotifyChangeKeyValue` assíncrono na chave do microfone (subárvore, nome e valor) com um
    evento do kernel, e `WaitForMultipleObjects` sobre o evento de parada (primeiro: o `cancel` vence) e o do registro.
    Tudo por ctypes, sem callback. `wait` e `close` na thread que lê; `cancel` de qualquer thread. Uma trava junta `cancel`
    e `close`: o `SetEvent` nunca pega um evento já fechado (R-10)."""

    def __init__(self, key: Any, stop: int | None, changed: int | None) -> None:
        self._key, self._stop, self._changed = key, stop, changed
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: str = CONSENT_MIC) -> Win32MicWatch | None:
        """Abre a chave, cria os eventos e arma; None quando algo falha (aí quem lê consulta no tempo-limite)."""
        if sys.platform != "win32":
            return None
        import winreg

        k32, _adv = _dlls()
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_NOTIFY)
        except OSError as e:
            log.debug("aviso do indicador de microfone: a chave não abriu (código %s)", getattr(e, "winerror", None))
            return None
        watch = cls(key, k32.CreateEventW(None, True, False, None), k32.CreateEventW(None, False, False, None))
        if not watch._stop or not watch._changed or not watch._arm():
            watch.close()
            return None
        return watch

    def _arm(self) -> bool:
        _k32, adv = _dlls()
        rc = adv.RegNotifyChangeKeyValue(self._key.handle, True, NOTIFY_FILTER, self._changed, True)
        if rc != 0:
            log.debug("RegNotifyChangeKeyValue: código %d", rc)
        return rc == 0

    def _wait_any(self, timeout_ms: int) -> int:
        from ctypes import wintypes

        k32, _adv = _dlls()
        handles = (wintypes.HANDLE * 2)(self._stop, self._changed)
        return int(k32.WaitForMultipleObjects(2, handles, False, timeout_ms))

    def wait(self, timeout_s: float) -> bool:
        """True quando o indicador mudou (e já rearmado); False no tempo-limite, depois de `cancel` ou de `close`. Levanta
        quando a espera ou o rearme falham (quem lê passa a consultar no tempo-limite)."""
        if self._closed:
            return False
        rc = self._wait_any(min(max(0, int(timeout_s * 1000)), MAX_WAIT_MS))
        if rc == WAIT_OBJECT_0 + 1:
            if not self._arm():
                raise OSError("o aviso do indicador de microfone não rearmou")
            return True
        if rc in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            return False
        raise OSError(f"WaitForMultipleObjects: {rc:#x}, erro {_last_error()}")

    def cancel(self) -> None:
        with self._lock:
            if not self._closed and self._stop:
                _dlls()[0].SetEvent(self._stop)

    def close(self) -> None:
        """Solta os eventos e a chave (o aviso pendente cai junto); de novo não faz nada."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            k32, _adv = _dlls()
            for handle in (self._stop, self._changed):
                if handle:
                    k32.CloseHandle(handle)
            self._key.Close()
