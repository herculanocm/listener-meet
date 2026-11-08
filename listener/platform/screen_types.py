"""F5.21 (A-10): os tipos da tela das sugestões ao vivo que a plataforma devolve, fora do `base.py` (que passa de 370
linhas). Tudo em pixels físicos do sistema (A-03): a interface só recorta, escala e codifica; nada aqui casa monitor pelo nome
do Qt, que no Windows é o nome amigável e repete em monitores iguais (A-01). Nenhum título de janela: o rótulo é o
executável ou o serviço reconhecido (A-06)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def clip(self, other: ScreenRect) -> ScreenRect | None:
        """A interseção com `other`, ou nada."""
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.x + self.width, other.x + other.width), min(self.y + self.height, other.y + other.height)
        return ScreenRect(x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


@dataclass(frozen=True, slots=True)
class Monitor:
    key: str                     # a identidade do sistema (no Windows, o `\\.\DISPLAYn`); no log, só resumida
    label: str                   # "Monitor 2 · 3840 x 2160 · principal", numerado da esquerda para a direita (A-28)
    rect: ScreenRect
    primary: bool


@dataclass(frozen=True, slots=True)
class MeetingWindow:
    rect: ScreenRect             # o retângulo visível da janela, cortado no monitor dela
    monitor: Monitor
    covered: tuple[ScreenRect, ...]   # as janelas de outros apps por cima, cortadas no retângulo (saem pretas, A-09)
    app: str                     # o executável ("ms-teams.exe") ou o serviço reconhecido ("Google Meet"); nunca o título


@dataclass(frozen=True, slots=True)
class Frame:
    """O print de um retângulo: BGRA de 32 bits, de cima para baixo (o alfa do BitBlt é indefinido, A-02). No Windows, o buffer
    gravável copiado uma vez da memória do BitBlt sem o GIL (R-06 da revisão do L2: o preparo pinta e lê nele, sem outra cópia)."""
    data: bytes | bytearray | memoryview
    width: int
    height: int
    stride: int


__all__ = ["Frame", "MeetingWindow", "Monitor", "ScreenRect"]
