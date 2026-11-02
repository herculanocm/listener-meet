"""F5.12 (U-122): a página "Sobre", a última das Configurações, com o Sobre (`ui/about_dialog.py`) dentro dela. A página
nasce vazia (o título e o subtítulo) e o conteúdo, quando ela aparece (`about_page_shown`, pela troca de página do diálogo,
como a página Prompts): a abertura das Configurações não paga os créditos nem a leitura da metadata (Aceite 4: as
Configurações até +10 %).

`AboutPageMixin` é parte do `SettingsDialog`: lê `store` dele."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .. import about
from ..model import SettingScope
from .about_dialog import AboutView, log_file
from .settings_pages import SettingsPage

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ..store import Store

ABOUT_TITLE = "Sobre"
SUBTITLE = f"O que é o {about.APP_NAME}, quem o mantém e as informações para o suporte."


class AboutPageMixin:
    """A página "Sobre" do `SettingsDialog`."""
    store: Store

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
        view = AboutView(self.store.base_dir, log_file(self.store), via="configurações")
        self.about_page.content.addWidget(view)
        self.about_page.finish()
        self.about_view = view
        return view


__all__ = ["ABOUT_TITLE", "AboutPageMixin"]
