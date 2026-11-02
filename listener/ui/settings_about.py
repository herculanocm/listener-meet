"""F5.12 (U-122): a página "Sobre", a última das Configurações, com o Sobre (`ui/about_dialog.py`) dentro dela. A página
nasce vazia (o título e o subtítulo) e o conteúdo, quando ela aparece (`about_page_shown`, pela troca de página do diálogo,
como a página Prompts): a abertura das Configurações não paga os créditos nem a leitura da metadata (Aceite 4: as
Configurações até +10 %).
L2 (U-123, U-127): a seção "Apoie o projeto" com os dois caminhos e, nela, "Mostrar o convite para apoiar de vez em quando"
(`donate.enabled`, ligado por padrão; CP8: desliga o convite a qualquer momento), gravado pelo Salvar como as outras chaves.
Sem o `Support` do controlador (os testes, a sonda), a página usa um seu: o apoio grava o estado, sem o aviso de
agradecimento.

`AboutPageMixin` é parte do `SettingsDialog`: lê `store` e `support` dele e registra o editor pelo `_add` dele."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFormLayout

from ..model import SettingScope
from .about_dialog import AboutView, log_file
from .donate import APP_NB, ENABLED, Support
from .settings_pages import SettingsPage

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ..store import Store
    from .settings_editors import SettingEditor
    from .theme import Tone

ABOUT_TITLE = "Sobre"
SUBTITLE = f"O que é o {APP_NB}, quem o mantém, como apoiar e as informações para o suporte. Aplica agora."


class AboutPageMixin:
    """A página "Sobre" do `SettingsDialog`."""
    store: Store
    support: Support | None

    def _add(self, page: SettingsPage, form: QFormLayout, key: str, editor: SettingEditor | None = None,
             badge: tuple[str, Tone] | None = None, *, compact: bool = False) -> SettingEditor:
        raise NotImplementedError

    def _about_page(self) -> SettingsPage:
        """Só o título e o subtítulo: o Sobre nasce quando a página aparece (`about_page_shown`)."""
        self.about_page = SettingsPage(ABOUT_TITLE, SUBTITLE, SettingScope.NOW)
        self.about_view: AboutView | None = None
        return self.about_page

    def about_page_shown(self, page: QWidget | None) -> None:
        """A troca de página do diálogo: a primeira vez na página Sobre monta o conteúdo."""
        if page is self.about_page and self.about_view is None:
            self.build_about()

    def build_about(self) -> AboutView:
        """O Sobre com o apoio e, na seção dele, a caixa do convite (U-127)."""
        support = self.support if self.support is not None else Support(self.store, parent=self.about_page)
        view = AboutView(self.store.base_dir, log_file(self.store), support=support, via="configurações")
        self.about_page.content.addWidget(view)
        if view.support_section is not None:
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            view.support_section.content.addLayout(form)
            self._add(self.about_page, form, ENABLED)
        self.about_page.finish()
        self.about_view = view
        return view


__all__ = ["ABOUT_TITLE", "AboutPageMixin"]
