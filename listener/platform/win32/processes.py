"""Processos e janelas no Windows via ctypes (Toolhelp32 e EnumWindows), sem psutil.

psutil abre um handle por processo e levava ~4 s nesta máquina (EDR); o snapshot leva ~30 ms. As estruturas e
assinaturas ctypes ficam no nível do módulo (M-12); `root_of`, `root_pid_for`, `app_root`, `group_by_app` e
`loopback_targets` são puras e rodam em qualquer sistema.
"""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
HOSTED = frozenset({"msedgewebview2.exe"})       # roda dentro de outro app (Teams novo, WhatsApp, Outlook novo)
SHELL = frozenset({"explorer.exe", "svchost.exe", "sihost.exe", "services.exe", "wininit.exe", "winlogon.exe",
                   "runtimebroker.exe", "applicationframehost.exe"})


@dataclass(frozen=True, slots=True)
class ProcInfo:
    pid: int
    name: str
    ppid: int


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
    )


_k32: Any = None
WNDENUMPROC: Any = None
if sys.platform == "win32":
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _k32 = ctypes.windll.kernel32
    _k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]


def snapshot_processes() -> dict[int, ProcInfo]:
    if sys.platform != "win32":
        raise OSError("snapshot_processes só existe no Windows")
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise OSError("CreateToolhelp32Snapshot falhou")
    procs: dict[int, ProcInfo] = {}
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = _k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            procs[int(pe.th32ProcessID)] = ProcInfo(int(pe.th32ProcessID), pe.szExeFile, int(pe.th32ParentProcessID))
            ok = _k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        _k32.CloseHandle(snap)
    return procs


def root_pid_for(exe: str, procs: dict[int, ProcInfo] | None = None) -> int | None:
    """PID raiz de um app (processo daquele nome cujo pai não tem o mesmo nome)."""
    procs = procs or snapshot_processes()
    want = exe.lower()
    same = [p for p in procs.values() if p.name.lower() == want]
    if not same:
        return None
    pids = {p.pid for p in same}
    roots = [p for p in same if p.ppid not in pids]
    return min(roots or same, key=lambda p: p.pid).pid


def root_of(pid: int, procs: dict[int, ProcInfo] | None = None) -> int:
    """Sobe a cadeia de pais enquanto o pai tiver o mesmo executável (Chromium, Electron, WebView2)."""
    procs = procs or snapshot_processes()
    cur = procs.get(pid)
    if cur is None:
        return pid
    seen = set()
    while cur.ppid in procs and cur.ppid not in seen and procs[cur.ppid].name.lower() == cur.name.lower():
        seen.add(cur.pid)
        cur = procs[cur.ppid]
    return cur.pid


def app_root(pid: int, procs: dict[int, ProcInfo] | None = None) -> int:
    """O app dono de uma sessão de áudio (D-055): sobe pelos pais de mesmo executável (Chromium, Electron) e pelo WebView2
    até o app que o hospeda; para no shell e nos serviços do sistema. No Teams novo, a chamada toca num msedgewebview2.exe
    neto do ms-teams.exe raiz, irmão do ms-teams.exe filho que tem a outra sessão: capturar a árvore do filho dava silêncio.
    O loopback de processo inclui a árvore inteira do pid dado, então a raiz pega tudo o que o app toca."""
    procs = procs or snapshot_processes()
    cur = procs.get(pid)
    if cur is None:
        return pid
    seen: set[int] = set()
    while cur.ppid in procs and cur.ppid not in seen:
        parent = procs[cur.ppid]
        name, pname = cur.name.lower(), parent.name.lower()
        if pname in SHELL or (pname != name and name not in HOSTED):
            break
        seen.add(cur.pid)
        cur = parent
    return cur.pid


def group_by_app(peaks: dict[int, float], procs: dict[int, ProcInfo]) -> dict[int, tuple[float, tuple[int, ...]]]:
    """As sessões de áudio por app (a raiz de `app_root`): o maior pico entre elas e os pids das sessões."""
    out: dict[int, tuple[float, tuple[int, ...]]] = {}
    for pid, peak in peaks.items():
        root = app_root(pid, procs) if procs else pid
        best, members = out.get(root, (0.0, ()))
        out[root] = (max(best, peak), (*members, pid))
    return out


def loopback_targets(root: int, session_pids: Iterable[int], procs: dict[int, ProcInfo]) -> list[int]:
    """Os processos a capturar para ter tudo o que o app de `root` toca (D-055): a raiz, cujo loopback cobre ela e os filhos
    diretos, e cada processo do app com sessão de áudio mais fundo que isso (o WebView2 da chamada do Teams é neto da raiz);
    um filho direto de outro alvo já está nele. Do mais raso ao mais fundo (o pai é decidido antes do filho), a raiz primeiro."""
    def depth(pid: int) -> int:
        n, cur = 0, procs.get(pid)
        while cur is not None and cur.pid != root and n < 64:
            n, cur = n + 1, procs.get(cur.ppid)
        return n

    targets = [root]
    members = [p for p in set(session_pids) if p != root and p in procs and app_root(p, procs) == root]
    for pid in sorted(members, key=lambda p: (depth(p), p)):
        if procs[pid].ppid not in targets:
            targets.append(pid)
    return targets


def window_titles_by_pid() -> dict[int, str]:
    """Título da maior janela visível de cada processo."""
    if sys.platform != "win32":
        raise OSError("window_titles_by_pid só existe no Windows")
    user32 = ctypes.windll.user32
    titles: dict[int, tuple[int, str]] = {}

    def cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        prev = titles.get(pid.value)
        if prev is None or area > prev[0]:
            titles[pid.value] = (area, buf.value)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return {pid: t for pid, (_, t) in titles.items()}
