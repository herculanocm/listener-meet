"""F5.20 (C14, U-162): a aba "Sugestões" da reunião aberta, só leitura. Uma linha por pedido ("[mm:ss] · Falar · 3 opções ·
US$ 0,0004", ou cancelada e falhou), as opções embaixo com a avaliação ("Boa", "Ruim (longa demais)", "Usei", "Boa e usei")
e, embaixo de cada opção, os fatos citados: o duplo clique ou o Enter num fato toca daquele ponto. Sem sugestão, o estado
vazio (U-059). Relê o banco no `reload` (a janela da reunião chama quando um job dela termina), com o que estava aberto, o
que estava fechado e o item atual de volta (R-06 da revisão do L3). A primeira coluna estica e a fala inteira vai na dica da
opção (R-01 da revisão do L3: a última coluna esticada dividia a largura ao meio e cortava a fala); a fala quebra em
linhas (`WrapDelegate`: a árvore mede a linha sem a largura da coluna, e a quebra não acontecia); sem custo conhecido, o
pedido que não terminou bem não diz "sem preço cadastrado".

F5.21 (U-167, A-34): o pedido com a tela leva a miniatura no ícone e "com a tela" (ou "tela preta", "tela não
capturada") na linha; a opção que usou a tela leva "[tela]". O duplo clique ou o Enter no pedido abre a cópia guardada
(960 px), lida e decodificada numa `Task` (só o pixmap nasce na thread da interface); sem a cópia (`suggest.screen_keep`
desligada), a miniatura maior, numa janela comum que o Esc fecha. A janela da reunião não sai da captura, como as outras
abas: a tela aparece nela depois da reunião."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QHeaderView, QLabel, QStackedWidget, QStyledItemDelegate, QStyleOptionViewItem, QTreeView,
                               QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..model import ScreenStatus, SuggestMode, SuggestStatus
from .components import EmptyState
from .tasks import Task, run_task

if TYPE_CHECKING:
    from ..store import Store

EMPTY = "Nenhuma sugestão pedida nesta reunião: durante a gravação, o botão Sugerir da bandeja de legendas propõe o que falar."
MODES = {str(SuggestMode.SPEAK): "Falar", str(SuggestMode.ORIENT): "Me situar"}
STATUS = {str(SuggestStatus.CANCELLED): "cancelada", str(SuggestStatus.ERROR): "falhou"}
T_ROLE = Qt.ItemDataRole.UserRole
KEY_ROLE = Qt.ItemDataRole.UserRole + 1          # ("s", id do pedido), ("o", id da opção) ou ("f", id da opção, n do fato)
SHOT_ROLE = Qt.ItemDataRole.UserRole + 2         # F5.21: o id do pedido com a tela (o duplo clique abre a cópia)
SCREEN_STATE = {str(ScreenStatus.SENT): "com a tela", str(ScreenStatus.EMPTY): "tela preta", str(ScreenStatus.FAILED): "tela não capturada"}
ICON = QSize(96, 54)


def verdict(rating: Any, reason: Any, used: Any) -> str:
    """"Boa", "Ruim (longa demais)", "Usei", "Boa e usei"; vazio sem avaliação."""
    text = "Boa" if rating == 1 else (f"Ruim ({reason})" if reason else "Ruim") if rating == 0 else ""
    if used:
        text = f"{text} e usei" if text else "Usei"
    return text


class WrapDelegate(QStyledItemDelegate):
    """A primeira coluna medida com a largura dela, para a fala quebrar em linhas: a `QTreeView` mede a linha com um retângulo
    sem largura, e o estilo só quebra o texto num retângulo válido."""

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        tree = self.parent()
        if not isinstance(tree, QTreeView) or index.column() != 0:
            return super().sizeHint(option, index)
        depth, parent = 1, index.parent()
        while parent.isValid():
            depth, parent = depth + 1, parent.parent()
        opt = QStyleOptionViewItem(option)
        opt.rect = QRect(0, 0, max(80, tree.header().sectionSize(0) - depth * tree.indentation()), 10_000)
        opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        return super().sizeHint(opt, index)


class SuggestionsTab(QWidget):
    def __init__(self, store: Store, meeting_id: int, *, play: Callable[[int], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store, self.meeting_id, self.play = store, meeting_id, play
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Quando e o quê", "Avaliação"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setWordWrap(True)
        self.tree.setItemDelegate(WrapDelegate(self.tree))
        self.tree.header().sectionResized.connect(self._relayout)        # a largura nova: as alturas medidas de novo
        self.tree.itemActivated.connect(self._activated)
        self.tree.setIconSize(ICON)
        self.copy_task: Task | None = None
        self.viewer: QDialog | None = None
        self.empty = EmptyState("lightbulb", EMPTY)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack)
        self.reload()

    def reload(self) -> None:
        suggestions = self.store.suggestions.list_for_meeting(self.meeting_id)
        options = self.store.suggestions.options_for_meeting(self.meeting_id)
        screens = self.store.suggestions.screens_for_meeting(self.meeting_id)
        opened, closed, current = self._kept()
        self.tree.clear()
        for s in suggestions:
            opts = options.get(int(s["id"]), [])
            state = STATUS.get(str(s.get("status")), "")
            cost = (fmt_usd(s["cost_usd"]) + (" estimado" if s.get("cost_estimated") else "") if s.get("cost_usd") is not None
                    else "" if state else fmt_usd(None))
            count = f"{len(opts)} {'opção' if len(opts) == 1 else 'opções'}"
            screen = screens.get(int(s["id"]))
            shot = SCREEN_STATE.get(str(screen.get("status")), "") if screen else ""
            parts = (f"[{stamp(int(s['t_ms']))}]", MODES.get(str(s["mode"]), str(s["mode"])), count, shot, cost, state)
            top = QTreeWidgetItem([" · ".join(p for p in parts if p), ""])
            top.setData(0, KEY_ROLE, ("s", int(s["id"])))
            thumb = screen.get("thumb") if screen else None
            if thumb:
                top.setIcon(0, QIcon(QPixmap.fromImage(QImage.fromData(thumb))))
                top.setData(0, SHOT_ROLE, int(s["id"]))
            for o in opts:
                tag = " [tela]" if o.get("screen") else ""
                child = QTreeWidgetItem([f"{o['kind']}: {o['text']}{tag}", verdict(o.get("rating"), o.get("reason"), o.get("used"))])
                child.setToolTip(0, str(o["text"]))         # a fala inteira (a árvore corta a linha longa)
                child.setData(0, KEY_ROLE, ("o", int(o["id"])))
                try:
                    facts = json.loads(str(o.get("facts_json") or "[]"))
                except ValueError:
                    facts = []
                for n, fact in enumerate(facts):
                    t = int(fact.get("t_ms") or 0)
                    leaf = QTreeWidgetItem([f"[{stamp(t)}]", ""])
                    leaf.setData(0, T_ROLE, t)
                    leaf.setData(0, KEY_ROLE, ("f", int(o["id"]), n))
                    leaf.setToolTip(0, "duplo clique ou Enter: ouvir deste ponto")
                    child.addChild(leaf)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self._restore(opened, closed, current)
        self.stack.setCurrentWidget(self.tree if suggestions else self.empty)

    def _items(self) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []
        it = QTreeWidgetItemIterator(self.tree)
        while (item := it.value()) is not None:
            out.append(item)
            it += 1
        return out

    def _kept(self) -> tuple[set[Any], set[Any], Any]:
        """O que estava aberto, o que estava fechado e o item atual (as chaves), para o `reload` repor."""
        opened: set[Any] = set()
        closed: set[Any] = set()
        for item in self._items():
            if item.childCount():
                (opened if item.isExpanded() else closed).add(item.data(0, KEY_ROLE))
        cur = self.tree.currentItem()
        return opened, closed, cur.data(0, KEY_ROLE) if cur is not None else None

    def _restore(self, opened: set[Any], closed: set[Any], current: Any) -> None:
        """O item atual primeiro: pô-lo rola até ele e abre os pais, e o que estava fechado volta a fechar depois."""
        items = self._items()
        for item in items:
            if current is not None and item.data(0, KEY_ROLE) == current:
                self.tree.setCurrentItem(item)
        for item in items:
            key = item.data(0, KEY_ROLE)
            if key in opened or key in closed:
                item.setExpanded(key in opened)

    def _relayout(self, *_args: object) -> None:
        self.tree.doItemsLayout()

    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        t = item.data(0, T_ROLE)
        if self.play is not None and isinstance(t, int):
            self.play(t)
        sid = item.data(0, SHOT_ROLE)
        if isinstance(sid, int) and self.copy_task is None:
            store, thumb = self.store, item.icon(0)
            self.copy_task = run_task(lambda: _decoded(store.suggestions.screen_copy(sid)), store,
                                      lambda image: self._show_copy(image, thumb, item.text(0).split(" · ")[0]))

    def _show_copy(self, image: object, thumb: QIcon, when: str) -> None:
        """A cópia decodificada (ou a miniatura maior, sem a cópia), numa janela comum; o Esc ou o fechar a fecham."""
        self.copy_task = None
        if self.viewer is not None:
            self.viewer.close()
        area = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        pixmap = QPixmap.fromImage(image) if isinstance(image, QImage) and not image.isNull() else thumb.pixmap(ICON * 3)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Tela enviada {when}")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        label = QLabel()
        label.setPixmap(pixmap.scaled(int(area.width() * 0.7), int(area.height() * 0.7), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        QVBoxLayout(dialog).addWidget(label)
        self.viewer = dialog
        dialog.show()


def _decoded(data: bytes | None) -> QImage | None:
    """Na `Task`: a cópia guardada decodificada (o `QImage` roda fora da thread da interface)."""
    return QImage.fromData(data) if data else None


__all__ = ["EMPTY", "SuggestionsTab", "WrapDelegate", "verdict"]
