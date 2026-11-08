"""F5.21 (A-01 a A-10): a tela das sugestões ao vivo no Windows (`Win32Screen`, herdada pelo `Win32Platform`; o `platform.py`
não cresce). O user32, o GDI e o DWM por ctypes, com instâncias próprias das DLLs e as assinaturas daqui (como a barra de
tarefas), sem UI Automation (CP4) nem WinRT (CP14). Tudo em pixels físicos: o processo roda em PMv2 (o Qt 6), e o
`DWMWA_EXTENDED_FRAME_BOUNDS`, o `rcMonitor` e o BitBlt falam a mesma unidade (A-03).

- `monitors()`: `EnumDisplayMonitors` e `GetMonitorInfoW`, da esquerda para a direita, "Monitor N · LxA · principal" (A-28;
  o nome amigável do monitor repete em dois iguais, A-01).
- `active_monitor()`: o monitor da janela em primeiro plano (a bandeja não ativa: é o app que o usuário está usando); sem ela,
  o do cursor (A-08).
- `meeting_window(pid, exe)`: as janelas do grupo do app (os pids com o mesmo `app_root`, que sobe pelo WebView2 do Teams
  novo) na ordem z do `EnumWindows`, do topo para baixo: a primeira visível, sem `IsIconic`, sem `DWMWA_CLOAKED` (outra área
  de trabalho) e com ao menos 640 x 360 px; no navegador, primeiro a que casa o serviço de reunião (o título só em memória,
  como o sensor da F5.7). O retângulo é o `DWMWA_EXTENDED_FRAME_BOUNDS` (sem a borda invisível do `GetWindowRect`), cortado no
  monitor dela; as janelas de outros apps acima dela na ordem z, visíveis e opacas, vão em `covered` para sair pretas (A-09):
  ficam de fora as do app (já saem da captura), as transparentes ou em camadas e as do próprio grupo (a borda do
  compartilhamento do Teams).
- `grab_screen(rect)`: BitBlt (`SRCCOPY | CAPTUREBLT`) só do retângulo, num DIB de 32 bits de cima para baixo (A-02); as
  janelas com `WDA_EXCLUDEFROMCAPTURE` não saem no Windows 10 2004 ou mais novo [fato: Microsoft; que o BitBlt respeita, exp].
Roda em qualquer thread (a do pedido, nunca a da interface). Nada daqui roda fora do Windows: as DLLs e os tipos de callback
nascem na primeira chamada. O log leva dimensões, pids e ms; nunca título (D-053)."""
from __future__ import annotations

import ctypes
import functools
import os
import sys
import time
from typing import Any

from ...log import get_logger
from ..screen_types import Frame, MeetingWindow, Monitor, ScreenRect
from . import processes

log = get_logger("platform.win32.screen")

MIN_W, MIN_H = 640, 360                       # A-07: a janela da reunião menor que isto não é o alvo
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x00000020, 0x00080000
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
BROWSERS = frozenset({"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"})


