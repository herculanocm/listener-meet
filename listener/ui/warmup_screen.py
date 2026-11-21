"""F5.27 (U-180, U-181): a gravação da tela no "Antes de gravar", fora do `ui/warmup.py` (548 linhas), que só põe a caixa
no rodapé, ao lado do Gravar, e a seção na largura toda, acima dele (A-17 da F5.4: a caixa não custa altura, e a seção, ~60 px
só quando ligada), e lê `monitor_key()` para `fields["screen_monitor"]` (o `Monitor.key` escolhido, ou None sem a tela).

- A caixa "Gravar a tela" nasce desligada a cada abertura (pergunta 1). Fora do Windows (a plataforma sem monitores), apagada,
  com o motivo na dica.
- Ligada: os monitores lado a lado, com o rótulo da plataforma ("Monitor 2 · 3840 x 2160 · principal", o número numa linha e o
  resto na outra, ao lado da miniatura; inteiro na dica) e a prévia, e a linha da U-181 ao lado. Um clique escolhe o monitor.
  O último escolhido (`screen_rec.monitor`, que o controller grava ao começar) vem marcado se ainda existir; senão nenhum, e o
  "Gravar" pede a escolha (`confirm`).
- A prévia: a cada 1 s, só com a caixa ligada e o diálogo aberto, um print de cada monitor numa tarefa fora da thread da
  interface (como o da F5.21), reduzido ali a uma miniatura em `QImage` (o `QPixmap` nasce só na volta); uma tarefa por vez,
  e a resposta velha (a caixa desligada, o diálogo fechado) é descartada. Nada da imagem é guardado nem vai ao log.
- A-34 da F5.21: se o "Antes de gravar" não saiu da captura de tela (`set_excluded(False)`, do `ui/warmup_source.py`), a
  prévia nem roda: só os rótulos.
O log leva contagens e o número do monitor, nunca a chave do sistema nem a imagem."""
from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ..log import get_logger
from .common import fit_to_screen
from .tasks import Task, run_task
from .theme import Tone, note, tone

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..platform.screen_types import Frame, Monitor, ScreenRect
    from ..store import Store

log = get_logger("ui.warmup")

TOGGLE_TEXT = "Gravar a tela"
TOGGLE_TIP = "Grava o monitor escolhido junto com o áudio, nesta gravação (1 quadro por segundo, nas Configurações)."
ONLY_WINDOWS = "A gravação da tela, por enquanto, só no Windows."
PRIVACY = ("Tudo o que aparecer neste monitor fica gravado, inclusive notificações. A bandeja, os avisos e as sugestões do "
           "listener-meet não aparecem.")
PICK_MONITOR = "Escolha o monitor a gravar, ou desligue \"Gravar a tela\"."
MONITOR_KEY = "screen_rec.monitor"
PREVIEW_MS = 1000
THUMB_MAX_W, THUMB_MIN_W, ROW_W = 96, 64, 300      # A-17 da F5.4: a linha da tela, com o texto ao lado, em ~60 px de altura


