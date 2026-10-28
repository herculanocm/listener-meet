"""O `ThemeStyle` da F5.4 (L1, passo 7 do A-21): um `QProxyStyle` sobre o estilo base (`windows11` no Windows, Fusion no fake
dos testes) que pinta o que a paleta não alcança, com os tokens do tema (`ui/theme.py`).

- A-04: [fato, 28/10] o proxy em Python custa ~+6 % na abertura só nos `draw*` e ~+37 % com `pixelMetric`/`styleHint`:
  sobrescreve só `drawPrimitive`, `drawControl`, `drawComplexControl` e `polish`; cada um sai cedo pelo elemento.
- A-02: primário e destrutivo pela propriedade `kind` do botão. [fato, 28/10, Qt 6.12] o `windows11` pinta o botão no
  `CE_PushButtonBevel` sem passar pelo `PE_PanelButtonCommand` e só segue o `ButtonText` posto na própria opção: o fundo
  sai aqui e o texto numa cópia da opção com `on_accent`.
- A-05: a seleção num só `CE_ItemViewItem`: o fundo `selected` na linha inteira (cada célula da linha) e a base sem
  `State_Selected` (o texto fica no `Text`; o `backgroundBrush` do item, a fala que toca, segue por cima); o tom do item
  (`TONE_ROLE`) vira a cor do texto na hora da pintura (a troca de tema não deixa cor velha).
- A-06: o grupo como cartão (preenchido em `card`, borda `stroke_card`, raio 8, o título dentro); as abas sublinhadas em
  `accent`; a borda `danger` no campo, combo, spin e tabela (revisão da F5.4, R-09) com a propriedade `invalid`; o anel
  de foco de 2 px só com `State_KeyboardFocusChange` (U-055); o campo com `theme_flat` (o título da reunião, U-078) sem
  moldura nem fundo até o foco ou o mouse. O indicador da caixa de seleção fica com a base: a sonda no escuro forçado não
  mostrou o defeito do A-06.
- Contraste alto (revisão da F5.4, R-03): os tokens vêm da paleta do sistema; a linha selecionada leva o texto do destaque
  (`selected_text`) e o primário apagado fica no fundo da janela com a borda no apagado (o preenchido some no sistema).
- `polish(widget)`: as alturas mínimas (Aceite 3), só nos controles medidos (`SIZED`; o resto sai sem chamada ao C++):
  botão, campo, combo e spin com 32 px e botão com 32 de largura; caixa de seleção e rádio com 24 (o `pequenos` da sonda
  acusou 422 controles de 29 px). As peças internas do Qt (o botão de limpar do campo, as setas das abas) ficam como estão.
- Superfície noutro esquema (`theme.surface`; no L1b, o widget de gravação, o marcador e os avisos, no tema desde o L2): [fato]
  o `windows11` pinta botão e campo dela no esquema do app e ignora a paleta herdada; aqui saem planos, nos tokens dela."""
from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QGroupBox, QLineEdit, QProxyStyle,
                               QPushButton, QRadioButton, QScrollBar, QStyle, QStyleOption, QStyleOptionButton, QStyleOptionComplex,
                               QStyleOptionToolButton, QStyleOptionViewItem, QTabBar, QToolButton, QWidget)

from .theme import DANGER, INVALID_PROP, KIND_PROP, PRIMARY, SCHEMES, SURFACE_PROP, TONE_PROP, TONE_ROLE, Tokens, Tone, mix, qcolor

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

PE, CE, CC, SC = QStyle.PrimitiveElement, QStyle.ControlElement, QStyle.ComplexControl, QStyle.SubControl
St = QStyle.StateFlag
POLISHED_PROP = "theme_polished"
FLAT_PROP = "theme_flat"                          # L4 (U-078): o campo sem moldura nem fundo até o foco ou o mouse
INTERNAL_PARENTS = (QLineEdit, QComboBox, QAbstractSpinBox, QTabBar, QScrollBar, QAbstractItemView)
TITLE_INSET = 6                                   # o título do cartão desce da borda de cima (px lógicos)
SIZED = (QGroupBox, QPushButton, QToolButton, QLineEdit, QComboBox, QAbstractSpinBox, QCheckBox, QRadioButton)   # o que o polish mede


