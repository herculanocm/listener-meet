"""Os componentes da F5.4 (L1, item 3), nos tokens do tema e sem folha de estilo: `Card`, `InfoBar`, `Badge` (também numa
célula, pelo `BadgeDelegate`), `EmptyState`, `IconButton` e o `HtmlView`, o `QTextBrowser` do HTML por classes (A-10).
Quem pinta lê os tokens na hora da pintura (a troca de tema só repinta); os ícones vêm de `ui/icons` (`theme_icon`). O
`HtmlView` já serve o Histórico e o Ao vivo (o HTML por classes do L1); os outros, as telas passam a usar do L2 ao L4. No
contraste alto (revisão da F5.4, R-03) a faixa e o selo ficam no fundo da janela, com a borda no tom (`Tokens.edge`).
F5.11 (A-34): `Collapsible`, o grupo recolhível (o "Avançado" das Configurações), com o chevron que já existe.
F5.12: `LinkLabel`, o link discreto (o Sobre no assistente, o apoio no rodapé do Histórico, o "Já doei" do convite)."""
from __future__ import annotations

import html
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QTransform
from PySide6.QtWidgets import (QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from ..model import MIC_ROOM, MIC_YOU
from .icons import BUTTON_SIZE, pixmap, screen_dprs, set_icon
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
        value = INFO_KINDS[self.kind][0]
        rounded(p, QRectF(self.rect()), t.metrics.radius, t.background(value), t.edge(value) or t.stroke)
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
        rounded(p, r, r.height() / 2, t.background(self.badge_tone), t.edge(self.badge_tone))
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
        rounded(painter, pill, height / 2, t.background(value), t.edge(value))
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


def mic_title(box: QGroupBox, in_room: bool) -> None:
    """U-106 (A-41): o cartão do microfone diz "Microfone: você" ou, com a sala presencial, "Microfone: você e a sala" (o
    microfone ouve outras pessoas)."""
    box.setTitle(MIC_ROOM if in_room else MIC_YOU)


class Collapsible(QWidget):
    """F5.11 (A-34): o grupo recolhível. O cabeçalho é um botão marcável com o chevron (para baixo fechado, para cima aberto,
    como o Expander do Windows 11); fechado, o corpo (`content`) some, mas o que está nele existe desde a abertura."""
    toggled = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = QToolButton(self)
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setAccessibleName(title)
        self.body = QWidget(self)
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 0, 0, 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.header, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.body)
        self.header.toggled.connect(self._set_open)
        self._set_open(False)

    def is_open(self) -> bool:
        return self.header.isChecked()

    def expand(self) -> None:
        self.header.setChecked(True)

    def _set_open(self, on: bool) -> None:
        self.body.setVisible(on)
        self._chevron()
        self.toggled.emit(on)

    def _chevron(self) -> None:
        """O chevron no tom do texto, virado para cima com o grupo aberto (a troca de tema o refaz, `changeEvent`)."""
        dpr = max(screen_dprs())
        px = pixmap("chevron-down", surface_tokens(self.header).text, BUTTON_SIZE, dpr)
        if self.header.isChecked():
            px = px.transformed(QTransform().rotate(180))
            px.setDevicePixelRatio(dpr)
        self.header.setIcon(QIcon(px))
        self.header.setIconSize(QSize(BUTTON_SIZE, BUTTON_SIZE))

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self._chevron()


def expand_ancestors(widget: QWidget) -> None:
    """A-34: o campo dentro de um grupo recolhível fechado fica à vista (o erro de validação abre o grupo)."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, Collapsible):
            parent.expand()
        parent = parent.parentWidget()


class LinkLabel(QLabel):
    """F5.12: o link discreto, um rótulo com um `<a>` na cor do link da paleta (`accent`, sem cor nova) que emite `clicked`
    sem o endereço e chama `on_click`, quando dado (ligado a um método: R-06). Com `keyboard`, o Tab chega nele e o Enter o
    aciona; sem, ele não pega foco (o cartão do convite, que nunca rouba o foco)."""
    clicked = Signal()

    def __init__(self, text: str, on_click: Callable[[], Any] | None = None, *, keyboard: bool = True, font_role: str = "body",
                 parent: QWidget | None = None) -> None:
        super().__init__(f'<a href="link">{html.escape(text)}</a>', parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(False)
        flags = Qt.TextInteractionFlag.LinksAccessibleByMouse
        if keyboard:
            flags |= Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        self.setTextInteractionFlags(flags)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if keyboard else Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(text)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        role(self, font_role)
        self.linkActivated.connect(self._activated)
        if on_click is not None:
            self.clicked.connect(on_click)

    def _activated(self, _href: str) -> None:
        self.clicked.emit()


__all__ = ["INFO_KINDS", "Badge", "BadgeDelegate", "Card", "Collapsible", "EmptyState", "HtmlView", "IconButton", "InfoBar",
           "LinkLabel", "expand_ancestors", "mic_title"]
