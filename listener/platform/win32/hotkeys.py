"""Atalhos globais no Windows com RegisterHotKey (F1.1 A1): registro exclusivo, a tecla é consumida (o app em foco não
a recebe) e o Windows devolve erro quando outro app já a registrou. Uma thread própria com loop de mensagens faz o
registro e recebe WM_HOTKEY; os callbacks rodam nela (quem registra repassa para a interface por sinal Qt).
`parse_combo` é pura e roda em qualquer sistema (usada pela Configurações para validar a sintaxe)."""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from ...log import get_logger

log = get_logger("platform.win32.hotkeys")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_QUIT, WM_HOTKEY, WM_APP = 0x0012, 0x0312, 0x8000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
        "win": MOD_WIN, "cmd": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN}
VK = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
      "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25,
      "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13, "printscreen": 0x2C, "scrolllock": 0x91, "numlock": 0x90}


def parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+alt+shift+r' -> (modificadores, tecla virtual). ValueError com o motivo se a sintaxe for inválida."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("atalho vazio")
    mods, key = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif key is None:
            key = p
        else:
            raise ValueError(f"mais de uma tecla em {combo!r}")
    if key is None:
        raise ValueError(f"falta a tecla em {combo!r}")
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in VK:
        vk = VK[key]
    else:
        raise ValueError(f"tecla desconhecida em {combo!r}: {key}")
    if mods == 0 and not (0x70 <= vk <= 0x87):
        raise ValueError(f"atalho global precisa de modificador (ctrl, alt, shift, win): {combo!r}")
    return mods, vk


class Win32Hotkeys:
    def __init__(self) -> None:
        self._requests: queue.Queue = queue.Queue()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._combos: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._ready = threading.Event()
        self._next_id = 1

    # ---- interface ----
    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        try:
            mods, vk = parse_combo(combo)
        except ValueError as e:
            self._errors[combo] = str(e)
            log.warning("atalho %r inválido: %s", combo, e)
            return False
        if not self._ensure_thread():
            self._errors[combo] = "thread de atalhos não iniciou"
            return False
        done = {"event": threading.Event(), "ok": False}
        self._requests.put(("register", (combo, mods, vk, callback), done))
        self._wake()
        done["event"].wait(2.0)
        return bool(done["ok"])

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def unregister_all(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            done = {"event": threading.Event(), "ok": True}
            self._requests.put(("unregister_all", None, done))
            self._wake()
            done["event"].wait(2.0)
        self._errors.clear()

    def stop(self) -> None:
        self.unregister_all()
        if self._thread is not None and self._thread.is_alive() and self._tid:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2.0)
        self._thread = None
        self._tid = None

    # ---- thread com loop de mensagens ----
    def _ensure_thread(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, name="hotkeys-win32", daemon=True)
        self._thread.start()
        return self._ready.wait(3.0)

    def _wake(self) -> None:
        import ctypes

        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        self._tid = int(kernel32.GetCurrentThreadId())
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, 0)   # cria a fila de mensagens da thread
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    cb = self._callbacks.get(int(msg.wParam))
                    if cb is not None:
                        try:
                            cb()
                        except Exception:
                            log.exception("callback do atalho falhou")
                elif msg.message == WM_APP:
                    self._drain(user32, ctypes)
        finally:
            for hk_id in list(self._callbacks):
                user32.UnregisterHotKey(None, hk_id)
            self._callbacks.clear()
            self._combos.clear()

    def _drain(self, user32, ctypes) -> None:
        while True:
            try:
                kind, payload, done = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "register":
                    combo, mods, vk, cb = payload
                    old = self._combos.pop(combo, None)
                    if old is not None:
                        user32.UnregisterHotKey(None, old)
                        self._callbacks.pop(old, None)
                    hk_id = self._next_id
                    self._next_id += 1
                    ok = bool(user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk))
                    if ok:
                        self._callbacks[hk_id] = cb
                        self._combos[combo] = hk_id
                        self._errors.pop(combo, None)
                        log.debug("atalho %r registrado", combo)
                    else:
                        err = ctypes.get_last_error()
                        self._errors[combo] = ("já usado por outro app" if err == ERROR_HOTKEY_ALREADY_REGISTERED
                                               else f"erro {err} do Windows")
                        log.warning("atalho %r não registrou: %s", combo, self._errors[combo])
                    done["ok"] = ok
                elif kind == "unregister_all":
                    for hk_id in list(self._callbacks):
                        user32.UnregisterHotKey(None, hk_id)
                    self._callbacks.clear()
                    self._combos.clear()
            finally:
                done["event"].set()
