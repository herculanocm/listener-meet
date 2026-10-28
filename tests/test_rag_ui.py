"""F5 L4, telas (pytest-qt, offscreen; só abrir, fechar e um clique por tela, a lógica é testada sem Qt): a reunião aberta
(cabeçalho, abas, abrir num ponto com a fala casada e a posição pedida ao player, marcadores, título que reindexa, exportar
com texto e HTML e escrita atômica, fechar para o som), o Histórico (lista carregada numa `Task` e filtrada por pessoa e
período, busca com os trechos agrupados e o destaque escapado contra `<script>`, clique que abre e toca, resultado velho
descartado, Esc, selo "busca por palavras" sem jargão; ações abertas com "Marcar feita" e a evidência; chat global com o
fake, avaliação U-027, citação que abre a reunião, U-029 e os avisos) e o controlador (atalho U-028, "Histórico…" na
bandeja, clique no balão, recarga da reunião aberta quando o índice termina, Q9 e a saída esperando player e tarefas).
Revisão do L4 e do L5: o warm-up e a fonte "sistema inteiro" pausam o player (R7-01); a saída com a rede presa cancela de
verdade, dentro do processo e num processo à parte com código 0 (R7-03); a recarga do chat da reunião (R7-05); o balão sem
reunião (R7-06); títulos e nomes com HTML ficam texto (R7-07). Revisão do R7: o loopback do sistema nunca abre com o player
tocando, também na troca automática (R8-01, com o estado confirmado pelo motor); a dica mostra o texto como está (R8-02); a
saída com o glossário do warm-up em voo (R8-04)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.capture.recorder import SourceSpec
from listener.model import ActionStatus, AppState, ChatRole, JobKind, JobStatus, SummaryKind
from listener.rag import chat_global, citations, export, index, search
from listener.store.actions import ActionInsert
from listener.store.segments import SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.fake_player import FakeOutputs, RealtimeOutput
from tests.test_rag_chat import QUESTION, _llm_ready, _slow_network
from tests.test_rag_index import _meeting, _people, _pipeline_job
from tests.test_rag_search import _other_meeting

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento                              # D-050: telas e os scripts de saída em subprocesso


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    yield
    search.clear_caches()


@pytest.fixture
def cleanup():
    """Páginas do Histórico e players criados no teste: param e esperam as threads no fim, mesmo se uma asserção falhar
    (uma QThread viva quando o banco fecha derruba o processo)."""
    items: list[object] = []
    yield items.append
    for item in reversed(items):
        item.shutdown()  # type: ignore[attr-defined]


def _audio(store, mid: int, t0_ms: int, seconds: int = 10) -> None:
    """Um pedaço de áudio por canal a partir de `t0_ms` (o player precisa de áudio para seguir tocando)."""
    for ch, value in (("L", 0.2), ("R", 0.6)):
        x = np.full(16000 * seconds, value, np.float32)
        store.insert_chunk(mid, ch, 0, t0_ms, len(x), 16000, 0, encode_flac(x, 16000))


def _realtime_player(controller: Any) -> list[RealtimeOutput]:
    """O player do controlador com fluxos que tocam em tempo real: o `write` volta sozinho, então o motor trata cada comando
    e confirma a pausa sem depender do prazo de um fake que prende o `write` (revisão R8)."""
    outs: list[RealtimeOutput] = []

    def factory() -> RealtimeOutput:
        outs.append(RealtimeOutput())
        return outs[-1]

    controller.player.engine.factory = factory
    return outs


def _indexed(store) -> tuple[int, int, dict[str, int]]:
    who = _people(store) | {"carla": store.people.create_person("Carla", role="Gerente")}
    a, _job = _meeting(store, who)
    b = _other_meeting(store, who)
    _llm_ready(store)
    from listener.llm.fake import FakeProvider

    for mid in (a, b):
        index.sync_text(store, mid)
        index.fill_vectors(store, mid, provider=FakeProvider())
    return a, b, who


def test_meeting_window_opens_at_a_point_exports_and_stops_its_sound_on_close(qtbot, store, tmp_path, monkeypatch, cleanup):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QMessageBox

    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import ERROR, PLAYING, STOPPED, Player

    who = _people(store)
    mid, job = _meeting(store, who)
    store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar a auditoria [01:10]\n", "gemini", "m",
                                         "minutes/1.0", job, None, "pt-BR"))
    store.add_marker(mid, 70_000, "auditoria")
    permits = threading.Semaphore(0)
    outs = FakeOutputs(permits=permits)
    player = Player(store, outs)
    cleanup(player)
    edited: list[tuple[int, str]] = []
    reviewed: list[int] = []
    win = MeetingWindow(store, mid, player=player, on_review=reviewed.append, on_edited=lambda m, r: edited.append((m, r)))
    qtbot.addWidget(win)
    cleanup(win)
    win.show()
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Ata", "Resumo", "Ações", "Transcrição", "Marcadores",
                                                                       "Linha do tempo", "Chat"]
    assert "5 min" in win.info.text() and "Participantes: Ana — CFO, Bia" in win.info.text()
    assert win.windowTitle().startswith("Orçamento 2027") and win.bar is not None and win.bar.btn_play.text() == "Tocar"
    # abrir num ponto (U-024): a fala de início mais perto, selecionada; toca do começo dela (sem palavras: início - 150 ms)
    row = win.open_at(95_000, play=True)
    assert row is not None and row.t_start_ms == 100_000 and win.tabs.currentWidget() is win.transcript
    assert win.transcript.current_row() == row and player.requests[-1].t_ms == 99_850 and player.requests[-1].meeting_id == mid
    qtbot.waitUntil(lambda: "não tem áudio gravado" in win.bar.note.text(), timeout=3000)       # a barra lê a cada 100 ms
    assert player.state().state == ERROR and win.bar.time.text() == "--:-- / --:--"
    win.markers.list.itemClicked.emit(win.markers.list.item(0))                      # o marcador toca do ponto menos 150 ms
    assert player.requests[-1].t_ms == 69_850
    # U-029 numa fala: "<fala>" — Nome — Cargo, título (dd/mm/aaaa) [mm:ss]
    plain = win.transcript.copy_quote(row)
    day = export.fmt_date(store.get_meeting(mid)["started_at"])
    assert plain == f'"Concordo, e o contrato com o cedente vence." — Bia, Orçamento 2027 ({day}) [01:40]'
    assert QGuiApplication.clipboard().mimeData().text() == plain and QGuiApplication.clipboard().mimeData().hasHtml()
    # título no cabeçalho: grava com revisão e reindexa a busca
    title = win.llm.title_tab
    title.title.setText("Orçamento revisto")
    title.title.returnPressed.emit()
    assert store.get_meeting(mid)["title"] == "Orçamento revisto" and edited == [(mid, "edit")]
    assert win.windowTitle().startswith("Orçamento revisto")
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'meeting' AND field = 'title'")[0][0] == 1
    # exportar: área de transferência com texto e HTML; .md com o nome padrão e escrita atômica; erro vira aviso
    md = win.copy_minutes()
    clip = QGuiApplication.clipboard().mimeData()
    assert clip.text() == md and "### Decisões" in md and clip.hasHtml() and "Aprovar a auditoria" in clip.html()
    assert "[01:40] Bia: Concordo" in win.copy_transcript()
    out = win.save_markdown(str(tmp_path / "ata"))
    assert out == tmp_path / "ata.md" and out.read_text(encoding="utf-8") == export.meeting_markdown(export.gather(store, mid))
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(str(a[2]))))
    assert win.save_markdown(str(tmp_path / "nao-existe" / "ata.md")) is None and "Não consegui salvar" in warned[0]
    win.btn_review.click()
    assert reviewed == [mid]
    # fechar a reunião para o som dela (comportamento 8)
    _audio(store, mid, 95_000)
    win.open_at(100_000, play=True)
    qtbot.waitUntil(lambda: player.state().state == PLAYING and player.engine.alive, timeout=3000)
    win.close()
    permits.release(1000)
    qtbot.waitUntil(lambda: player.state().state == STOPPED and not player.engine.alive, timeout=3000)
    assert outs.opened[-1].calls[-1] == "close"


def test_history_lists_filters_searches_and_opens_at_the_hit(qtbot, store, monkeypatch, cleanup):
    from PySide6.QtCore import QPoint, Qt, QUrl
    from PySide6.QtGui import QGuiApplication

    from listener.ui.history import HistoryPage, hit_link, results_html

    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, b, who = _indexed(store)
    evil = store.create_meeting("<b>Títulão</b>", None, None, None)
    store.finish_meeting(evil, 60_000)
    job = _pipeline_job(store, evil)
    store.segments.insert_segments([SegmentInsert(evil, job, "R", 0, 5000, "a auditoria <script>alert(1)</script> terminou", "pyannote",
                                                  "final", person_id=who["carla"])])
    index.sync_text(store, evil)
    old = store.create_meeting("Antiga", None, None, None)
    store._x("UPDATE meetings SET started_at = '2020-01-02T10:00:00.000-03:00' WHERE id = ?", (old,))
    opened: list[tuple[object, ...]] = []
    page = HistoryPage(store, on_open=lambda *args: opened.append(args))
    qtbot.addWidget(page)
    cleanup(page)
    page.show()
    qtbot.waitUntil(lambda: page.model.rowCount() == 4, timeout=5000)
    lines = {line.meeting_id: line for line in page.model.lines}
    assert lines[a].participants == ("Ana", "Bia") and lines[a].title == "Orçamento 2027" and lines[a].texts()[2] == "5 min"
    assert "Índice: 3 de 3 reuniões processadas" in page.coverage.text()
    page.period.setCurrentIndex(page.period.findData("30"))                         # período lembrado em `ui.history_filters`
    qtbot.waitUntil(lambda: page.model.rowCount() == 3, timeout=5000)
    page.person.setCurrentIndex(page.person.findData(who["carla"]))
    qtbot.waitUntil(lambda: page.model.rowCount() == 2, timeout=5000)
    assert {line.meeting_id for line in page.model.lines} == {b, evil}
    assert '"person": ' in store.get_setting("ui.history_filters") and '"period": "30"' in store.get_setting("ui.history_filters")
    page.table.activated.emit(page.model.index(0, 0))
    assert opened[-1] == (page.model.lines[0].meeting_id, None, None, False)
    page.person.setCurrentIndex(0)
    # busca (U-024): trechos agrupados por reunião, destaque escapado; o clique abre no ponto e toca
    page.search.setText("auditoria")
    page._search_now()
    qtbot.waitUntil(lambda: page.result is not None, timeout=5000)
    res = page.result
    assert res.hybrid and page.stack.currentWidget() is page.results and "busca híbrida" in page.badge.text()
    body = results_html(res)
    assert "<script>" not in body and "&lt;script&gt;" in body and "&lt;b&gt;Títulão&lt;/b&gt;" in body and "<b>auditoria</b>" in body
    hit = next(h for h in res.hits if h.meeting_id == a and h.t_start_ms is not None)
    page._results_link(QUrl(hit_link(hit)))
    assert opened[-1] == (a, "transcricao", hit.t_start_ms, True)
    page._results_link(QUrl(f"listener://meeting?m={b}"))
    assert opened[-1] == (b, None, None, False)
    labels = [label for label, _fn in page._hit_actions(QPoint(), hit_link(hit))]
    assert labels == ["Abrir neste ponto", "Copiar com citação"] and page._hit_actions(QPoint(), "") == []
    copied = page.copy_hit(hit)
    assert copied.endswith(f"Orçamento 2027 ({export.fmt_date(hit.meeting_date)}) [{citations.stamp(hit.t_start_ms)}]")
    assert QGuiApplication.clipboard().mimeData().text() == copied
    page._on_search(page._gen - 1, None)                                          # resultado velho: descartado
    assert page.result is res
    store.providers.set_active("gemini", embed=False)                             # sem provedor para o sentido: o selo diz
    page._search_now()
    qtbot.waitUntil(lambda: page.result is not res, timeout=5000)
    assert "busca por palavras: escolha um provedor" in page.badge.text() and "embedding" not in page.badge.text()
    qtbot.keyClick(page.search, Qt.Key.Key_Escape)                                  # Esc limpa e volta à lista
    assert page.search.text() == "" and page.stack.currentWidget() is page.table and page.result is None
    page.search.setText("orçamento")
    page.focus_search()
    assert page.search.selectedText() == "orçamento"


def test_history_open_actions_mark_done_and_open_the_evidence(qtbot, store, cleanup):
    from listener.ui.history import HistoryPage

    who = _people(store)
    mid, job = _meeting(store, who)
    old = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "# velha", "gemini", "m", "minutes/1.0", job, None, "pt-BR"))
    store.actions.insert_many([ActionInsert(mid, old, "Da ata superada", None, who["ana"], None, None, None, job)])
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "# nova", "gemini", "m", "minutes/1.0", job, None, "pt-BR"))
    store.actions.insert_many([ActionInsert(mid, sid, "Mandar a planilha", None, who["bia"], "2020-01-10", 200_000, None, job),
                               ActionInsert(mid, sid, "Fechar a proposta", "Ana", who["ana"], "2099-12-31", None, None, job),
                               ActionInsert(mid, sid, "Já feita", None, who["ana"], None, None, None, job, ActionStatus.DONE)])
    edited: list[tuple[int, str]] = []
    opened: list[tuple[object, ...]] = []
    page = HistoryPage(store, on_open=lambda *args: opened.append(args), on_edited=lambda m, r: edited.append((m, r)))
    qtbot.addWidget(page)
    cleanup(page)
    page.tabs.setCurrentIndex(1)
    page.reload_actions()
    texts = [[page.action_table.item(r, c).text() for c in range(4)] for r in range(page.action_table.rowCount())]
    assert [t[0] for t in texts] == ["Mandar a planilha", "Fechar a proposta"]                  # só abertas, da ata atual
    assert texts[0][1:3] == ["Bia", "10/01/2020 (atrasada)"] and texts[1][2] == "31/12/2099" and texts[0][3].startswith("Orçamento 2027 — ")
    assert page.action_table.item(0, 0).foreground().color().name() == "#c0392b" and "1 atrasada" in page.actions_note.text()
    page.action_table.selectRow(0)
    page.action_table.activated.emit(page.action_table.model().index(0, 0))
    assert opened[-1] == (mid, "transcricao", 200_000, False)                        # a evidência da ação
    assert page.mark_done() == 1 and edited == [(mid, "edit")]
    assert page.action_table.rowCount() == 1 and store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'action_item'")[0][0] == 1
    page.action_table.selectRow(0)
    page.open_action()
    assert opened[-1] == (mid, "acoes", None, False)                                  # sem evidência: a aba Ações
    page.person.setCurrentIndex(page.person.findData(who["bia"]))
    assert page.action_table.rowCount() == 0 and page.actions_note.text().startswith("Nenhuma ação aberta")


def test_history_global_chat_with_the_fake_rating_citation_link_and_warnings(qtbot, store, monkeypatch, cleanup):
    from PySide6.QtCore import QPoint, QUrl

    from listener.ui.history import CHAT_EMPTY, HistoryPage

    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, _b, _who = _indexed(store)
    opened: list[tuple[object, ...]] = []
    page = HistoryPage(store, on_open=lambda *args: opened.append(args))
    qtbot.addWidget(page)
    cleanup(page)
    page.show()
    page.tabs.setCurrentIndex(2)
    assert "Sem filtros" in page.chat_note.text() and page.threads.count() == 1
    page.question.setText(QUESTION)
    page.ask()
    qtbot.waitUntil(lambda: bool(page.answers) and page.chat_task is None, timeout=10000)
    ans = page.answers[0]
    assert ans.error is None and page.thread_id == ans.thread_id and page.threads.currentData() == ans.thread_id
    assert "citação(ões) com link" in page.chat_status.text() and "busca híbrida" in page.chat_status.text()
    assert "Citação certa?" in page.chat_view.toPlainText()
    page._chat_link(QUrl(chat_global.rate_link(ans.message_id, 1)))                 # U-027: grava e redesenha
    assert store.chat.get(ans.message_id)["rating"] == 1 and "Citação certa: sim" in page.chat_view.toPlainText()
    cite = next(c for c in ans.citations if c.get("t_ms") is not None)
    page._chat_link(QUrl(citations.open_link(cite["meeting_id"], cite["t_ms"], cite["segment_id"])))
    assert opened[-1] == (cite["meeting_id"], "transcricao", cite["t_ms"], True)
    page._chat_link(QUrl(citations.open_link(a)))
    assert opened[-1] == (a, None, None, False)
    block = page.chat_view.document().findBlockByNumber(1)                           # a resposta: um parágrafo por mensagem
    rect = page.chat_view.document().documentLayout().blockBoundingRect(block)
    actions = page._answer_actions(QPoint(int(rect.x()) + 4, int(rect.y()) + 4), "")
    assert [label for label, _fn in actions] == ["Copiar com citação"]
    actions[0][1]()
    copied = page.copied[-1]
    assert copied.startswith("Resposta simulada") and "[Orçamento 2027, " in copied and "listener://" not in copied
    page.new_thread()
    assert page.thread_id is None and page.chat_view.toPlainText().startswith(CHAT_EMPTY[22:40])
    page.thread_id = 99999                                                           # conversa apagada: ValueError vira aviso
    page.question.setText("e agora?")
    page.ask()
    qtbot.waitUntil(lambda: page.chat_task is None, timeout=5000)
    assert "Esta conversa não existe mais" in page.chat_status.text()
    store.providers.set_active("gemini", llm=False)
    page.question.setText("e agora?")
    page.ask()
    assert page.chat_task is None and "Nenhum provedor de LLM ativo com chave" in page.chat_status.text()


def test_controller_history_hotkey_tray_balloon_reload_q9_and_exit(qtbot, qapp, platform, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.ui import controller as ctl
    from listener.ui.controller import Controller
    from listener.ui.player import PlayRequest
    from listener.ui.tray import TrayIcon

    who = _people(store)
    mid, _job = _meeting(store, who)
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        assert ctl.HOTKEY_ACTIONS[-1] == "history" and store.get_setting("hotkeys.history") == "ctrl+alt+shift+h"
        assert c.register_hotkeys() == {} and "ctrl+alt+shift+h" in platform.hotkeys().registered          # U-028
        c.hotkey_pressed.emit("history")
        hist = c._history
        assert hist is not None and hist.isVisible()
        hist.page.search.setText("planilha")
        c._on_hotkey("history")
        assert c._history is hist and hist.page.search.selectedText() == "planilha"                       # cursor na busca
        tray = TrayIcon(c)
        names = [a.text() for a in tray.menu.actions()]
        assert names.index("Histórico…") == names.index("Fila de processamento…") - 1
        # o clique no balão abre a reunião do balão que está na tela (Q6); um balão sem reunião zera a guardada (R7-06). F5.3:
        # o balão só com `ui.toast_seconds` = 0 (o aviso do app faz a própria ação no clique)
        store.set_setting("ui.toast_seconds", "0")
        assert c.open_last_notified() is None
        c._notify_meeting(mid, "ata pronta")
        c.notify("Atalhos que não registraram: …")
        assert c.open_last_notified() is None and c._meetings == {}                  # não abre a reunião do balão anterior
        c._notify_meeting(mid, "ata pronta")
        win = c.open_last_notified()
        assert win is not None and c._meetings[mid] is win and win.isVisible()
        tray.messageClicked.emit()
        assert len(c._meetings) == 1
        # o `index` da reunião aberta terminou: a janela recarrega (uma vez)
        c._poll_queue()                                                              # o pipeline concluído do setup: visto aqui
        reloads: list[int] = []
        monkeypatch.setattr(win, "reload", lambda: reloads.append(1))
        c._poll_queue()
        job = store.jobs.enqueue_index(mid, "edit")
        store.jobs.claim(pid=3, kinds=("index",))
        store.jobs.finish(job, JobStatus.DONE)
        c._poll_queue()
        c._poll_queue()
        assert reloads == [1]
        # Q9: gravando o sistema inteiro, o player pede confirmação; com a fonte do app, toca direto
        asked: list[str] = []
        answer = {"value": QMessageBox.StandardButton.No}

        def question(*a: object, **_k: object) -> object:
            asked.append(str(a[2]))
            return answer["value"]

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
        c.recorder = SimpleNamespace(source=SourceSpec("system"))                    # type: ignore[assignment]
        c.state = AppState.RECORDING
        req = PlayRequest(mid, 0)
        assert c._confirm_play(req) is False and "som do sistema inteiro" in asked[0]
        answer["value"] = QMessageBox.StandardButton.Yes
        assert c._confirm_play(req) is True
        c.recorder = SimpleNamespace(source=SourceSpec("app", pid=4242, exe="meeting.exe"))   # type: ignore[assignment]
        assert c._confirm_play(req) is True and len(asked) == 2
        c.recorder, c.state = None, AppState.IDLE
        assert c.open_transcript(mid) is win and win.tabs.currentWidget() is win.transcript
        assert JobKind.INDEX in c._meeting_index or mid in c._meeting_index
    finally:
        c.emergency_stop()
    assert not c.player.engine.alive and c._history is not None and c._history.page._closed


def test_q9_the_warmup_and_the_whole_system_source_pause_the_player(qtbot, qapp, platform, store, monkeypatch):
    """R7-01: o som que já toca não entra na gravação do sistema inteiro. Abrir o warm-up, começar a gravar com a fonte
    "sistema inteiro" e trocar a fonte para ela pausam o player, com o aviso na barra (e no log), sem balão; retomar passa
    pela confirmação do Q9 (recusada: segue pausado). Com a fonte do app o som segue; fora da gravação, retoma direto; o
    aviso sai quando a gravação acaba. R8: o estado conferido é o que o motor confirmou (os fluxos tocam em tempo real e a
    pausa descarta o buffer: um `abort` por pausa), sem depender do prazo de um fake que prende o `write`. R-11: a retomada
    depois do warm-up relê os dispositivos (o fluxo foi solto e a saída pode ter mudado)."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    from listener.capture.recorder import RecordConfig
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller
    from listener.ui.player import PAUSED, PLAYING
    from listener.ui.sources import SourceTable

    who = _people(store)
    mid, _job = _meeting(store, who)
    _audio(store, mid, 0, seconds=60)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    outs = _realtime_player(c)
    balloons: list[str] = []
    asked: list[str] = []

    def question(*a: object, **_k: object) -> object:
        asked.append(str(a[2]))
        return QMessageBox.StandardButton.No

    def sounding() -> None:                                  # o motor está escrevendo: mais dois blocos saíram
        n = len(outs[-1].chunks) if outs else 0
        qtbot.waitUntil(lambda: bool(outs) and len(outs[-1].chunks) >= n + 2, timeout=3000)

    def paused(aborts: int, notice: str) -> None:            # o motor pausou: o buffer do dispositivo foi descartado
        qtbot.waitUntil(lambda: outs[-1].calls.count("abort") == aborts, timeout=3000)
        assert (c.player.state().state, c.player.state().message) == (PAUSED, notice)

    monkeypatch.setattr(c, "notify", balloons.append)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: SimpleNamespace(exec=lambda: None))
    monkeypatch.setattr(ctl, "WarmupDialog", lambda *a, **k: SimpleNamespace(exec=lambda: QDialog.DialogCode.Rejected,
                                                                            deleteLater=lambda: None))
    fast = {"chunk_seconds": 0.5, "stop_timeout_s": 1.0, "retry_base_s": 0.001}
    try:
        win = c.open_meeting(mid)
        assert c.player.play_from(mid, 0)                                                    # sem gravação: toca direto
        sounding()
        c.open_warmup()                                                                      # o warm-up abriu (e fechou)
        paused(1, ctl.PLAYER_PAUSED_WARMUP)
        qtbot.waitUntil(lambda: outs[0].calls[-1] == "close", timeout=3000)                 # U-043 (A-04): soltou o fluxo
        assert win.bar.note.text() == ctl.PLAYER_PAUSED_WARMUP and asked == []
        platform.next_devices = list(platform.devices)                                       # o sistema mudou na pausa
        assert c.player.toggle() and platform.refreshes == 1                                 # retoma direto e relê (R-11)
        sounding()
        assert (c.player.state().state, win.bar.note.text(), len(outs)) == (PLAYING, "", 2)   # a retomada abriu outro
        assert c.start_recording(RecordConfig(source=SourceSpec("system", label="Sistema"), **fast), {"title": "q9"})
        paused(1, ctl.PLAYER_PAUSED_SYSTEM)                                                  # o gancho já esperou a pausa
        assert win.bar.note.text() == ctl.PLAYER_PAUSED_SYSTEM
        assert c.player.toggle() is False and "som do sistema inteiro" in asked[-1] and c.player.state().state == PAUSED
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert (c.player.state().state, c.player.state().message, win.bar.note.text()) == (PAUSED, "", "")   # o aviso saiu
        assert c.player.toggle()                                                             # parou de gravar: direto
        sounding()
        app_source = SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião")
        assert c.start_recording(RecordConfig(source=app_source, **fast), {"title": "app"})
        sounding()                                                                           # fonte do app: o som segue
        assert c.player.state().state == PLAYING
        monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(SourceTable, "selected_spec", lambda _self: SourceSpec("system", label="Sistema"))
        c.switch_source()                                                                    # trocou para o sistema inteiro
        assert c.recorder is not None and str(c.recorder.source.kind) == "system"
        paused(2, ctl.PLAYER_PAUSED_SYSTEM)
        assert not any("player" in b or "pausad" in b for b in balloons)                     # sem balão
    finally:
        c.emergency_stop()


