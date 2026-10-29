"""A revisão de falantes no sistema de design (F5.4 L4, U-086, U-035), fora do `ui/review.py`, que passava de 400 linhas:

- `VoiceCardDelegate`: cada voz num cartão, com o título ("Voz 2", U-035), o estado num selo (a confirmar, sem nome,
  confirmada, automática, ignorada, mesclada; o texto e o tom, nunca só a cor, U-054), os minutos de fala à direita e o
  resumo (a sugestão do pyannote e a da LLM) embaixo. O `DisplayRole` do item segue com a linha inteira, para o leitor de
  tela e os testes.
- `KeyButton`: o botão da ação com o atalho num selo de tecla à direita ("Confirmar pyannote [Enter]"); a tecla também vai
  na dica e na descrição acessível.
- `stage_primary`: o primário segue a etapa (com voz pendente, "Confirmar pyannote"; sem nenhuma, "Concluir"). Nenhum vira
  o padrão do Enter: na lista, Enter segue confirmando o pyannote (U-006), e num campo de texto não fecha a revisão; num
  botão com foco, o Enter aciona esse botão (`ReviewDialog.keyPressEvent`, R-01 da revisão da F5.4)."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QListView, QListWidgetItem, QPushButton, QStyle, QStyledItemDelegate,
                               QStyleOptionButton, QStyleOptionFocusRect, QStyleOptionViewItem, QStylePainter, QWidget)

from ..model import ClusterStatus
from .style import rounded
from .theme import KIND_PROP, PRIMARY, Tone, font_for, qcolor, set_kind, surface_tokens

if TYPE_CHECKING:
    from ..pipeline.review import ReviewItem
    from ..pipeline.voices import VoiceNames

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


def card_item(item: ReviewItem, names: VoiceNames) -> QListWidgetItem:
    """O item da lista de uma voz: os papéis do cartão e a linha inteira no `DisplayRole`."""
    title = names.title(item.cluster_id, item.title)
    badge, tone = badge_of(item)
    if item.merged_into is not None:
        target = names.title(item.merged_into, f"voz #{item.merged_into}")
        detail, shown = f"mesclada com a {target}", f"é a mesma que a {target}"
    else:
        detail = item.summary()
        shown = card_detail(detail, badge)
    out = QListWidgetItem(f"{title} · {item.minutes_text} · {detail}")
    out.setData(Qt.ItemDataRole.UserRole, item.cluster_id)
    for role, value in ((TITLE_ROLE, title), (DETAIL_ROLE, shown), (META_ROLE, item.minutes_text), (BADGE_ROLE, badge),
                        (BADGE_TONE_ROLE, tone.value)):
        out.setData(role, value)
    return out


class VoiceCardDelegate(QStyledItemDelegate):
    """O cartão de uma voz na lista da revisão (a lista dá o espaço entre os cartões com `setSpacing`)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.strong = font_for("body_strong")
        self.caption = font_for("caption")
        self._strong_fm, self._caption_fm = QFontMetrics(self.strong), QFontMetrics(self.caption)

    def _head_h(self) -> int:
        return max(self._strong_fm.height(), self._caption_fm.height() + 2 * PILL_PAD_Y)

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
        room = max(0.0, right - meta_w - 12 - x - (pill_w + 8 if badge else 0))
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
        self.key = key
        self.cap_font = font_for("caption")         # por botão: nada de fonte do Qt viva no nível da classe até a saída (R-06)
        fm = QFontMetrics(self.cap_font)
        self._cap = QSize(fm.horizontalAdvance(key) + 2 * CAP_PAD_X, fm.height() + 4)     # o selo, medido uma vez
        self.full_width = BUTTON_PAD + self.fontMetrics().horizontalAdvance(text) + CAP_GAP + self._cap.width() + BUTTON_PAD
        self.setMinimumWidth(self.full_width)            # a largura do texto e do selo, uma vez (sem medida em Python no layout)
        self.setToolTip(f"{text} (tecla {key})")
        self.setAccessibleDescription(f"tecla {key}")

    def _cap_size(self) -> QSize:
        return self._cap

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
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
        if self.width() < self.full_width:                                                  # só corta quando não cabe
            text = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room.width())
        p.setPen(qcolor(t.on_accent if on_fill else t.text if enabled else t.disabled))     # o texto à esquerda (o rótulo do
        p.drawText(room, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)   # estilo centraliza e corta)
        ink = t.on_accent if on_fill else t.secondary if enabled else t.disabled
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


__all__ = ["BADGE_ROLE", "BADGE_TONE_ROLE", "DETAIL_ROLE", "META_ROLE", "STATUS_BADGES", "TITLE_ROLE", "KeyButton",
           "VoiceCardDelegate", "badge_of", "card_detail", "card_item", "stage_primary"]
