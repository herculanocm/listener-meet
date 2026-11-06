"""F5.17 (U-140, U-142, U-146): as contas puras da bandeja de legendas, sem Qt. Ler e gravar a posição e o encosto, dizer
se a bandeja está encostada no widget de gravação, onde ela fica encostada (acima ou abaixo dele, alinhada pela borda
direita), a borda que fica no lugar ao recolher e ao restaurar, e o redimensionar pelas bordas. Retângulos em (x, y,
largura, altura), como em `common.clamp_to_screens`."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag

type Rect = tuple[int, int, int, int]
type Size = tuple[int, int]

GAP = 8                           # entre a bandeja encostada e o widget
SLOP = 16                         # até esta distância (cruzando na horizontal), a bandeja está encostada
EDGE = 6                          # a faixa das bordas que redimensiona, dentro do cartão
MIN_W, MIN_H = 320, 160           # o mínimo da bandeja aberta (abaixo de 320 px o cabeçalho cortaria)
DEFAULT_SIZE: Size = (360, 300)   # a largura do widget
ABOVE, BELOW, LOOSE = "acima", "abaixo", "solta"


class Edge(IntFlag):
    NONE = 0
    LEFT = 1
    TOP = 2
    RIGHT = 4
    BOTTOM = 8


@dataclass(frozen=True, slots=True)
class Dock:
    """Encostada no widget: o lado (acima ou abaixo dele) e os px da borda direita dele até a da bandeja."""
    side: str
    right: int = 0


def parse_rect(raw: str) -> Rect | None:
    """"x,y,largura,altura" gravado; None quando ilegível ou sem tamanho."""
    try:
        x, y, w, h = (int(v) for v in str(raw or "").split(","))
    except ValueError:
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def rect_text(r: Rect) -> str:
    return ",".join(str(int(v)) for v in r)


def parse_dock(raw: str) -> Dock | None:
    """"acima,0" ou "abaixo,12" é encostada; "solta" (e o ilegível) é solta, None."""
    side, _sep, right = str(raw or "").partition(",")
    if side not in (ABOVE, BELOW):
        return None
    try:
        return Dock(side, int(right or 0))
    except ValueError:
        return Dock(side)


def dock_text(dock: Dock | None) -> str:
    return f"{dock.side},{dock.right}" if dock is not None else LOOSE


def docked(widget: Rect, tray: Rect) -> Dock | None:
    """A bandeja encostada no widget: cruzando com ele na horizontal e com a borda de baixo dela até `SLOP` px acima do
    topo dele (acima), ou a de cima até `SLOP` px abaixo da base dele (abaixo)."""
    wx, wy, ww, wh = widget
    tx, ty, tw, th = tray
    if min(wx + ww, tx + tw) - max(wx, tx) <= 0:
        return None
    right = (wx + ww) - (tx + tw)
    if 0 <= wy - (ty + th) <= SLOP:
        return Dock(ABOVE, right)
    if 0 <= ty - (wy + wh) <= SLOP:
        return Dock(BELOW, right)
    return None


def default_dock(widget: Rect, area: Rect) -> Dock:
    """Sem nada salvo: acima do widget; sem espaço acima para o mínimo, do lado que tiver mais espaço."""
    room_above = widget[1] - GAP - area[1]
    room_below = area[1] + area[3] - (widget[1] + widget[3] + GAP)
    return Dock(ABOVE) if room_above >= MIN_H or room_above >= room_below else Dock(BELOW)


def fit_dock(dock: Dock, widget: Rect, area: Rect, need: int) -> Dock:
    """O lado do encosto que cabe: sem `need` px do lado guardado e com mais espaço do outro, troca de lado (o widget foi
    arrastado para perto da borda da tela)."""
    room_above = widget[1] - GAP - area[1]
    room_below = area[1] + area[3] - (widget[1] + widget[3] + GAP)
    if dock.side == ABOVE and room_above < need and room_below > room_above:
        return Dock(BELOW, dock.right)
    if dock.side == BELOW and room_below < need and room_above > room_below:
        return Dock(ABOVE, dock.right)
    return dock


def dock_rect(widget: Rect, size: Size, dock: Dock, area: Rect, min_h: int = MIN_H) -> Rect:
    """Onde fica a bandeja encostada: alinhada pela borda direita do widget (menos `dock.right`), a `GAP` px dele e dentro
    da `area` na horizontal. Sem espaço na vertical, a altura encolhe até `min_h` antes de cobrir o widget."""
    wx, wy, ww, wh = widget
    ax, ay, aw, ah = area
    w, h = size
    x = min(max(wx + ww - dock.right - w, ax), ax + aw - w)
    if dock.side == ABOVE:
        bottom = wy - GAP
        h = max(min(h, bottom - ay), min_h)
        return (x, bottom - h, w, h)
    top = wy + wh + GAP
    h = max(min(h, ay + ah - top), min_h)
    return (x, top, w, h)


def anchored(rect: Rect, height: int, dock: Dock | None, area: Rect | None = None, min_h: int = 0) -> Rect:
    """Recolher ou restaurar solta ou encostada: a borda que toca o widget fica no lugar (solta, a de baixo). Crescendo para
    cima sem espaço na `area`, a altura encolhe até `min_h`."""
    x, y, w, h = rect
    if dock is not None and dock.side == BELOW:
        return (x, y, w, height)
    bottom = y + h
    if area is not None:
        height = max(min(height, bottom - area[1]), min_h)
    return (x, bottom - height, w, height)


def edges_at(x: int, y: int, size: Size, collapsed: bool = False, edge: int = EDGE) -> Edge:
    """As bordas sob o ponto (x, y) da bandeja; recolhida, só as laterais (a altura é a da faixa)."""
    w, h = size
    found = Edge.NONE
    if x < edge:
        found |= Edge.LEFT
    elif x >= w - edge:
        found |= Edge.RIGHT
    if not collapsed:
        if y < edge:
            found |= Edge.TOP
        elif y >= h - edge:
            found |= Edge.BOTTOM
    return found


def resized(start: Rect, edges: Edge, dx: int, dy: int, minimum: Size = (MIN_W, MIN_H)) -> Rect:
    """O retângulo depois de arrastar as `edges` por (dx, dy) a partir de `start`: a borda oposta fica no lugar, e o tamanho
    não passa do mínimo."""
    x, y, w, h = start
    mw, mh = minimum
    if edges & Edge.LEFT:
        nw = max(mw, w - dx)
        x, w = x + w - nw, nw
    elif edges & Edge.RIGHT:
        w = max(mw, w + dx)
    if edges & Edge.TOP:
        nh = max(mh, h - dy)
        y, h = y + h - nh, nh
    elif edges & Edge.BOTTOM:
        h = max(mh, h + dy)
    return (x, y, w, h)


__all__ = ["ABOVE", "BELOW", "DEFAULT_SIZE", "EDGE", "GAP", "LOOSE", "MIN_H", "MIN_W", "SLOP", "Dock", "Edge", "Rect", "Size",
           "anchored", "default_dock", "dock_rect", "dock_text", "docked", "edges_at", "fit_dock", "parse_dock", "parse_rect",
           "rect_text", "resized"]
