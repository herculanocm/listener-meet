"""Atalhos globais com pynput (Linux e macOS; no Windows o adaptador usa RegisterHotKey, F1.1 A1).
Os callbacks rodam na thread do pynput: quem os registra deve repassar para a thread da interface (sinais Qt).
Limitação conhecida: o pynput só observa a tecla; o app em primeiro plano também a recebe."""
from __future__ import annotations

import threading
from collections.abc import Callable

from ...log import get_logger

log = get_logger("platform.hotkeys")

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
        self._errors: dict[str, str] = {}
        self._listener = None
        self._lock = threading.Lock()

    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        try:
            from pynput.keyboard import HotKey

            key = to_pynput(combo)
            HotKey.parse(key)            # valida a sintaxe antes de registrar
        except Exception as e:
            self._errors[combo] = f"sintaxe inválida ({e})"
            log.warning("atalho %r inválido: %s", combo, e)
            return False
        with self._lock:
            self._map[key] = callback
            self._errors.pop(combo, None)
            try:
                self._restart_locked()
            except Exception as e:
                self._errors[combo] = f"não registrou ({e})"
                log.warning("atalho %r não registrou: %s", combo, e)
                return False
        return True

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def unregister_all(self) -> None:
        with self._lock:
            self._map.clear()
            self._errors.clear()
            self._stop_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    # ---- interno ----
    def _stop_locked(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as e:
                log.debug("parar listener do pynput: %s", e)
            self._listener = None

    def _restart_locked(self) -> None:
        self._stop_locked()
        if not self._map:
            return
        from pynput import keyboard

        self._listener = keyboard.GlobalHotKeys(dict(self._map))
        self._listener.daemon = True
        self._listener.start()
