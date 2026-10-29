"""F5.4 L4 (R-06): a reunião, a revisão e o Histórico sem pai ficam vivos no Python até o Qt apagá-los. Com os sinais
ligados a métodos (sem lambda, que prendia a janela pelo C++), essas janelas formam ciclos só do Python (a janela, as abas e
os retornos entre elas); a coleta de ciclos as apagava no meio do caminho, até durante uma pintura ([fato, 28/10] acesso
inválido nos testes). O registro do `keep_until_destroyed` as segura até o `destroyed` e some sozinho depois dele.
Revisão da F5.4: nenhum sinal em `listener/` vai para um lambda com `self` (o padrão da queda 0xC0000409 da F5.3)."""
from __future__ import annotations

import ast
import gc
from pathlib import Path

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


def _self_lambdas(source: str) -> list[int]:
    """As linhas de `.connect(lambda …)` cujo lambda usa `self`: a conexão, no C++, segura o lambda e por ele o widget."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"
            and node.args and isinstance(node.args[0], ast.Lambda)
            and any(isinstance(n, ast.Name) and n.id == "self" for n in ast.walk(node.args[0]))]


def test_no_signal_goes_to_a_lambda_that_holds_self():
    """F5.3 (Medições, 28/10): um lambda com `self` numa conexão prendia o widget pelo C++ e o app caía na saída
    (0xC0000409); os sinais vão para métodos. A revisão da F5.4 achou os dois que sobravam (a página de chaves do assistente)."""
    root = Path(__file__).resolve().parent.parent / "listener"
    found = [f"{path.relative_to(root).as_posix()}:{line}" for path in sorted(root.rglob("*.py"))
             for line in _self_lambdas(path.read_text(encoding="utf-8"))]
    assert found == []
    assert _self_lambdas("b.clicked.connect(lambda: self.go())\nb.clicked.connect(self.go)\nq.connect(lambda: stop())\n") == [1]
