"""F5.21 (R-02, R-03 e R-10 da revisão do L2): a lógica do `Win32Screen.meeting_window` no Linux, com um user32 e um dwmapi de
mentira (as janelas na ordem z, do topo para baixo, com o pid, o retângulo, o título, o estilo, a afinidade de captura e o
dono). Quem é a janela da reunião: o app da fonte, o executável e cada app de reunião, na ordem; no navegador, a do serviço. Quem
sai preto por cima: outro app; este app, quando sai na captura (as Configurações); do próprio app da reunião, a grande que não
é dela (o Gmail no mesmo Chrome). O que fica de fora: a transparente ou em camadas, a excluída da captura (a bandeja), a
possuída pela janela da reunião e a pequena do app (a barra do compartilhamento)."""
from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from listener.platform.screen_types import ScreenRect
from listener.platform.win32 import processes, screen

R = ScreenRect
OWN = os.getpid()
PROCS = {1: ("explorer.exe", 0), 100: ("ms-teams.exe", 1), 101: ("ms-teams.exe", 100), 102: ("msedgewebview2.exe", 101),
         200: ("outlook.exe", 1), 300: ("chrome.exe", 1), 301: ("chrome.exe", 300), 400: ("spotify.exe", 1), 500: ("slack.exe", 1),
         OWN: ("python.exe", 1)}


@dataclass(frozen=True)
class Win:
    hwnd: int
    pid: int
    rect: ScreenRect
    title: str = ""
    exstyle: int = 0
    affinity: int = 0
    owner: int = 0


TEAMS = Win(10, 102, R(0, 0, 1920, 1040), "Reunião | Microsoft Teams")          # a janela no WebView2 neto do Teams


def _api(windows: list[Win]) -> Any:
    by = {w.hwnd: w for w in windows}

    def enum_windows(cb: Callable[[int, int], bool], _lp: int) -> int:
        for w in windows:
            if not cb(w.hwnd, 0):
                break
        return 1

    def attribute(hwnd: int, attr: int, ref: Any, _size: int) -> int:
        obj = ref._obj
        if attr == screen.DWMWA_CLOAKED:
            obj.value = 0
            return 0
        r = by[hwnd].rect
        obj.left, obj.top, obj.right, obj.bottom = r.x, r.y, r.x + r.width, r.y + r.height
        return 0

    def thread_pid(hwnd: int, ref: Any) -> int:
        ref._obj.value = by[hwnd].pid
        return 1

    def affinity(hwnd: int, ref: Any) -> int:
        ref._obj.value = by[hwnd].affinity
        return 1

    def title(hwnd: int, buf: Any, _n: int) -> int:
        buf.value = by[hwnd].title
        return len(by[hwnd].title)

    def monitor_info(_h: int, ref: Any) -> int:
        info = ref._obj
        info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom = 0, 0, 1920, 1080
        info.dwFlags, info.szDevice = 1, "\\\\.\\DISPLAY1"
        return 1

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32))

    u32 = SimpleNamespace(EnumWindows=enum_windows, IsWindowVisible=lambda h: 1, IsIconic=lambda h: 0, GetWindowThreadProcessId=thread_pid,
                          GetWindowLongW=lambda h, i: by[h].exstyle, GetWindowTextLengthW=lambda h: len(by[h].title),
                          GetWindowTextW=title, GetWindowDisplayAffinity=affinity,
                          GetWindow=lambda h, cmd: by[h].owner if h in by and cmd == screen.GW_OWNER else 0,
                          MonitorFromWindow=lambda h, f: 7, GetMonitorInfoW=monitor_info,
                          EnumDisplayMonitors=lambda a, b, cb, d: cb(7, None, None, 0))
    return SimpleNamespace(u32=u32, dwm=SimpleNamespace(DwmGetWindowAttribute=attribute), wt=wintypes, MonitorInfoEx=MonitorInfoEx,
                           win_proc=lambda f: f, mon_proc=lambda f: f)


@pytest.fixture
def desktop(monkeypatch: Any) -> Callable[[list[Win]], screen.Win32Screen]:
    """`desktop(janelas)` põe a área de trabalho de mentira e devolve o `Win32Screen` que a lê."""
    monkeypatch.setattr(processes, "snapshot_processes", lambda: {p: processes.ProcInfo(p, n, pp) for p, (n, pp) in PROCS.items()})

    def install(windows: list[Win]) -> screen.Win32Screen:
        api = _api(windows)
        monkeypatch.setattr(screen, "_api", lambda: api)
        return screen.Win32Screen()

    return install


def test_the_windows_of_other_apps_and_of_this_one_over_the_meeting_go_black(desktop):
    """R-02 (a): o e-mail de outro app e as janelas deste app que saem na captura (as Configurações) por cima saem pretas; a
    excluída da captura (a bandeja) e a em camadas (o aviso), não. O Teams novo: a janela no WebView2 neto, o rótulo do raiz."""
    mail = Win(20, 200, R(100, 100, 800, 600), "Caixa de entrada")
    settings = Win(30, OWN, R(1000, 100, 700, 500), "Configurações")
    tray = Win(31, OWN, R(1500, 600, 360, 400), affinity=screen.WDA_EXCLUDEFROMCAPTURE)
    toast = Win(40, 200, R(0, 0, 400, 300), exstyle=screen.WS_EX_LAYERED)
    win = desktop([mail, settings, tray, toast, TEAMS]).meeting_window(100, "ms-teams.exe")
    assert win is not None and win.app == "ms-teams.exe" and win.rect == TEAMS.rect and win.monitor.primary
    assert win.covered == (R(100, 100, 800, 600), R(1000, 100, 700, 500))


def test_in_the_browser_the_meeting_is_the_service_window_and_the_other_windows_of_it_go_black(desktop):
    """R-02 (b): o Gmail no mesmo Chrome por cima do Meet sai preto; do próprio navegador, a possuída pela janela do Meet e a
    pequena (a barra do compartilhamento) ficam."""
    meet = Win(50, 301, R(0, 0, 1920, 1040), "Meet - abc-defg-hij")
    gmail = Win(51, 300, R(300, 150, 1000, 700), "Caixa de entrada - Gmail")
    popup = Win(52, 301, R(800, 400, 700, 500), "Compartilhar a tela", owner=50)
    bar = Win(53, 300, R(700, 980, 500, 60), "meet.google.com está compartilhando")
    win = desktop([gmail, popup, bar, meet]).meeting_window(300, "chrome.exe")
    assert win is not None and win.app == "Google Meet" and win.covered == (R(300, 150, 1000, 700),)


def test_without_the_source_app_window_each_meeting_app_is_tried_in_order(desktop):
    """R-03: a fonte "sistema inteiro" (sem pid nem executável) e a fonte cujo app não tem janela: cada app, na ordem dada (o
    `fallback_exe` primeiro), numa leitura só; o Slack por cima do Teams é outro app e sai preto."""
    slack = Win(60, 500, R(200, 200, 900, 600), "Slack")
    found = desktop([slack, TEAMS])
    win = found.meeting_window(None, "", ("ms-teams.exe", "slack.exe"))
    assert win is not None and win.app == "ms-teams.exe" and win.covered == (R(200, 200, 900, 600),)
    assert found.meeting_window(None, "", ("zoom.exe", "slack.exe")).app == "slack.exe"
    assert found.meeting_window(400, "spotify.exe", ("ms-teams.exe",)).app == "ms-teams.exe"     # o app da fonte sem janela
    assert found.meeting_window(None, "") is None and found.meeting_window(None, "", ("zoom.exe",)) is None
