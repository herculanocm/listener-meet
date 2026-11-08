"""F5.21 (A-01 a A-10): a tela das sugestões ao vivo no Windows (`Win32Screen`, herdada pelo `Win32Platform`; o `platform.py`
não cresce). O user32, o GDI e o DWM por ctypes, com instâncias próprias das DLLs e as assinaturas daqui (como a barra de
tarefas), sem UI Automation (CP4) nem WinRT (CP14). Tudo em pixels físicos: o processo roda em PMv2 (o Qt 6), e o
`DWMWA_EXTENDED_FRAME_BOUNDS`, o `rcMonitor` e o BitBlt falam a mesma unidade (A-03).

- `monitors()`: `EnumDisplayMonitors` e `GetMonitorInfoW`, da esquerda para a direita, "Monitor N · LxA · principal" (A-28;
  o nome amigável do monitor repete em dois iguais, A-01).
- `active_monitor()`: o monitor da janela em primeiro plano (a bandeja não ativa: é o app que o usuário está usando); sem ela,
  o do cursor (A-08).
- `meeting_window(pid, exe, apps)`: as janelas do grupo do app (os pids com o mesmo `app_root`, que sobe pelo WebView2 do
  Teams novo) na ordem z do `EnumWindows`, do topo para baixo: a primeira visível, sem `IsIconic`, sem `DWMWA_CLOAKED` (outra
  área de trabalho) e com ao menos 640 x 360 px; no navegador, primeiro a que casa o serviço de reunião (o título só em memória,
  como o sensor da F5.7). Procura o app do pid, depois o do executável e depois cada um dos `apps`, na ordem (o `fallback_exe`
  e os apps de reunião da lista de fábrica: a fonte "sistema inteiro", R-03 da revisão do L2), com uma leitura dos processos
  e uma das janelas. O retângulo é o `DWMWA_EXTENDED_FRAME_BOUNDS` (sem a borda invisível do `GetWindowRect`), cortado no monitor
  dela. As janelas acima dela na ordem z vão em `covered`, para sair pretas (A-09), salvo: as transparentes ao clique ou em
  camadas (a borda, a dica, a notificação); as excluídas da captura (`WDA_EXCLUDEFROMCAPTURE`: a bandeja e os popups deste
  app, que nem saem no print); e, do próprio app da reunião, as possuídas pela janela dela e as pequenas (o menu, a barra do
  compartilhamento). As outras janelas do mesmo app (o Gmail sobre o Meet no mesmo Chrome) e as deste app que saem na
  captura (o Histórico, as Configurações, o widget) saem pretas (R-02 da revisão do L2).
- `grab_screen(rect)`: BitBlt (`SRCCOPY | CAPTUREBLT`) só do retângulo, num DIB de 32 bits de cima para baixo (A-02), copiado
  uma vez por `memmove` (sem o GIL) para um buffer gravável (R-06); as janelas com `WDA_EXCLUDEFROMCAPTURE` não saem no
  Windows 10 2004 ou mais novo [fato: Microsoft; que o BitBlt respeita, exp].
Roda em qualquer thread (a do pedido, nunca a da interface). Nada daqui roda fora do Windows: as DLLs e os tipos de callback
nascem na primeira chamada. O log leva dimensões, pids e ms; nunca título (D-053)."""
from __future__ import annotations

import ctypes
import functools
import sys
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

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
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GW_OWNER = 4
SMALL_AREA = MIN_W * MIN_H // 2               # R-02: a janela do próprio app menor que isto não cobre (o menu, a barra)
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
    u32.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    u32.GetWindow.restype = wintypes.HWND
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


def _z_order(api: Any) -> list[tuple[Any, int, ScreenRect]]:
    """As janelas visíveis, sem `IsIconic` nem `DWMWA_CLOAKED`, com o pid e o retângulo, do topo para baixo [exp]."""
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

    api.u32.EnumWindows(api.win_proc(each), 0)
    return order