@functools.cache
def _api() -> Any:
    """As DLLs, as estruturas e os tipos de callback, uma vez (o `WINFUNCTYPE` só existe no Windows)."""
    if sys.platform != "win32":
        raise OSError("a tela só existe no Windows")
    from ctypes import wintypes

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32))

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = (("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD))

    class BitmapInfo(ctypes.Structure):
        _fields_ = (("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3))

    win_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    mon_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    u32.EnumDisplayMonitors.argtypes = (wintypes.HDC, ctypes.c_void_p, mon_proc, wintypes.LPARAM)
    u32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.c_void_p)
    u32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    u32.MonitorFromWindow.restype = wintypes.HMONITOR
    u32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    u32.MonitorFromPoint.restype = wintypes.HMONITOR
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    u32.EnumWindows.argtypes = (win_proc, wintypes.LPARAM)
    u32.IsWindowVisible.argtypes = (wintypes.HWND,)
    u32.IsIconic.argtypes = (wintypes.HWND,)
    u32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    u32.GetWindowLongW.restype = wintypes.LONG
    u32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    u32.GetDC.argtypes = (wintypes.HWND,)
    u32.GetDC.restype = wintypes.HDC
    u32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    g32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    g32.CreateCompatibleDC.restype = wintypes.HDC
    g32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                     wintypes.HANDLE, wintypes.DWORD)
    g32.CreateDIBSection.restype = wintypes.HBITMAP
    g32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    g32.SelectObject.restype = wintypes.HGDIOBJ
    g32.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                           wintypes.DWORD)
    g32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    g32.DeleteDC.argtypes = (wintypes.HDC,)
    dwm.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    return type("Api", (), {"u32": u32, "g32": g32, "dwm": dwm, "wt": wintypes, "MonitorInfoEx": MonitorInfoEx, "BitmapInfo": BitmapInfo,
                            "BitmapInfoHeader": BitmapInfoHeader, "win_proc": win_proc, "mon_proc": mon_proc})


def _rect(r: Any) -> ScreenRect:
    return ScreenRect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))


def _device(api: Any, hmon: Any) -> tuple[str, ScreenRect, bool] | None:
    info = api.MonitorInfoEx()
    info.cbSize = ctypes.sizeof(info)
    if not hmon or not api.u32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return None
    return str(info.szDevice), _rect(info.rcMonitor), bool(info.dwFlags & MONITORINFOF_PRIMARY)


def _frame_bounds(api: Any, hwnd: Any) -> ScreenRect | None:
    r = api.wt.RECT()
    if api.dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        return None
    rect = _rect(r)
    return rect if rect.area > 0 else None


def _cloaked(api: Any, hwnd: Any) -> bool:
    value = api.wt.DWORD()
    return api.dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) == 0 and value.value != 0


