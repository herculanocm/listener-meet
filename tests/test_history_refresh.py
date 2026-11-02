"""O Histórico aberto relê a lista quando uma reunião muda (defeito visto pelo dono em 02/11): concluir a revisão de
falantes com o Histórico aberto deixava "falantes a revisar" até fechar e abrir a janela de novo; o mesmo valia para o
processamento que termina com a janela aberta."""
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import Qt

from listener.model import JobStatus, MeetingStatus

pytest.importorskip("pytestqt")


def _status(page: Any, meeting_id: int) -> str | None:
    from listener.ui.history_data import STATUS_COL

    for row in range(page.model.rowCount()):
        if page.model.data(page.model.index(row, 0), Qt.ItemDataRole.UserRole) == meeting_id:
            return str(page.model.data(page.model.index(row, STATUS_COL), Qt.ItemDataRole.DisplayRole))
    return None


def _controller(qapp: Any, platform: Any, store: Any) -> Any:
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    return c


def _meeting(store: Any, status: MeetingStatus) -> int:
    mid = store.create_meeting("Comitê", "sistema", None, None)
    store.finish_meeting(mid, 60_000)
    store.meetings.set_status(mid, status)
    return mid


def test_concluding_the_review_updates_the_open_history_and_keeps_the_selection(qtbot, qapp, platform, store):
    mid = _meeting(store, MeetingStatus.PROCESSED)
    c = _controller(qapp, platform, store)
    try:
        page = c.open_history().page
        qtbot.waitUntil(lambda: _status(page, mid) == "falantes a revisar", timeout=5000)
        page.table.selectRow(0)
        c.open_review(mid).finish()                              # o "Concluir" da revisão, com o Histórico aberto atrás
        qtbot.waitUntil(lambda: _status(page, mid) == "revisada", timeout=5000)
        assert page.table.currentIndex().row() == 0              # a recarga mantém a reunião selecionada
    finally:
        c.emergency_stop()


def test_a_finished_job_updates_the_open_history(qtbot, qapp, platform, store):
    mid = _meeting(store, MeetingStatus.RECORDED)
    c = _controller(qapp, platform, store)
    try:
        page = c.open_history().page
        qtbot.waitUntil(lambda: _status(page, mid) == "gravada", timeout=5000)
        job = store.jobs.enqueue(mid)
        store.jobs.claim(pid=99)
        store.meetings.set_status(mid, MeetingStatus.PROCESSED)                  # o que o worker grava ao concluir
        store.jobs.finish(job, JobStatus.DONE)
        c._poll_queue()                                          # o polling da fila acha o job concluído
        qtbot.waitUntil(lambda: _status(page, mid) == "falantes a revisar", timeout=5000)
    finally:
        c.emergency_stop()
