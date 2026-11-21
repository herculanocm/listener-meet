"""F5.26 (D-112), o vídeo com legendas só com as vozes decididas: a contagem das vozes pendentes que falam (ignorada e
mesclada contam como decididas; a mesclada numa pendente fala por ela), o aviso do U-178 com "Revisar falantes…" no Enter e
"Fechar" no Esc, o reprocessamento em curso que avisa e não abre nada, e o vídeo que sai depois de decidir."""
from __future__ import annotations

from typing import Any

import pytest

from listener.model import ClusterStatus
from listener.pipeline import review
from listener.store.segments import ClusterInsert, SegmentInsert
from listener.ui import media_save as msave
from listener.ui import media_video_dialog as mvd
from tests.test_media_export import _audio_meeting
from tests.test_rag_index import _pipeline_job

pytest.importorskip("PySide6")


def _voices_meeting(store: Any) -> tuple[int, dict[str, int]]:
    """A reunião de 12 s com cinco vozes: Ana confirmada, A sem nome que fala, B a confirmar que não fala, C sem nome mesclada
    na Ana e D sem nome que só fala pela E, mesclada nela."""
    mid = _audio_meeting(store)
    job = _pipeline_job(store, mid)
    ana = store.people.create_person("Ana", role="CFO")
    ids = store.segments.insert_clusters([
        ClusterInsert(mid, job, "R", "SPK_0", 3000, None, None, ClusterStatus.CONFIRMED, person_id=ana),
        ClusterInsert(mid, job, "R", "SPK_1", 2000, None, None, ClusterStatus.UNNAMED),
        ClusterInsert(mid, job, "R", "SPK_2", 0, None, None, ClusterStatus.AMBIGUOUS, person_id=ana),
        ClusterInsert(mid, job, "R", "SPK_3", 1000, None, None, ClusterStatus.UNNAMED),
        ClusterInsert(mid, job, "R", "SPK_4", 0, None, None, ClusterStatus.UNNAMED),
        ClusterInsert(mid, job, "R", "SPK_5", 1000, None, None, ClusterStatus.UNNAMED)])
    store.segments.insert_segments([
        SegmentInsert(mid, job, "R", 0, 2000, "Bom dia a todos.", "pyannote", "final", "SPK_0", ids["SPK_0"], ana),
        SegmentInsert(mid, job, "R", 2500, 3500, "Oi, tudo bem?", "pyannote", "final", "SPK_1", ids["SPK_1"]),
        SegmentInsert(mid, job, "R", 7000, 8000, "Concordo.", "pyannote", "final", "SPK_3", ids["SPK_3"]),
        SegmentInsert(mid, job, "R", 8500, 9500, "Fechado.", "pyannote", "final", "SPK_5", ids["SPK_5"]),
        SegmentInsert(mid, job, "R", 10000, 10500, "   ", "pyannote", "final", "SPK_2", ids["SPK_2"])])
    review.merge(store, ids["SPK_3"], ids["SPK_0"])
    review.merge(store, ids["SPK_5"], ids["SPK_4"])
    return mid, ids


def test_only_pending_voices_that_speak_block_counting_the_speech_of_voices_merged_into_them(store):
    mid, ids = _voices_meeting(store)
    assert mvd.blocking_voices(store, mid) == 2                    # A fala; D fala pela E; B só tem fala vazia
    review.ignore(store, ids["SPK_1"])                             # "Ignorar" conta como decisão (sai "Voz N")
    assert mvd.blocking_voices(store, mid) == 1
    review.merge(store, ids["SPK_4"], ids["SPK_0"])                # mesclada numa voz decidida
    assert mvd.blocking_voices(store, mid) == 0


def test_the_notice_agrees_in_number():
    assert mvd.blocked_text(2) == "2 vozes sem nome falam no vídeo. Decida quem é cada uma para salvar."
    assert mvd.blocked_text(1) == "1 voz sem nome fala no vídeo. Decida quem é ela para salvar."


def _window(qtbot: Any, store: Any, mid: int, reviewed: list[int] | None = None) -> Any:
    from listener.ui.meeting import MeetingWindow

    win = MeetingWindow(store, mid, on_review=reviewed.append if reviewed is not None else None)
    qtbot.addWidget(win)
    return win


def test_the_review_notice_has_review_on_enter_and_close_on_escape_and_no_save_anyway(qtbot):
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    qtbot.addWidget(parent)
    box, go = mvd.review_box(parent, 2, True)
    names = sorted(b.text() for b in box.buttons())
    assert box.text() == mvd.blocked_text(2) and names == ["Fechar", "Revisar falantes…"]
    assert box.defaultButton() is go and box.escapeButton().text() == "Fechar"
    box, go = mvd.review_box(parent, 1, False)                     # sem a revisão: o botão apagado, o Enter fecha
    assert not go.isEnabled() and box.defaultButton().text() == "Fechar"


def _press_on_modal(qtbot: Any, key: Any) -> None:
    """Aperta a tecla na caixa modal assim que ela abrir."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def press() -> None:
        box = QApplication.activeModalWidget()
        if box is None:
            QTimer.singleShot(10, press)
            return
        qtbot.keyClick(box, key)

    QTimer.singleShot(0, press)


def test_enter_on_the_notice_opens_the_review_and_escape_only_closes(qtbot, store, tmp_path):
    from PySide6.QtCore import Qt

    pytest.importorskip("av")
    mid, _ids = _voices_meeting(store)
    reviewed: list[int] = []
    win = _window(qtbot, store, mid, reviewed)
    _press_on_modal(qtbot, Qt.Key.Key_Escape)
    assert win.media.save_video(str(tmp_path / "v.mp4")) is None and reviewed == []
    _press_on_modal(qtbot, Qt.Key.Key_Return)
    assert win.media.save_video(str(tmp_path / "v.mp4")) is None and reviewed == [mid]     # 1 clique do aviso à revisão
    assert not win.media.busy and sorted(p.name for p in tmp_path.iterdir()) == ["db"]


def test_with_a_pending_voice_the_video_does_not_save_and_after_deciding_it_does(qtbot, store, tmp_path, monkeypatch):
    mid, ids = _voices_meeting(store)
    asked: list[int] = []

    def ask(_parent: Any, n: int, can_review: bool) -> bool:
        asked.append(n)
        return False

    monkeypatch.setattr(msave, "ask_review", ask)
    win = _window(qtbot, store, mid)
    assert win.media.save_video(str(tmp_path / "v.mp4")) is None and asked == [2] and not win.media.busy
    review.ignore(store, ids["SPK_1"])
    review.merge(store, ids["SPK_4"], ids["SPK_0"])
    if msave.video_available():
        task = win.media.save_video(str(tmp_path / "v.mp4"))
        assert task is not None and asked == [2]                   # decididas: segue direto, sem o aviso
        qtbot.waitUntil(lambda: win.media.task is None, timeout=60000)
        assert (tmp_path / "v.mp4").exists()
    msave._running.clear()


def test_a_reprocessing_in_progress_warns_and_opens_nothing(qtbot, store, tmp_path, monkeypatch):
    mid, _ids = _voices_meeting(store)
    asked: list[int] = []

    def ask(_parent: Any, n: int, _can_review: bool) -> bool:
        asked.append(n)
        return False

    monkeypatch.setattr(msave, "ask_review", ask)
    store.jobs.enqueue(mid)                                      # o processamento novo na fila
    win = _window(qtbot, store, mid)
    if msave.video_available():
        assert win.media.save_video(str(tmp_path / "v.mp4")) is None
        assert win.message.text() == mvd.REPROCESSING and asked == [] and not win.media.busy