def _title(api: Any, hwnd: Any) -> str:
    """O título só em memória, para casar o serviço de reunião no navegador; nunca vai ao retorno nem ao log."""
    n = api.u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    api.u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class Win32Screen:
    def monitors(self) -> tuple[Monitor, ...]:
        try:
            api = _api()
            found: list[tuple[str, ScreenRect, bool]] = []

            def each(hmon: Any, _hdc: Any, _r: Any, _lp: Any) -> bool:
                dev = _device(api, hmon)
                if dev is not None:
                    found.append(dev)
                return True

            api.u32.EnumDisplayMonitors(None, None, api.mon_proc(each), 0)
        except OSError as e:
            log.debug("monitores não lidos: %s", e)
            return ()
        found.sort(key=lambda d: (d[1].x, d[1].y))
        return tuple(Monitor(key, f"Monitor {i} · {r.width} x {r.height}" + (" · principal" if primary else ""), r, primary)
                     for i, (key, r, primary) in enumerate(found, 1))

    def _monitor_of(self, api: Any, hmon: Any) -> Monitor | None:
        dev = _device(api, hmon)
        if dev is None:
            return None
        return next((m for m in self.monitors() if m.key == dev[0]), None)

    def active_monitor(self) -> Monitor | None:
        try:
            api = _api()
            hwnd = api.u32.GetForegroundWindow()
            hmon = api.u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) if hwnd else None
            if not hmon:                                   # sem janela em primeiro plano: o monitor do cursor
                pt = api.wt.POINT()
                api.u32.GetCursorPos(ctypes.byref(pt))
                hmon = api.u32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            return self._monitor_of(api, hmon)
        except OSError as e:
            log.debug("monitor em uso não lido: %s", e)
            return None

    def meeting_window(self, pid: int | None, exe: str) -> MeetingWindow | None:
        t0 = time.perf_counter()
        try:
            api = _api()
            procs = processes.snapshot_processes()
        except OSError as e:
            log.debug("janela da reunião não procurada: %s", e)
            return None
        roots = {processes.app_root(pid, procs)} if pid and pid in procs else set()
        name = procs[pid].name if pid and pid in procs else exe
        if not roots and exe:
            low = exe.lower()
            roots = {processes.app_root(p, procs) for p, info in procs.items() if info.name.lower() == low}
        if not roots:
            return None
        group = {p for p in procs if processes.app_root(p, procs) in roots}
        own = os.getpid()
        order: list[tuple[Any, int, ScreenRect]] = []

        def each(hwnd: Any, _lp: Any) -> bool:
            if not api.u32.IsWindowVisible(hwnd) or api.u32.IsIconic(hwnd) or _cloaked(api, hwnd):
                return True
            rect = _frame_bounds(api, hwnd)
            if rect is not None:
                wpid = api.wt.DWORD()
                api.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                order.append((hwnd, int(wpid.value), rect))
            return True

        api.u32.EnumWindows(api.win_proc(each), 0)          # do topo para baixo na ordem z [exp]
        mine = [i for i, (_h, p, r) in enumerate(order) if p in group and r.width >= MIN_W and r.height >= MIN_H]
        if not mine:
            return None
        app = name
        pick = mine[0]
        if name.lower() in BROWSERS:                         # no navegador, a janela do serviço de reunião primeiro
            from .activity import match_service

            for i in mine:
                service = match_service(_title(api, order[i][0]))
                if service:
                    pick, app = i, service
                    break
        hwnd, _p, rect = order[pick]
        monitor = self._monitor_of(api, api.u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
        if monitor is None:
            return None
        rect = rect.clip(monitor.rect) or rect
        covered = tuple(c for h, p, r in order[:pick] if p not in group and p != own and not self._passthrough(api, h)
                        for c in (r.clip(rect),) if c is not None)
        log.debug("janela da reunião: pid %s, %dx%d, %d por cima, em %.1f ms", pid, rect.width, rect.height, len(covered),
                  (time.perf_counter() - t0) * 1000)
        return MeetingWindow(rect, monitor, covered, app)

    @staticmethod
    def _passthrough(api: Any, hwnd: Any) -> bool:
        """A janela transparente ao clique ou em camadas não sai preta (A-09: a borda, a dica, a notificação)."""
        return bool(int(api.u32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & (WS_EX_TRANSPARENT | WS_EX_LAYERED))

    def grab_screen(self, rect: ScreenRect) -> Frame | None:
        if rect.width <= 0 or rect.height <= 0:
            return None
        t0 = time.perf_counter()
        try:
            api = _api()
        except OSError as e:
            log.debug("print não tirado: %s", e)
            return None
        u32, g32 = api.u32, api.g32
        screen = u32.GetDC(None)
        if not screen:
            return None
        mem = g32.CreateCompatibleDC(screen)
        bmi = api.BitmapInfo()
        bmi.bmiHeader.biSize = ctypes.sizeof(api.BitmapInfoHeader)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = rect.width, -rect.height     # de cima para baixo
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        dib = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        data: bytes | None = None
        try:
            if dib and bits.value:
                old = g32.SelectObject(mem, dib)
                try:
                    if g32.BitBlt(mem, 0, 0, rect.width, rect.height, screen, rect.x, rect.y, SRCCOPY | CAPTUREBLT):
                        data = ctypes.string_at(bits.value, rect.width * rect.height * 4)
                finally:
                    g32.SelectObject(mem, old)
        finally:
            if dib:
                g32.DeleteObject(dib)
            g32.DeleteDC(mem)
            u32.ReleaseDC(None, screen)
        if data is None:
            log.debug("print falhou: %dx%d", rect.width, rect.height)
            return None
        log.debug("print de %dx%d em %.1f ms", rect.width, rect.height, (time.perf_counter() - t0) * 1000)
        return Frame(data, rect.width, rect.height, rect.width * 4)


__all__ = ["BROWSERS", "MIN_H", "MIN_W", "Win32Screen"]
