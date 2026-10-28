"""As telas do L4 da F5.4 no `win_probe shots` (não fazem parte do produto): a reunião com dados em todas as abas, montada
pelo pipeline falso como nos testes (a `FakePlatform` grava 60 s com duas vozes no R e você no L; o `JobRunner` com o
`FakeDeps` transcreve e separa as vozes; a primeira vira "Ana — CFO" e a outra fica "Voz 2"), com a ata lida e em edição,
o executivo, as ações (uma atrasada), os marcadores, a linha do tempo e o chat; uma reunião sem nada para os estados vazios;
o Histórico com a busca, as ações abertas e uma conversa (o LLM falso, `LISTENER_LLM=fake`); a revisão e a ficha; cada passo
do assistente; o Ao vivo com legendas, uma marcada, e o resumo parcial.

`l4_shots(store, platform, shot, tabs_of, app, tmp)` usa o `shot` e o `tabs_of` do `win_probe` (a foto, os `cortados` e os
`pequenos` de cada tela). Nada aqui sai da máquina: o banco é temporário e o LLM é o falso."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MINUTES = """## Contexto

Comitê mensal sobre o **orçamento de 2027** e o fluxo de caixa do fundo.

## Decisões

- Cortar 8 % das despesas de todas as áreas [00:22]
- Fazer a auditoria do contrato com o cedente em abril [00:35]

## Pendências

- Confirmar o prazo do contrato com o cedente

## Próximos passos

