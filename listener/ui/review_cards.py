"""A revisão de falantes no sistema de design (F5.4 L4, U-086, U-035), fora do `ui/review.py`, que passava de 400 linhas:

- `VoiceCardDelegate`: cada voz num cartão, com o título ("Voz 2", U-035), o estado num selo (a confirmar, sem nome,
  confirmada, automática, ignorada, mesclada; o texto e o tom, nunca só a cor, U-054), os minutos de fala à direita e o
  resumo (a sugestão pela voz e a pelo contexto) embaixo. O `DisplayRole` do item segue com a linha inteira, para o leitor
  de tela e os testes. F5.11 (U-099, A-20): no cartão selecionado, o "Ouvir 5 s" pintado (nada de widget por linha); o
  clique por `editorEvent` emite `play_requested`, e o Espaço segue tocando.
- `KeyButton`: o botão da ação com o atalho num selo de tecla à direita ("É Ana [Enter]"); a tecla também vai na dica e na
  descrição acessível. `set_label` troca o texto e a tecla com o estado da voz (A-19); sem espaço, o texto é cortado no
  meio, o que corta o nome e deixa o começo e o fim. Sem tecla e com menu, a seta no lugar do selo (o "Mais" da F5.11).
- `stage_primary`: o primário segue a etapa (com voz pendente, o da voz; sem nenhuma, "Concluir"). Nenhum vira o padrão do
  Enter: na lista, o Enter faz o que o principal da voz diz (U-098), e num campo de texto não fecha a revisão; num botão
  com foco, o Enter aciona esse botão (`ReviewDialog.keyPressEvent`, R-01 da revisão da F5.4)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QListView, QListWidgetItem, QPushButton, QStyle,
                               QStyledItemDelegate, QStyleOptionButton, QStyleOptionFocusRect, QStyleOptionViewItem, QStylePainter,
                               QToolTip, QWidget)

from ..model import ClusterStatus
from ..store.segments import ClusterRow, resolve_person
from . import icons
from .style import rounded
from .theme import KIND_PROP, PRIMARY, Tone, font_for, qcolor, set_kind, surface_tokens

if TYPE_CHECKING:
    from ..pipeline.review import ReviewItem
    from ..pipeline.voices import VoiceNames
    from .tokens import Tokens

TITLE_ROLE = Qt.ItemDataRole.UserRole + 0x5201      # "Voz 2"
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 0x5202     # o resumo da voz (pyannote, LLM, similaridade)
META_ROLE = Qt.ItemDataRole.UserRole + 0x5203       # os minutos de fala
BADGE_ROLE = Qt.ItemDataRole.UserRole + 0x5204      # o texto do selo do estado
BADGE_TONE_ROLE = Qt.ItemDataRole.UserRole + 0x5205  # o tom do selo (`Tone.value`)

STATUS_BADGES: dict[str, tuple[str, Tone]] = {ClusterStatus.AMBIGUOUS: ("a confirmar", Tone.WARNING),
                                              ClusterStatus.UNNAMED: ("sem nome", Tone.WARNING),
                                              ClusterStatus.CONFIRMED: ("confirmada", Tone.SUCCESS),
                                              ClusterStatus.AUTO: ("automática", Tone.INFO),
                                              ClusterStatus.IGNORED: ("ignorada", Tone.NEUTRAL)}
MERGED_BADGE = ("mesclada", Tone.NEUTRAL)
CARD_PAD, CARD_GAP, CARD_MARGIN = 12, 4, 3            # px lógicos: dentro do cartão, entre o título e o resumo, em volta dele
PILL_PAD_X, PILL_PAD_Y = 8, 2
CAP_PAD_X, CAP_GAP, BUTTON_PAD = 6, 12, 12            # o selo de tecla: por dentro, até o texto e a borda do botão
META_GAP = 12                                          # entre os minutos e o que vem antes deles no cabeçalho do cartão
ARROW = 16                                             # a seta do botão com menu, no lugar do selo de tecla
PLAY_TEXT, PLAY_TIP = "Ouvir 5 s", "Ouvir 5 s desta voz (tecla Espaço)"   # U-099
PLAY_ICON, PLAY_PAD_X, PLAY_GAP = 14, 8, 4             # o "Ouvir 5 s": o ícone, por dentro e entre o ícone e o texto
DETAIL_FLAGS = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


def badge_of(item: ReviewItem) -> tuple[str, Tone]:
    if item.merged_into is not None:
        return MERGED_BADGE
    return STATUS_BADGES.get(str(item.status), ("sem nome", Tone.WARNING))


def card_detail(summary: str, badge: str) -> str:
    """O resumo no cartão sem repetir o selo ("confirmada: Ana" vira "Ana"; "sem nome" sozinho, nada)."""
    for sep in (": ", "; ", " · "):
        if summary.startswith(badge + sep):
            return summary[len(badge) + len(sep):]
    return "" if summary == badge else summary


def taken_voices(items: Sequence[ReviewItem]) -> dict[int, int]:
    """U-137 (F5.15, pergunta 2): a voz pendente cuja sugestão é uma pessoa já decidida (automática ou confirmada, pela
    `resolve_person`) noutra voz da reunião -> essa outra voz. Calculado aqui, com os itens da tela, e não no `review_items`,
    que conta pendentes noutros lugares (A-07)."""
    rows = {it.cluster_id: ClusterRow(id=it.cluster_id, person_id=it.person_id, status=str(it.status), merged_into=it.merged_into)
            for it in items}
    decided: dict[int, int] = {}
    for it in items:
        if it.merged_into is None and (pid := resolve_person(rows[it.cluster_id], rows)) is not None:
            decided.setdefault(pid, it.cluster_id)
    return {it.cluster_id: decided[it.person_id] for it in items if it.pending and it.merged_into is None
            and it.person_id is not None and decided.get(it.person_id, it.cluster_id) != it.cluster_id}


def card_item(item: ReviewItem, names: VoiceNames, taken: int | None = None) -> QListWidgetItem:
    """O item da lista de uma voz: os papéis do cartão e a linha inteira no `DisplayRole`. `taken` (U-137): a voz onde a
    pessoa sugerida já está decidida, dita depois do nome ("Ana (já na Voz 2)")."""
    title = names.title(item.cluster_id, item.title)
    badge, tone = badge_of(item)
    if item.merged_into is not None:
        target = names.title(item.merged_into, f"voz #{item.merged_into}")
        detail, shown = f"mesclada com a {target}", f"é a mesma que a {target}"
    else:
        detail = item.summary()
        if taken is not None and item.person_name:
            detail = detail.replace(item.person_name, f"{item.person_name} (já na {names.title(taken, f'voz #{taken}')})", 1)
        shown = card_detail(detail, badge)
    out = QListWidgetItem(f"{title} · {item.minutes_text} · {detail}")
    out.setData(Qt.ItemDataRole.UserRole, item.cluster_id)
    for role, value in ((TITLE_ROLE, title), (DETAIL_ROLE, shown), (META_ROLE, item.minutes_text), (BADGE_ROLE, badge),
                        (BADGE_TONE_ROLE, tone.value)):
        out.setData(role, value)
    return out


class VoiceCardDelegate(QStyledItemDelegate):
    """O cartão de uma voz na lista da revisão (a lista dá o espaço entre os cartões com `setSpacing`); no selecionado, o
    "Ouvir 5 s", que toca pelo clique (`play_requested`, A-20)."""

    play_requested = Signal(int)            # a linha do cartão cujo "Ouvir 5 s" foi clicado

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.strong = font_for("body_strong")
        self.caption = font_for("caption")
        self._strong_fm, self._caption_fm = QFontMetrics(self.strong), QFontMetrics(self.caption)
        self._play_w = 2 * PLAY_PAD_X + PLAY_ICON + PLAY_GAP + self._caption_fm.horizontalAdvance(PLAY_TEXT)
        self._armed: int | None = None      # a linha onde o botão do mouse desceu no "Ouvir 5 s" (o clique vale se subir nela)

    def _head_h(self) -> int:
        return max(self._strong_fm.height(), self._caption_fm.height() + 2 * PILL_PAD_Y)

    def play_rect(self, rect: QRect, index: QModelIndex | QPersistentModelIndex) -> QRectF:
        """O "Ouvir 5 s" no cabeçalho do cartão de `rect` (o retângulo do item), à esquerda dos minutos."""
        card = QRectF(rect).adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)
        right = card.right() - CARD_PAD - self._caption_fm.horizontalAdvance(str(index.data(META_ROLE) or "")) - META_GAP
        pill_h = self._caption_fm.height() + 2 * PILL_PAD_Y
        return QRectF(right - self._play_w, card.top() + CARD_PAD + (self._head_h() - pill_h) / 2, self._play_w, pill_h)

    def _hit(self, view: QWidget | None, rect: QRect, index: QModelIndex | QPersistentModelIndex) -> QRectF | None:
        """A área do clique no "Ouvir 5 s" do cartão selecionado, com a altura de um controle (o alvo de 32 px da F5.4);
        None fora do selecionado, onde ele não está pintado."""
        if not isinstance(view, QAbstractItemView) or not view.selectionModel().isSelected(index):
            return None
        pill = self.play_rect(rect, index)
        grow = max(0.0, (surface_tokens(view).metrics.control_h - pill.height()) / 2)
        return pill.adjusted(0, -grow, 0, grow)

    @override
    def editorEvent(self, event: QEvent, model: QAbstractItemModel, option: QStyleOptionViewItem,
                    index: QModelIndex | QPersistentModelIndex) -> bool:
        """A-20: o clique no "Ouvir 5 s" toca. O aperto chega antes de a seleção mudar: o cartão que o próprio clique
        seleciona não tinha o botão à vista, e o clique nele só seleciona."""
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return super().editorEvent(event, model, option, index)
        hit = self._hit(option.widget, option.rect, index)
        inside = hit is not None and hit.contains(event.position())
        if event.type() == QEvent.Type.MouseButtonPress:
            self._armed = index.row() if inside else None
            return inside
        if event.type() == QEvent.Type.MouseButtonRelease and self._armed is not None:
            row, self._armed = self._armed, None
            if inside and row == index.row():
                self.play_requested.emit(row)
                return True
        return super().editorEvent(event, model, option, index)

    @override
    def helpEvent(self, event: QHelpEvent, view: QAbstractItemView, option: QStyleOptionViewItem,
                  index: QModelIndex | QPersistentModelIndex) -> bool:
        """A dica do "Ouvir 5 s", com a tecla (o Espaço segue tocando a voz selecionada)."""
        hit = self._hit(view, option.rect, index)
        if event.type() == QEvent.Type.ToolTip and hit is not None and hit.contains(QPointF(event.pos())):
            QToolTip.showText(event.globalPos(), PLAY_TIP, view)
            return True
        return super().helpEvent(event, view, option, index)

    def _paint_play(self, painter: QPainter, pill: QRectF, t: Tokens) -> None:
        """O "Ouvir 5 s" num selo de botão: o ícone de tocar no destaque e o texto (U-099)."""
        rounded(painter, pill, t.metrics.radius, t.card, t.stroke_strong)
        dpr = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
        painter.drawPixmap(QPointF(pill.left() + PLAY_PAD_X, pill.center().y() - PLAY_ICON / 2),
                           icons.pixmap("play", t.accent, PLAY_ICON, dpr))
        left = pill.left() + PLAY_PAD_X + PLAY_ICON + PLAY_GAP
        painter.setFont(self.caption)
        painter.setPen(qcolor(t.text))
        painter.drawText(QRectF(left, pill.top(), pill.right() - left, pill.height()),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), PLAY_TEXT)

    @staticmethod
    def _item_w(option: QStyleOptionViewItem) -> int:
        """A largura do cartão: a que a lista dá (ou, sem ela, a área visível), menos o espaçamento dos dois lados."""
        view, full = option.widget, option.rect.width()
        if full <= 0:
            full = view.viewport().width() if isinstance(view, QAbstractScrollArea) else 400
        return full - 2 * view.spacing() if isinstance(view, QListView) else full

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        detail = str(index.data(DETAIL_ROLE) or "")
        item_w = self._item_w(option)
        width = max(60, item_w - 2 * (CARD_MARGIN + CARD_PAD))            # a mesma do resumo na pintura
        detail_h = option.fontMetrics.boundingRect(QRect(0, 0, width, 10_000), DETAIL_FLAGS, detail).height() if detail else 0
        height = 2 * (CARD_MARGIN + CARD_PAD) + self._head_h() + (CARD_GAP + detail_h if detail else 0)
        return QSize(item_w, height)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = surface_tokens(option.widget)
        state = option.state
        selected, hover = bool(state & QStyle.StateFlag.State_Selected), bool(state & QStyle.StateFlag.State_MouseOver)
        card = QRectF(option.rect).adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)
        fill = t.selected if selected else t.hover if hover else t.card
        rounded(painter, card, t.metrics.radius_card, fill, t.accent if selected else t.stroke_card, 1.5 if selected else 1.0)
        painter.save()
        x, y = card.left() + CARD_PAD, card.top() + CARD_PAD
        right = card.right() - CARD_PAD
        head_h = self._head_h()
        meta = str(index.data(META_ROLE) or "")
        meta_w = self._caption_fm.horizontalAdvance(meta)
        painter.setFont(self.caption)
        painter.setPen(qcolor(t.on_selected(t.secondary, selected)))       # contraste alto: o texto do destaque (R-03)
        painter.drawText(QRectF(right - meta_w, y, meta_w, head_h), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         meta)
        title = str(index.data(TITLE_ROLE) or "")
        badge = str(index.data(BADGE_ROLE) or "")
        pill_w = self._caption_fm.horizontalAdvance(badge) + 2 * PILL_PAD_X if badge else 0
        play_w = self._play_w + META_GAP if selected else 0                 # U-099: o "Ouvir 5 s" só no selecionado
        room = max(0.0, right - meta_w - META_GAP - play_w - x - (pill_w + 8 if badge else 0))
        shown = self._strong_fm.elidedText(title, Qt.TextElideMode.ElideRight, int(room))
        painter.setFont(self.strong)
        painter.setPen(qcolor(t.on_selected(t.text, selected)))
        painter.drawText(QRectF(x, y, room, head_h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), shown)
        if badge:
            value = Tone(str(index.data(BADGE_TONE_ROLE) or Tone.NEUTRAL.value))
            pill_h = self._caption_fm.height() + 2 * PILL_PAD_Y
            pill = QRectF(x + self._strong_fm.horizontalAdvance(shown) + 8, y + (head_h - pill_h) / 2, pill_w, pill_h)
            rounded(painter, pill, pill_h / 2, t.background(value), t.edge(value))
            painter.setFont(self.caption)
            painter.setPen(qcolor(t.tone(value)))
            painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), badge)
        detail = str(index.data(DETAIL_ROLE) or "")
        if detail:
            painter.setFont(option.font)
            painter.setPen(qcolor(t.on_selected(t.secondary, selected)))
            top = y + head_h + CARD_GAP
            painter.drawText(QRectF(x, top, right - x, card.bottom() - CARD_PAD - top), DETAIL_FLAGS, detail)
        if selected:
            self._paint_play(painter, self.play_rect(option.rect, index), t)
        painter.restore()
        if state & QStyle.StateFlag.State_HasFocus and option.widget is not None:
            focus = QStyleOptionFocusRect()
            focus.initFrom(option.widget)
            focus.rect = card.toAlignedRect()
            option.widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus, painter, option.widget)


class KeyButton(QPushButton):
    """O botão de uma ação da revisão: o texto à esquerda e a tecla num selo à direita (U-086)."""

    def __init__(self, text: str, key: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.cap_font = font_for("caption")         # por botão: nada de fonte do Qt viva no nível da classe até a saída (R-06)
        self.set_label(text, key)
        self.setMinimumWidth(self.full_width)            # a largura do texto inicial e do selo (sem medida em Python no layout)

    def set_label(self, text: str, key: str) -> None:
        """A-19 (F5.11): o texto e a tecla conforme o estado da voz; refaz o selo, a largura cheia e a dica. A largura mínima
        fica a do texto inicial: a coluna é fixa e um nome comprido é cortado no meio (`paintEvent`)."""
        fm = QFontMetrics(self.cap_font)
        self.setText(text)
        self.key = key
        self._cap = QSize(fm.horizontalAdvance(key) + 2 * CAP_PAD_X, fm.height() + 4) if key else QSize(ARROW, ARROW)
        self.full_width = BUTTON_PAD + self.fontMetrics().horizontalAdvance(text) + CAP_GAP + self._cap.width() + BUTTON_PAD
        if key:
            self.setToolTip(f"{text} (tecla {key})")
            self.setAccessibleDescription(f"tecla {key}")
        self.updateGeometry()
        self.update()

    @override
    def sizeHint(self) -> QSize:
        """A largura do texto e do selo (a do `QPushButton` não conta o selo; a sonda dos `cortados` mede por esta)."""
        base = super().sizeHint()
        return QSize(max(base.width(), self.full_width), base.height())

    def _cap_size(self) -> QSize:
        return self._cap

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.features &= ~QStyleOptionButton.ButtonFeature.HasMenu        # a seta do menu é a nossa, no lugar do selo
        p.drawControl(QStyle.ControlElement.CE_PushButtonBevel, opt)
        cap = self._cap_size()
        rect = opt.rect
        cap_rect = QRectF(rect.right() - BUTTON_PAD - cap.width() + 1, rect.center().y() - cap.height() / 2 + 1, cap.width(),
                          cap.height())
        t = surface_tokens(self)
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        on_fill = enabled and self.property(KIND_PROP) == PRIMARY
        room = QRect(rect.left() + BUTTON_PAD, rect.top(), int(cap_rect.left()) - CAP_GAP - rect.left() - BUTTON_PAD, rect.height())
        text = self.text()
        if self.width() < self.full_width:                       # só corta quando não cabe, no meio: o nome (A-19)
            text = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, room.width())
        p.setPen(qcolor(t.on_accent if on_fill else t.text if enabled else t.disabled))     # o texto à esquerda (o rótulo do
        p.drawText(room, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)   # estilo centraliza e corta)
        ink = t.on_accent if on_fill else t.secondary if enabled else t.disabled
        if not self.key:
            p.drawPixmap(cap_rect.topLeft(), icons.pixmap("chevron-down", ink, ARROW, self.devicePixelRatioF()))
        else:
            rounded(p, cap_rect, t.metrics.radius, None, t.on_accent if on_fill else t.stroke_strong if enabled else t.disabled)
            p.setFont(self.cap_font)
            p.setPen(qcolor(ink))
            p.drawText(cap_rect, int(Qt.AlignmentFlag.AlignCenter), self.key)
        if opt.state & QStyle.StateFlag.State_HasFocus:
            focus = QStyleOptionFocusRect()
            focus.initFrom(self)
            p.drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus)
        p.end()


def stage_primary(primary: QPushButton, others: Iterable[QPushButton]) -> None:
    """O primário da etapa (U-086) em destaque; os outros sem `kind`. Nenhum é o padrão do Enter (veja o módulo)."""
    for other in others:
        if other is not primary:
            set_kind(other, None)
            other.setAutoDefault(False)
    set_kind(primary, PRIMARY)
    primary.setAutoDefault(False)
    primary.setDefault(False)


__all__ = ["BADGE_ROLE", "BADGE_TONE_ROLE", "DETAIL_ROLE", "META_ROLE", "PLAY_TEXT", "PLAY_TIP", "STATUS_BADGES", "TITLE_ROLE",
           "KeyButton", "VoiceCardDelegate", "badge_of", "card_detail", "card_item", "stage_primary", "taken_voices"]
