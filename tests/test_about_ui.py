"""F5.12 L1 (U-121, U-122): o Sobre na tela (pytest-qt, offscreen, com a plataforma falsa). O conteúdo (a marca, o nome e a
versão, a frase, a licença e o autor, o repositório, "Este computador" e os créditos), a cópia para suporte sem caminho, o
sistema pelo Qt (nunca pelo WMI da biblioteca padrão) e as três entradas: a bandeja, a página Sobre das Configurações (que
nasce ao aparecer) e o link nas boas-vindas do assistente."""
from __future__ import annotations

import logging
import platform as stdlib_platform
from typing import Any

import pytest

from listener import about

pytest.importorskip("pytestqt")


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """O `exec` do Sobre não bloqueia: guarda o diálogo e volta já (o conteúdo fica vivo para o teste olhar)."""
    from listener.ui.about_dialog import AboutDialog

    seen: list[Any] = []

    def fake_exec(self: Any) -> int:
        seen.append(self)
        return 0

    monkeypatch.setattr(AboutDialog, "exec", fake_exec)
    monkeypatch.setattr(AboutDialog, "deleteLater", lambda _self: None)
    return seen


def _texts(widget: Any) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QGroupBox, QLabel

    out = [w.text() for w in widget.findChildren(QLabel)] + [b.text() for b in widget.findChildren(QAbstractButton)]
    return out + [g.title() for g in widget.findChildren(QGroupBox)]


def test_the_about_shows_the_brand_the_project_this_computer_and_the_credits(qtbot, store, monkeypatch):
    from PySide6.QtCore import QSysInfo, qVersion

    from listener.ui.about_dialog import COMPUTER, CREDITS, TITLE, AboutDialog

    for name in ("platform", "machine", "version"):                       # o WMI travou a suíte: nada disso aqui
        monkeypatch.setattr(stdlib_platform, name, lambda *_a, **_k: pytest.fail("platform da biblioteca padrão"))
    log_path = store.base_dir / "logs" / "listener.log"
    dlg = AboutDialog(store.base_dir, log_path)
    qtbot.addWidget(dlg)
    view = dlg.view
    assert dlg.windowTitle() == TITLE == "Sobre o listener-meet"
    assert (view.name.text(), view.version.text()) == ("listener-meet", f"versão {about.VERSION}")
    assert view.tagline.text() == "Grave qualquer reunião, saiba quem falou e ache qualquer decisão, tudo no seu computador."
    assert view.license.text() == "Gratuito e de código aberto (licença MIT), feito por Herculano."
    assert about.REPOSITORY in view.repository.text() and view.repository.openExternalLinks()
    assert (view.computer.title(), view.credits.title()) == (COMPUTER, CREDITS)
    values = {k: v.text().replace("\u200b", "") for k, v in view.values.items()}
    assert values["Qt"] == qVersion() and values["Sistema"] == QSysInfo.prettyProductName() and values["Python"].count(".") == 2
    assert values["Pasta do banco"] == str(store.base_dir) and values["Arquivo de log"] == str(log_path)
    texts = _texts(dlg)
    assert all(any(name == t for t in texts) for name, _lic in about.credits())          # cada biblioteca, com a licença
    assert "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only" in texts and "Apoie o projeto" not in texts   # o apoio vem no L2


def test_copy_for_support_takes_the_versions_and_the_system_without_any_path(qtbot, store, caplog):
    from PySide6.QtGui import QGuiApplication

    from listener.ui.about_dialog import AboutView

    view = AboutView(store.base_dir, store.base_dir / "logs" / "listener.log", via="teste")
    qtbot.addWidget(view)
    assert view.copied.isHidden()
    with caplog.at_level(logging.INFO, logger="listener.ui.about"):
        view.copy_button.click()
    copied = QGuiApplication.clipboard().text()
    assert copied == view.support_text() and copied.startswith(f"listener-meet {about.VERSION}\nPython ")
    assert str(store.base_dir) not in copied and "listener.log" not in copied and not view.copied.isHidden()
    assert "informações para suporte copiadas (teste)" in caplog.text and str(store.base_dir) not in caplog.text


def test_the_tray_opens_the_about_with_the_database_folder(qtbot, qapp, platform, store, monkeypatch, opened):
    from listener.ui import controller as ctl
    from listener.ui.tray import ABOUT_ITEM, TrayIcon

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    tray = TrayIcon(c)
    names = [a.text() for a in tray.menu.actions()]
    assert ABOUT_ITEM == "Sobre o listener-meet…" and names.index(ABOUT_ITEM) > names.index("Configurações…")
    assert names.index(ABOUT_ITEM) < names.index("Sair")
    tray.act_about.trigger()
    assert len(opened) == 1 and opened[0].view.values["Pasta do banco"].text().replace("\u200b", "") == str(store.base_dir)
    for dlg in opened:
        qtbot.addWidget(dlg)
    tray.hide()
    tray.deleteLater()


def test_the_about_page_is_the_last_of_the_settings_and_is_born_when_shown(qtbot, store, platform):
    from listener.ui.settings_about import ABOUT_TITLE
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    assert list(dlg.pages)[-1] == ABOUT_TITLE == "Sobre" and dlg.about_view is None       # Aceite 4: nasce ao aparecer
    dlg.nav.setCurrentRow(dlg.nav.count() - 1)
    view = dlg.about_view
    assert view is not None and dlg.page(ABOUT_TITLE).isAncestorOf(view)
    assert view.values["Pasta do banco"].text().replace("\u200b", "") == str(store.base_dir)
    dlg.nav.setCurrentRow(0)
    dlg.nav.setCurrentRow(dlg.nav.count() - 1)
    assert dlg.about_view is view                                                         # uma vez só


def test_the_wizard_welcome_has_the_about_link_without_a_database_yet(qtbot, tmp_path, opened):
    from listener.ui.about_dialog import NOT_YET
    from listener.ui.wizard import FirstRunWizard
    from tests.fake_platform import FakePlatform

    wiz = FirstRunWizard(FakePlatform(tmp_path), tmp_path / "db")
    qtbot.addWidget(wiz)
    link = wiz.intro_page.about_link
    assert link.accessibleName() == "Sobre o listener-meet" and wiz.intro_page.isAncestorOf(link)
    link.linkActivated.emit("link")                                       # o clique no link
    assert len(opened) == 1
    dlg = opened[0]                                                        # filho da página: sai com o assistente
    assert dlg.parent() is wiz.intro_page
    assert dlg.view.values["Pasta do banco"].text() == NOT_YET == dlg.view.values["Arquivo de log"].text()
    assert "Apoie o projeto" not in _texts(dlg)                            # no assistente, só o Sobre (pergunta 4)
