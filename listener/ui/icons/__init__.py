"""Os ícones da F5.4 (L1, U-053; A-20): SVG próprios nesta pasta, numa grade de 20 px com traço de 1,5 px, desenhados em
preto e recoloridos na cor do tom por `SourceIn` (só o alfa do desenho conta). Lidos por `importlib.resources` (o
`package-data` do `pyproject.toml` os leva na instalação), sem a Segoe Fluent Icons (não é aberta, decisão 4).

- Cache por nome, cor, tamanho e DPR (`pixmap`); o `QIcon` leva um pixmap por DPR das telas presentes, o normal na cor do
  tom e o desabilitado em `disabled` (Comportamento).
- `set_icon(w, nome, tom)` põe o ícone num botão (ou o pixmap num rótulo) nos tokens da janela dele e guarda o pedido na
  propriedade `theme_icon`; a troca de tema chama `reapply` (A-10: a varredura de `theme`).
- `brand` é a marca do app (o ícone da janela e, no L2, o da bandeja, U-070).
- `clear_cache()` esvazia os caches no `theme.release` (R-06: nada de pixmap vivo até a derrubada do PySide)."""
from __future__ import annotations

from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from ..theme import Tokens, Tone, surface_tokens

ICON_PROP = "theme_icon"
GRID = 20                                          # a grade dos desenhos (px lógicos)
BUTTON_SIZE = 16                                   # o ícone ao lado do texto de um botão
NAMES = frozenset(p.name.removesuffix(".svg") for p in resources.files(__name__).iterdir() if p.name.endswith(".svg"))


@lru_cache(maxsize=64)
def svg(name: str) -> bytes:
    """O SVG de um ícone (KeyError para um nome que não existe)."""
    if name not in NAMES:
        raise KeyError(f"ícone desconhecido: {name}")
    return (resources.files(__name__) / f"{name}.svg").read_bytes()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = GRID, dpr: float = 1.0) -> QPixmap:
    """O ícone `name` em `size` px lógicos no DPR dado, pintado em `color` ("#rrggbb" dos tokens)."""
    px = max(1, round(size * dpr))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name))).render(painter, QRectF(0, 0, px, px))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(image.rect(), QColor(color))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    return result


def screen_dprs() -> tuple[float, ...]:
    """Os DPR das telas presentes (sempre com o 1,0)."""
    found = {1.0}
    if QGuiApplication.instance() is not None:
        found |= {float(s.devicePixelRatio()) for s in QGuiApplication.screens()}
    return tuple(sorted(found))


def icon(name: str, color: str, size: int = GRID, disabled: str | None = None) -> QIcon:
    """O `QIcon` com um pixmap por DPR das telas, o normal em `color` e, dado, o desabilitado em `disabled`."""
    return _icon(name, color, size, disabled, screen_dprs())


@lru_cache(maxsize=256)
def _icon(name: str, color: str, size: int, disabled: str | None, dprs: tuple[float, ...]) -> QIcon:
    result = QIcon()
    for dpr in dprs:
        result.addPixmap(pixmap(name, color, size, dpr), QIcon.Mode.Normal)
        if disabled is not None:
            result.addPixmap(pixmap(name, disabled, size, dpr), QIcon.Mode.Disabled)
    return result


def tone_icon(name: str, value: Tone = Tone.TEXT, t: Tokens | None = None, size: int = GRID) -> QIcon:
    """O ícone no tom dos tokens `t` (os do tema vigente quando não dados), com o desabilitado em `disabled`."""
    tk = t if t is not None else surface_tokens(None)
    return icon(name, tk.tone(value), size, tk.disabled)


def set_icon(w: QWidget, name: str, value: Tone = Tone.TEXT, size: int = BUTTON_SIZE) -> None:
    """O ícone num botão (ou o pixmap num rótulo) nos tokens da janela de `w`; a troca de tema o repinta (`theme_icon`)."""
    if name not in NAMES:
        raise KeyError(f"ícone desconhecido: {name}")
    w.setProperty(ICON_PROP, f"{name}:{value.value}:{size}")
    reapply(w)


def reapply(w: QWidget) -> None:
    """O ícone guardado em `theme_icon` de novo, nos tokens vigentes da janela de `w` (a varredura da troca de tema)."""
    name, value, size = str(w.property(ICON_PROP)).split(":")
    t, px = surface_tokens(w), int(size)
    if isinstance(w, QAbstractButton):
        w.setIcon(tone_icon(name, Tone(value), t, px))
        w.setIconSize(QSize(px, px))
    elif isinstance(w, QLabel):
        w.setPixmap(pixmap(name, t.tone(Tone(value)), px, max(screen_dprs())))


def clear_cache() -> None:
    """Os caches vazios (o `theme.release`, antes da derrubada do PySide)."""
    _icon.cache_clear()
    pixmap.cache_clear()


__all__ = ["BUTTON_SIZE", "GRID", "ICON_PROP", "NAMES", "clear_cache", "icon", "pixmap", "reapply", "screen_dprs", "set_icon", "svg",
           "tone_icon"]