def thumb_width(n: int) -> int:
    """A largura da miniatura com `n` monitores lado a lado (16:9): 96 px com um ou dois, menor com mais."""
    return max(THUMB_MIN_W, min(THUMB_MAX_W, ROW_W // max(1, n) - 8))


def tile_text(label: str) -> str:
    """"Monitor 2 · 3840 x 2160 · principal" em duas linhas: o número e o resto."""
    head, sep, rest = label.partition(" · ")
    return f"{head}\n{rest}" if sep else head


def to_thumb(frame: Frame, width: int, height: int) -> QImage:
    """O print (BGRA de cima para baixo) reduzido para caber em `width` x `height`; a imagem devolvida é própria (o `scaled`
    copia), e o buffer do print pode morrer depois."""
    buf = np.frombuffer(frame.data, dtype=np.uint8)
    if not buf.flags.writeable:
        buf = buf.copy()                                 # o `bytes` dos testes; o do BitBlt já é gravável
    lines = buf.reshape(frame.height, frame.stride)
    img = QImage(lines.data, frame.width, frame.height, frame.stride, QImage.Format.Format_RGB32)
    return img.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def thumbnails(platform: Platform, rects: Sequence[tuple[str, ScreenRect]], width: int, height: int) -> dict[str, QImage]:
    """Na tarefa: a miniatura de cada monitor; o print que falha deixa o monitor sem prévia nesta volta."""
    out: dict[str, QImage] = {}
    for key, rect in rects:
        try:
            frame = platform.grab_screen(rect)
        except OSError as e:
            log.debug("prévia da tela sem um monitor nesta volta (%s)", type(e).__name__)
            continue
        if frame is not None:
            out[key] = to_thumb(frame, width, height)
    return out


class ScreenSection(QWidget):
    """A caixa "Gravar a tela", os monitores com a prévia e a linha da U-181."""

    def __init__(self, platform: Platform, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.platform, self.store = platform, store
        self.excluded: bool | None = None                # o "Antes de gravar" saiu da captura? (A-34; None: ainda não apareceu)
        self.monitors: tuple[Monitor, ...] = self._read_monitors()
        self.chosen: str | None = None
        self.tiles: dict[str, QToolButton] = {}
        self.previews = 0                                # instrumentação dos testes: voltas da prévia pedidas
        self._task: Task | None = None
        self._seq = 0
        self._dpr = 1.0
        self.check = QCheckBox(TOGGLE_TEXT)
        self.check.setToolTip(TOGGLE_TIP if self.monitors else ONLY_WINDOWS)
        self.check.setEnabled(bool(self.monitors))
        self.check.toggled.connect(self._on_toggled)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.privacy = QLabel(PRIVACY)                   # U-181
        self.privacy.setWordWrap(True)
        note(self.privacy)
        self.pick_note = QLabel(PICK_MONITOR)
        self.pick_note.setWordWrap(True)
        note(self.pick_note, Tone.WARNING)
        self.pick_note.hide()
        notes = QVBoxLayout()
        notes.setSpacing(4)
        notes.addStretch(1)
        notes.addWidget(self.privacy)
        notes.addWidget(self.pick_note)
        notes.addStretch(1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(self.row)
        lay.addLayout(notes, 1)
        self.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(PREVIEW_MS)
        self.timer.timeout.connect(self.tick)
        if isinstance(parent, QDialog):
            parent.finished.connect(self.stop)           # aceitar, cancelar ou fechar: a prévia para

    def _read_monitors(self) -> tuple[Monitor, ...]:
        try:
            return tuple(self.platform.monitors())
        except OSError as e:
            log.warning("\"Antes de gravar\": os monitores não foram lidos (%s)", type(e).__name__)
            return ()

    # ---------------------------------------------------------------- a caixa e os monitores
    @property
    def on(self) -> bool:
        return self.check.isChecked()

    def _on_toggled(self, on: bool) -> None:
        self.pick_note.hide()
        if not on:
            self.stop()
            self.hide()
            log.info("\"Antes de gravar\": gravar a tela desligada")
            return
        self.monitors = self._read_monitors() or self.monitors
        self._build_tiles(self.chosen or str(self.store.get_setting(MONITOR_KEY) or ""))
        self.show()
        QTimer.singleShot(0, self._fit)                  # A-17: a janela que cresceu segue dentro da área livre
        log.info("\"Antes de gravar\": gravar a tela ligada (%d monitores, prévia %s, último %s)", len(self.monitors),
                 "sim" if self.excluded else "não", "marcado" if self.chosen else "nenhum")
        self._start_preview()

    def _fit(self) -> None:
        window = self.window()
        if window.isVisible():
            fit_to_screen(window)

    def _build_tiles(self, wanted: str | None) -> None:
        """Um botão por monitor; `wanted` vem marcado se ainda existir."""
        for tile in self.tiles.values():
            self.group.removeButton(tile)
            self.row.removeWidget(tile)
            tile.deleteLater()
        self.tiles.clear()
        self.chosen = wanted if wanted in {m.key for m in self.monitors} else None
        width = thumb_width(len(self.monitors))
        size = QSize(width, width * 9 // 16)
        blank = QPixmap(size)
        blank.fill(Qt.GlobalColor.transparent)
        for m in self.monitors:
            tile = QToolButton()
            tile.setText(tile_text(m.label))
            tile.setToolTip(m.label)
            tile.setAccessibleName(m.label)
            tile.setCheckable(True)
            tile.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            tile.setIconSize(size)
            tile.setIcon(QIcon(blank))                   # o lugar da miniatura, até a primeira volta da prévia
            tile.setChecked(m.key == self.chosen)
            tile.clicked.connect(functools.partial(self._picked, m.key))
            self.group.addButton(tile)
            self.row.addWidget(tile)
            self.tiles[m.key] = tile
        self._set_preview_look(bool(self.excluded))
        self._mark()

    def _mark(self) -> None:
        """O escolhido também no tom de destaque (no tema escuro, o botão marcado mal se distingue) e para o leitor de tela."""
        for key, tile in self.tiles.items():
            tone(tile, Tone.ACCENT if key == self.chosen else None)
            tile.setAccessibleDescription("escolhido" if key == self.chosen else "")

    def _set_preview_look(self, preview: bool) -> None:
        style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon if preview else Qt.ToolButtonStyle.ToolButtonTextOnly
        for tile in self.tiles.values():
            tile.setToolButtonStyle(style)

    def _picked(self, key: str, *_args: Any) -> None:
        self.chosen = key
        self.pick_note.hide()
        self._mark()
        label = next((m.label for m in self.monitors if m.key == key), "")
        log.info("\"Antes de gravar\": tela no %s", label.split(" · ", maxsplit=1)[0] or "monitor")

    def set_excluded(self, ok: bool) -> None:
        """A-34: com o "Antes de gravar" fora da captura, a prévia; senão, só os rótulos."""
        self.excluded = ok
        if self.on:
            self._set_preview_look(ok)
            if ok:
                self._start_preview()
            else:
                self.stop()

    # ---------------------------------------------------------------- a prévia
    def _start_preview(self) -> None:
        if not (self.on and self.excluded and self.monitors):
            return
        self.tick()
        self.timer.start()

    def tick(self) -> None:
        """Uma volta da prévia: os prints e as miniaturas numa tarefa; uma por vez."""
        if not (self.on and self.excluded) or self._task is not None:
            return
        self._dpr = max(1.0, self.devicePixelRatioF())
        width = thumb_width(len(self.monitors))
        w, h = round(width * self._dpr), round(width * 9 // 16 * self._dpr)
        rects = [(m.key, m.rect) for m in self.monitors]
        self.previews += 1
        self._task = run_task(functools.partial(thumbnails, self.platform, rects, w, h), None,
                              functools.partial(self._on_thumbs, self._seq))

    def _on_thumbs(self, seq: int, result: object) -> None:
        self._task = None
        if seq != self._seq or not (self.on and self.excluded):
            return                                       # velha: a caixa desligou ou o diálogo fechou
        if not isinstance(result, dict):
            log.info("prévia da tela indisponível nesta volta (%s)", type(result).__name__)
            return
        for key, image in result.items():
            tile = self.tiles.get(key)
            if tile is None or not isinstance(image, QImage) or image.isNull():
                continue
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self._dpr)
            tile.setIcon(QIcon(pixmap))

    def stop(self, *_args: Any) -> None:
        """Para a prévia e descarta a volta em curso (roda mais de uma vez: aceitar e depois fechar)."""
        self.timer.stop()
        self._seq += 1

    @property
    def running(self) -> bool:
        return self.timer.isActive()

    # ---------------------------------------------------------------- o resultado
    def confirm(self) -> bool:
        """No "Gravar": com a caixa ligada, um monitor escolhido que ainda existe; senão a seção pede a escolha e o diálogo
        fica aberto."""
        if not self.on:
            return True
        fresh = self._read_monitors()
        if self.chosen is not None and self.chosen in {m.key for m in fresh}:
            return True
        if self.chosen is not None:                      # o escolhido saiu: os botões dos monitores de agora, nenhum marcado
            self.monitors = fresh
            self._build_tiles(None)
        self.pick_note.show()
        first = next(iter(self.tiles.values()), None)
        if first is not None:
            first.setFocus(Qt.FocusReason.OtherFocusReason)
        log.info("\"Antes de gravar\": gravar a tela sem monitor escolhido; a seção pede a escolha")
        return False

    def monitor_key(self) -> str | None:
        """O `Monitor.key` escolhido, ou None com a caixa desligada (o `fields["screen_monitor"]` do contrato)."""
        return self.chosen if self.on else None


__all__ = ["ONLY_WINDOWS", "PICK_MONITOR", "PRIVACY", "TOGGLE_TEXT", "ScreenSection", "thumb_width", "thumbnails", "tile_text",
           "to_thumb"]
