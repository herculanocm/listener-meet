"""Os componentes da F5.4 (L1, item 3), nos tokens do tema e sem folha de estilo: `Card`, `InfoBar`, `Badge` (também numa
célula, pelo `BadgeDelegate`), `EmptyState`, `IconButton` e o `HtmlView`, o `QTextBrowser` do HTML por classes (A-10).
Quem pinta lê os tokens na hora da pintura (a troca de tema só repinta); os ícones vêm de `ui/icons` (`theme_icon`). O
`HtmlView` já serve o Histórico e o Ao vivo (o HTML por classes do L1); os outros, as telas passam a usar do L2 ao L4."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .icons import set_icon
from .style import rounded
from .theme import PRIMARY, STATUS_TONES, TONE_ROLE, Tone, html_css, qcolor, role, set_kind, surface_tokens, tone

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

# U-057: o tipo da faixa no tom e no ícone dele (estado nunca só pela cor, U-054)
INFO_KINDS: dict[str, tuple[Tone, str]] = {"info": (Tone.INFO, "info"), "success": (Tone.SUCCESS, "success"),
                                           "warning": (Tone.WARNING, "warning"), "error": (Tone.DANGER, "error")}
EMPTY_ICON_PX = 48
BADGE_PAD_X, BADGE_PAD_Y = 8, 2


class Card(QGroupBox):
    """O cartão: um `QGroupBox` (o título segue para os testes e o leitor de tela) que o `ThemeStyle` pinta preenchido, com
    16 px por dentro e 8 entre as linhas (`content`)."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        m = surface_tokens(None).metrics
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(m.card_padding, m.card_padding, m.card_padding, m.card_padding)
        self.content.setSpacing(m.form_row)


class InfoBar(QFrame):
    """A faixa (U-057): o ícone do tipo, o texto e a ação opcional, sobre o fundo do tipo (`*_bg`, com o texto em `text`, 4,5:1
    nos dois temas); `action` é o botão (escondido sem rótulo)."""

    def __init__(self, kind: str = "info", text: str = "", action: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.action = QPushButton(action, self)          # [fato, 28/10] com o pai já na criação: sem ele, o `setVisible(True)`
        self.action.setVisible(bool(action))             # abria e fechava uma janela nativa à toa (~60 ms por botão)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        if kind not in INFO_KINDS:
            raise ValueError(f"tipo de faixa desconhecido: {kind}")
        self.kind = kind
        value, name = INFO_KINDS[kind]
        set_icon(self.icon, name, value, 20)
        self.setAccessibleDescription(kind)
        self.update()

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        rounded(p, QRectF(self.rect()), t.metrics.radius, t.background(INFO_KINDS[self.kind][0]), t.stroke)
        p.end()


class Badge(QLabel):
    """O selo (pílula): o texto no tom de estado sobre o fundo dele (os de `STATUS_TONES`), em legenda de 12 px."""

    def __init__(self, text: str = "", value: Tone = Tone.NEUTRAL, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.badge_tone = value
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setContentsMargins(BADGE_PAD_X, BADGE_PAD_Y, BADGE_PAD_X, BADGE_PAD_Y)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        role(self, "caption")
        self.set_tone(value)

    def set_tone(self, value: Tone) -> None:
        if value not in STATUS_TONES:
            raise ValueError(f"o selo só leva tom de estado: {value}")
        self.badge_tone = value
        tone(self, value)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        t = surface_tokens(self)
        p = QPainter(self)
        r = QRectF(self.rect())
        rounded(p, r, r.height() / 2, t.background(self.badge_tone))
        p.end()
        super().paintEvent(event)


class BadgeDelegate(QStyledItemDelegate):
    """O selo numa célula (A-05: o único delegate do tema): o texto do `DisplayRole` (o que o leitor de tela lê) numa pílula
    no tom de `TONE_ROLE`; sem tom, a célula comum."""

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        name = index.data(TONE_ROLE)
        if not name or Tone(str(name)) not in STATUS_TONES:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)     # fundo e seleção
        if not text:
            return
        t, value = surface_tokens(opt.widget), Tone(str(name))
        fm = opt.fontMetrics
        height = fm.height() + 2 * BADGE_PAD_Y
        width = min(fm.horizontalAdvance(text) + 2 * BADGE_PAD_X, max(0, opt.rect.width() - 8))
        pill = QRectF(opt.rect.left() + 4, opt.rect.center().y() - height / 2 + 1, width, height)
        painter.save()
        rounded(painter, pill, height / 2, t.background(value))
        painter.setPen(qcolor(t.tone(value)))
        painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter),
                         fm.elidedText(text, Qt.TextElideMode.ElideRight, int(width) - 2 * BADGE_PAD_X + 2))
        painter.restore()

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width() + 2 * BADGE_PAD_X + 8, size.height())


class EmptyState(QWidget):
    """O estado vazio (U-059): o ícone grande, a frase e a ação opcional (o primário da tela)."""

    def __init__(self, icon_name: str = "document", text: str = "", action: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_icon(self.icon, icon_name, Tone.SECONDARY, EMPTY_ICON_PX)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        tone(self.label, Tone.SECONDARY)
        self.action = QPushButton(action, self)          # o pai já na criação (veja a `InfoBar`)
        self.action.setVisible(bool(action))
        set_kind(self.action, PRIMARY)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)


class IconButton(QToolButton):
    """O botão só de ícone: o nome no `text()`, no `accessibleName` e na dica (com o atalho, quando há); 32 por 32 px no mínimo
    (Aceite 3) e sem moldura até o mouse passar."""

    def __init__(self, icon_name: str, text: str, value: Tone = Tone.TEXT, shortcut: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.setToolTip(f"{text} ({shortcut})" if shortcut else text)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setAutoRaise(True)
        self.setMinimumSize(32, 32)
        set_icon(self, icon_name, value, 20)


class HtmlView(QTextBrowser):
    """O `QTextBrowser` do tema (A-10): o HTML do app diz `class='note'` (e `success`, `warning`, `danger`, `info`,
    `accent`), nunca a cor; a folha de classes nos tokens fica no `document().setDefaultStyleSheet` e a troca de tema refaz a
    folha e o documento (`retheme`) na mesma rolagem. O texto mostrado guarda o HTML de origem (`source_html`)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_html = ""

    @override
    def setHtml(self, text: str) -> None:
        self.source_html = text
        self.document().setDefaultStyleSheet(html_css(surface_tokens(self)))
        super().setHtml(text)

    def retheme(self) -> None:
        bar = self.verticalScrollBar()
        position = bar.value()
        self.setHtml(self.source_html)
        bar.setValue(position)


__all__ = ["INFO_KINDS", "Badge", "BadgeDelegate", "Card", "EmptyState", "HtmlView", "IconButton", "InfoBar"]
