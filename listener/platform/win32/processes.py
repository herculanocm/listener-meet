"""Processos e janelas no Windows via ctypes (Toolhelp32 e EnumWindows), sem psutil.

psutil abre um handle por processo e levava ~4 s nesta máquina (EDR); o snapshot leva ~30 ms.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    name: str
    ppid: int


def snapshot_processes() -> dict[int, ProcInfo]:
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise OSError("CreateToolhelp32Snapshot falhou")
    procs: dict[int, ProcInfo] = {}
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            procs[int(pe.th32ProcessID)] = ProcInfo(int(pe.th32ProcessID), pe.szExeFile, int(pe.th32ParentProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
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


def window_titles_by_pid() -> dict[int, str]:
    """Título da maior janela visível de cada processo."""
    user32 = ctypes.windll.user32
    titles: dict[int, tuple[int, str]] = {}
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
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