def rounded(painter: QPainter, rect: QRectF, radius: float, fill: str | None, stroke: str | None = None, width: float = 1.0) -> None:
    """Um retângulo de cantos redondos com preenchimento e borda dos tokens (a borda por dentro do retângulo)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(qcolor(stroke), width) if stroke else Qt.PenStyle.NoPen)
    painter.setBrush(qcolor(fill) if fill else Qt.BrushStyle.NoBrush)
    half = width / 2 if stroke else 0.0
    painter.drawRoundedRect(rect.adjusted(half, half, -half, -half), radius, radius)
    painter.restore()


def kind_fill(t: Tokens, kind: str, state: QStyle.StateFlag) -> str:
    """O fundo do primário (destaque) e do destrutivo (perigo) em cada estado; apagado, o traço do controle."""
    if not state & St.State_Enabled:
        return t.window if t.outline else t.stroke
    sunken, hover = bool(state & St.State_Sunken), bool(state & St.State_MouseOver)
    if kind == PRIMARY:
        return t.accent_pressed if sunken else t.accent_hover if hover else t.accent
    return mix(t.danger, t.text, 0.2 if sunken else 0.1 if hover else 0.0)


def flat_fill(t: Tokens, state: QStyle.StateFlag) -> str:
    """O fundo de um botão plano numa superfície (o widget de gravação): ligado (o mudo) em perigo."""
    if state & St.State_On:
        return t.danger
    sunken, hover = bool(state & St.State_Sunken), bool(state & St.State_MouseOver)
    return mix(t.stroke, t.text, 0.2 if sunken else 0.1 if hover else 0.0)


def _with_text(option: QStyleOption, color: str) -> None:
    """A cor do texto do botão na própria opção (o `windows11` só segue o `ButtonText` posto nela), ativo e inativo."""
    pal = QPalette(option.palette)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.ButtonText, qcolor(color))
    option.palette = pal


class ThemeStyle(QProxyStyle):
    """O estilo do app por cima do estilo base, com os tokens do tema vigente (`tokens`, trocados pelo `theme` na troca)."""

    def __init__(self, base: str, tokens: Tokens) -> None:
        super().__init__(base)
        self.base_name = base
        self.tokens = tokens

    # ---------------------------------------------------------------- tokens de quem pinta
    def _tokens(self, widget: QWidget | None) -> Tokens:
        scheme = widget.window().property(SURFACE_PROP) if widget is not None else None
        return SCHEMES.get(str(scheme), self.tokens) if scheme else self.tokens

    @staticmethod
    def _on_surface(widget: QWidget | None) -> bool:
        return widget is not None and bool(widget.window().property(SURFACE_PROP))

    @staticmethod
    def _kind(option: QStyleOption, widget: QWidget | None) -> str | None:
        source: QObject | None = option.styleObject or widget
        kind = source.property(KIND_PROP) if source is not None else None
        return str(kind) if kind in (PRIMARY, DANGER) else None

    # ---------------------------------------------------------------- primitivas
    @override
    def drawPrimitive(self, element: QStyle.PrimitiveElement, option: QStyleOption, painter: QPainter,
                      widget: QWidget | None = None) -> None:
        if element == PE.PE_FrameFocusRect:
            self._focus_ring(option, painter, widget)
            return
        if element == PE.PE_PanelButtonTool and self._on_surface(widget):
            idle = option.state & St.State_AutoRaise and not option.state & (St.State_MouseOver | St.State_Sunken | St.State_On)
            if not idle:                                    # o botão sem moldura (o "✕" do aviso) só ganha fundo sob o mouse
                rounded(painter, QRectF(option.rect), self.tokens.metrics.radius, flat_fill(self._tokens(widget), option.state))
            return
        if (element == PE.PE_PanelLineEdit and widget is not None and widget.property(FLAT_PROP)
                and not option.state & (St.State_HasFocus | St.State_MouseOver)):
            return                                          # U-078: o título da reunião, sem moldura até o foco
        if element == PE.PE_PanelLineEdit and self._on_surface(widget):
            t = self._tokens(widget)
            rounded(painter, QRectF(option.rect), t.metrics.radius, t.input, t.stroke_strong)
            return
        if element == PE.PE_PanelItemViewRow and option.state & St.State_Selected and isinstance(option, QStyleOptionViewItem):
            opt = QStyleOptionViewItem(option)
            painter.fillRect(opt.rect, qcolor(self._tokens(widget).selected))
            opt.state &= ~St.State_Selected
            super().drawPrimitive(element, opt, painter, widget)
            return
        super().drawPrimitive(element, option, painter, widget)
        if element == PE.PE_PanelLineEdit and widget is not None and widget.property(INVALID_PROP):
            self._invalid_border(option.rect, painter, widget)

    def _focus_ring(self, option: QStyleOption, painter: QPainter, widget: QWidget | None) -> None:
        """U-055: o anel de 2 px só quando o foco veio do teclado (o clique não acende)."""
        if not option.state & St.State_KeyboardFocusChange:
            return
        t = self._tokens(widget)
        rounded(painter, QRectF(option.rect), t.metrics.radius, None, t.focus, t.metrics.focus_w)

    def _invalid_border(self, rect: QRect, painter: QPainter, widget: QWidget) -> None:
        t = self._tokens(widget)
        rounded(painter, QRectF(rect), t.metrics.radius, None, t.danger, 1.5)

    # ---------------------------------------------------------------- controles
    @override
    def drawControl(self, element: QStyle.ControlElement, option: QStyleOption, painter: QPainter,
                    widget: QWidget | None = None) -> None:
        if element == CE.CE_ItemViewItem and isinstance(option, QStyleOptionViewItem):
            self._item(option, painter, widget)
        elif element == CE.CE_PushButtonBevel and isinstance(option, QStyleOptionButton):
            self._bevel(option, painter, widget)
        elif element == CE.CE_PushButtonLabel and isinstance(option, QStyleOptionButton):
            self._button_label(element, QStyleOptionButton(option), painter, widget)
        elif element == CE.CE_ToolButtonLabel and isinstance(option, QStyleOptionToolButton):
            self._button_label(element, QStyleOptionToolButton(option), painter, widget)
        elif element == CE.CE_TabBarTabShape:
            self._tab_shape(option, painter, widget)
        else:
            super().drawControl(element, option, painter, widget)
            if element == CE.CE_ShapedFrame and widget is not None and widget.property(INVALID_PROP):
                self._invalid_border(option.rect, painter, widget)      # R-09: a tabela de preços com erro

    def _item(self, option: QStyleOptionViewItem, painter: QPainter, widget: QWidget | None) -> None:
        """A-05: a linha selecionada em `selected` (cada célula pinta a sua parte) e o tom do item na cor do texto; a célula
        comum (sem tom e sem seleção, quase todas) vai direto para a base, sem cópia da opção."""
        name = option.index.data(TONE_ROLE) if option.index.isValid() else None
        if not name and not option.state & St.State_Selected:
            super().drawControl(CE.CE_ItemViewItem, option, painter, widget)
            return
        opt = QStyleOptionViewItem(option)
        t = self._tokens(widget)
        selected = bool(opt.state & St.State_Selected)
        color = t.tone(Tone(str(name))) if name else None
        if selected and opt.backgroundBrush.style() == Qt.BrushStyle.NoBrush:      # o fundo do item fica por cima da seleção
            color = t.on_selected(color, True) if color else t.selected_text
        if color:
            pal = QPalette(opt.palette)
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                pal.setColor(group, QPalette.ColorRole.Text, qcolor(color))
            opt.palette = pal
        if selected:
            painter.fillRect(opt.rect, qcolor(t.selected))
            opt.state &= ~St.State_Selected
        super().drawControl(CE.CE_ItemViewItem, opt, painter, widget)

    def _bevel(self, option: QStyleOptionButton, painter: QPainter, widget: QWidget | None) -> None:
        kind = self._kind(option, widget)
        if kind is None and not self._on_surface(widget):
            super().drawControl(CE.CE_PushButtonBevel, option, painter, widget)
            return
        t = self._tokens(widget)
        fill = kind_fill(t, kind, option.state) if kind else flat_fill(t, option.state)
        edge = t.disabled if t.outline and not option.state & St.State_Enabled else None
        rounded(painter, QRectF(option.rect), t.metrics.radius, fill, edge)

    def _button_label(self, element: QStyle.ControlElement, opt: QStyleOptionButton | QStyleOptionToolButton, painter: QPainter,
                      widget: QWidget | None) -> None:
        color = self._label_color(opt, widget)
        if color is not None and opt.state & St.State_Enabled:      # apagado, o `disabled` da paleta
            _with_text(opt, color)
        super().drawControl(element, opt, painter, widget)

    def _label_color(self, opt: QStyleOption, widget: QWidget | None) -> str | None:
        """A cor do texto do botão: `on_accent` no primário, no destrutivo e no ligado de uma superfície; o tom do botão
        (`theme.tone`); o texto da superfície; nenhuma (a da base) no resto."""
        t, on_surface = self._tokens(widget), self._on_surface(widget)
        if self._kind(opt, widget) is not None or (on_surface and opt.state & St.State_On):
            return t.on_accent
        name = widget.property(TONE_PROP) if widget is not None else None
        if name:
            return t.tone(Tone(str(name)))
        return t.text if on_surface else None

    def _tab_shape(self, option: QStyleOption, painter: QPainter, widget: QWidget | None) -> None:
        """A aba sublinhada: o traço em `accent` embaixo da selecionada e o fundo `hover` sob o mouse; sem moldura."""
        t = self._tokens(widget)
        r = QRectF(option.rect)
        if option.state & St.State_Selected:
            rounded(painter, QRectF(r.left() + 8, r.bottom() - 3, max(0.0, r.width() - 16), 3), 1.5, t.accent)
        elif option.state & St.State_MouseOver and option.state & St.State_Enabled:
            rounded(painter, r.adjusted(2, 2, -2, -5), t.metrics.radius, t.hover)

    # ---------------------------------------------------------------- controles complexos
    @override
    def drawComplexControl(self, control: QStyle.ComplexControl, option: QStyleOptionComplex, painter: QPainter,
                           widget: QWidget | None = None) -> None:
        if control == CC.CC_GroupBox:
            self._group_box(option, painter, widget)
            return
        super().drawComplexControl(control, option, painter, widget)
        if control in (CC.CC_ComboBox, CC.CC_SpinBox) and widget is not None and widget.property(INVALID_PROP):
            self._invalid_border(option.rect, painter, widget)

    def _group_box(self, option: QStyleOptionComplex, painter: QPainter, widget: QWidget | None) -> None:
        """O grupo como cartão: o retângulo inteiro preenchido em `card` com a borda `stroke_card` e o título dentro, em
        semibold (o `polish` abre o espaço dele no layout)."""
        t = self._tokens(widget)
        rounded(painter, QRectF(option.rect), t.metrics.radius_card, t.card, t.stroke_card)
        text = getattr(option, "text", "")
        if text:
            label = self.proxy().subControlRect(CC.CC_GroupBox, option, SC.SC_GroupBoxLabel, widget)
            room = QRect(label.left(), label.top() + TITLE_INSET, option.rect.right() - label.left() - TITLE_INSET, label.height())
            painter.save()
            painter.setFont(_title_font(painter.font() if widget is None else widget.font(), text, room.width()))
            shown = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room.width())
            self.proxy().drawItemText(painter, room, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                      option.palette, bool(option.state & St.State_Enabled), shown, QPalette.ColorRole.WindowText)
            painter.restore()

    # ---------------------------------------------------------------- polish
    @override
    def polish(self, arg: object) -> object:            # type: ignore[override]  # o PySide manda as três sobrecargas aqui
        """Só o `polish(widget)` é nosso; a paleta e o app seguem para a base (a paleta é mudada no lugar)."""
        result = super().polish(arg)  # type: ignore[call-overload]
        if isinstance(arg, SIZED):                       # o resto (rótulos, a maioria) sai aqui, sem chamada ao C++ (A-04)
            min_size(arg, self.tokens)
        return result


def _title_font(base: QFont, text: str, room: int) -> QFont:
    """O título do cartão em semibold quando cabe no cartão; senão no peso do grupo, que o Qt já mediu ao reservar o espaço
    (sem cortar o título que cabia, o "Saída (canal R: …)" do "Antes de gravar")."""
    strong = QFont(base)
    strong.setWeight(QFont.Weight.DemiBold)
    return strong if QFontMetrics(strong).horizontalAdvance(text) <= room else base


def _internal(w: QWidget) -> bool:
    """Peças internas do Qt: o botão de limpar do campo, o campo do combo e do spin, as setas das abas e das barras."""
    return w.objectName().startswith("qt_") or isinstance(w.parentWidget(), INTERNAL_PARENTS)


def min_size(w: QWidget, t: Tokens) -> None:
    """As alturas mínimas do Aceite 3 (botão, campo e combo com 32 px; caixa de seleção com 24; botão com 32 de largura), só
    onde o tamanho natural do widget fica abaixo e sem passar do máximo que quem o montou fixou. [fato, 28/10] o mínimo
    explícito substitui o do `minimumSizeHint` no layout: posto num botão de texto, deixava o layout espremê-lo até 32 px
    (o "Dispositivos…" do widget cortado); por isso só sobe o que é menor. O cartão ganha o espaço do título por dentro."""
    if isinstance(w, QGroupBox):
        _card_room(w)
        return
    if _internal(w):
        return
    target = 0
    if isinstance(w, QCheckBox | QRadioButton):
        target = t.metrics.check_h
    elif isinstance(w, QPushButton | QToolButton | QLineEdit | QComboBox | QAbstractSpinBox):
        target = t.metrics.control_h
    if not target:
        return
    hint = w.sizeHint()
    if w.minimumHeight() < target <= w.maximumHeight() and hint.height() < target:
        w.setMinimumHeight(target)
    side = t.metrics.control_h
    if isinstance(w, QPushButton | QToolButton) and w.minimumWidth() < side <= w.maximumWidth() and hint.width() < side:
        w.setMinimumWidth(side)


def _card_room(box: QGroupBox) -> None:
    """O título do cartão desce `TITLE_INSET`: o conteúdo desce junto (margem de cima do layout), uma vez."""
    lay = box.layout()
    if lay is None or not box.title() or box.property(POLISHED_PROP):
        return
    box.setProperty(POLISHED_PROP, True)
    m = lay.contentsMargins()
    lay.setContentsMargins(max(m.left(), TITLE_INSET + 2), m.top() + TITLE_INSET, max(m.right(), TITLE_INSET + 2), m.bottom())


__all__ = ["FLAT_PROP", "POLISHED_PROP", "ThemeStyle", "flat_fill", "kind_fill", "min_size", "rounded"]
