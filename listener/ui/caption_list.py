"""F5.17 (D-094): a lista das legendas comum à bandeja (`ui/caption_tray.py`) e à janela Ao vivo (`ui/live.py`).

A altura de cada fala fica no próprio item (`SizeHintRole`), medida uma vez por quem desenha (`size_of`), e o layout do Qt
fica no C++. Com o `sizeHint` em Python, cada fala nova pedia a altura de todas: [fato, 06/11, Windows] 73 ms por legenda
com 2.000 falas, contra ~2 ms assim. A largura que muda refaz as alturas uma vez, 100 ms depois de parar: o `QListView` não
refaz a quebra sozinho. As falas entram na ordem do tempo, `(t_start_ms, id)` por bisect (o filho grava por fim de janela,
e L e R chegam fora de ordem); muitas de uma vez entram com a pintura desligada (`bulk`). O "fim à vista" conta em pixels.

Presa ao fim (U-143): só a rolagem de quem lê a solta; enquanto presa, toda mudança da faixa da barra (fala nova, alturas
refeitas, a bandeja mais baixa) a leva de volta ao fim. A roda anda 20 px por passo: em pixels, o Qt usaria a altura da
primeira fala (uma tela inteira por clique na bandeja)."""
from __future__ import annotations

import bisect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import override

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QWidget

RELAYOUT_MS = 100                 # a largura parou de mudar: as alturas são refeitas uma vez
AT_END_PX = 24                    # até esta distância do fim, a lista está "no fim" (e rola sozinha)
SCROLL_STEP_PX = 20               # um passo da roda (o explícito tira a troca automática do Qt)

type SizeOf = Callable[[QListWidgetItem, int], QSize]


class CaptionList(QListWidget):
    """As falas na ordem do tempo, com a altura no item. `size_of(item, largura)` mede uma fala na largura do item."""

    def __init__(self, size_of: SizeOf, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.size_of = size_of
        self.keys: list[tuple[int, int]] = []         # (t_start_ms, id) de cada linha, na ordem da lista
        self.laid_width = 0                           # a largura em que as alturas foram medidas
        self.pinned = True                            # presa ao fim: só a rolagem de quem lê a solta
        self._scrolling = False                       # a rolagem é nossa (não solta nem prende)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(RELAYOUT_MS)
        self._relayout.timeout.connect(self.relayout)
        bar = self.verticalScrollBar()
        bar.setSingleStep(SCROLL_STEP_PX)
        bar.valueChanged.connect(self._value_changed)
        bar.rangeChanged.connect(self._range_changed)

    def _value_changed(self, _value: int) -> None:
        if not self._scrolling:
            self.pinned = self.at_end()

    def _range_changed(self, _low: int, _high: int) -> None:
        if self.pinned:
            self.scroll_to_end()

    def scroll_to_end(self) -> None:
        self._scrolling = True
        try:
            self.scrollToBottom()
        finally:
            self._scrolling = False
        self.pinned = True

    def item_width(self) -> int:
        return max(1, self.viewport().width() - 2 * self.spacing())

    def place(self, key: tuple[int, int], item: QListWidgetItem) -> int:
        """Põe a fala no lugar do tempo, já com a altura (medida antes de entrar: nada de `dataChanged`); devolve a linha.
        Antes da primeira largura de verdade, não mede: a primeira medida, ao aparecer, mede todas de uma vez."""
        pos = bisect.bisect_right(self.keys, key)
        self.keys.insert(pos, key)
        if self.laid_width:
            self.resize_item(item)
        self.insertItem(pos, item)
        return pos

    def resize_row(self, item: QListWidgetItem) -> None:
        """A altura de uma fala que mudou (o cabeçalho da bandeja), se a lista já tem largura."""
        if self.laid_width:
            self.resize_item(item)

    def resize_item(self, item: QListWidgetItem) -> None:
        item.setData(Qt.ItemDataRole.SizeHintRole, self.size_of(item, self.item_width()))

    def relayout(self, force: bool = False) -> None:
        """As alturas de todas as falas na largura de agora, com o modelo calado, e um layout só no fim; presa ao fim,
        continua no fim (o chat)."""
        width = self.item_width()
        if width == self.laid_width and not force:
            return
        self.laid_width = width
        model = self.model()
        blocked = model.blockSignals(True)
        try:
            for i in range(self.count()):
                self.resize_item(self.item(i))
        finally:
            model.blockSignals(blocked)
        self.doItemsLayout()
        if self.pinned:
            self.scroll_to_end()
        self.viewport().update()

    @contextmanager
    def bulk(self) -> Iterator[None]:
        """Muitas falas de uma vez (abrir, voltar de fechada): a pintura só no fim."""
        self.setUpdatesEnabled(False)
        try:
            yield
        finally:
            self.setUpdatesEnabled(True)

    def at_end(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - AT_END_PX

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """A primeira largura de verdade (ao aparecer) mede na hora; as seguintes, 100 ms depois de parar."""
        super().resizeEvent(event)
        if self.item_width() == self.laid_width:
            return
        if self.laid_width == 0:
            self.relayout()
        else:
            self._relayout.start()


__all__ = ["AT_END_PX", "RELAYOUT_MS", "SCROLL_STEP_PX", "CaptionList", "SizeOf"]
