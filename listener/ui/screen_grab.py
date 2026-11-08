"""F5.21 (A-02, A-08, A-09, A-17, A-23, A-37): a tela de um pedido de sugestão, do lado da interface, toda dentro da tarefa do
pedido (a thread da interface não espera nada daqui). A escolha do alvo, o print pela plataforma e o preparo: as janelas de
outros apps por cima pintadas de preto, a imagem de 1.600 px em JPEG (a enviada), a miniatura de 320 x 180 (sempre), a cópia
de 960 px (com `suggest.screen_keep`) e a imagem vazia. O `QImage` roda fora da thread da interface [fato: a sonda da revisão
de arquitetura]; o `QPixmap`, nunca. A escolha do alvo também roda na tarefa: a leitura dos processos leva ~30 ms (D-103).

- O alvo (`pick_target`), pela ordem: o monitor fixo (`monitor:<chave>`, se ainda existir; senão o automático, com a nota); a
  janela da reunião (`janela`): o app da fonte da gravação, o `fallback_exe` e os apps de reunião da lista de fábrica (a fonte
  "sistema inteiro", R-03 da revisão do L2); o monitor em uso, o da janela em primeiro plano (`primeiro_plano`). A escolha "o
  monitor em uso" (`cursor`) pula a janela da reunião e grava `cursor`.
- O preparo pinta de preto, no próprio buffer do print (sem outra cópia, R-06), o que está por cima; a reunião coberta assim
  em 80 % ou mais não vai, com a nota própria (R-04).
- A vazia (`is_empty`, sobre o print reduzido a uma grade de 160 x 90 pela média de cada bloco, antes de pintar; R-04 da
  revisão do L2): uma cor só; quase preta (nada acima de 16); ou o miolo escuro, com ao menos metade da tela, sem nada claro
  dentro: o conteúdo protegido preto no meio da moldura do Teams (A-37). O slide escuro com texto, o terminal e o editor
  escuro vão: o texto claro fica dentro do escuro (o traço de 1 px ainda clareia a média do bloco).
Nada aqui grava nem loga a imagem; o log leva dimensões, ms e o estado."""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageWriter

from ..llm.suggest_ctx import ScreenShot
from ..llm.types import ImagePart
from ..log import get_logger
from ..model import ScreenSource, ScreenStatus
from ..platform.base import KIND_MEETING
from ..platform.screen_types import Frame, ScreenRect
from .call_config import label_for

if TYPE_CHECKING:
    from ..platform.base import Platform, WatchedApp

log = get_logger("ui.suggest")

SENT_SIDE, COPY_SIDE = 1_600, 960                # o lado maior da enviada e o da cópia guardada (A-23)
THUMB_W, THUMB_H = 320, 180
SENT_QUALITY, COPY_QUALITY, THUMB_QUALITY = 80, 70, 75
AUTO, CURSOR, MONITOR = "auto", "cursor", "monitor:"
NO_MONITOR = "o monitor fixo não está ligado: foi a tela automática"
COVERED_NOTE = "a reunião estava coberta por outras janelas: só as legendas"
GRID_W, GRID_H = 160, 90                         # a grade da regra da vazia (a média de cada bloco)
DARK, NEAR_BLACK = 8, 16                         # o bloco escuro; a tela quase preta (nada acima)
CORE_SHARE = 0.5                                 # o miolo escuro do protegido: ao menos metade da tela...
LIGHT_INSIDE = 0.003                             # ...com quase nada claro dentro (o slide escuro tem o texto)
COVERED_SHARE = 0.8                              # a reunião coberta assim por outras janelas não vai


@dataclass(frozen=True, slots=True)
class Target:
    rect: ScreenRect
    source: ScreenSource
    app: str                                     # o rótulo ("janela do Teams", "Monitor 2"); nunca o título da janela
    covered: tuple[ScreenRect, ...] = ()
    note: str = ""