1. Ana manda a planilha revisada até sexta
2. Levar a proposta ao comitê de investimentos
"""
EXECUTIVE = "**Resumo:** o comitê aprovou o corte de 8 % e a auditoria em abril; a planilha revisada sai até sexta."
PRE_SUMMARY = "- Orçamento de 2027 e fluxo de caixa apertado\n- Corte de despesas e auditoria do contrato"
SCRIPT = {"R": [(0.5, 6.0, "Bom dia a todos, vamos falar do orçamento de dois mil e vinte e sete."),
                (7.0, 14.0, "O fluxo de caixa do fundo está apertado neste trimestre e precisamos agir."),
                (15.5, 22.0, "Concordo, e o contrato com o cedente vence em março do ano que vem."),
                (23.0, 29.5, "Proponho cortar oito por cento das despesas de todas as áreas."),
                (30.5, 37.0, "Fechado então: corte de oito por cento e auditoria do contrato em abril."),
                (38.0, 44.0, "Eu mando a planilha revisada até sexta para todo mundo."),
                (45.5, 52.0, "Perfeito, eu levo a proposta para o comitê de investimentos."),
                (53.0, 59.0, "Combinado, obrigado a todos e até a próxima reunião.")],
          "L": [(16.0, 20.0, "Faz sentido, o prazo é curto."), (40.0, 43.0, "Pode mandar para mim também.")]}
CAPTIONS = ((2000, "Bom dia a todos, vamos falar do orçamento de 2027.", "Ana — CFO"),
            (9000, "O fluxo de caixa do fundo está apertado neste trimestre.", "Ana — CFO"),
            (16000, "Concordo, e o contrato com o cedente vence em março.", "Bruno — Diretor jurídico"),
            (17500, "Faz sentido, o prazo é curto.", None),
            (24000, "Proponho cortar oito por cento das despesas de todas as áreas.", "Bruno — Diretor jurídico"))


def _settle(app: Any, until: Callable[[], bool], seconds: float = 8.0) -> None:
    deadline = time.perf_counter() + seconds
    while not until() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)


def record_meeting(store: Any, tmp: Path) -> int:
    """60 s gravados pela `FakePlatform` (R: o app a 220 Hz e o sistema a 330 Hz, 15 s cada, duas vezes; L: o microfone a 440
    Hz) e processados pelo `JobRunner` com o `FakeDeps`, como nos testes do pipeline."""
    from listener.capture.recorder import RecordConfig, Recorder, SourceSpec
    from listener.pipeline.runner import JobRunner
    from listener.pipeline.types import JobConfig
    from tests.fake_pipeline import FakeDeps
    from tests.fake_platform import FakePlatform

    platform = FakePlatform(tmp, r_format=(16000, 1), system_format=(16000, 1))
    app_source = SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião")
    cfg = RecordConfig(source=app_source, chunk_seconds=0.5, silence_rms=0.01, l_alert_seconds=1.0, r_alert_seconds=1.0,
                       retry_base_s=0.001, stop_timeout_s=0.2)
    mid = store.create_meeting("Comitê de investimentos", "meeting", None, "Ana, Bruno")
    rec = Recorder(platform, store, mid, cfg, clock=platform.clock, inline_worker=True)
    rec.start()
    for source in (None, SourceSpec("system", label="Sistema"), app_source, SourceSpec("system", label="Sistema")):
        if source is not None:
            rec.switch_source(source)
        platform.pump(15.0)
    rec.stop()
    store.jobs.enqueue(mid)
    job = store.jobs.claim(pid=1)
    config = JobConfig(diar_window_s=2, whisper_window_s=1, whisper_overlap_s=0, diar_overlap_s=1, silence_rms=0.01, threshold=0.6,
                       margin=0.05)
    JobRunner(store, FakeDeps(SCRIPT), job, config=config, heartbeat_thread=False).run()
    return mid


def furnish(store: Any, mid: int) -> int:
    """A primeira voz vira "Ana — CFO" (a outra fica sem nome); a ata, o executivo, o pré-resumo, as ações, os marcadores, o
    chat da reunião, o índice e uma conversa do "Perguntar às reuniões" (LLM falso). Devolve a conversa."""
    from listener.llm.fake import FakeProvider
    from listener.model import ActionStatus, ChatRole, SummaryKind
    from listener.pipeline import review
    from listener.rag import chat_global, index
    from listener.store.actions import ActionInsert
    from listener.store.summaries import SummaryInsert
    from listener.ui.voices import voice_names

    names = voice_names(store, mid)
    first = next(cid for cid, n in names.by_cluster.items() if n == 1)
    review.new_person(store, first, "Ana", role="CFO")
    job = store.segments.current_job_id(mid)
    minutes_id = 0
    for kind, text in ((SummaryKind.MINUTES, MINUTES), (SummaryKind.EXECUTIVE, EXECUTIVE), (SummaryKind.PRE_SUMMARY, PRE_SUMMARY)):
        sid = store.summaries.insert(SummaryInsert(mid, kind, "{}", text, "gemini", "gemini-2.5-flash", "1.0", job, None, "pt-BR"))
        if kind == SummaryKind.MINUTES:
            minutes_id = sid
    ana = next(int(p["id"]) for p in store.people.list_people() if p["name"] == "Ana")
    store.actions.insert_many([
        ActionInsert(mid, minutes_id, "Mandar a planilha revisada do orçamento de 2027 para todas as áreas, com o corte de 8 % "
                     "aplicado e as premissas do fluxo de caixa", None, ana, "2026-10-10", 38_000, None, job),
        ActionInsert(mid, minutes_id, "Levar a proposta ao comitê de investimentos", "Bruno", None, "2026-12-15", 45_500, None, job),
        ActionInsert(mid, minutes_id, "Confirmar o prazo do contrato com o cedente", None, ana, None, None, None, job, ActionStatus.DONE)])
    store.add_marker(mid, 22_000, "decisão: corte de 8 %")
    store.add_marker(mid, 35_000, "auditoria em abril")
    store.chat.add(mid, ChatRole.USER, "Quais foram as decisões?")
    store.chat.add(mid, ChatRole.ASSISTANT, "Cortar 8 % das despesas [00:23] e fazer a auditoria do contrato em abril [00:30].",
                   "gemini", "gemini-2.5-flash")
    os.environ["LISTENER_LLM"] = "fake"                   # nada sai da máquina: o provedor é o falso dos testes
    store.providers.update_provider("gemini", api_key="chave-falsa-da-sonda", fast_model="fake-fast", strong_model="fake-strong")
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    answer = chat_global.run_global_chat(store, None, "Quais decisões saíram sobre o orçamento?")
    return int(answer.thread_id or 0)


def _meeting_shots(store: Any, mid: int, shot: Callable[..., None], tabs_of: Callable[..., None], app: Any) -> None:
    from listener.model import SummaryKind
    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player

    player = Player(store)
    mw = MeetingWindow(store, mid, player=player)
    shot(mw, "reuniao")
    tabs_of(mw, "reuniao")
    ata = mw.llm.text_tabs[str(SummaryKind.MINUTES)]
    mw.show_tab("ata")
    ata.start_edit()                                      # A-15: o Markdown aberto
    app.processEvents()
    shot(mw, "reuniao-ata-em-edicao")
    ata.cancel_edit()
    mw.show_message("Ata copiada (texto e formatação).")  # U-057: o aviso numa faixa
    mw.show_tab("transcricao")
    mw.transcript.view.setCurrentIndex(mw.transcript.model.index(2))
    mw.timeline.set_cursor(25_000)
    shot(mw, "reuniao-transcricao-aviso")
    _discard(mw)
    empty = store.create_meeting("Diretoria", "meeting", None, None)
    store.finish_meeting(empty, 600_000)
    job = store.jobs.enqueue(empty)
    store.jobs.claim(pid=2)
    from listener.model import JobStatus

    store.jobs.finish(job, JobStatus.DONE)
    vazia = MeetingWindow(store, empty, player=player)    # U-059: os estados vazios
    shot(vazia, "reuniao-vazia")
    tabs_of(vazia, "reuniao-vazia")
    _discard(vazia)
    player.shutdown()


def _history_shots(store: Any, thread: int, shot: Callable[..., None], tabs_of: Callable[..., None], app: Any) -> None:
    from listener.ui.history import HistoryWindow

    hw = HistoryWindow(store)
    page = hw.page
    shot(hw, "historico-abrindo")
    _settle(app, lambda: page.list_note.text() != "carregando…")
    shot(hw, "historico")
    tabs_of(hw, "historico")
    page.tabs.setCurrentIndex(2)
    page.thread_id = thread
    page._load_threads(select=thread)
    page._render_thread()
    shot(hw, "historico-conversa")
    page.tabs.setCurrentIndex(0)
    page.search.setText("orçamento")
    page.run_search(with_vector=True)
    _settle(app, lambda: page.result is not None)
    shot(hw, "historico-busca")
    page.shutdown()
    _discard(hw)


def _review_shots(store: Any, mid: int, shot: Callable[..., None], tabs_of: Callable[..., None]) -> None:
    from listener.ui.person import PersonData, PersonDialog
    from listener.ui.review import ReviewDialog

    dlg = ReviewDialog(store, mid)
    shot(dlg, "revisao")
    tabs_of(dlg, "revisao")
    _discard(dlg)
    ficha = PersonDialog(data=PersonData("Ana", "CFO", "Empresa X", "ana@empresa.com.br"), can_delete=True)
    shot(ficha, "ficha-editar")
    _discard(ficha)
    nova = PersonDialog()
    shot(nova, "ficha-nova")
    _discard(nova)


def _wizard_shots(platform: Any, shot: Callable[..., None]) -> None:
    from listener.ui.wizard import FirstRunWizard

    wiz = FirstRunWizard(platform, None)
    ids = wiz.pageIds()
    for n, pid in enumerate(ids, 1):                       # cada passo aberto direto (sem validar os anteriores)
        wiz.setStartId(pid)
        wiz.restart()
        title = wiz.page(pid).title().lower().replace(" ", "-")
        shot(wiz, f"assistente-{n}-{title}")
    _discard(wiz)


def _mark(*_args: Any) -> tuple[bool, str]:
    return True, "marcador criado"


def _refresh() -> str:
    return "pedido"


def _live_shots(store: Any, shot: Callable[..., None]) -> None:
    from listener.model import SummaryKind
    from listener.store.captions import CaptionInsert
    from listener.store.summaries import SummaryInsert
    from listener.ui.live import LiveWindow, caption_key

    live = store.create_meeting("Comitê de investimentos (ao vivo)", "meeting", None, None)
    ids = [store.captions.insert(CaptionInsert(live, "L" if label is None else "R", t0, t0 + 3000, text, None, label, 0.8, "fake:small",
                                               6000)) for t0, text, label in CAPTIONS]
    data = {"summary": "Discutiram o orçamento de 2027 e o fluxo de caixa apertado; há proposta de corte de 8 %.",
            "decisions": [{"text": "Cortar 8 % das despesas", "caption_id": ids[-1], "t_ms": 24000}],
            "actions": [{"text": "Mandar a planilha revisada", "caption_id": None, "t_ms": None, "owner": "Ana"}]}
    sid = store.summaries.insert(SummaryInsert(live, SummaryKind.PARTIAL, json.dumps(data), "md", "fake", "fake-fast", "partial/1.0",
                                               None, None, "pt-BR"))
    store._x("UPDATE summaries SET t_until_ms = 27000 WHERE id = ?", (sid,))
    marked: set[Any] = set()
    win = LiveWindow(store, live, on_mark_caption=_mark, on_mark_item=_mark, on_refresh=_refresh, marked_captions=marked)
    marked.add(caption_key(win.captions[1]))
    win._paint(win.list.item(1), win.captions[1])          # uma legenda que virou marcador, com a bandeira
    win.set_live_state("live")
    shot(win, "ao-vivo")
    _discard(win)


def _discard(w: Any) -> None:
    """R-06: a janela da foto é apagada, não só fechada (sem dono e viva até a derrubada do PySide, sairia fora de ordem)."""
    w.close()
    w.deleteLater()


def l4_shots(store: Any, platform: Any, shot: Callable[..., None], tabs_of: Callable[..., None], app: Any, tmp: Path) -> None:
    """Todas as telas do L4, na ordem: assistente, reunião, Histórico, revisão e ficha, Ao vivo."""
    _wizard_shots(platform, shot)
    mid = record_meeting(store, tmp)
    thread = furnish(store, mid)
    _meeting_shots(store, mid, shot, tabs_of, app)
    _history_shots(store, thread, shot, tabs_of, app)
    _review_shots(store, mid, shot, tabs_of)
    _live_shots(store, shot)


__all__ = ["furnish", "l4_shots", "record_meeting"]
