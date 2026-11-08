"""F5.21 (A-02, A-08, A-09, A-17, A-23, A-37): a tela de um pedido de sugestão, do lado da interface, toda dentro da tarefa do
pedido (a thread da interface não espera nada daqui). A escolha do alvo, o print pela plataforma e o preparo: as janelas de
outros apps por cima pintadas de preto, a imagem de 1.600 px em JPEG (a enviada), a miniatura de 320 x 180 (sempre), a cópia
de 960 px (com `suggest.screen_keep`) e a imagem vazia. O `QImage` roda fora da thread da interface [fato: a sonda da revisão
de arquitetura]; o `QPixmap`, nunca. A escolha do alvo também roda na tarefa: a leitura dos processos leva ~30 ms (D-103).

- O alvo (`pick_target`), pela ordem: o monitor fixo (`monitor:<chave>`, se ainda existir; senão o automático, com a nota); a
  janela da reunião da fonte da gravação (`janela`); o monitor em uso, o da janela em primeiro plano (`primeiro_plano`). A
  escolha "o monitor em uso" (`cursor`) pula a janela da reunião.
- A vazia (`is_empty`): o cinza de 32 x 18 de uma cor só, quase preto e sem variação, ou com 80 % das células abaixo de 8 (o
  conteúdo protegido preto no meio da moldura do Teams, A-37).
Nada aqui grava nem loga a imagem; o log leva dimensões, ms e o estado."""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageWriter, QPainter

from ..llm.suggest_ctx import ScreenShot
from ..llm.types import ImagePart
from ..log import get_logger
from ..model import ScreenSource, ScreenStatus
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


def pick_target(platform: Platform, source: object, pid: int | None, exe: str, watched: Sequence[WatchedApp]) -> Target | None:
    kind, key = parse_source(source)
    note = ""
    if kind == "monitor":
        fixed = next((m for m in platform.monitors() if m.key == key), None)
        if fixed is not None:
            return Target(fixed.rect, ScreenSource.MONITOR, fixed.label.split(" · ")[0])
        note = NO_MONITOR
    if kind != CURSOR and (pid or exe):
        win = platform.meeting_window(pid, exe)
        if win is not None:
            return Target(win.rect, ScreenSource.WINDOW, app_label(win.app, watched), win.covered, note)
    mon = platform.active_monitor()
    if mon is None:
        return None
    return Target(mon.rect, ScreenSource.FOREGROUND, mon.label.split(" · ")[0], (), note)


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


def is_empty(img: QImage) -> bool:
    small = img.scaled(32, 18, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    gray = small.convertToFormat(QImage.Format.Format_Grayscale8)
    cells = np.frombuffer(gray.constBits(), dtype=np.uint8, count=gray.sizeInBytes()).reshape(18, gray.bytesPerLine())[:, :32]
    std = float(cells.std())
    return std < 0.5 or (float(cells.mean()) < 4 and std < 2) or float((cells < 8).mean()) >= 0.8


def prepare(frame: Frame | None, target: Target, keep: bool) -> ScreenShot:
    """O print pronto para o pedido: a enviada, a miniatura, a cópia e o estado."""
    t0 = time.perf_counter()
    if frame is None:
        return ScreenShot(ScreenStatus.FAILED, target.source, target.app)
    w, h = frame.width, frame.height                     # só as dimensões vão ao log (D-053)
    img = QImage(frame.data, w, h, frame.stride, QImage.Format.Format_RGB32).copy()   # dona dos bytes
    if target.covered:                                   # A-09: o que está por cima da reunião sai preto
        painter = QPainter(img)
        for r in target.covered:
            painter.fillRect(r.x - target.rect.x, r.y - target.rect.y, r.width, r.height, Qt.GlobalColor.black)
        painter.end()
    thumb = _jpeg(img.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation),
                  THUMB_QUALITY)
    if is_empty(img):
        log.info("tela vazia (%dx%d): a sugestão segue sem ela", w, h)
        return ScreenShot(ScreenStatus.EMPTY, target.source, target.app, None, thumb, None, len(target.covered))
    sent = _fit(img, SENT_SIDE)
    data = _jpeg(sent, SENT_QUALITY)
    copy = _jpeg(_fit(img, COPY_SIDE), COPY_QUALITY) if keep else None
    log.info("tela pronta: %dx%d para %dx%d, %d kB, %d por cima, em %.0f ms", w, h, sent.width(), sent.height(),
             len(data) // 1024, len(target.covered), (time.perf_counter() - t0) * 1000)
    return ScreenShot(ScreenStatus.SENT, target.source, target.app, ImagePart(data, "image/jpeg", sent.width(), sent.height(),
                                                                              f"tela: {target.app}"), thumb, copy, len(target.covered))


def capture(platform: Platform, source: object, pid: int | None, exe: str, watched: Sequence[WatchedApp], keep: bool) -> ScreenShot:
    """Na tarefa do pedido: o alvo, o print e o preparo; o que falha vira o estado `falhou`, e a sugestão segue sem a tela."""
    try:
        target = pick_target(platform, source, pid, exe, watched)
        if target is None:
            return ScreenShot(ScreenStatus.FAILED, ScreenSource.CURSOR, "tela")
        return prepare(platform.grab_screen(target.rect), target, keep)
    except Exception as e:                               # o print nunca derruba o pedido
        log.warning("tela não capturada: %s", type(e).__name__)
        return ScreenShot(ScreenStatus.FAILED, ScreenSource.CURSOR, "tela")


__all__ = ["AUTO", "CURSOR", "MONITOR", "NO_MONITOR", "Target", "app_label", "capture", "is_empty", "parse_source", "pick_target",
           "prepare"]
