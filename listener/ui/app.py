"""Ponto de entrada da interface: primeira inicialização, banco, bandeja, controlador."""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import bootstrap
from ..platform import get_platform
from ..store import Store


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication(argv or sys.argv)
    app.setApplicationName("listener-meet")
    app.setQuitOnLastWindowClosed(False)
    platform = get_platform()
    db_dir = bootstrap.read_db_dir(platform.config_dir())
    store: Store | None = None
    if db_dir and (Path(db_dir) / "listener.db").exists():
        try:
            store = Store(db_dir)
        except Exception:
            store = None
    if store is None or store.get_me() is None:
        from .wizard import FirstRunWizard

        wiz = FirstRunWizard(platform, initial_dir=db_dir)
        if wiz.exec() != QDialog.DialogCode.Accepted or wiz.store is None:
            return 0
        store = wiz.store
    from .controller import Controller

    controller = Controller(app, platform, store)
    controller.start()
    app._listener_controller = controller   # mantém a referência viva
    if controller.tray is None:
        controller.open_warmup()
    return app.exec()
