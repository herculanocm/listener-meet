"""As telas da F5.12 no `win_probe shots` (não fazem parte do produto): o Sobre, no topo e rolado até os créditos. A página
Sobre das Configurações sai com as outras páginas (`settings_shots` do `win_probe`) e o link "Sobre o listener-meet" com as
boas-vindas do assistente (`_wizard_shots` do `probe_l4`).

`f512_shots(store, platform, shot, app)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela).
Nada sai da máquina: o banco é o temporário da sonda."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _discard(w: Any) -> None:
    """R-06: a janela da foto é apagada, não só fechada."""
    w.close()
    w.deleteLater()


def _about_shots(store: Any, shot: Callable[..., None], app: Any) -> None:
    from PySide6.QtWidgets import QScrollArea

    from listener.ui.about_dialog import AboutDialog

    dlg = AboutDialog(store.base_dir, store.default_log_path())
    shot(dlg, "sobre")
    scroll = dlg.findChild(QScrollArea)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        shot(dlg, "sobre-creditos")
    _discard(dlg)


def f512_shots(store: Any, platform: Any, shot: Callable[..., None], app: Any) -> None:
    """Todas as telas da F5.12, na ordem: o Sobre."""
    del platform                                          # o L2 usa (o convite sai da captura pela plataforma)
    _about_shots(store, shot, app)


__all__ = ["f512_shots"]
