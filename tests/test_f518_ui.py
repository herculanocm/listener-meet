"""F5.18, lote 4, as telas (offscreen): a fila que só refaz as linhas que mudaram e lê só as reuniões dos jobs listados
(PF-16), a lista das falas em layout por lotes com o `scrollTo` chegando à fala pedida (PF-21)."""
from __future__ import annotations

from typing import Any

import pytest

from listener.model import JobStatus
from listener.store import Store

pytest.importorskip("PySide6")


def _meeting(store: Store, title: str = "Diretoria") -> int:
    mid = store.create_meeting(title, "teams", None, None)
    store.finish_meeting(mid, 1_800_000)
    return mid


# ---------------------------------------------------------------- PF-16: a fila
def test_queue_refresh_rebuilds_only_the_rows_that_changed_and_reads_only_the_listed_meetings(qtbot, store, monkeypatch):
    from listener.ui.queue import MEETING_COL, STATE_COL, QueueDialog

    a, b = _meeting(store), _meeting(store, "Planejamento")
    _meeting(store, "Sem job")                                                     # fora da fila: nunca lida
    done = store.jobs.enqueue(a)
    store.jobs.claim(pid=1, kinds=("meeting",))
    store.jobs.finish(done, JobStatus.DONE)
    pending = store.jobs.enqueue(b)
    asked: list[Any] = []
    real_titles = store.meetings.titles_for

    def spy_titles(ids: Any) -> Any:
        asked.append(sorted(ids))
        return real_titles(asked[-1])

    monkeypatch.setattr(store.meetings, "titles_for", spy_titles)
    monkeypatch.setattr(store.meetings, "list_meetings", lambda: pytest.fail("a fila não lê a lista inteira de reuniões (PF-16)"))
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    assert asked == [[a, b]] and dlg.table.rowCount() == 2
    assert dlg.table.item(0, STATE_COL).text() == JobStatus.PENDING and dlg.table.item(1, STATE_COL).text() == JobStatus.DONE
    assert dlg.table.item(1, MEETING_COL).text().startswith("Diretoria (")
    items: list[tuple[int, int]] = []
    real_set = dlg.table.setItem

    def spy_set(r: int, c: int, item: Any) -> None:
        items.append((r, c))
        real_set(r, c, item)

    monkeypatch.setattr(dlg.table, "setItem", spy_set)
    dlg.table.selectRow(1)
    dlg.refresh()
    dlg.refresh()
    assert items == [] and dlg.selected_job_id() == done                           # nada mudou: nenhuma célula refeita
    store.jobs.claim(pid=2, kinds=("meeting",))                                    # o pendente passa a rodar
    dlg.refresh()
    assert {r for r, _c in items} == {0} and len(items) == dlg.table.columnCount()  # só a linha dele
    assert dlg.table.item(0, STATE_COL).text() == JobStatus.RUNNING and dlg.selected_job_id() == done
    items.clear()
    store.jobs.finish(pending, JobStatus.DONE)                                     # terminou: vai para baixo do mais antigo? não:
    dlg.refresh()                                                                  # o mais recente primeiro entre os terminados
    assert [int(j["id"]) for j in dlg.jobs] == [pending, done] and dlg.selected_job_id() == done
    assert {r for r, _c in items} == {0} and dlg.table.item(0, STATE_COL).text() == JobStatus.DONE
    items.clear()
    store.jobs.enqueue(a)                                                          # um job novo no topo: as duas linhas descem
    dlg.refresh()
    assert dlg.table.rowCount() == 3 and {r for r, _c in items} == {0, 1, 2}
    dlg.refresh()
    assert len(items) == 3 * dlg.table.columnCount()                               # e o tique seguinte não refaz nada


