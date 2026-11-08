"""F5.21 (U-170, A-34): a tela enviada, maior, quando o usuário clica na miniatura do chat da bandeja. As bandeiras da bandeja
(ferramenta, sem moldura, por cima e sem foco) e `WA_ShowWithoutActivating`: o clique não tira o foco do app de reunião. A
exclusão da captura de tela é pedida no `__init__`, pelo `winId()`, antes de a janela aparecer, e de novo no `WinIdChange`
(como a bandeja); se a exclusão falhar, a janela não mostra a imagem, só o aviso (pergunta 15). Fecha no clique; ocupa até
70 % da área livre da tela onde está a bandeja. A imagem chega decodificada e escalada por `fitted`, numa `Task` (R-07 da
revisão do L2): aqui só nasce o pixmap; maior que a área (quem não passou pela `Task`), ela é escalada aqui."""
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..log import get_logger
from .theme import note

if TYPE_CHECKING:
    from ..platform.base import Platform

log = get_logger("ui.suggest")

FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
         | Qt.WindowType.WindowDoesNotAcceptFocus)
SHARE = 0.7
HIDDEN = "A janela não saiu da captura de tela: a imagem fica escondida."


class ScreenView(QWidget):
    def __init__(self, image: QImage, caption: str, platform: Platform | None, near: QRect | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, FLAGS)
        for attr in (Qt.WidgetAttribute.WA_ShowWithoutActivating, Qt.WidgetAttribute.WA_DeleteOnClose):
            self.setAttribute(attr, True)
        self.setObjectName("screenview")
        self.setWindowTitle("Tela enviada")
        self.platform = platform
        self.excluded = self.exclude_from_capture()
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption = QLabel(f"{caption} · clique para fechar")
        note(self.caption)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.picture, 1)
        lay.addWidget(self.caption)
        area = _area(near)
        if self.excluded and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            box = self.fit_size(near)
            if pixmap.width() > box.width() or pixmap.height() > box.height():
                pixmap = pixmap.scaled(box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.picture.setPixmap(pixmap)
        else:
            self.picture.setText(HIDDEN)
        self.adjustSize()
        self.move(area.center() - self.rect().center())

    @staticmethod
    def fit_size(near: QRect | None) -> QSize:
        """O tamanho máximo da imagem: 70 % da área livre da tela onde está `near` (a bandeja)."""
        area = _area(near)
        return QSize(int(area.width() * SHARE), int(area.height() * SHARE))

    def exclude_from_capture(self) -> bool:
        if self.platform is None:
            return False
        try:
            return bool(self.platform.exclude_from_capture(int(self.winId())))
        except Exception as e:
            log.debug("a tela maior não saiu da captura: %s", e)
            return False

    @override
    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WinIdChange and hasattr(self, "picture"):
            QTimer.singleShot(0, self, self._exclude_again)     # a janela nativa foi refeita: a exclusão também
        return super().event(event)

    def _exclude_again(self) -> None:
        if not self.exclude_from_capture():
            with suppress(RuntimeError):
                self.picture.clear()
                self.picture.setText(HIDDEN)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        self.close()


def _area(near: QRect | None) -> QRect:
    screen = QGuiApplication.screenAt(near.center()) if near is not None else None
    return (screen or QGuiApplication.primaryScreen()).availableGeometry()


def fitted(data: bytes, width: int, height: int) -> QImage:
    """Na `Task` (R-07): a imagem guardada decodificada e, maior que `width` x `height`, escalada (o `QImage` roda fora da
    thread da interface; o `QPixmap`, não)."""
    image = QImage.fromData(data)
    if image.isNull() or (image.width() <= width and image.height() <= height):
        return image
    return image.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


__all__ = ["HIDDEN", "ScreenView", "fitted"]
