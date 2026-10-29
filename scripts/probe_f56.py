"""As telas da F5.6 no `win_probe shots` (não fazem parte do produto). L3 (U-093, A-51): o Histórico com três reuniões
selecionadas e o "Excluir…" sob a lista, o menu de contexto aberto e a confirmação com um título longo e com 12 reuniões (dez
listadas, "e mais 1" e, à parte, uma que fica de fora porque está gravando; revisão do L3, R-11: o título e o botão contam
as 11 que saem) e a de uma reunião só, gravando (o "Excluir" desabilitado). Nada é excluído: o `on_delete` da sonda só guarda
o pedido.

L1b (U-089 a U-091, A-51): o resumo da gravação com as caixas "Gerar ata e ações" e "Gerar resumo" (com a chave falsa da sonda
e sem chave), o popup do pedido nas quatro situações (vozes pendentes, todas com nome, reunião revisada, antes da transcrição)
e o do resumo com a ata junto e o núcleo pendente, a barra da reunião nos seis estados (e sem chave), a troca de idioma com as
caixas e o "Antes de gravar" com o "Resumo parcial". Nada é pedido à LLM. Revisão do L1b: o popup com o reprocessamento na
fila (R-04) e com a ata adiada que sai de novo (o desvio 9), e a reunião sem chave no tamanho mínimo (R-08: a nota da barra
quebra a linha em vez de cortar).

`f56_shots(store, shot, app, platform)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela). O
banco é o temporário da sonda."""
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


def f56_shots(store: Any, shot: Callable[..., None], app: Any, platform: Any = None) -> None:
    """O Histórico com a seleção e o botão, o menu de contexto aberto e as duas confirmações; depois as telas do L1b."""
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
    l1b_shots(store, shot, platform)


# ---------------------------------------------------------------- L1b (U-089 a U-091, A-51)
PROBE_KEY = "chave-falsa-da-sonda"          # a do `l4_shots`: nada sai da máquina (o banco é o temporário da sonda)
COST = 0.0412


def _questions() -> list[tuple[str, Any]]:
    """As quatro situações do popup (e o resumo com a ata junto e o núcleo pendente, o reprocessamento na fila e a ata
    adiada que sai de novo), montadas pelos textos do app."""
    from listener.pipeline.llm_requests import Estimate, Voices
    from listener.ui.llm_ask import Facts, compose

    minutes = ("minutes",)
    by_text = Estimate(COST, 12_000, False, minutes)
    cases = (("vozes-pendentes", Facts(minutes, Voices(2, True, False), False, False, by_text)),
             ("todas-com-nome", Facts(minutes, Voices(0, True, False), False, False, by_text)),
             ("revisada", Facts(minutes, Voices(1, True, False), False, True, by_text)),
             ("antes-da-transcricao", Facts(minutes, Voices(0, False, False), True, False, Estimate(0.0861, 15_000, True, minutes))),
             ("resumo-com-a-ata-e-o-nucleo", Facts(("executive", "pre_summary"), Voices(2, True, False), False, False,
                                                  Estimate(0.1294, 12_000, False, ("minutes", "executive", "pre_summary"),
                                                           ("identify", "title", "revise")))),
             ("reprocessamento-na-fila", Facts(minutes, Voices(2, True, False), True, False, by_text, rerun=True)),
             ("resumo-com-a-ata-adiada", Facts(("executive", "pre_summary"), Voices(2, True, False), False, False,
                                              Estimate(0.0733, 12_000, False, ("minutes", "executive", "pre_summary")),
                                              deferred_minutes=True)))
    return [(name, compose(facts)) for name, facts in cases]