def _pick(api: Any, order: list[tuple[Any, int, ScreenRect]], procs: dict[int, Any], root_of: dict[int, int],
          roots: set[int]) -> tuple[int, str] | None:
    """A janela da reunião entre as dos `roots`: a mais de cima com o tamanho mínimo; no navegador, a do serviço de reunião.
    Devolve o índice na ordem z e o rótulo (o executável do app, ou o serviço)."""
    mine = [i for i, (_h, p, r) in enumerate(order) if root_of.get(p, p) in roots and r.width >= MIN_W and r.height >= MIN_H]
    if not mine:
        return None
    root = root_of.get(order[mine[0]][1], order[mine[0]][1])
    name = procs[root].name if root in procs else ""
    if name.lower() in BROWSERS:                             # no navegador, a janela do serviço de reunião primeiro
        from .activity import match_service

        for i in mine:
            service = match_service(_title(api, order[i][0]))
            if service:
                return i, service
    return mine[0], name


def _covers(api: Any, hwnd: Any, same_app: bool, rect: ScreenRect, target: Any) -> bool:
    """A janela acima da reunião sai preta (A-09), salvo a transparente ao clique ou em camadas, a excluída da captura e, do
    próprio app da reunião, a possuída pela janela dela ou a pequena (R-02 da revisão do L2)."""
    if int(api.u32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & (WS_EX_TRANSPARENT | WS_EX_LAYERED):
        return False
    affinity = api.wt.DWORD()
    if api.u32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)) and affinity.value == WDA_EXCLUDEFROMCAPTURE:
        return False
    return not same_app or not (_owned_by(api, hwnd, target) or rect.area < SMALL_AREA)


def _owned_by(api: Any, hwnd: Any, target: Any) -> bool:
    owner = hwnd
    for _ in range(8):                                       # o menu do popup da janela: a cadeia de donos é curta
        owner = api.u32.GetWindow(owner, GW_OWNER)
        if not owner:
            return False
        if owner == target:
            return True
    return False


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

    def meeting_window(self, pid: int | None, exe: str, apps: Sequence[str] = ()) -> MeetingWindow | None:
        t0 = time.perf_counter()
        try:
            api = _api()
            procs = processes.snapshot_processes()
        except OSError as e:
            log.debug("janela da reunião não procurada: %s", e)
            return None
        root_of = {p: processes.app_root(p, procs) for p in procs}
        tries: list[set[int]] = [{root_of[pid]}] if pid and pid in procs else []
        for name in (exe, *apps):                           # o executável da fonte e depois cada app, na ordem (R-03)
            low = name.lower()
            roots = {root_of[p] for p, info in procs.items() if low and info.name.lower() == low}
            if roots and roots not in tries:
                tries.append(roots)
        if not tries:
            return None
        order = _z_order(api)
        hit = next((h for h in (_pick(api, order, procs, root_of, roots) for roots in tries) if h is not None), None)
        if hit is None:
            return None
        pick, app = hit
        hwnd, wpid, rect = order[pick]
        monitor = self._monitor_of(api, api.u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
        if monitor is None:
            return None
        rect = rect.clip(monitor.rect) or rect
        root = root_of.get(wpid, wpid)
        covered = tuple(c for h, p, r in order[:pick] if _covers(api, h, root_of.get(p, p) == root, r, hwnd)
                        for c in (r.clip(rect),) if c is not None)
        log.debug("janela da reunião: pid %s, %dx%d, %d por cima, em %.1f ms", wpid, rect.width, rect.height, len(covered),
                  (time.perf_counter() - t0) * 1000)
        return MeetingWindow(rect, monitor, covered, app)

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
        data: memoryview | None = None
        try:
            if dib and bits.value:
                old = g32.SelectObject(mem, dib)
                try:
                    if g32.BitBlt(mem, 0, 0, rect.width, rect.height, screen, rect.x, rect.y, SRCCOPY | CAPTUREBLT):
                        n = rect.width * rect.height * 4
                        out = np.empty(n, dtype=np.uint8)            # sem zerar: o memmove escreve tudo
                        ctypes.memmove(out.ctypes.data, bits.value, n)   # R-06: uma cópia, sem o GIL (o `string_at` o prendia)
                        data = memoryview(out)
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