def test_the_system_loopback_never_opens_with_the_player_playing(qtbot, qapp, tmp_path, store, clock, monkeypatch):
    """R8-01: o gravador cai sozinho para o loopback do sistema quando o do app não abre, no começo e na troca de fonte, e o
    reabre quando o dispositivo cai (o `check_devices` do `_tick`); antes de cada abertura o player pausa e o motor confirma:
    na hora da abertura o fluxo do player já foi descartado (`abort` depois do último bloco) e nenhum bloco sai depois.
    Retomar durante a gravação pergunta (aqui, aceito: toca de novo até a próxima abertura); o aviso sai quando a gravação
    acaba."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    from listener.capture.recorder import RecordConfig
    from listener.model import Health
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller
    from listener.ui.player import PAUSED
    from listener.ui.sources import SourceTable
    from tests.fake_platform import FakePlatform

    platform = FakePlatform(tmp_path / "plat", clock=clock, app_fail=True)         # o loopback do app nunca abre
    who = _people(store)
    mid, _job = _meeting(store, who)
    _audio(store, mid, 0, seconds=60)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    outs = _realtime_player(c)
    opened: list[tuple[int, str, str]] = []                 # em cada abertura: (blocos já escritos, estado do motor, última chamada)
    real_open = platform.open_system_loopback

    def spy(*args: Any, **kwargs: Any) -> Any:
        opened.append((len(outs[-1].chunks), c.player.engine.snapshot().state, outs[-1].calls[-1]))
        return real_open(*args, **kwargs)

    def play_again() -> None:                               # retomar: gravando o sistema, pergunta (aceito); o som volta
        n = len(outs[-1].chunks)
        assert c.player.toggle()
        qtbot.waitUntil(lambda: len(outs[-1].chunks) >= n + 2, timeout=3000)

    def nothing_after_the_opening(count: int) -> None:
        assert len(opened) == count
        written, state, last_call = opened[-1]
        assert (state, last_call) == (PAUSED, "abort")                               # parou antes: buffer descartado
        qtbot.wait(400)                                                              # mais que um bloco
        assert len(outs[-1].chunks) == written                                       # nenhum bloco depois da abertura
        assert (c.player.state().state, c.player.state().message) == (PAUSED, ctl.PLAYER_PAUSED_SYSTEM)

    monkeypatch.setattr(platform, "open_system_loopback", spy)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: SimpleNamespace(exec=lambda: None))
    fast = {"chunk_seconds": 0.5, "stop_timeout_s": 1.0, "retry_base_s": 0.001}
    app_source = SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião")
    try:
        c.open_meeting(mid)
        assert c.player.play_from(mid, 0)
        qtbot.waitUntil(lambda: bool(outs) and len(outs[-1].chunks) >= 2, timeout=3000)
        assert c.start_recording(RecordConfig(source=app_source, **fast), {"title": "fallback"})   # no começo
        assert c.recorder is not None and str(c.recorder.source.kind) == "system"
        nothing_after_the_opening(1)
        play_again()
        monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(SourceTable, "selected_spec", lambda _self: app_source)
        c.switch_source()                                                                         # na troca de fonte
        assert str(c.recorder.source.kind) == "system"
        nothing_after_the_opening(2)
        play_again()
        loopback = next(s for s in reversed(platform.live_streams()) if s.freq == 330.0)          # o loopback do sistema
        loopback.fail("dispositivo removido")
        qtbot.waitUntil(lambda: len(opened) == 3, timeout=5000)      # na reabertura: o `_tick` chama o `check_devices`
        assert c.recorder.health()["R"] != Health.LOST
        nothing_after_the_opening(3)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        assert (c.player.state().state, c.player.state().message) == (PAUSED, "")                # o aviso sai com a gravação
    finally:
        c.emergency_stop()


def test_the_pause_notice_goes_away_when_the_recording_does_not_start(qtbot, qapp, platform, store, monkeypatch):
    """R8-01: o gravador pausou o player para abrir o loopback do sistema e a gravação não começou: o aviso sai da barra (o
    som segue pausado) e retomar, sem gravação, não pergunta nada."""
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import RecordConfig
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller
    from listener.ui.player import PAUSED, PLAYING

    class FailingRecorder(ctl.Recorder):
        def start(self) -> None:
            assert self.before_system_loopback is not None
            self.before_system_loopback()                    # o loopback do sistema ia abrir: o player pausou
            raise RuntimeError("a captura não abriu")

    who = _people(store)
    mid, _job = _meeting(store, who)
    _audio(store, mid, 0, seconds=60)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    outs = _realtime_player(c)
    asked: list[object] = []

    def question(*a: object, **_k: object) -> object:
        asked.append(a)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(ctl, "Recorder", FailingRecorder)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    try:
        win = c.open_meeting(mid)
        assert c.player.play_from(mid, 0)
        qtbot.waitUntil(lambda: bool(outs) and len(outs[-1].chunks) >= 2, timeout=3000)
        cfg = RecordConfig(source=SourceSpec("system", label="Sistema"), chunk_seconds=0.5, stop_timeout_s=1.0, retry_base_s=0.001)
        assert c.start_recording(cfg, {"title": "não começa"}) is False and c.recorder is None
        assert outs[-1].calls.count("abort") == 1 and len(store.list_meetings()) == 1           # pausou; a reunião saiu
        assert (c.player.state().state, c.player.state().message, win.bar.note.text()) == (PAUSED, "", "")
        assert c.player.toggle() and asked == []                                                # sem gravação: direto
        qtbot.waitUntil(lambda: c.player.state().state == PLAYING and outs[-1].calls[-1] == "start", timeout=3000)
    finally:
        c.emergency_stop()


def test_exit_cancels_the_search_vector_and_both_chats_stuck_on_the_network(qtbot, store, monkeypatch):
    """R7-03: com a rede presa (o fake demora 30 s), o Enter da busca (o vetor da consulta), uma pergunta do chat global e
    uma do chat da reunião antes do primeiro pedaço: o `shutdown` cancela de verdade e nenhuma thread fica viva, em ~1 s
    (antes as esperas de 3 e 2,5 s venciam e o banco fechava com as QThreads rodando); as chamadas canceladas ficam em
    `llm_calls` e nenhuma resposta vazia é gravada. Passado o prazo, a espera da saída avisa e espera o fim."""
    from listener.ui.chat import ChatPanel
    from listener.ui.history import HistoryPage
    from listener.ui.tasks import run_task, wait_before_close

    monkeypatch.setenv("LISTENER_LLM", "fake")
    a, _b, _who = _indexed(store)
    _slow_network(monkeypatch)
    page = HistoryPage(store)
    qtbot.addWidget(page)
    page.search.setText("auditoria")
    page._search_now()                                       # Enter: o vetor da consulta preso no fake
    page.question.setText(QUESTION)
    page.ask()                                               # o chat global, preso no vetor da consulta dele
    panel = ChatPanel(store, a)
    qtbot.addWidget(panel)
    panel.question.setText("qual é o prazo?")
    panel.ask()                                              # o chat da reunião, sem o primeiro pedaço
    tasks = [*page._tasks, panel.task]
    qtbot.wait(500)                                          # as três chamadas entram no fake
    assert len(tasks) == 3 and all(t.isRunning() for t in tasks)
    t0 = time.monotonic()
    page.shutdown()
    panel.shutdown()
    assert time.monotonic() - t0 < 1.5 and not any(t.isRunning() for t in tasks)
    cancelled = sorted(str(r[0]) for r in store._q("SELECT task FROM llm_calls WHERE error = 'cancelado'"))
    assert cancelled == ["chat", "rag_embed", "rag_embed"] and store.chat.list_threads() == []
    assert [str(m["role"]) for m in store.chat.list_for_meeting(a)] == [ChatRole.USER]
    slow = run_task(lambda: time.sleep(0.4), None, lambda _r: None)
    t0 = time.monotonic()
    assert wait_before_close(slow, 50, "uma tarefa lenta") is False and time.monotonic() - t0 >= 0.3 and slow.isFinished()


@pytest.mark.parametrize("variant", [(), ("--glossary",)], ids=["busca-e-chats", "com-o-glossario"])
def test_app_exit_with_the_network_stuck_exits_with_code_0(variant):
    """R7-03: o script de saída (`tests/exit_scenario.py`) num processo à parte, com a busca por sentido e os dois chats
    presos na rede e a saída pelo caminho do app (`aboutToQuit` -> `emergency_stop`): código 0, sem "QThread: Destroyed
    while thread is still running" (antes: 134 no Linux e 0xC0000409 no Windows, depois de ~20 s de esperas). R8-04: a
    variante com o glossário do warm-up em voo, uma `Task` comum que a saída não esperava (também 0xC0000409 antes)."""
    root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_LLM"} | {"PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"}
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, str(root / "tests" / "exit_scenario.py"), *variant], cwd=str(root), env=env,  # noqa: S603
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "QThread: Destroyed" not in proc.stderr and time.monotonic() - t0 < 60
    if variant:                                                                          # o glossário foi cancelado, não venceu
        assert "glossário em llm_calls: ['cancelado']" in proc.stderr, proc.stderr[-3000:]


def test_meeting_chat_reload_keeps_the_question_in_flight_and_the_scroll(qtbot, store, monkeypatch):
    """R7-05: a recarga da reunião aberta (um job `llm` ou `index` terminou) com uma pergunta em voo não apaga a pergunta
    nem a parcial; sem pergunta em voo, a rolagem volta para onde estava, salvo se estava no fim (aí segue no fim)."""
    from listener.ui.chat import ChatPanel

    who = _people(store)
    mid, _job = _meeting(store, who)
    _llm_ready(store)
    for i in range(24):
        store.chat.add(mid, ChatRole.USER if i % 2 == 0 else ChatRole.ASSISTANT, f"mensagem {i}: " + "texto comprido " * 30)
    panel = ChatPanel(store, mid)
    qtbot.addWidget(panel)
    panel.resize(480, 320)
    panel.show()
    qtbot.waitExposed(panel)
    bar = panel.view.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 3)
    kept = bar.value()
    panel.reload()
    assert bar.value() == kept and "mensagem 0:" in panel.view.toPlainText()          # a leitura fica onde estava
    bar.setValue(bar.maximum())
    panel.reload()
    assert bar.value() == bar.maximum()                                               # estava no fim: segue no fim
    _slow_network(monkeypatch)
    panel.question.setText("e o prazo?")
    panel.ask()
    panel._on_chunk("Resposta parcial")                                               # um pedaço já chegou
    panel.reload()                                                                    # um job terminou com a pergunta em voo
    shown = panel.view.toPlainText()
    assert "Você: e o prazo?" in shown and "Resposta parcial" in shown and panel.task is not None
    panel.shutdown()


def test_titles_and_names_stay_plain_text_in_labels_and_tooltips(qtbot, store, cleanup):
    """R7-07: um título e um nome "<b>Comitê</b> <img>" aparecem como texto (não viram HTML) na barra do player, no
    cabeçalho da reunião e nas notas do Histórico. R8-02: a dica da lista de reuniões mostra o texto como está, também com
    "&", aspas e apóstrofo (o `html.escape` deixava "S&amp;OP" na tela: a dica do Qt só lê HTML quando acha um "<")."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextDocument
    from PySide6.QtGui import Qt as QtGuiQt

    from listener.ui.history import HistoryPage
    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player

    def shown(tip: str) -> str:                              # o que a dica mostra: AutoText, como o `QToolTip` decide
        doc = QTextDocument()
        if QtGuiQt.mightBeRichText(tip):
            doc.setHtml(tip)
        else:
            doc.setPlainText(tip)
        return doc.toPlainText()

    evil = "<b>Comitê</b> <img>"
    title = f'Revisão S&OP "Q4" {evil}'
    who = {"ana": store.people.create_person(evil), "bia": store.people.create_person("Ana D'Ávila")}
    mid, _job = _meeting(store, who, title=title)
    _audio(store, mid, 0)
    permits = threading.Semaphore(0)
    player = Player(store, FakeOutputs(permits=permits))
    cleanup(player)
    win = MeetingWindow(store, mid, player=player)
    qtbot.addWidget(win)
    cleanup(win)
    plain = Qt.TextFormat.PlainText
    assert win.info.textFormat() == plain and evil in win.info.text() and win.message.textFormat() == plain
    assert player.play_from(mid, 0, title=win.title_text()) and win.bar is not None
    assert (win.bar.what.textFormat(), win.bar.note.textFormat()) == (plain, plain) and win.bar.what.text().endswith(title)
    page = HistoryPage(store)
    qtbot.addWidget(page)
    cleanup(page)
    page.show()
    qtbot.waitUntil(lambda: page.model.rowCount() == 1, timeout=5000)
    assert shown(page.model.data(page.model.index(0, 1), Qt.ItemDataRole.ToolTipRole)) == title
    assert shown(page.model.data(page.model.index(0, 3), Qt.ItemDataRole.ToolTipRole)) == f"{evil}, Ana D'Ávila"
    page.person.setCurrentIndex(page.person.findData(who["ana"]))
    qtbot.waitUntil(lambda: evil in page.list_note.text(), timeout=5000)
    assert all(label.textFormat() == plain for label in (page.list_note, page.actions_note, page.chat_note, page.coverage))
    assert evil in page.actions_note.text() and evil in page.chat_note.text()
    permits.release(1000)
