"""F5.7, revisão (R-05), só no Windows: os títulos de janela (`processes.window_titles_by_pid`) nunca leem uma janela do
próprio processo, cujo texto vem por `SendMessage` à thread dona e espera enquanto ela está ocupada (2 s medidos); com
`only`, só os pids pedidos. A janela do teste é uma ferramenta de 1 por 1 px fora da tela, mostrada sem ativar (não aparece
na barra de tarefas nem pega o foco). As asserções comparam pids, nunca títulos (nada da tela vai para a saída)."""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from listener.platform.win32 import processes

pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="as janelas do Windows só existem no Windows"),
              pytest.mark.lento]                       # D-050: tempo real (a thread dona fica 2 s ocupada)

WS_POPUP, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000000, 0x00000080, 0x08000000
SW_SHOWNOACTIVATE, PM_REMOVE = 4, 1


@contextmanager
def busy_own_window(hold_s: float) -> Iterator[None]:
    """Uma janela visível do próprio processo cuja thread dona fica `hold_s` sem processar mensagens."""
    if sys.platform != "win32":
        raise OSError("as janelas do Windows só existem no Windows")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.PeekMessageW.argtypes = (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
    made = threading.Event()

    def owner() -> None:
        hwnd = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "STATIC", "x", WS_POPUP, -32000, -32000, 1, 1,
                                      None, None, None, None)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        made.set()
        time.sleep(hold_s)                                 # ocupada: não processa mensagens
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            pass
        user32.DestroyWindow(hwnd)

    t = threading.Thread(target=owner)
    t.start()
    made.wait(5)
    try:
        yield
    finally:
        t.join(hold_s + 5)


def test_a_window_of_our_own_busy_thread_neither_blocks_nor_appears():
    with busy_own_window(2.0):
        t0 = time.perf_counter()
        got = processes.window_titles_by_pid()
        elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"{elapsed:.2f} s"
    assert os.getpid() not in set(got)


def test_only_reads_the_pids_asked():
    assert len(processes.window_titles_by_pid(only=set())) == 0
    full = set(processes.window_titles_by_pid())
    if not full:
        pytest.skip("nenhuma janela com título nesta sessão")
    pid = min(full)
    assert set(processes.window_titles_by_pid(only={pid})) <= {pid}        # a janela pode fechar entre as duas leituras
