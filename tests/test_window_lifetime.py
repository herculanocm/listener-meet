"""F5.4 L4 (R-06): a reunião, a revisão e o Histórico sem pai ficam vivos no Python até o Qt apagá-los. Com os sinais
ligados a métodos (sem lambda, que prendia a janela pelo C++), essas janelas formam ciclos só do Python (a janela, as abas e
os retornos entre elas); a coleta de ciclos as apagava no meio do caminho, até durante uma pintura ([fato, 28/10] acesso
inválido nos testes). O registro do `keep_until_destroyed` as segura até o `destroyed` e some sozinho depois dele."""
from __future__ import annotations

import gc

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent

pytest.importorskip("pytestqt")


def test_parentless_windows_survive_the_cycle_collector_until_qt_deletes_them(qapp, store):
    from listener.ui import common
    from listener.ui.history import HistoryWindow
    from listener.ui.meeting import MeetingWindow
    from listener.ui.review import ReviewDialog

    mid = store.create_meeting("Comitê", None, None, None)
    windows = [MeetingWindow(store, mid), ReviewDialog(store, mid), HistoryWindow(store)]
    keys = [id(w) for w in windows]
    pages = [w.page for w in windows if isinstance(w, HistoryWindow)]
    del windows
    gc.collect()                                                      # sem o registro, a coleta apagava as três aqui
    alive = [common._ALIVE[k] for k in keys]
    assert all(shiboken6.isValid(w) for w in alive)
    for page in pages:
        page.shutdown()
    for w in alive:
        w.deleteLater()
    del alive
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(k in common._ALIVE for k in keys)                 # o `destroyed` tira do registro
