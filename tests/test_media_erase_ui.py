"""F5.27 lote 4 (U-184, item 12): "Apagar a tela desta reunião (…)…" no "⋯" da reunião aberta. O item com o tamanho, só com
pedaços da tela e a reunião parada, apagado com uma exportação de mídia dela em curso; a confirmação com "Manter" como padrão
e no Esc; apagar tira só os pedaços (o áudio fica), grava o evento, solta os arquivos, pede o espaço de volta e avisa na
faixa; "Manter" não apaga nada; a exportação não começa com a tela sendo apagada."""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from listener.model import EventType, MeetingStatus
from listener.pipeline import delete
from listener.store import space
from listener.ui import media_erase as me
from listener.ui import media_save as msave
from listener.ui.common import human_size

pytest.importorskip("PySide6")

SIZE = 3_000_000


@pytest.fixture(autouse=True)
def no_leak() -> Iterator[None]:
    yield
    msave._running.clear()


def _meeting(store: Any, chunks: int = 3) -> int:
    mid = store.create_meeting("Comitê", None, None, None)
    store.insert_chunk(mid, "R", 0, 0, 16000, 16000, 0, b"flac")
    store.screen.ensure_file(mid)
    for idx in range(chunks):
        store.screen.insert_chunk(mid, idx=idx, t_start_ms=idx * 60_000, t_end_ms=(idx + 1) * 60_000, frames=60, width=1920,
                                  height=1080, codec="libx264", data=os.urandom(SIZE // 3))
    store.finish_meeting(mid, 180_000)
    return mid


def _window(qtbot: Any, store: Any, mid: int, freed: list[Any] | None = None) -> Any:
    from listener.ui.meeting import MeetingWindow

    def free() -> None:                                  # o que o controlador faz (`deleter.start_space`), sem a QThread
        if freed is not None:
            freed.extend(delete.free_pending(store, recording=lambda: False))

    win = MeetingWindow(store, mid, on_free_space=free)
    qtbot.addWidget(win)
    win.refresh_screen_item()
    return win


def test_the_item_shows_the_size_only_with_screen_chunks_and_a_stopped_meeting(qtbot, store):
    mid = _meeting(store)
    win = _window(qtbot, store, mid)
    act = win.screen_eraser.action
    assert act.isVisible() and act.isEnabled() and act.text() == f"Apagar a tela desta reunião ({human_size(SIZE)})…"
    assert act in win.more_menu.actions() and win.act_screen_sep.isVisible()
    msave._running[mid] = "video"                       # uma exportação de mídia desta reunião em curso: apagado
    win.refresh_screen_item()
    assert act.isVisible() and not act.isEnabled()
    msave._running.clear()
    store._x("UPDATE meetings SET status = ? WHERE id = ?", (MeetingStatus.RECORDING, mid))
    win.refresh_screen_item()
    assert not act.isVisible() and not win.act_screen_sep.isVisible()


def test_the_item_is_hidden_without_screen_chunks(qtbot, store):
    plain = store.create_meeting("Sem tela", None, None, None)
    store.finish_meeting(plain, 1000)
    assert not _window(qtbot, store, plain).screen_eraser.action.isVisible()
    empty = _meeting(store, chunks=0)
    assert not _window(qtbot, store, empty).screen_eraser.action.isVisible()


def test_the_item_stays_with_the_month_file_waiting_for_the_space_to_be_freed(qtbot, store):
    """Revisão do diff: o arquivo da tela do mês na fila do espaço (outra reunião apagada) não esconde o item das outras."""
    a, b = _meeting(store), _meeting(store)
    store.screen.delete_for_meeting(a)                   # põe o arquivo do mês na fila do espaço a liberar
    assert store.screen.file_for(b) in space.pending(store.core)
    win = _window(qtbot, store, b)
    assert win.screen_eraser.action.isVisible() and win.screen_eraser.action.isEnabled()


def test_the_confirmation_keeps_on_enter_and_escape():
    box, erase = me.confirm_box(None, SIZE)
    assert box.defaultButton().text() == me.KEEP == "Manter" and box.escapeButton() is box.defaultButton()
    assert erase.text() == "Apagar a tela" and erase is not box.defaultButton()
    assert human_size(SIZE) in box.text()


def test_erasing_deletes_only_the_screen_writes_the_event_gives_the_disk_back_and_tells(qtbot, store, monkeypatch):
    mid = _meeting(store)
    audio = store.list_chunks(mid)
    freed: list[Any] = []
    win = _window(qtbot, store, mid, freed)
    asked: list[int] = []

    def ask(size: int) -> bool:
        asked.append(size)
        return True

    monkeypatch.setattr(win.screen_eraser, "ask", ask)
    task = win.screen_eraser.erase()
    assert task is not None and not win.screen_eraser.action.isEnabled()
    assert win.media.save_video(str(win.media.store.base_dir / "x.mp4")) is None          # a exportação espera o apagar
    assert win.message.text() == me.ERASING
    qtbot.waitUntil(lambda: win.screen_eraser.task is None, timeout=10_000)
    assert asked == [SIZE] and store.screen.list_chunks(mid) == [] and store.list_chunks(mid) == audio
    events = [e for e in store.list_events(mid) if e["type"] == EventType.SCREEN_DELETED]
    assert len(events) == 1 and events[0]["data"] == {"chunks": 3, "bytes": SIZE} and events[0]["t_ms"] is None
    file = store.screen.file_for(mid)
    assert [r.outcome for r in freed if r.file == file] == [space.SpaceOutcome.FREED] and file not in space.pending(store.core)
    assert win.message.text() == f"Tela apagada: {human_size(SIZE)} liberados."
    assert not win.screen_eraser.action.isVisible()


def test_keep_deletes_nothing(qtbot, store, monkeypatch):
    mid = _meeting(store)
    win = _window(qtbot, store, mid)
    monkeypatch.setattr(win.screen_eraser, "ask", lambda _size: False)
    assert win.screen_eraser.erase() is None
    assert len(store.screen.list_chunks(mid)) == 3
    assert not [e for e in store.list_events(mid) if e["type"] == EventType.SCREEN_DELETED]
