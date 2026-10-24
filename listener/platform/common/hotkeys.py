"""Atalhos globais com pynput (multiplataforma). Os callbacks rodam na thread do pynput:
quem os registra deve repassar para a thread da interface (sinais Qt)."""
from __future__ import annotations

import threading
from typing import Callable

MODS = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>", "shift": "<shift>",
        "win": "<cmd>", "cmd": "<cmd>", "super": "<cmd>", "meta": "<cmd>"}


def to_pynput(combo: str) -> str:
    """'ctrl+shift+r' -> '<ctrl>+<shift>+r'; 'f9' -> '<f9>'."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("atalho vazio")
    out = []
    for p in parts:
        if p in MODS:
            out.append(MODS[p])
        elif len(p) == 1:
            out.append(p)
        else:
            out.append(f"<{p}>")
    return "+".join(out)


class PynputHotkeys:
    def __init__(self) -> None:
        self._map: dict[str, Callable[[], None]] = {}
        self._listener = None
        self._lock = threading.Lock()

    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        try:
            from pynput.keyboard import HotKey

            key = to_pynput(combo)
            HotKey.parse(key)            # valida a sintaxe antes de registrar
        except Exception:
            return False
        with self._lock:
            self._map[key] = callback
            self._restart_locked()
        return True

    def unregister_all(self) -> None:
        with self._lock:
            self._map.clear()
            self._stop_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    # ---- interno ----
    def _stop_locked(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _restart_locked(self) -> None:
        self._stop_locked()
        if not self._map:
            return
        from pynput import keyboard

        self._listener = keyboard.GlobalHotKeys(dict(self._map))
        self._listener.daemon = True
        self._listener.start()
