"""F5.17 (U-141, U-143, U-144): o chat da bandeja de legendas, separado da janela (`ui/caption_tray.py`) para os dois
ficarem abaixo de 400 linhas.

- As falas: `CaptionList` com o `ChatDelegate`, a altura no item (D-094), lidas do banco por `after_id`, na ordem do tempo.
  A fala seguida do mesmo falante (lado, canal e rótulo) até 60 s depois do início da anterior não repete o cabeçalho;
  "Participante" e sem nome ficam sem nome; as cores são as da gravação, as mesmas da janela Ao vivo.
- Vazio: "Aguardando a primeira legenda…".
- Rolagem (U-143): com o fim à vista, rola sozinha; quem subiu fica onde está, com "↓ N novas" sobre o pé do chat.
- Clique direito (U-144): "Criar marcador neste instante" (pelo `on_mark` do controlador, uma vez por legenda; a falha vai
  ao `on_notice`) e "Copiar". O menu pede a frente à plataforma: numa janela que não ativa, ele não fecharia com o clique
  fora nem com o Esc. E ignora o Alt que o `bring_to_front` injeta (o menu do Windows fecharia nele).
- F5.21 (U-170, A-32, A-33): a tela enviada numa sugestão entra como um anexo seu, no tempo do pedido (`add_screen`): o item
  tem o `SCREEN_ROLE`, o `UserRole` vazio (o menu, as marcas e a rolagem até a legenda o pulam) e o grupo vazio (a fala
  seguinte volta a ter cabeçalho); o clique pede a tela maior (`screen_clicked`). Só a bandeja: a janela Ao vivo não sai da
  captura e nunca recebe a tela."""
from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtGui import QGuiApplication, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QListWidgetItem, QMenu, QPushButton, QStackedLayout, QWidget)

from ..llm.prompts.common import stamp
from ..log import get_logger
from .caption_list import CaptionList
from .chat_delegate import HEAD_ROLE, MINE_ROLE, SCREEN_ROLE, SENT_ROLE, THUMB_ROLE, ChatDelegate
from .live_text import YOU, MarkCaption, caption_key, caption_line, caption_note, speaker_parts, you_label
from .speech_delegate import FLAG_ROLE, STAMP_ROLE, TEXT_ROLE, speech_roles
from .theme import PRIMARY, note, set_kind

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.captions")
GROUP_ROLE = Qt.ItemDataRole.UserRole + 0x5313      # str: lado, canal e rótulo (a sequência do mesmo falante)
GROUP_MS = 60_000                 # a fala seguida do mesmo falante até 60 s depois não repete o cabeçalho
PARTICIPANT = "Participante"      # o rótulo do filho abaixo do limiar (`live/names.py`): sem nome na bandeja
WAITING = "Aguardando a primeira legenda…"
NEW_GAP = 8                       # o "↓ N novas" acima do pé do chat
BULK = 20                         # mais falas que isto de uma vez entram com a pintura desligada


