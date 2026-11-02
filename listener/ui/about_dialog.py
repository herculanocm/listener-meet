"""F5.12 L1 (U-121, U-122): o Sobre do listener-meet. `AboutView` é o conteúdo, no diálogo (`AboutDialog`, pela bandeja e pelo
assistente) e na página Sobre das Configurações (`ui/settings_about.py`): a marca, o nome e a versão; a frase; "Gratuito e
de código aberto (licença MIT), feito por <autor>" com o link do repositório; "Este computador" (as versões do Python, do Qt
e do sistema, a pasta do banco e o arquivo de log) com "Copiar informações para suporte"; e as bibliotecas abertas com a
licença de cada uma. Os dados do projeto vêm de `listener/about.py` (CP15).

- O sistema é o `QSysInfo.prettyProductName()` do Qt: o `platform.platform()`, o `machine()` e o `version()` da biblioteca
  padrão consultam o WMI no Windows e já travaram a suíte; nada de SO fora de `listener/platform/` (CP15).
- "Copiar informações para suporte" leva só as versões e o sistema (`about.support_info`): a pasta do banco e o arquivo de
  log ficam na tela e fora da cópia (têm o nome do usuário). Os caminhos quebram a linha em vez de alargar a janela.
- No assistente ainda não há banco nem log: as duas linhas dizem que eles são escolhidos nos próximos passos (desvio
  técnico: a spec não diz o que mostrar antes da escolha).
- Log: a abertura e a cópia, com a origem; nunca um caminho (D-053).
L2 (U-123): com o `Support`, a seção "Apoie o projeto" (`ui/donate_dialogs.py`) logo depois do link do repositório, com os
dois caminhos; no assistente não há `Support` e a seção não aparece (pergunta 4)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QSysInfo, Qt, qVersion
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import about
from ..log import current_file, get_logger
from .common import copy_rich, fit_to_screen
from .components import Card
from .donate import PAYPAL, PIX
from .donate_dialogs import SupportSection
from .icons import set_icon
from .keys import WrapLabel
from .settings_general import breakable_path
from .theme import METRICS, Tone, note, role

if TYPE_CHECKING:
    from ..store import Store
    from .donate import Support

log = get_logger("ui.about")

TITLE = f"Sobre o {about.APP_NAME}"
SIZE, MIN_SIZE = (600, 600), (480, 400)            # cabe no notebook do dono a 150 % (A-17: 1280 x 672 livres)
BRAND_PX = 48
COMPUTER, CREDITS = "Este computador", "Bibliotecas abertas"
COPY_INFO = "Copiar informações para suporte"
COPY_TIP = "Copia as versões e o sistema (sem a pasta do banco e o arquivo de log, que têm o nome do usuário)"
COPIED = "Copiado para a área de transferência."
NOT_YET = "escolhido nos próximos passos"
CREDITS_NOTE = f"O {about.APP_NAME} é feito sobre estas bibliotecas de código aberto, cada uma com a sua licença:"


def computer_info() -> dict[str, str]:
    """As versões e o sistema desta máquina (o que a cópia para suporte leva)."""
    return {"python": ".".join(str(n) for n in sys.version_info[:3]), "qt": qVersion(), "system": QSysInfo.prettyProductName()}


def log_file(store: Store | None) -> Path | None:
    """O arquivo de log em uso (o anexado) ou, sem ele, o das Configurações (o padrão na pasta do banco)."""
    attached = current_file()
    if attached is not None or store is None:
        return attached
    return Path(store.get_setting("log.path") or store.default_log_path())


def _label(text: str, *, wrap: bool = False, selectable: bool = False) -> QLabel:
    """Texto puro (um caminho com "<" fica texto); com `wrap`, a altura acompanha a largura (`WrapLabel`: no formulário
    dentro da rolagem, o rótulo que quebra ganhava espaço em branco em volta)."""
    label = WrapLabel(text) if wrap else QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return label


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(METRICS.form_row)
    form.setHorizontalSpacing(16)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    return form


class AboutView(QWidget):
    """O conteúdo do Sobre; `db_dir` e `log_path` None no assistente (ainda não escolhidos). `support` traz a seção "Apoie o
    projeto" (None no assistente). `via` é a origem, para o log."""

    def __init__(self, db_dir: Path | None, log_path: Path | None, *, support: Support | None = None, via: str = "sobre",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.via, self.support = via, support
        self.info = computer_info()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(METRICS.card_gap)
        self._head()
        self.support_section = self._support_section() if support is not None else None
        self.computer = self._computer_card(db_dir, log_path)
        self.credits = self._credits_card()

    def _support_section(self) -> SupportSection:
        """U-123: "Apoie o projeto", com o coração e os dois caminhos; cada um abre o diálogo dele sobre esta janela."""
        section = SupportSection()
        section.pix_clicked.connect(self._pix)
        section.paypal_clicked.connect(self._paypal)
        self.lay.addWidget(section)
        return section

    def _pix(self) -> None:
        if self.support is not None:
            self.support.open_pay(PIX, self.window(), self.via)

    def _paypal(self) -> None:
        if self.support is not None:
            self.support.open_pay(PAYPAL, self.window(), self.via)

    def _head(self) -> None:
        """A marca, o nome e a versão; a frase; a licença e o autor; o link do repositório (abre no navegador)."""
        brand = QLabel()
        brand.setFixedSize(BRAND_PX, BRAND_PX)
        set_icon(brand, "brand", Tone.ACCENT, BRAND_PX)
        brand.setAccessibleName(f"Marca do {about.APP_NAME}")
        self.name = _label(about.APP_NAME)
        role(self.name, "subtitle")
        self.version = _label(f"versão {about.VERSION}")
        note(self.version)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(self.name)
        names.addWidget(self.version)
        top = QHBoxLayout()
        top.setSpacing(METRICS.card_padding)
        top.addWidget(brand, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addLayout(names, 1)
        self.tagline = _label(about.TAGLINE, wrap=True)
        self.license = _label(f"Gratuito e de código aberto (licença {about.LICENSE}), feito por {about.AUTHOR}.", wrap=True)
        shown = about.REPOSITORY.removeprefix("https://")
        self.repository = QLabel(f'<a href="{about.REPOSITORY}">{shown}</a>')
        self.repository.setTextFormat(Qt.TextFormat.RichText)
        self.repository.setOpenExternalLinks(True)                     # o navegador, pelo Qt (portátil, CP15)
        self.repository.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                                | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.repository.setToolTip("Abre o repositório do projeto no navegador")
        self.lay.addLayout(top)
        for widget in (self.tagline, self.license, self.repository):
            self.lay.addWidget(widget)

    def _computer_card(self, db_dir: Path | None, log_path: Path | None) -> Card:
        """U-121: as versões, o sistema, a pasta do banco e o arquivo de log, e a cópia para suporte (sem os caminhos)."""
        card = Card(COMPUTER)
        form = _form()
        rows = (("Python", self.info["python"]), ("Qt", self.info["qt"]), ("Sistema", self.info["system"]),
                ("Pasta do banco", breakable_path(db_dir) if db_dir is not None else NOT_YET),
                ("Arquivo de log", breakable_path(log_path) if log_path is not None else NOT_YET))
        self.values: dict[str, QLabel] = {}
        for name, value in rows:
            self.values[name] = _label(value, wrap=True, selectable=True)
            form.addRow(name, self.values[name])
        card.content.addLayout(form)
        self.copy_button = QPushButton(COPY_INFO)
        self.copy_button.setToolTip(COPY_TIP)
        self.copy_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)     # no tamanho do texto
        self.copy_button.clicked.connect(self.copy_info)
        self.copied = _label(COPIED)
        note(self.copied, Tone.SUCCESS)
        self.copied.hide()
        row = QHBoxLayout()
        row.setSpacing(METRICS.form_row)
        row.addWidget(self.copy_button)
        row.addWidget(self.copied)
        row.addStretch(1)                                              # o botão à esquerda, com ou sem o "Copiado"
        card.content.addLayout(row)
        self.lay.addWidget(card)
        return card

    def _credits_card(self) -> Card:
        """As bibliotecas abertas e a licença de cada uma (lida da metadata, `about.credits`; sem rede)."""
        card = Card(CREDITS)
        card.content.addWidget(note_label(CREDITS_NOTE))
        form = _form()
        for name, lic in about.credits():
            value = _label(lic, wrap=True)
            note(value)
            form.addRow(name, value)
        card.content.addLayout(form)
        self.lay.addWidget(card)
        return card

    def support_text(self) -> str:
        return about.support_info(self.info["python"], self.info["qt"], self.info["system"])

    def copy_info(self) -> str:
        """"Copiar informações para suporte": as versões e o sistema, sem os caminhos."""
        text = self.support_text()
        copy_rich(text)
        self.copied.show()
        log.info("sobre: informações para suporte copiadas (%s)", self.via)
        return text


def note_label(text: str) -> QLabel:
    label = _label(text, wrap=True)
    note(label)
    return label


class AboutDialog(QDialog):
    """O Sobre numa janela com rolagem e "Fechar" (A-17: cabe na área livre da tela)."""

    def __init__(self, db_dir: Path | None, log_path: Path | None, *, support: Support | None = None, via: str = "sobre",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(*SIZE)
        self.setMinimumSize(*MIN_SIZE)
        self.view = AboutView(db_dir, log_path, support=support, via=via)
        body = QWidget()
        inner = QVBoxLayout(body)
        m = METRICS.margin
        inner.setContentsMargins(m, m, m, m)
        inner.addWidget(self.view)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        footer = QHBoxLayout()
        footer.setContentsMargins(m, METRICS.card_gap, m, m)
        footer.addWidget(buttons)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll, 1)
        lay.addLayout(footer)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fit_to_screen(self)


def open_about(parent: QWidget | None, store: Store | None = None, *, support: Support | None = None, via: str = "bandeja") -> int:
    """O Sobre, modal: pela bandeja (com o banco e o apoio) ou pelo assistente (sem banco, sem log e sem pedido de doação).
    Devolve o resultado."""
    db_dir = store.base_dir if store is not None else None
    dlg = AboutDialog(db_dir, log_file(store), support=support, via=via, parent=parent)
    log.info("sobre aberto (%s)", via)
    try:
        return dlg.exec()
    finally:
        dlg.deleteLater()


__all__ = ["COMPUTER", "COPY_INFO", "CREDITS", "TITLE", "AboutDialog", "AboutView", "computer_info", "log_file", "note_label",
           "open_about"]
