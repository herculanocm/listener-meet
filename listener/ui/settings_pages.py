"""As páginas das Configurações (F5.4 L3, U-071, U-074 e U-075): cada página é uma área com rolagem, com o título de 20 px,
o subtítulo que diz quando o que está nela vale ("Aplica agora", "Vale na próxima gravação") e os cartões; na linha de um
campo só aparece o momento quando ele difere do da página. `DownloadRow` é um download (os modelos do ao vivo, a busca
local): o botão e a barra numa linha e, embaixo, o estado em selo com o detalhe. O selo fica no tamanho do texto, no meio
da linha (`pill`), e a nota de um formulário estica na coluna do campo (`add_note`). Revisão da F5.4 (R-04): o valor salvo
que a `SPECS` recusa vira um aviso numa faixa no topo da página (`warn`)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..model import SettingScope
from .components import Badge, Card, InfoBar
from .keys import note as wrap_note
from .settings_editors import BoolEditor, SettingEditor, TextEditor
from .theme import METRICS, Tone, note, role

SCOPE_TEXT = {SettingScope.NOW: "aplica agora", SettingScope.NEXT_RECORDING: "na próxima gravação"}
FORM_SPACING = 16                                      # entre o rótulo e o campo


def pill(badge: Badge) -> Badge:
    """O selo no tamanho do texto: sem esticar na largura nem na altura da linha (U-075)."""
    badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return badge


class SettingsPage(QScrollArea):
    """Uma página: o título, o subtítulo e os cartões (`card`, `form`), numa área que rola na vertical. `scope` é o momento
    da página: o campo com outro momento diz o seu na linha (U-074)."""

    def __init__(self, title: str, subtitle: str, scope: SettingScope | None = None) -> None:
        super().__init__()
        self.page_title = title
        self.scope = scope
        self.cards: dict[str, Card] = {}
        self.warning: InfoBar | None = None                # R-04: montada no primeiro aviso
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        m = METRICS.margin
        self.content.setContentsMargins(m, m - 8, m, m)
        self.content.setSpacing(METRICS.card_gap)
        heading = QLabel(title)
        role(heading, "subtitle")
        self.subtitle = wrap_note(subtitle)
        self.content.addWidget(heading)
        self.content.addWidget(self.subtitle)
        self.setWidget(self.body)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(title)

    def card(self, title: str = "") -> Card:
        """Um cartão novo no fim da página (sem título quando a página tem um só)."""
        card = Card(title)
        self.content.addWidget(card)
        self.cards[title] = card
        return card

    def form(self, title: str = "") -> QFormLayout:
        """Um cartão com um formulário: os campos de texto esticam; spin, lista e caixa ficam no tamanho deles."""
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(METRICS.form_row)
        form.setHorizontalSpacing(FORM_SPACING)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.card(title).content.addLayout(form)
        return form

    def finish(self) -> None:
        self.content.addStretch(1)

    def warn(self, text: str) -> InfoBar:
        """Um aviso na faixa do topo da página, depois do título e do subtítulo (uma faixa por página, uma linha por aviso)."""
        if self.warning is None:
            self.warning = InfoBar("warning", text)
            self.content.insertWidget(2, self.warning)
        else:
            self.warning.set_text(f"{self.warning.label.text()}\n{text}")
        return self.warning

    def scope_note(self, scope: SettingScope) -> str:
        """U-074: o momento do campo só quando difere do da página."""
        return "" if scope == self.scope else SCOPE_TEXT.get(scope, "")

    def add_note(self, form: QFormLayout, text: str) -> QLabel:
        """Uma nota embaixo de um campo, na largura da coluna dos campos (sem esticar, a nota quebrava na largura dela)."""
        label = wrap_note(text)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        form.addRow("", label)
        return label

    def add_row(self, form: QFormLayout, editor: SettingEditor, badge: tuple[str, Tone] | None = None, *,
                compact: bool = False) -> Badge | None:
        """A linha de um editor: o rótulo da `SPECS` (na caixa de seleção, o texto dela), o campo, o selo (`badge`) e o
        momento quando difere do da página. O campo de texto estica, salvo `compact` (os atalhos, todos da mesma largura,
        com o selo logo depois). Devolve o selo posto."""
        extra = self.scope_note(editor.spec.scope)
        field: QWidget = editor.widget
        shown: Badge | None = None
        stretch = isinstance(editor, TextEditor) and not compact
        if extra or badge is not None or not stretch:
            field = QWidget()
            box = QHBoxLayout(field)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(METRICS.form_row)
            box.addWidget(editor.widget, 1 if stretch else 0)
            if badge is not None:
                shown = pill(Badge(*badge))
                box.addWidget(shown, 0, Qt.AlignmentFlag.AlignVCenter)
            if extra:
                scope = QLabel(extra)
                note(scope)
                box.addWidget(scope)
            if stretch:
                field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            else:
                box.addStretch(1)
        if isinstance(editor, BoolEditor):
            form.addRow(field)
        else:
            form.addRow(editor.spec.label, field)
        return shown


class DownloadRow(QWidget):
    """Um download (U-075): o botão e a barra (escondida parado) numa linha; embaixo, o selo do estado (Prontos, Faltam,
    Baixando, Falhou) e o detalhe em nota."""

    def __init__(self, text: str, tip: str) -> None:
        super().__init__()
        self.btn = QPushButton(text)
        self.btn.setToolTip(tip)
        self.btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)      # parado, sem a barra: não estica
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.badge = pill(Badge())
        self.status = wrap_note("")
        top = QHBoxLayout()
        top.addWidget(self.btn)
        top.addWidget(self.bar, 1)
        top.addStretch(0)                              # com a barra escondida, o espaço fica à direita do botão
        bottom = QHBoxLayout()
        bottom.setSpacing(METRICS.form_row)
        bottom.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.status, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(METRICS.form_row)
        lay.addLayout(top)
        lay.addLayout(bottom)

    def set_badge(self, text: str, value: Tone) -> None:
        self.badge.setText(text)
        self.badge.set_tone(value)


__all__ = ["SCOPE_TEXT", "DownloadRow", "SettingsPage", "pill"]
