"""F5.6 L3 (U-093), telas (pytest-qt, offscreen; lento como as outras): a lista do Histórico em seleção múltipla, "Excluir…"
no menu de contexto e no botão sob a lista, a tecla Delete só na tabela (A-40, P-04), a confirmação com até 10 reuniões e
"e mais N", "Cancelar" como padrão e o título em texto puro (A-41); o controlador recusa a reunião gravando, fecha a reunião,
a revisão e o ao vivo dela e para o player antes, exclui fora da thread da interface e recarrega a lista (A-42); a citação de
uma reunião excluída diz que ela não existe mais (P-02); a liberação de espaço interrompe a conexão ao cancelar, começar a
gravar a interrompe antes do `rec.start()` e a saída do app a espera (A-39)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from listener.rag import citations
from listener.store import space
from tests.test_meeting_delete import _meeting
from tests.test_rag_index import _pipeline_job

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento


@pytest.fixture
def cleanup():
    items: list[Any] = []
    yield items.append
    for item in reversed(items):
        item.shutdown()


def _loaded(qtbot: Any, page: Any, n: int) -> None:
    qtbot.waitUntil(lambda: page.model.rowCount() == n and page.list_note.text() != "carregando…", timeout=5000)


def _select(page: Any, *meeting_ids: int) -> None:
    from PySide6.QtCore import QItemSelectionModel

    sel = page.table.selectionModel()
    sel.clearSelection()
    for row, line in enumerate(page.model.lines):
        if line.meeting_id in meeting_ids:
            flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
            sel.select(page.model.index(row, 0), flags)


def test_multi_selection_context_menu_button_and_the_delete_key_only_on_the_table(qtbot, store, cleanup):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QAbstractItemView

    from listener.ui.history import HistoryPage

    mids = [_meeting(store, f"Reunião {i}") for i in range(3)]
    asked: list[tuple[list[int], Any]] = []
    page = HistoryPage(store, on_delete=lambda lines, view: asked.append(([line.meeting_id for line in lines], view)))
    qtbot.addWidget(page)
    cleanup(page)
    page.show()
    _loaded(qtbot, page, 3)
    d = page.deleting
    assert page.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert not d.button.isEnabled() and d.button.text() == "Excluir…" and d.button.isVisibleTo(page)
    _select(page, mids[0], mids[2])
    assert d.button.isEnabled() and sorted(line.meeting_id for line in d.selected()) == [mids[0], mids[2]]
    menu = d.menu_for(page.table.visualRect(page.model.index(0, 1)).center())
    assert menu is not None and [a.text() for a in menu.actions()] == ["Excluir 2 reuniões…"]
    menu.actions()[0].trigger()
    assert sorted(asked[-1][0]) == [mids[0], mids[2]] and asked[-1][1] is d
    d.button.click()
    assert len(asked) == 2
    qtbot.keyClick(page.table, Qt.Key.Key_Delete)                                   # A-40: Delete na tabela pede
    assert len(asked) == 3
    page.search.setText("abc")
    page.search.setCursorPosition(0)
    qtbot.keyClick(page.search, Qt.Key.Key_Delete)                                   # na busca, apaga a letra
    assert len(asked) == 3 and page.search.text() == "bc"
    page.search.clear()
    _select(page)                                                                   # o clique direito numa linha a escolhe
    row = next(r for r, line in enumerate(page.model.lines) if line.meeting_id == mids[1])
    menu = d.menu_for(page.table.visualRect(page.model.index(row, 1)).center())
    assert menu is not None and [a.text() for a in menu.actions()] == ["Excluir…"] and [x.meeting_id for x in d.selected()] == [mids[1]]
    assert d.menu_for(QPoint(-5, -5)) is not None                                   # fora das linhas: a seleção que havia
    loose = HistoryPage(store)                                                       # sem quem exclua: sem botão nem menu
    qtbot.addWidget(loose)
    cleanup(loose)
    loose.show()
    _loaded(qtbot, loose, 3)
    _select(loose, mids[0])
    assert not loose.deleting.button.isVisibleTo(loose) and loose.deleting.menu_for(QPoint(1, 1)) is None
    assert not loose.deleting.request()


def test_the_confirmation_lists_up_to_ten_with_and_more_keeps_titles_as_text_and_cancel_is_the_default(qtbot, store):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from listener.ui.history_data import MeetingLine
    from listener.ui.history_delete import GOES, STAYS, DeleteConfirm

    long_title = "<b>Planejamento</b> " + "orçamento " * 20
    lines = [MeetingLine(i, "2026-10-28T14:03:00.000-03:00", long_title if i == 0 else f"Reunião {i}", 3_725_000, (), "revisada", None)
             for i in range(12)]
    dlg = DeleteConfirm(lines, {5: "A reunião está sendo gravada: pare a gravação antes de excluir."})
    qtbot.addWidget(dlg)
    text = dlg.items.text().splitlines()
    assert len(text) == 11 and text[-1] == "e mais 1 reunião" and text[1] == "• 28/10/2026 14:03 · Reunião 1 · 1 h 02 min"
    assert text[0].endswith("… · 1 h 02 min") and "<b>Planejamento</b>" in text[0] and dlg.items.textFormat() == Qt.TextFormat.PlainText
    assert "Reunião 5" not in dlg.items.text()                                      # R-11: a que fica de fora não está na lista
    assert dlg.head.text() == "Excluir 11 reuniões?" and dlg.btn_delete.text() == "Excluir 11 reuniões" and dlg.btn_delete.isEnabled()
    assert dlg.btn_cancel.isDefault() and not dlg.btn_delete.isDefault()
    assert dlg.refused_label is not None and dlg.refused_label.text().splitlines() == [
        "1 reunião fica de fora:", "• 28/10/2026 14:03 · Reunião 5 · 1 h 02 min",
        "A reunião está sendo gravada: pare a gravação antes de excluir."]
    body = " ".join(w.text() for w in dlg.findChildren(type(dlg.items)))
    assert GOES in body and STAYS in body and "perfis de voz" in STAYS and "sem lixeira" in GOES
    dlg.show()
    dlg.activateWindow()
    qtbot.waitUntil(lambda: dlg.focusWidget() is not None, timeout=3000)
    assert dlg.focusWidget() is dlg.btn_cancel                                     # o foco começa no Cancelar
    qtbot.keyClick(dlg.focusWidget(), Qt.Key.Key_Return)                           # Enter cancela (A-41)
    assert dlg.result() == QDialog.DialogCode.Rejected
    one = DeleteConfirm(lines[1:2])
    qtbot.addWidget(one)
    assert one.windowTitle() == "Excluir reunião" and one.btn_delete.text() == "Excluir" and one.refused_label is None
    assert one.head.text() == "Excluir esta reunião?"
    none = DeleteConfirm(lines[1:3], {1: "gravando", 2: "gravando"})                # R-11: todas de fora: o "Excluir" apagado
    qtbot.addWidget(none)
    assert not none.btn_delete.isEnabled() and none.head.text() == "Nenhuma das reuniões selecionadas pode ser excluída agora."
    assert none.items.parent() is None and "2 reuniões ficam de fora:" in none.refused_label.text()


def _spy(seen: list[Any], real: Any, mid: int | None = None) -> None:
    seen.append(mid)
    real(mid)


def _show_everything(c: Any, store: Any, thread: int, a: int, b: int) -> Any:
    """O Histórico com a conversa que cita `a`, a reunião `a` aberta, a revisão de `b` e a janela ao vivo de `b` (ligada como
    o `open_live_window` liga); devolve a página."""
    import functools

    from listener.ui.live import LiveWindow

    page = c.open_history().page
    page.tabs.setCurrentIndex(2)
    page.thread_id = thread
    page._render_thread()
    c.open_meeting(a)
    c.open_review(b)
    live = c.live_window = LiveWindow(store, b)
    c.live_caption.connect(live.on_caption)
    c.partial_state.connect(live.on_partial_state)
    live.closed.connect(functools.partial(c._live_window_closed, live))
    return page


def test_the_controller_refuses_recording_closes_windows_stops_the_player_and_reloads(qtbot, qapp, platform, store, monkeypatch):
    import functools

    from PySide6.QtCore import QUrl

    from listener.pipeline import delete
    from listener.ui.controller import Controller

    a, b, keep = (_meeting(store, t, chunks=2) for t in ("Aberta", "Em revisão", "Fica"))
    for mid in (a, b):
        _pipeline_job(store, mid)
    thread = store.chat.create_thread("antiga")
    store.chat.add(None, "assistant", "Visto [R1 00:01].", citations=[{"alias": "R1", "meeting_id": a, "t_ms": 1000}], thread_id=thread)
    c = Controller(qapp, platform, store)
    c.tray = None
    stops: list[Any] = []
    monkeypatch.setattr(c.player, "stop", functools.partial(_spy, stops, c.player.stop))
    confirmed: list[tuple[list[int], list[int]]] = []

    def confirm(_parent: Any, lines: Any, refused: Any) -> bool:
        confirmed.append((sorted(x.meeting_id for x in lines), sorted(refused)))
        return True

    c.deleter.confirm = confirm
    try:
        page = _show_everything(c, store, thread, a, b)
        _loaded(qtbot, page, 3)
        assert "Aberta" in page.chat_view.toPlainText()
        c.recorder = SimpleNamespace(meeting_id=keep)  # type: ignore[assignment]    # salvando: o banco já diz gravada
        _select(page, keep)
        page.deleting.request()                                          # R-11: a tela mostra a recusada; nada sai
        assert confirmed == [([keep], [keep])] and c.deleter.refusals(page.deleting.selected()) == {keep: delete.RECORDING_TEXT}
        assert store.get_meeting(keep) is not None and c.deleter.task is None
        c.recorder = None
        _select(page, a, b)
        assert page.deleting.request() and confirmed[-1] == (sorted([a, b]), [])
        assert a not in c._meetings and b not in c._reviews and c.live_window is None and a in stops and b in stops   # A-42: antes
        assert not page.deleting.button.isEnabled() and page.list_note.text().startswith("Excluindo")
        qtbot.waitUntil(lambda: c.deleter.task is None, timeout=10_000)
        assert store.get_meeting(a) is None and store.get_meeting(b) is None and store.get_meeting(keep) is not None
        _loaded(qtbot, page, 1)
        assert c.notices[-1].text == "2 reuniões excluídas." and page.deleting.button.isEnabled() is False
        assert "reunião excluída, 00:01" in page.chat_view.toPlainText()             # a conversa redesenhada (P-02)
        page._chat_link(QUrl(citations.open_link(a, 1000)))                          # o link antigo, de antes do redesenho
        assert c.notices[-1].text == "Esta reunião não existe mais." and a not in c._meetings
        qtbot.waitUntil(lambda: c.deleter.space_task is None, timeout=10_000)
        assert space.pending(store.core) == []                                       # A-38: o espaço voltou em segundo plano
    finally:
        c.emergency_stop()


def test_space_task_cancel_interrupts_the_connection_and_recording_interrupts_it_first(qtbot, qapp, platform, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller
    from listener.ui.meeting_delete import SpaceTask

    class Conn:
        interrupted = 0

        def interrupt(self) -> None:
            Conn.interrupted += 1

    task = SpaceTask(store, lambda: False)
    task._hold(Conn())  # type: ignore[arg-type]
    task.cancel()
    assert Conn.interrupted == 1 and task.cancel_event.is_set()
    task._hold(None)
    task.cancel()                                                                      # sem conexão aberta: só o evento
    assert Conn.interrupted == 1
    c = Controller(qapp, platform, store)
    c.tray = None
    order: list[str] = []
    monkeypatch.setattr(c.deleter, "interrupt_space", lambda: order.append("espaço"))

    class FailingRecorder(ctl.Recorder):
        def start(self) -> None:
            order.append("gravar")
            raise RuntimeError("a captura não abriu")

    monkeypatch.setattr(ctl, "Recorder", FailingRecorder)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    try:
        cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe"), chunk_seconds=0.5, stop_timeout_s=1.0, retry_base_s=0.001)
        assert c.start_recording(cfg, {"title": "não começa"}) is False
        assert order == ["espaço", "gravar"]                                            # A-39: antes do `rec.start()`
        monkeypatch.undo()
        mid = _meeting(store, "sai", chunks=6)
        store.delete_meeting(mid)
        assert c.deleter.resume_space() and c.deleter.space_task is not None             # a abertura ociosa tenta o pendente
        qtbot.waitUntil(lambda: c.deleter.space_task is None, timeout=10_000)
        assert space.pending(store.core) == []
        c.recorder = SimpleNamespace(meeting_id=0)  # type: ignore[assignment]          # gravando: não começa
        store.delete_meeting(_meeting(store, "outra", chunks=2))
        assert not c.deleter.start_space() and space.pending(store.core)
        c.recorder = None
    finally:
        c.emergency_stop()                                                              # a saída cancela e espera a `SpaceTask`


def test_while_deleting_off_the_ui_thread_the_meeting_does_not_reopen_and_a_failure_is_told(qtbot, qapp, platform, store, monkeypatch):
    """R-07: com a exclusão em curso, a reunião e a revisão dela não reabrem (Histórico, fila, aviso); a exclusão roda fora da
    thread da interface; uma exclusão nova interrompe a liberação de espaço (R-02); a falha de uma reunião vai no aviso (R-10);
    no fim, a conexão da interface solta os arquivos de áudio (R-02)."""
    import sqlite3
    import threading

    from listener.pipeline import delete
    from listener.ui.controller import Controller
    from listener.ui.notices import NoticeKind

    a, b = (_meeting(store, t, chunks=2) for t in ("Sai", "Falha"))
    c = Controller(qapp, platform, store)
    c.tray = None
    c.deleter.confirm = lambda *_a: True
    gate, threads, order = threading.Event(), [], []
    real_many, real_delete = delete.delete_many, store.delete_meeting

    def slow_many(*args: Any, **kw: Any) -> Any:
        gate.wait(10)
        return real_many(*args, **kw)

    def flaky(mid: int) -> None:
        threads.append(threading.get_ident())
        if mid == b:
            raise sqlite3.OperationalError("database is locked")
        real_delete(mid)

    monkeypatch.setattr(delete, "delete_many", slow_many)
    monkeypatch.setattr(store, "delete_meeting", flaky)
    monkeypatch.setattr(c.deleter, "interrupt_space", lambda: order.append("espaço"))
    try:
        page = c.open_history().page
        _loaded(qtbot, page, 2)
        _select(page, a, b)
        assert page.deleting.request() and order == ["espaço"] and c.deleter.deleting == {a, b}
        assert c.open_meeting(a) is None and c.open_review(b) is None and c.open_transcript(a) is None
        assert c.notices[-1].text == "Esta reunião está sendo excluída." and a not in c._meetings and b not in c._reviews
        c.deleter.open_from_history(a, "transcricao", 1000, True)
        assert c.notices[-1].text == "Esta reunião está sendo excluída." and a not in c._meetings
        gate.set()
        qtbot.waitUntil(lambda: c.deleter.task is None, timeout=10_000)
        assert threads and threading.get_ident() not in threads                     # fora da thread da interface
        assert c.notices[-1].kind == NoticeKind.ERROR and c.notices[-1].text == (
            "1 reunião excluída. 1 ficou: A exclusão falhou (OperationalError); tente de novo.")
        assert store.get_meeting(a) is None and store.get_meeting(b) is not None and c.deleter.deleting == set()
        assert store.core.files.users(store.core.current_audio_file()) == 0         # a interface soltou o arquivo do mês
        assert c.open_meeting(b) is not None                                       # depois da exclusão, a que ficou abre
    finally:
        gate.set()
        c.emergency_stop()