def parse_source(value: object) -> tuple[str, str]:
    """`suggest.screen_source`: ("auto" | "cursor" | "monitor", a chave do monitor); o que não se entende vira o automático."""
    text = str(value or "").strip()
    if text == CURSOR:
        return CURSOR, ""
    if text.startswith(MONITOR) and len(text) > len(MONITOR):
        return "monitor", text[len(MONITOR):]
    return AUTO, ""


def app_label(app: str, watched: Sequence[WatchedApp]) -> str:
    """"janela do Teams": o rótulo da lista de fábrica pelo executável, ou o serviço reconhecido no navegador (A-06)."""
    return f"janela do {label_for(watched, app) if app.lower().endswith('.exe') else app}"


def meeting_apps(watched: Sequence[WatchedApp], fallback: str = "") -> tuple[str, ...]:
    """O `fallback_exe` da fonte e depois os apps de reunião da lista de fábrica, sem repetir (R-03)."""
    return tuple(dict.fromkeys(e for e in (fallback.lower(), *(a.exe for a in watched if a.kind == KIND_MEETING)) if e))


def pick_target(platform: Platform, source: object, pid: int | None, exe: str, watched: Sequence[WatchedApp],
                fallback: str = "") -> Target | None:
    kind, key = parse_source(source)
    note = ""
    if kind == "monitor":
        fixed = next((m for m in platform.monitors() if m.key == key), None)
        if fixed is not None:
            return Target(fixed.rect, ScreenSource.MONITOR, fixed.label.split(" · ")[0])
        note = NO_MONITOR
    if kind != CURSOR:
        win = platform.meeting_window(pid, exe, meeting_apps(watched, fallback))
        if win is not None:
            return Target(win.rect, ScreenSource.WINDOW, app_label(win.app, watched), win.covered, note)
    mon = platform.active_monitor()
    if mon is None:
        return None
    return Target(mon.rect, ScreenSource.CURSOR if kind == CURSOR else ScreenSource.FOREGROUND, mon.label.split(" · ")[0], (), note)


def _jpeg(img: QImage, quality: int) -> bytes:
    """O JPEG pelo `QImageWriter` (o `QImage.save` do PySide6 recusa o formato em bytes, que os tipos pedem)."""
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buf, QByteArray(b"jpeg"))
    writer.setQuality(quality)
    if not writer.write(img):
        raise OSError(f"JPEG não gerado: {writer.errorString()}")
    buf.close()
    return bytes(data.data())


