"""Utilidades da interface: níveis em dB, cores de saúde, formatação."""
from __future__ import annotations

import math

HEALTH_COLORS = {"ok": "#2ecc71", "warn": "#f1c40f", "alert": "#e74c3c", "muted": "#7f8c8d",
                 "paused": "#95a5a6", "off": "#34495e"}
HEALTH_TEXT = {"ok": "ok", "warn": "sem sinal", "alert": "MUDO", "muted": "mudo", "paused": "pausado", "off": "desligado"}


def level_fraction(rms_value: float, floor_db: float = -60.0) -> float:
    """RMS (0 a 1) -> fração 0 a 1 numa escala de dB com piso em -60 dBFS."""
    db = 20.0 * math.log10(max(float(rms_value), 1e-6))
    return max(0.0, min(1.0, (db - floor_db) / -floor_db))


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def clamp_to_screens(x: int, y: int, w: int, h: int, screens: list[tuple[int, int, int, int]]) -> tuple[int, int] | None:
    """Mantém a janela (x, y, w, h) dentro do monitor com que mais se sobrepõe (retângulos x, y, largura, altura).
    None quando ela não toca monitor nenhum (monitor desplugado): quem chama volta à posição padrão (F1.1 A9)."""
    best, best_area = None, 0
    for sx, sy, sw, sh in screens:
        ix = max(0, min(x + w, sx + sw) - max(x, sx))
        iy = max(0, min(y + h, sy + sh) - max(y, sy))
        if ix * iy > best_area:
            best, best_area = (sx, sy, sw, sh), ix * iy
    if best is None:
        return None
    sx, sy, sw, sh = best
    return int(min(max(x, sx), sx + sw - w)), int(min(max(y, sy), sy + sh - h))
