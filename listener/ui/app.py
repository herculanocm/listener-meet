"""Ponto de entrada da interface: identidade do processo, log, primeira inicialização, banco, bandeja, controlador."""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import bootstrap
from ..log import attach_file, get_logger, setup
from ..platform import get_platform
from ..store import Store

log = get_logger("ui.app")


def attach_log(store: Store) -> str | None:
    """Liga o arquivo de log escolhido nas Configurações (ou o padrão na pasta do banco). Devolve o erro, se houver."""
    path = store.get_setting("log.path") or str(store.default_log_path())
    try:
        attach_file(path, store.get_setting("log.level"))
        return None
    except Exception as e:
        log.warning("log em arquivo indisponível (%s): %s", path, e)
        return f"{path}: {e}"


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    setup()
    platform = get_platform()
    platform.app_init()                       # identidade do processo antes de qualquer janela (F1.1 A11)
    app = QApplication(argv or sys.argv)
    app.setApplicationName("listener-meet")
    app.setQuitOnLastWindowClosed(False)
    from .tray import STATE_COLORS, make_icon

    app.setWindowIcon(make_icon(STATE_COLORS["idle"]))
    db_dir = bootstrap.read_db_dir(platform.config_dir())
    store: Store | None = None
    if db_dir and (Path(db_dir) / "listener.db").exists():
        try:
            store = Store(db_dir)
        except Exception as e:
            log.error("não consegui abrir o banco em %s: %s", db_dir, e)
            QMessageBox.critical(None, "listener-meet", f"Não consegui abrir o banco em {db_dir}:\n{e}")
            return 1
    first_run = store is None or store.get_me() is None
    if first_run:
        from .wizard import FirstRunWizard

        wiz = FirstRunWizard(platform, initial_dir=db_dir)
        if wiz.exec() != QDialog.DialogCode.Accepted or wiz.store is None:
            return 0
        store = wiz.store
    log_error = attach_log(store)
    log.info("listener-meet iniciando; banco em %s (esquema v%d)", store.base_dir, store.schema_version())
    from .controller import Controller

    controller = Controller(app, platform, store, first_run=first_run)
    controller.start()
    if log_error and controller.tray:
        controller.tray.showMessage("listener-meet", f"Log em arquivo indisponível: {log_error}")
    app._listener_controller = controller   # mantém a referência viva
    if controller.tray is None:
        controller.open_warmup()
    return app.exec()
