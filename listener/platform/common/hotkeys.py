"""Gramática única dos atalhos globais (F1.2 M-25) e o adaptador pynput (Linux e macOS; no Windows o adaptador usa
RegisterHotKey, F1.1 A1). `parse_combo` é pura e vale nos três sistemas: a interface valida por `Hotkeys.validate()`.
Os callbacks do pynput rodam na thread dele: quem os registra deve repassar para a thread da interface (sinais Qt).
Limitação conhecida: o pynput só observa a tecla; o app em primeiro plano também a recebe."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...log import get_logger

if TYPE_CHECKING:
    from pynput import keyboard

log = get_logger("platform.hotkeys")

MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
             "win": "win", "cmd": "win", "super": "win", "meta": "win"}
NAMED_KEYS = frozenset({"space", "tab", "enter", "return", "esc", "escape", "backspace", "delete", "insert", "home", "end",
                        "pageup", "pagedown", "left", "up", "right", "down", "pause", "printscreen", "scrolllock", "numlock"})
PYNPUT_MODS = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "win": "<cmd>"}


def parse_combo(combo: str) -> tuple[frozenset[str], str]:
    """'ctrl+alt+shift+r' -> ({'ctrl', 'alt', 'shift'}, 'r'). ValueError com o motivo se a sintaxe for inválida.

    Teclas: uma letra ou dígito ASCII, f1 a f24, ou um nome de `NAMED_KEYS`. Atalho global precisa de modificador,
    salvo teclas de função. A mesma regra vale nos três sistemas (o que o Windows aceita, o pynput também)."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("atalho vazio")
    mods: set[str] = set()
    key: str | None = None
    for p in parts:
        if p in MODIFIERS:
            mods.add(MODIFIERS[p])
        elif key is None:
            key = p
        else:
            raise ValueError(f"mais de uma tecla em {combo!r}")
    if key is None:
        raise ValueError(f"falta a tecla em {combo!r}")
    is_fkey = key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24
    if not (is_fkey or (len(key) == 1 and key.isascii() and key.isalnum()) or key in NAMED_KEYS):
        raise ValueError(f"tecla desconhecida em {combo!r}: {key}")
    if not mods and not is_fkey:
        raise ValueError(f"atalho global precisa de modificador (ctrl, alt, shift, win): {combo!r}")
    return frozenset(mods), key


def validate_combo(combo: str) -> str | None:
    """Motivo da recusa ou None quando o atalho é aceitável."""
    try:
        parse_combo(combo)
    except ValueError as e:
        return str(e)
    return None


def to_pynput(combo: str) -> str:
    """'ctrl+shift+r' -> '<ctrl>+<shift>+r'; 'ctrl+alt+f9' -> '<ctrl>+<alt>+<f9>'."""
    mods, key = parse_combo(combo)
    out = [PYNPUT_MODS[m] for m in ("ctrl", "alt", "shift", "win") if m in mods]
    out.append(key if len(key) == 1 else f"<{key}>")
    return "+".join(out)


class PynputHotkeys:
    def __init__(self) -> None:
        self._map: dict[str, Callable[[], None]] = {}
        self._errors: dict[str, str] = {}
        self._listener: keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()

    def validate(self, combo: str) -> str | None:
        return validate_combo(combo)

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
