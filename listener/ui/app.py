"""Ponto de entrada da interface: identidade do processo, log, primeira inicialização, banco, bandeja, controlador.
`aboutToQuit` e `commitDataRequest` (logoff, desligamento) param a gravação sem diálogo (M-05). F5.4 (A-09): o tema (fonte,
paleta e estilo) entra depois da tradução e antes do ícone e do assistente; o estilo em Python sai quando o laço de eventos
volta (depois do `aboutToQuit` e da parada de emergência) e em toda saída antecipada (assistente cancelado, banco que não
abre), sempre antes da derrubada do PySide (R-06)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import bootstrap
from ..log import attach_file, get_logger, log_versions, set_crash_notifier, setup
from ..platform import get_platform
from ..store import Store

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from ..platform.base import Platform

log = get_logger("ui.app")


def attach_log(store: Store) -> str | None:
    """Liga o arquivo de log escolhido nas Configurações (ou o padrão na pasta do banco). Devolve o erro, se houver."""
    path = store.get_setting("log.path") or str(store.default_log_path())
    try:
        attach_file(path, store.get_setting("log.level"))
    except Exception as e:
        log.warning("log em arquivo indisponível (%s): %s", path, e)
        return f"{path}: {e}"
    return None


def run(argv: list[str] | None = None, platform: Platform | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    setup()
    platform = platform or get_platform()
    platform.app_init()                       # identidade do processo antes de qualquer janela (F1.1 A11)
    app = QApplication(argv or sys.argv)
    app.setApplicationName("listener-meet")
    app.setQuitOnLastWindowClosed(False)
    from . import theme
    from .common import install_translations

    if install_translations(app) is None:
        log.info("tradução pt-BR do Qt indisponível: os botões padrão ficam em inglês")
    theme.apply(app, platform)                # F5.4 (A-09): antes do ícone e do assistente
    try:
        return _run_app(app, platform)
    finally:
        theme.release(app)                    # A-09 (R-06): o estilo em Python não chega à derrubada do PySide


def _run_app(app: QApplication, platform: Platform) -> int:
    """O ícone, o banco (ou o assistente da primeira vez), o controlador e o laço de eventos."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QMessageBox

    from ..model import AppState
    from .tray import make_icon

    app.setWindowIcon(make_icon(AppState.IDLE))
    db_dir = bootstrap.read_db_dir(platform.config_dir())
    store: Store | None = None
    if db_dir and (Path(db_dir) / "listener.db").exists():
        try:
            store = Store(db_dir)
        except Exception as e:
            log.exception("não consegui abrir o banco em %s", db_dir)
            QMessageBox.critical(None, "listener-meet", f"Não consegui abrir o banco em {db_dir}:\n{e}")
            return 1
    first_run = store is None or store.get_me() is None
    if first_run:
        if store is not None:
            store.close()                     # o assistente abre o seu próprio (M-13)
            store = None
        from .wizard import FirstRunWizard

        wiz = FirstRunWizard(platform, initial_dir=db_dir)
        if wiz.exec() != QDialog.DialogCode.Accepted or wiz.store is None:
            return 0
        store = wiz.store
    if store is None:                         # impossível aqui; mantém o verificador de tipos honesto
        return 1
    log_error = attach_log(store)
    log_versions({"esquema": f"v{store.schema_version()}", "banco": str(store.base_dir)})
    from .controller import Controller

    controller = Controller(app, platform, store, first_run=first_run)   # vive até o app.exec() voltar
    set_crash_notifier(controller.notify_crash)   # F5.3 (A-10): chamado na thread que falhou; o aviso sai na da interface
    app.aboutToQuit.connect(controller.emergency_stop)
    app.commitDataRequest.connect(lambda _manager: controller.emergency_stop(), Qt.ConnectionType.DirectConnection)
    controller.start()
    if log_error:
        controller.notify_error(f"Log em arquivo indisponível: {log_error}")
    if controller.tray is None:
        controller.open_warmup()
    try:
        return app.exec()
    finally:
        set_crash_notifier(None)