def _fit(img: QImage, side: int) -> QImage:
    if max(img.width(), img.height()) <= side:
        return img
    return img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def is_empty(small: QImage) -> bool:
    """`small`: o print reduzido à grade (a média de cada bloco), antes de pintar."""
    img = small.convertToFormat(QImage.Format.Format_RGB32)
    rgb = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4,
                                                                                         4)[:, :img.width(), :3]
    if int((rgb.max(axis=(0, 1)).astype(int) - rgb.min(axis=(0, 1))).max()) <= 2:
        return True                                          # uma cor só
    top = rgb.max(axis=2)
    if int(top.max()) < NEAR_BLACK:
        return True                                          # quase preta: nada à vista
    dark = top < DARK
    if float(dark.mean()) < CORE_SHARE:
        return False
    rows, cols = np.flatnonzero(dark.mean(axis=1) >= 0.5), np.flatnonzero(dark.mean(axis=0) >= 0.5)
    if rows.size == 0 or cols.size == 0:
        return False
    core = dark[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    return core.size >= CORE_SHARE * dark.size and float((~core).mean()) <= LIGHT_INSIDE


def covered_share(target: Target) -> float:
    """A parte do alvo coberta por outras janelas (a união, pelos centros de uma grade)."""
    r = target.rect
    if not target.covered or r.area == 0:
        return 0.0
    xs = r.x + (np.arange(GRID_W) + 0.5) * r.width / GRID_W
    ys = r.y + (np.arange(GRID_H) + 0.5) * r.height / GRID_H
    mask = np.zeros((GRID_H, GRID_W), dtype=bool)
    for c in target.covered:
        mask |= ((ys >= c.y) & (ys < c.y + c.height))[:, None] & ((xs >= c.x) & (xs < c.x + c.width))[None, :]
    return float(mask.mean())


def prepare(frame: Frame | None, target: Target, keep: bool) -> ScreenShot:
    """O print pronto para o pedido: a enviada, a miniatura, a cópia e o estado."""
    t0 = time.perf_counter()
    if frame is None:
        return ScreenShot(ScreenStatus.FAILED, target.source, target.app, note=target.note)
    w, h = frame.width, frame.height                     # só as dimensões vão ao log (D-053)
    buf = np.frombuffer(frame.data, dtype=np.uint8)
    if not buf.flags.writeable:
        buf = buf.copy()                                 # o `bytes` dos testes; o do BitBlt já é gravável (R-06)
    lines = buf.reshape(h, frame.stride)
    img = QImage(lines.data, w, h, frame.stride, QImage.Format.Format_RGB32)   # sem cópia: `lines` vive até o fim daqui
    small = img.scaled(GRID_W, GRID_H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    empty = is_empty(small)                              # antes de pintar: o protegido é do conteúdo (R-04)
    share = covered_share(target)
    px = lines.reshape(h, frame.stride // 4, 4)[:, :w]
    for r in target.covered:                             # A-09: o que está por cima da reunião sai preto, no mesmo buffer
        x0, y0 = max(0, r.x - target.rect.x), max(0, r.y - target.rect.y)
        px[y0:y0 + r.height, x0:x0 + r.width, :3] = 0
    thumb = _jpeg(img.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation),
                  THUMB_QUALITY)
    if empty or share >= COVERED_SHARE:
        log.info("tela não enviada (%dx%d, %s): a sugestão segue sem ela", w, h, "vazia" if empty else "coberta")
        return ScreenShot(ScreenStatus.EMPTY, target.source, target.app, None, thumb, None, len(target.covered),
                          "" if empty else COVERED_NOTE)
    sent = _fit(img, SENT_SIDE)
    data = _jpeg(sent, SENT_QUALITY)
    copy = _jpeg(_fit(img, COPY_SIDE), COPY_QUALITY) if keep else None
    log.info("tela pronta: %dx%d para %dx%d, %d kB, %d por cima, em %.0f ms", w, h, sent.width(), sent.height(),
             len(data) // 1024, len(target.covered), (time.perf_counter() - t0) * 1000)
    return ScreenShot(ScreenStatus.SENT, target.source, target.app, ImagePart(data, "image/jpeg", sent.width(), sent.height(),
                                                                              f"tela: {target.app}"), thumb, copy,
                      len(target.covered), target.note)


def capture(platform: Platform, source: object, pid: int | None, exe: str, watched: Sequence[WatchedApp], keep: bool,
            fallback: str = "") -> ScreenShot:
    """Na tarefa do pedido: o alvo, o print e o preparo; o que falha vira o estado `falhou`, com a fonte e o rótulo do alvo
    quando ele já se sabia (R-08), e a sugestão segue sem a tela."""
    target: Target | None = None
    try:
        target = pick_target(platform, source, pid, exe, watched, fallback)
        if target is None:
            return ScreenShot(ScreenStatus.FAILED, _intended(source), "tela")
        return prepare(platform.grab_screen(target.rect), target, keep)
    except Exception as e:                               # o print nunca derruba o pedido
        log.warning("tela não capturada: %s", type(e).__name__)
        if target is not None:
            return ScreenShot(ScreenStatus.FAILED, target.source, target.app, note=target.note)
        return ScreenShot(ScreenStatus.FAILED, _intended(source), "tela")


def _intended(source: object) -> ScreenSource:
    """A fonte de um print sem alvo (nenhum monitor): a da escolha."""
    kind = parse_source(source)[0]
    return ScreenSource.CURSOR if kind == CURSOR else ScreenSource.MONITOR if kind == "monitor" else ScreenSource.FOREGROUND


__all__ = ["AUTO", "COVERED_NOTE", "CURSOR", "MONITOR", "NO_MONITOR", "Target", "app_label", "capture", "covered_share", "is_empty",
           "meeting_apps", "parse_source", "pick_target", "prepare"]
