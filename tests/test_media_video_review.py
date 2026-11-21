"""F5.26 (D-112), o vídeo com legendas só com as vozes decididas: a contagem das vozes pendentes que falam (ignorada e
mesclada contam como decididas; a mesclada numa pendente fala por ela), o aviso do U-178 com "Revisar falantes…" no Enter e
"Fechar" no Esc, o reprocessamento em curso que avisa e não abre nada, e o vídeo que sai depois de decidir. O diálogo do
texto da legenda (U-179): o padrão no que a Transcrição mostra, a opção apagada sem a revisada, "Gerar a revisão" com o custo,
e a escolha no lugar do `prefer_llm`. A faixa de texto: uma amostra por legenda, as falas ao mesmo tempo juntas, e o MP4
relido com as três faixas e as legendas nos tempos da imagem (Aceite)."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from listener.model import ClusterStatus
from listener.pipeline import review
from listener.store.segments import ClusterInsert, SegmentInsert
from listener.ui import media_save as msave
from listener.ui import media_video_dialog as mvd
from tests.test_media_export import _audio_meeting, _speech_meeting
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


# ---------------------------------------------------------------- a faixa de texto (puro)
def test_the_text_track_has_one_sample_per_cue_with_the_speaker_and_simultaneous_cues_share_the_interval():
    from listener.ui.media_cues import Cue, text_track

    alone = [Cue(0, 1200, "Ana", "CFO", "Bom dia."), Cue(1500, 2700, "Bia", "", "Oi.")]
    assert [(s.start_ms, s.end_ms, s.lines) for s in text_track(alone, 10_000)] == [   # a sobra até a seguinte e até 1,5 s
        (0, 1500, ("Ana — CFO: Bom dia.",)), (1500, 4200, ("Bia: Oi.",))]
    both = [Cue(0, 2000, "Ana", "CFO", "Falando."), Cue(1000, 3000, "Bia", "", "Junto.")]
    assert [(s.start_ms, s.end_ms, s.lines) for s in text_track(both, 4000)] == [
        (0, 1000, ("Ana — CFO: Falando.",)), (1000, 2000, ("Ana — CFO: Falando.", "Bia: Junto.")), (2000, 4000, ("Bia: Junto.",))]
    repeated = [Cue(0, 1200, "Bia", "", "Sim."), Cue(1200, 2400, "Bia", "", "Sim.")]  # coladas e iguais: duas, como na imagem
    assert [(s.start_ms, s.end_ms) for s in text_track(repeated, 2400)] == [(0, 1200), (1200, 2400)]
    assert text_track([]) == []


def test_the_text_sample_holds_the_cue_like_the_picture_until_the_next_or_the_end_of_the_file():
    """Revisão do diff: a imagem segura a legenda mais `HOLD_MS` quando nada começa antes; a amostra também, e o fim dela é
    uma troca da imagem (±1 ms)."""
    from listener.ui import media_cues as mc

    single = [mc.Cue(0, 2000, "Ana", "CFO", "Só eu.")]
    assert [(s.start_ms, s.end_ms) for s in mc.text_track(single, 10_000)] == [(0, 3500)]
    assert dict(mc.screens(single, 10_000))[3500] == mc.IDLE                 # a imagem apaga no mesmo ms
    assert [(s.start_ms, s.end_ms) for s in mc.text_track(single, 2800)] == [(0, 2800)]   # não passa o fim do arquivo
    cues = [mc.Cue(0, 2000, "Ana", "CFO", "um"), mc.Cue(2600, 4000, "Bia", "", "dois"), mc.Cue(9000, 9500, "Ana", "CFO", "três")]
    samples = mc.text_track(cues, 20_000)
    assert [(s.start_ms, s.end_ms) for s in samples] == [(0, 2600), (2600, 5500), (9000, 11_000)]
    changes = [t for t, _s in mc.screens(cues, 20_000)]
    for s in samples:
        assert any(abs(s.end_ms - t) <= 1 for t in changes), (s, changes)   # o fim da amostra é uma troca da imagem
        assert any(abs(s.start_ms - t) <= 1 for t in changes)


def test_the_text_of_a_sample_escapes_the_backslash_and_puts_one_line_per_speaker():
    from listener.ui.media_text import ass_text

    assert ass_text(["Ana: a\\b {ok}", "Bia: oi"]) == "Ana: a" + chr(0x2216) + "b {ok}\\NBia: oi"


# ---------------------------------------------------------------- o MP4 com a faixa (Aceite)
def _write_and_read(store: Any, mid: int, cues: list[Any], out: Any) -> tuple[list[str], list[int], list[tuple[int, int, str]]]:
    """Grava o MP4 com a faixa (numa thread, como a tarefa) e relê: os tipos das faixas, os quadros (ms) e as amostras de
    texto com conteúdo (começo e duração em ms, texto)."""
    import av

    from listener.ui import media_audio as ma
    from listener.ui import media_cues as mc
    from listener.ui.media_frames import FrameRenderer
    from listener.ui.media_video import write_video

    plan = ma.plan_audio(store, mid)
    assert plan is not None
    renderer = FrameRenderer("Orçamento 2027", "07/11/2026", cues)

    def run() -> None:
        try:
            write_video(store, plan, mc.screens(cues, plan.duration_ms), renderer.render, out, lambda _s, _f: False,
                        (renderer.width, renderer.height), mc.text_track(cues, plan.duration_ms))
        finally:
            store.close_thread()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(120)
    with av.open(str(out)) as c:
        kinds = [str(s.type) for s in c.streams]
        assert c.streams.subtitles[0].codec_context.name == "mov_text"
        frames = [round(float(f.time) * 1000) for f in c.decode(video=0)]
    with av.open(str(out)) as c:
        text = c.streams.subtitles[0]
        read = [(round(float(p.pts * text.time_base) * 1000), round(float(p.duration * text.time_base) * 1000), bytes(p)[2:].decode())
                for p in c.demux(text) if p.pts is not None and p.size > 2]
    return kinds, frames, read


def _assert_track(cues: list[Any], frames: list[int], read: list[tuple[int, int, str]], duration_ms: int) -> None:
    """Cada amostra relida tem o começo e a duração da sua (±1 ms), o texto dela e um quadro da imagem no começo."""
    from listener.ui.media_cues import text_track

    samples = text_track(cues, duration_ms)
    assert len(read) == len(samples)
    for (start, span, body), sample in zip(read, samples, strict=True):
        assert abs(start - sample.start_ms) <= 1 and abs(span - (sample.end_ms - sample.start_ms)) <= 1
        assert body == chr(10).join(line.replace(chr(92), chr(0x2216)) for line in sample.lines)   # \N vira a quebra de linha
        assert any(abs(start - t) <= 1 for t in frames)                     # a legenda da faixa troca com a imagem


def test_the_mp4_has_video_audio_and_text_and_the_text_has_the_cue_times_of_the_picture(store, tmp_path, qapp):
    pytest.importorskip("av")
    from listener.ui import media_audio as ma
    from listener.ui import media_cues as mc

    mid = _speech_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    cues = mc.build_cues(msave.speeches_for(store, mid, True), plan.pauses, plan.duration_ms)
    kinds, frames, read = _write_and_read(store, mid, cues, tmp_path / "reuniao.mp4")
    assert kinds == ["video", "audio", "subtitle"] and len(cues) == len(read) == 4
    _assert_track(cues, frames, read, plan.duration_ms)
    assert read[0][2] == "Ana — CFO: Bom dia a todos."


def test_a_longer_mp4_keeps_the_text_times_across_blocks_gaps_repeats_and_simultaneous_speech(store, tmp_path, qapp):
    """34 s de áudio (quatro blocos de 10 s): uma legenda em cada bloco, uma cruzando os 10 s, duas iguais coladas, falas
    ao mesmo tempo e um silêncio de mais de 10 s."""
    pytest.importorskip("av")
    from listener.ui.media_cues import Cue

    mid = _audio_meeting(store, seconds=34, pause=None)
    cues = [Cue(500, 2000, "Ana", "CFO", "Começando."), Cue(9200, 11300, "Bia", "", "Cruzando os dez segundos."),
            Cue(11300, 12500, "Bia", "", "Sim."), Cue(12500, 13700, "Bia", "", "Sim."),
            Cue(14000, 16000, "Ana", "CFO", "Falo junto."), Cue(15000, 17000, "Bia", "", "Eu também."),
            Cue(29000, 31000, "Ana", "CFO", "Depois do silêncio a" + chr(92) + "b.")]
    kinds, frames, read = _write_and_read(store, mid, cues, tmp_path / "longa.mp4")
    assert kinds == ["video", "audio", "subtitle"] and len(read) == 8
    _assert_track(cues, frames, read, 34_000)
    assert read[-1][2] == "Ana — CFO: Depois do silêncio a" + chr(0x2216) + "b."


# ---------------------------------------------------------------- o diálogo do texto (U-179)
def test_the_text_dialog_defaults_to_what_the_transcript_shows_with_choose_as_default(qtbot):
    dlg = mvd.VideoTextDialog(None, revised=True, prefer_llm=True)
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Vídeo com legendas" and dlg.llm.text() == "Revisado pela IA"
    assert dlg.original.text() == "Original (como foi transcrito)" and dlg.llm.isEnabled() and dlg.use_llm
    assert dlg.btn_choose.text() == "Escolher onde salvar…" and dlg.btn_choose.isDefault() and dlg.btn_cancel.text() == "Cancelar"
    assert dlg.btn_generate is None and dlg.missing is None
    dlg = mvd.VideoTextDialog(None, revised=True, prefer_llm=False)        # a Transcrição mostrando a original
    qtbot.addWidget(dlg)
    assert dlg.original.isChecked() and not dlg.use_llm


def test_without_the_revised_text_the_dialog_offers_to_generate_it_with_the_cost_and_closes(qtbot):
    asked: list[bool] = []
    dlg = mvd.VideoTextDialog(None, revised=False, prefer_llm=True, cost=0.04, on_generate=lambda: asked.append(True))
    qtbot.addWidget(dlg)
    assert not dlg.llm.isEnabled() and dlg.original.isChecked() and not dlg.use_llm
    assert dlg.missing is not None and dlg.missing.text() == "A revisão pela IA não foi gerada."
    assert dlg.btn_generate is not None and dlg.btn_generate.text() == "Gerar a revisão (~US$ 0,04)"
    dlg.btn_generate.click()
    assert asked == [True] and dlg.result() == 0                           # pediu e fechou, sem salvar
    assert mvd.generate_label(None) == "Gerar a revisão"


def test_the_revision_cost_comes_from_the_job_estimate_and_is_none_without_a_provider(store):
    from tests.test_llm_steps import _provider_ready

    mid = _speech_meeting(store)
    assert mvd.revise_cost(store, mid) is None
    _provider_ready(store)
    cost = mvd.revise_cost(store, mid)
    assert cost is not None and cost > 0


def _fake_video(captured: list[Any]) -> Any:
    def write(_store: Any, _plan: Any, _timeline: Any, _render: Any, target: Any, _tick: Any, _size: Any, captions: Any = ()) -> Any:
        captured.append(captions)
        target.write_bytes(b"mp4")
        return target, 1
    return write


def test_the_choice_in_the_dialog_replaces_what_the_transcript_shows_and_goes_into_the_text_track(qtbot, store, tmp_path, monkeypatch):
    mid = _speech_meeting(store)
    store._x("UPDATE segments SET text_llm = 'Bom dia, pessoal.' WHERE meeting_id = ? AND t_start_ms = 0", (mid,))
    captured: list[Any] = []
    asked: list[tuple[bool, bool]] = []

    def ask(_parent: Any, *, revised: bool, prefer_llm: bool, cost: Any, on_generate: Any) -> bool:
        asked.append((revised, prefer_llm))
        return False                                                         # "Original (como foi transcrito)"

    monkeypatch.setattr(msave, "ask_text", ask)
    monkeypatch.setattr(msave, "write_video", _fake_video(captured))
    win = _window(qtbot, store, mid)
    win.media.video_ready = True
    monkeypatch.setattr(win.media, "_ask_video", lambda _m: str(tmp_path / "v.mp4"))
    assert win.transcript.prefer_llm and win.media.save_video() is not None and asked == [(True, True)]
    qtbot.waitUntil(lambda: win.media.task is None, timeout=20000)
    assert captured and captured[0][0].lines == ("Ana — CFO: Bom dia a todos.",)
    assert win.media.save_video(str(tmp_path / "w.mp4"), prefer_llm=True) is not None   # com o destino dado, sem o diálogo
    qtbot.waitUntil(lambda: win.media.task is None, timeout=20000)
    assert captured[1][0].lines == ("Ana — CFO: Bom dia, pessoal.",) and len(asked) == 1


def test_generate_in_the_dialog_asks_for_the_revision_and_opens_no_save_dialog(qtbot, store, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    win = _window(qtbot, store, _speech_meeting(store))
    win.media.video_ready = True
    revised: list[bool] = []
    saved: list[Any] = []
    monkeypatch.setattr(win.transcript, "request_revise", lambda: revised.append(True))
    monkeypatch.setattr(win.media, "_ask_video", saved.append)
    labels: list[str] = []

    def click() -> None:
        dlg = QApplication.activeModalWidget()
        if not isinstance(dlg, mvd.VideoTextDialog):
            QTimer.singleShot(10, click)
            return
        if dlg.btn_generate is None:
            dlg.reject()
            return
        labels.append(dlg.btn_generate.text())
        dlg.btn_generate.click()

    QTimer.singleShot(0, click)
    assert win.media.save_video() is None and revised == [True] and saved == [] and not win.media.busy
    assert labels == ["Gerar a revisão"]                                      # sem chave da IA: sem valor