class CaptionChat(QWidget):
    """O chat de uma reunião. `marked` e `speakers` são os conjuntos da gravação, do controlador."""
    screen_clicked = Signal(int)          # F5.21: a miniatura de uma tela (a chave: o tempo do pedido)

    def __init__(self, store: Store, meeting_id: int, *, platform: Platform | None = None, on_mark: MarkCaption | None = None,
                 on_notice: Callable[[str], Any] | None = None, marked: set[tuple[Any, ...]] | None = None,
                 speakers: dict[str, int] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store, self.meeting_id, self.platform = store, meeting_id, platform
        self.on_mark, self.on_notice = on_mark, on_notice
        self.marked = marked if marked is not None else set()
        self.speakers = speakers if speakers is not None else {}
        me, meeting = store.get_me(), store.get_meeting(meeting_id) or {}
        self.me_id = int(me["id"]) if me else None        # U-108: "Você" pela pessoa marcada como você
        self.in_room = bool(meeting.get("is_in_room"))
        self.finished = False
        self.last_id = self.unread = 0
        self.menu: QMenu | None = None
        self.delegate = ChatDelegate(self)
        self.list = CaptionList(self._item_size)
        self.list.setItemDelegate(self.delegate)
        self.list.setFont(self.delegate.body)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.viewport().setAutoFillBackground(False)   # o fundo é o do cartão
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemClicked.connect(self._clicked)
        self.list.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.empty = QLabel(WAITING)
        note(self.empty)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.pages = QStackedLayout(self)
        self.pages.setContentsMargins(0, 0, 0, 0)
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.list)
        self.new_btn = QPushButton("", self)              # sobre o pé do chat, fora do layout
        self.new_btn.setMinimumHeight(32)                 # o alvo de clique do Aceite 3 da F5.4
        set_kind(self.new_btn, PRIMARY)                   # preenchido no destaque: legível sobre os balões
        self.new_btn.setDefault(False)                    # sem o Enter da janela (a bandeja não pega o foco)
        self.new_btn.clicked.connect(self.scroll_to_end)
        self.new_btn.hide()

    def label_of(self, row: Mapping[str, Any]) -> str | None:
        return you_label(row, self.me_id, self.in_room)

    def _item_size(self, item: QListWidgetItem, width: int) -> QSize:
        if item.data(SCREEN_ROLE) is not None:
            return self.delegate.screen_size(width)
        return self.delegate.item_size(str(item.data(TEXT_ROLE) or ""), bool(item.data(HEAD_ROLE)), width)

    # ---------------------------------------------------------------- F5.21: a tela enviada
    def add_screen(self, t_ms: int, thumb: QImage | None, text: str, sent: bool) -> None:
        """A miniatura da tela no tempo do pedido, do lado de quem pede; antes da primeira legenda, a lista aparece (A-33)."""
        follow = self.list.pinned
        self.pages.setCurrentWidget(self.list)
        item = QListWidgetItem()
        item.setData(SCREEN_ROLE, int(t_ms))
        item.setData(THUMB_ROLE, thumb)
        item.setData(SENT_ROLE, sent)
        item.setData(STAMP_ROLE, int(t_ms))
        item.setData(MINE_ROLE, True)
        item.setData(HEAD_ROLE, False)
        item.setData(GROUP_ROLE, "")
        item.setText(text)                                   # a linha de baixo
        what, _sep, label = text.partition(" · ")            # R-08: o leitor de tela diz "Tela enviada [mm:ss] · rótulo"
        item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{what} [{stamp(int(t_ms))}]" + (f" · {label}" if label else ""))
        item.setToolTip("Clique para ver a tela maior")    # neutra: a dica fica fora da exclusão da captura (D-095)
        pos = self.list.place((int(t_ms), 0), item)
        self._restyle(pos + 1)
        if follow:
            self.list.scroll_to_end()

    def scroll_to_screen(self, t_ms: int) -> bool:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(SCREEN_ROLE) == t_ms:
                self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def _clicked(self, item: QListWidgetItem) -> None:
        key = item.data(SCREEN_ROLE)
        if key is not None:
            self.screen_clicked.emit(int(key))

    # ---------------------------------------------------------------- falas
    def load_new(self) -> dict[str, Any] | None:
        """As legendas novas do banco (o filho grava antes de emitir); devolve a mais nova, para a linha da recolhida."""
        rows = [dict(r) for r in self.store.captions.list_for_meeting(self.meeting_id, after_id=self.last_id)]
        if not rows:
            return None
        follow = self.list.pinned
        self.pages.setCurrentWidget(self.list)           # antes das falas: a lista ganha a largura de verdade
        if len(rows) > BULK:
            with self.list.bulk():
                for row in rows:
                    self._add(row, restyle=False)
                self._restyle_all()
        else:
            for row in rows:
                self._add(row)
        newest = max(rows, key=_row_id)
        self.last_id = max(self.last_id, _row_id(newest))
        if follow:
            self.list.scroll_to_end()
        else:
            self.unread += len(rows)
            self._show_new()
        return newest

    def _add(self, row: dict[str, Any], restyle: bool = True) -> None:
        pos = self.list.place((int(row.get("t_start_ms") or 0), _row_id(row)), self._item(row))
        if restyle:
            self._restyle(pos)
            self._restyle(pos + 1)

    def _item(self, row: dict[str, Any]) -> QListWidgetItem:
        label = self.label_of(row)
        mine = label == YOU
        name, job = ("", "") if mine or label in (None, PARTICIPANT) else speaker_parts(label)
        color = self.speakers.setdefault(name, len(self.speakers)) if name else None
        t0 = int(row.get("t_start_ms") or 0)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, row)
        speech_roles(item, t0, name, job, " ".join(str(row.get("text") or "").split()), color)
        item.setData(MINE_ROLE, mine)
        item.setData(HEAD_ROLE, True)
        item.setData(GROUP_ROLE, f"{int(mine)}|{row.get('channel') or ''}|{label or ''}")
        item.setData(FLAG_ROLE, caption_key(row) in self.marked)
        item.setText(caption_line({**row, "label": label}))          # o leitor de tela e os testes
        item.setToolTip(f"[{stamp(t0)}] · clique direito: criar marcador ou copiar")
        return item

    @staticmethod
    def continues(prev: QListWidgetItem | None, item: QListWidgetItem) -> bool:
        """A fala segue a anterior: o mesmo falante (lado, canal e rótulo), até 60 s depois do início dela."""
        if prev is None or prev.data(GROUP_ROLE) != item.data(GROUP_ROLE):
            return False
        return 0 <= int(item.data(STAMP_ROLE) or 0) - int(prev.data(STAMP_ROLE) or 0) <= GROUP_MS

    def _restyle(self, i: int) -> None:
        item = self.list.item(i)
        if item is None or item.data(SCREEN_ROLE) is not None:      # F5.21: a tela não tem cabeçalho
            return
        head = not self.continues(self.list.item(i - 1) if i > 0 else None, item)
        if bool(item.data(HEAD_ROLE)) != head:
            item.setData(HEAD_ROLE, head)
            self.list.resize_row(item)

    def _restyle_all(self) -> None:
        model = self.list.model()
        blocked = model.blockSignals(True)
        try:
            prev = None
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item.data(SCREEN_ROLE) is None:
                    item.setData(HEAD_ROLE, not self.continues(prev, item))
                prev = item
        finally:
            model.blockSignals(blocked)
        if self.list.laid_width:                         # sem largura ainda, a primeira medida mede todas
            self.list.relayout(force=True)

    def refresh_mark(self, key: tuple[Any, ...]) -> None:
        """A bandeira da legenda `key`, pelo conjunto da gravação (marcada aqui ou na janela Ao vivo)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(row, dict) and caption_key(row) == key:
                item.setData(FLAG_ROLE, key in self.marked)

    # ---------------------------------------------------------------- rolagem
    def _scrolled(self, _value: int) -> None:
        if self.unread and self.list.pinned:            # quem lê voltou ao fim
            self.unread = 0
            self.new_btn.hide()

    def scroll_to_caption(self, caption_id: int) -> bool:
        """F5.20 (U-154): o fato citado numa sugestão rola o chat até a legenda dele (o fim deixa de ficar preso)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(row, dict) and _row_id(row) == caption_id:
                self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def scroll_to_end(self) -> None:
        self.list.scroll_to_end()
        self.unread = 0
        self.new_btn.hide()

    def _show_new(self) -> None:
        self.new_btn.setText(f"↓ {self.unread} {'nova' if self.unread == 1 else 'novas'}")
        self.new_btn.adjustSize()
        self._place_new()
        self.new_btn.show()
        self.new_btn.raise_()

    def _place_new(self) -> None:
        b = self.new_btn
        b.move((self.width() - b.width()) // 2, self.height() - b.height() - NEW_GAP)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.new_btn.isVisible():
            self._place_new()

    # ---------------------------------------------------------------- menu
    def _context_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(row, dict):
            return
        menu = AltProofMenu(self)
        mark = menu.addAction("Criar marcador neste instante")
        mark.setEnabled(not self.finished and self.on_mark is not None and caption_key(row) not in self.marked)
        mark.triggered.connect(functools.partial(self.mark, row))
        menu.addAction("Copiar").triggered.connect(functools.partial(self.copy, row))
        menu.aboutToHide.connect(menu.deleteLater)
        self.menu = menu
        menu.popup(self.list.viewport().mapToGlobal(pos))
        if self.platform is not None:
            with suppress(Exception):
                self.platform.bring_to_front(int(menu.winId()))

    def mark(self, row: Mapping[str, Any], *_args: object) -> None:
        """U-144: o marcador no instante da legenda (U-022), pelo controlador; a falha vira um aviso do app."""
        if self.finished or self.on_mark is None:
            return
        ok, message = self.on_mark(row)
        if ok:
            self.refresh_mark(caption_key(row))
        elif self.on_notice is not None:
            self.on_notice(message)

    @staticmethod
    def copy(row: Mapping[str, Any], *_args: object) -> None:
        QGuiApplication.clipboard().setText(caption_note(row))      # o rótulo gravado, como a nota do marcador (U-108)


class AltProofMenu(QMenu):
    """O menu que o Alt não fecha: o `bring_to_front` do Windows injeta um Alt para a janela vir à frente, e o `QMenu` do
    estilo do Windows fecharia nele (revisão do diff da F5.17) [chute: conferir no uso, Aceite 4]."""

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Alt:
            event.accept()
            return
        super().keyPressEvent(event)

    @override
    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Alt:
            event.accept()
            return
        super().keyReleaseEvent(event)


def _row_id(row: Mapping[str, Any]) -> int:
    return int(row.get("id") or 0)


__all__ = ["GROUP_MS", "PARTICIPANT", "WAITING", "AltProofMenu", "CaptionChat"]
