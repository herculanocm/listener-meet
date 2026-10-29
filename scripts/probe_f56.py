"""As telas da F5.6 no `win_probe shots` (não fazem parte do produto). L3 (U-093, A-51): o Histórico com três reuniões
selecionadas e o "Excluir…" sob a lista, o menu de contexto aberto e a confirmação com um título longo e com 12 reuniões (dez
listadas, "e mais 1" e, à parte, uma que fica de fora porque está gravando; revisão do L3, R-11: o título e o botão contam
as 11 que saem) e a de uma reunião só, gravando (o "Excluir" desabilitado). Nada é excluído: o `on_delete` da sonda só guarda
o pedido.

`f56_shots(store, shot, app)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela). O banco é o
temporário da sonda."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

LONG_TITLE = ("Comitê trimestral de investimentos, riscos e compliance do fundo de recebíveis com a diretoria executiva, o "
              "conselho consultivo e os gestores convidados")
TITLES = ("Planejamento 2027", "Ritual semanal do time de dados", "Comitê de crédito", "Revisão do orçamento",
          "Alinhamento com o cedente", "Daily", "Retrospectiva do trimestre", "Entrevista de candidato", "Diretoria",
          "Treinamento de LGPD", "Fechamento do mês")


def _settle(app: Any, until: Callable[[], bool], seconds: float = 8.0) -> None:
    deadline = time.perf_counter() + seconds
    while not until() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)


def _meetings(store: Any) -> list[int]:
    """As 12 reuniões da confirmação (a primeira com o título longo), já terminadas, de durações variadas."""
    out = []
    for i, title in enumerate((LONG_TITLE, *TITLES)):
        mid = store.create_meeting(title, "teams", None, None)
        store.finish_meeting(mid, (25 + 7 * i) * 60_000)
        out.append(mid)
    return out


def _discard(w: Any) -> None:
    w.close()
    w.deleteLater()


def _asked(_lines: Any, _view: Any) -> None:
    """O `on_delete` da sonda: a foto não exclui nada."""


def f56_shots(store: Any, shot: Callable[..., None], app: Any) -> None:
    """O Histórico com a seleção e o botão, o menu de contexto aberto e as duas confirmações."""
    from PySide6.QtCore import QItemSelectionModel

    from listener.pipeline import delete
    from listener.ui.history import HistoryWindow
    from listener.ui.history_delete import DeleteConfirm

    mids = _meetings(store)
    hw = HistoryWindow(store, on_delete=_asked)
    page = hw.page
    shot(hw, "f56-historico-abrindo")
    _settle(app, lambda: page.list_note.text() != "carregando…")
    sel = page.table.selectionModel()
    for row in (0, 2, 3):
        sel.select(page.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    app.processEvents()
    shot(hw, "f56-historico-selecao")
    menu = page.deleting.menu_for(page.table.visualRect(page.model.index(0, 1)).center())
    if menu is not None:
        shot(menu, "f56-historico-menu-excluir")
        _discard(menu)
    lines = {line.meeting_id: line for line in page.model.lines}
    one = DeleteConfirm([lines[mids[0]]], parent=hw)
    shot(one, "f56-confirmacao-titulo-longo")
    _discard(one)
    many = DeleteConfirm([lines[m] for m in mids], {mids[5]: delete.RECORDING_TEXT}, parent=hw)     # R-11: 11 saem, 1 de fora
    shot(many, "f56-confirmacao-12-reunioes-uma-gravando")
    _discard(many)
    none = DeleteConfirm([lines[mids[1]]], {mids[1]: delete.RECORDING_TEXT}, parent=hw)            # todas de fora: desabilitado
    shot(none, "f56-confirmacao-so-gravando")
    _discard(none)
    page.shutdown()
    _discard(hw)


__all__ = ["f56_shots"]