def _bar_states() -> list[tuple[str, dict[Any, Any], str, dict[Any, int]]]:
    """Os seis estados, dois por foto (ata, resumo), e a barra sem chave."""
    from listener.pipeline.llm_requests import RequestState, Species, State
    from listener.ui.llm_ask import NO_KEY_TIP

    m, s = Species.MINUTES, Species.SUMMARY
    when = "2026-10-29T15:39:00.000-03:00"
    return [("nao-gerado-e-pedido", {m: RequestState(State.REQUESTED), s: RequestState(State.NONE)}, "", {m: 2}),
            ("gerando-e-gerado", {m: RequestState(State.RUNNING), s: RequestState(State.DONE, when)}, "", {}),
            ("desatualizado-e-falhou", {m: RequestState(State.STALE, when, ("nomes",)),
                                        s: RequestState(State.FAILED, error="Resumo executivo falhou: tempo esgotado")}, "", {}),
            ("sem-chave", {m: RequestState(State.NONE), s: RequestState(State.NONE)}, NO_KEY_TIP, {})]


def _keyless(store: Any, on: bool) -> None:
    store.providers.update_provider("gemini", api_key="" if on else PROBE_KEY)


def l1b_shots(store: Any, shot: Callable[..., None], platform: Any = None) -> None:
    """As caixas ao parar (com e sem chave), o popup nas situações, os estados da barra, o warm-up com o "Resumo parcial" e a
    troca de idioma com as caixas. Nada é pedido: os diálogos só são fotografados e descartados."""
    from listener.pipeline.llm_requests import Species
    from listener.ui import llm_ask
    from listener.ui.llm_request import RequestDialog
    from listener.ui.llm_request_bar import RequestBar
    from listener.ui.meeting_language import ChangeConfirm, change_question
    from listener.ui.summary import SummaryDialog

    mid = store.create_meeting("Comitê de investimentos", "teams", None, None)
    store.finish_meeting(mid, 47 * 60_000)
    _keyless(store, False)
    llm_ask.save_choice(store, {Species.MINUTES})                    # a última escolha: a ata marcada
    for name, keyless in (("f56-resumo-gravacao-caixas", False), ("f56-resumo-gravacao-sem-chave", True)):
        _keyless(store, keyless)
        su = SummaryDialog(store, mid, {"duration_s": 47 * 60.0, "warnings": []})
        shot(su, name)
        _discard(su)
    _keyless(store, False)
    for name, question in _questions():
        dlg = RequestDialog(question)
        shot(dlg, f"f56-popup-{name}")
        _discard(dlg)
    for name, states, off, waiting in _bar_states():
        bar = RequestBar(_asked_species)
        bar.show_states(states, off, waiting)
        bar.resize(760, bar.heightForWidth(760) if bar.hasHeightForWidth() else bar.sizeHint().height())
        shot(bar, f"f56-barra-{name}")
        _discard(bar)
    change = ChangeConfirm(change_question(store, mid, "en"), "Reprocessar em Inglês (en)", store)
    shot(change, "f56-idioma-com-caixas")
    _discard(change)
    llm_ask.save_choice(store, ())
    _narrow_shot(store, shot, mid)
    if platform is not None:
        _warmup_shot(store, shot, platform)


def _narrow_shot(store: Any, shot: Callable[..., None], mid: int) -> None:
    """R-08: a reunião sem chave no tamanho mínimo (640 por 420), com a nota da barra na linha de baixo."""
    from listener.ui.meeting import MIN_SIZE, MeetingWindow

    _keyless(store, True)
    try:
        win = MeetingWindow(store, mid)
        win.resize(*MIN_SIZE)
        shot(win, "f56-reuniao-estreita-sem-chave")
        _discard(win)
    finally:
        _keyless(store, False)


def _warmup_shot(store: Any, shot: Callable[..., None], platform: Any) -> None:
    """O "Antes de gravar" com a legenda ligada e o "Resumo parcial" marcado (U-091), na plataforma da sonda."""
    from listener.ui.warmup import WarmupDialog

    before = store.get_setting("live.enabled")
    store.set_setting("live.enabled", True)
    try:
        dlg = WarmupDialog(platform, store)
        dlg.partial_check.setChecked(True)
        shot(dlg, "f56-warmup-resumo-parcial")
        dlg.reject()
        _discard(dlg)
    finally:
        store.set_setting("live.enabled", before)


def _asked_species(_species: Any) -> None:
    """O `on_ask` da barra na sonda: a foto não pede nada."""


__all__ = ["f56_shots", "l1b_shots"]