# ---------------------------------------------------------------- PF-19: o cache aquecido ao abrir o Histórico
def test_history_warms_the_signature_cache_with_the_local_session(qtbot, store, tmp_path, monkeypatch):
    from listener.llm import local_embed
    from listener.rag import index, search
    from listener.ui.history import HistoryPage
    from tests.fake_local import KEY, long_meeting, use_local

    search.clear_caches()
    use_local(store, tmp_path, monkeypatch)
    mid = long_meeting(store, 3)
    index.sync_text(store, mid)
    assert index.fill_vectors(store, mid).written == 4
    local_embed.shared().release()
    search.clear_caches()
    page = HistoryPage(store)
    qtbot.addWidget(page)
    try:
        assert not search.signature_cache(store, KEY).loaded
        page.refresh()
        qtbot.waitUntil(lambda: not page._preloading, timeout=5000)
        cache = search.signature_cache(store, KEY)
        assert local_embed.loaded() and cache.loaded and cache.full_loads == 1 and len(cache.vids) == 4
        assert page.preload_local() is False                                      # sessão e cache prontos: nada a fazer
    finally:
        page.shutdown()
        search.clear_caches()


# ---------------------------------------------------------------- PF-21: a lista das falas em lotes
def test_transcript_list_lays_out_in_batches_and_scroll_to_reaches_the_asked_row(qtbot, store):
    from PySide6.QtWidgets import QListView

    from listener.store.segments import SegmentInsert
    from listener.ui.transcript import LAYOUT_BATCH, TranscriptTab

    mid = _meeting(store, "Longa")
    job = store.jobs.enqueue(mid)
    store.jobs.claim(pid=1, kinds=("meeting",))
    store.jobs.finish(job, JobStatus.DONE)
    n = 3 * LAYOUT_BATCH
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 4000, i * 4000 + 3500, f"fala {i} " + "palavra " * (i % 9),
                                                  "pyannote", "final", cluster=f"SPK_{i % 3}") for i in range(n)])
    tab = TranscriptTab(store, mid)
    qtbot.addWidget(tab)
    tab.resize(640, 400)
    tab.show()
    qtbot.waitExposed(tab)
    assert tab.view.layoutMode() == QListView.LayoutMode.Batched and tab.view.batchSize() == LAYOUT_BATCH
    assert tab.model.rowCount() == n
    target = n - 20
    row = tab.select_time(target * 4000)
    assert row is not None and row.segment_id == tab.model.rows[target].segment_id and tab.view.currentIndex().row() == target

    def shown() -> bool:
        rect = tab.view.visualRect(tab.model.index(target))
        return rect.isValid() and tab.view.viewport().rect().contains(rect.center())

    qtbot.waitUntil(shown, timeout=5000)
    tab.show_playing(target * 4000 + 1000)                                        # a fala que toca, destacada e à vista
    assert tab.model.highlight == target
    tab.show_playing(10 * 4000 + 1000)
    qtbot.waitUntil(lambda: tab.view.viewport().rect().contains(tab.view.visualRect(tab.model.index(10)).center()), timeout=5000)
    assert tab.followed and tab.followed[-1] == 10


# ---------------------------------------------------------------- PF-22: a posição anda entre dois blocos
def test_player_position_advances_between_blocks_and_never_passes_what_was_written(store):
    import threading
    import time

    from listener.ui.player import PLAYING, PlaybackEngine, PlayRequest
    from tests.fake_player import FakeOutputs
    from tests.test_player import SR, _meeting as _audio_meeting, _wait

    mid = _audio_meeting(store, seconds=8, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, latency=0.05, permits=permits)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 1000))
    permits.release(4)                                                            # 1 s de som escrito
    _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) == 4)
    _wait(lambda: eng.snapshot().t_ms > 1000)
    first = eng.snapshot().t_ms
    assert 1950 <= first <= 2000 and eng.snapshot().state == PLAYING
    time.sleep(0.03)
    second = eng.snapshot().t_ms
    assert second >= first                                                        # o relógio anda entre dois blocos
    time.sleep(0.12)
    assert eng.snapshot().t_ms == 2000                                            # e para no que foi escrito (1 s a partir de 1000)
    permits.release(1)                                                            # mais um bloco: segue dali
    _wait(lambda: len(outs.opened[0].chunks) == 5)
    _wait(lambda: eng.snapshot().t_ms > 2000)
    assert eng.snapshot().t_ms <= 2250
    eng.stop()
    permits.release(100)
    _wait(lambda: not eng.alive)
    assert eng.shutdown() is True
