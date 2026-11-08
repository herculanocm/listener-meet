"""F5.19, exportar o áudio e o vídeo com legendas, sem a interface: as partes e os tempos das legendas (equilíbrio, mínimo,
sobra, anterior, falas ao mesmo tempo, tempos depois de uma pausa), o plano do áudio como o do player, a mistura e o pico em
-1 dBFS, os arquivos relidos (MP3, FLAC e WAV com a duração da reunião sem as pausas), o MP4 relido pelo PyAV (as duas
faixas, a duração e um quadro em cada troca da tela), o cancelamento sem sobrar arquivo e a tela desenhada numa thread que
não é a da interface. O PyAV e o MP3 do libsndfile com `importorskip`/`skipif`."""
from __future__ import annotations

import itertools
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from listener.capture.audio import encode_flac
from listener.model import EventType
from listener.store.segments import SegmentInsert
from listener.ui import media_audio as ma
from listener.ui import media_cues as mc
from listener.ui.player_source import Chunks
from tests.test_rag_index import _pipeline_job

SR = 16000
HAS_MP3 = "MP3" in sf.available_formats()
LONG = ("Então a gente fecha o orçamento de 2027 na semana que vem e eu mando a planilha revisada com os números do "
        "terceiro trimestre para todo mundo, inclusive para o financeiro.")


def _audio_meeting(store, seconds: int = 12, left: float = 0.2, right: float = 0.6,
                   pause: tuple[int, int] | None = (4000, 6000), tone: bool = False) -> int:
    """L e R em pedaços de 2 s, constantes ou (`tone`) um seno de 440 Hz com essa amplitude (o MP3 não guarda o nível
    contínuo); uma pausa da gravação (eventos `pause` e `resume`)."""
    mid = store.create_meeting("Orçamento 2027", None, None, None)
    for ch, value in (("L", left), ("R", right)):
        for idx in range(seconds // 2):
            n = np.arange(idx * 2 * SR, (idx + 1) * 2 * SR)
            x = (value * np.sin(2 * np.pi * 440 * n / SR) if tone else np.full(SR * 2, value)).astype(np.float32)
            store.insert_chunk(mid, ch, idx, idx * 2000, len(x), SR, 0, encode_flac(x, SR))
    if pause is not None:
        store.add_event(mid, pause[0], EventType.PAUSE)
        store.add_event(mid, pause[1], EventType.RESUME)
    store.finish_meeting(mid, seconds * 1000)
    return mid


def _speech_meeting(store) -> int:
    """A reunião de 12 s com a pausa de 4 a 6 s e três falas: Ana (CFO), Bia (sem cargo) e a fala longa da Ana."""
    mid = _audio_meeting(store)
    job = _pipeline_job(store, mid)
    ana, bia = store.people.create_person("Ana", role="CFO"), store.people.create_person("Bia")
    store.segments.insert_segments([
        SegmentInsert(mid, job, "R", 0, 2000, "Bom dia a todos.", "pyannote", "final", person_id=ana),
        SegmentInsert(mid, job, "R", 2500, 3000, "Oi.", "pyannote", "final", person_id=bia),
        SegmentInsert(mid, job, "R", 7000, 11000, LONG, "pyannote", "final", person_id=ana)])
    return mid


# ---------------------------------------------------------------- legendas (puro)
def test_split_text_balances_parts_under_the_limit_and_keeps_every_word():
    assert mc.split_text("") == [] and mc.split_text("   ") == []
    assert mc.split_text("Oi.") == ["Oi."]
    parts = mc.split_text(LONG, 110)
    assert len(parts) == 2 and all(len(p) <= 110 for p in parts) and " ".join(parts) == " ".join(LONG.split())
    assert abs(len(parts[0]) - len(parts[1])) < 40                       # equilibradas: a última não sobra curta
    many = mc.split_text(" ".join(["palavra"] * 60), 50)                  # 479 caracteres: 10 partes de até 50
    assert all(len(p) <= 50 for p in many) and len(many) == 10 and " ".join(many) == " ".join(["palavra"] * 60)
    assert mc.split_text("x" * 130 + " fim", 110) == ["x" * 130, "fim"]   # a palavra maior que o limite fica sozinha


def test_build_cues_maps_to_the_file_time_splits_long_speech_and_extends_short_cues():
    pauses = [(4000, 6000)]
    speeches = [mc.Speech(0, 2000, "Ana", "CFO", "Bom dia a todos.", 0), mc.Speech(2500, 3000, "Bia", "", "Oi.", 1),
                mc.Speech(7000, 11000, "Ana", "CFO", LONG, 0)]
    cues = mc.build_cues(speeches, pauses, 10_000)
    assert [(c.start_ms, c.end_ms, c.name) for c in cues[:2]] == [(0, 2000, "Ana"), (2500, 3700, "Bia")]   # o mínimo de 1,2 s
    first, second = cues[2], cues[3]
    assert first.start_ms == 5000 and second.end_ms == 9000                 # 7 s e 11 s da reunião, sem os 2 s da pausa
    assert first.end_ms == second.start_ms and 6500 < first.end_ms < 7500   # o tempo dividido pelo tamanho das partes
    assert first.text + " " + second.text == " ".join(LONG.split()) and first.role == "CFO"
    squeezed = mc.build_cues([mc.Speech(0, 200, "Ana", "", "Sim.", 0), mc.Speech(500, 900, "Bia", "", "Não.", 1)], [], 5000)
    assert squeezed[0].end_ms == 500                                        # o mínimo não passa o começo da seguinte
    assert mc.build_cues([mc.Speech(9000, 12000, "Ana", "", "fim", 0)], [], 10_000)[0].end_ms == 10_000
    assert mc.build_cues([mc.Speech(11_000, 12_000, "Ana", "", "fora", 0)], [], 10_000) == []


def test_screens_show_the_previous_cue_hold_after_the_end_and_overlapping_speech():
    cues = [mc.Cue(0, 2000, "Ana", "CFO", "um"), mc.Cue(2500, 4000, "Bia", "", "dois"),
            mc.Cue(3000, 3500, "Ana", "CFO", "três"), mc.Cue(20_000, 21_000, "Bia", "", "quatro")]
    timeline = dict(mc.screens(cues, 30_000))
    assert timeline[0] == mc.Screen(0)                                      # sem anterior
    assert timeline[2500] == mc.Screen(1, 0, False)                         # a anterior terminou: apagada
    assert timeline[3000] == mc.Screen(2, 1, True)                          # falas ao mesmo tempo: a anterior ainda fala
    assert timeline[4000] == mc.Screen(2, 1, False)
    assert timeline[5000] == mc.Screen(1, 0, False)                         # a curta saiu (3,5 + 1,5 s); a longa segue na sobra
    assert timeline[5500] == mc.IDLE                                        # 4 s + 1,5 s: a tela vazia
    assert timeline[20_000] == mc.Screen(3)                                 # 16 s depois: longe demais para a anterior
    assert 22_500 in timeline and timeline[22_500] == mc.IDLE
    times = [t for t, _s in mc.screens(cues, 30_000)]
    assert times == sorted(times) and times[0] == 0 and mc.screens(cues, 0) == []
    states = [s for _t, s in mc.screens(cues, 30_000)]
    assert all(a != b for a, b in itertools.pairwise(states))               # sem duas telas iguais seguidas


# ---------------------------------------------------------------- áudio
def test_sound_spans_drop_pauses_and_an_open_pause_runs_to_the_end():
    assert ma.sound_spans(10_000, []) == ((0, 10_000),)
    assert ma.sound_spans(10_000, [(4000, 6000)]) == ((0, 4000), (6000, 10_000))
    assert ma.sound_spans(10_000, [(0, 1000), (8000, None)]) == ((1000, 8000),)
    assert ma.sound_spans(10_000, [(12_000, 13_000)]) == ((0, 10_000),)


def test_plan_matches_the_player_and_blocks_sum_both_channels_without_pauses(store):
    mid = _audio_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None and plan.rate == SR and plan.channels == ("L", "R") and plan.end_ms == 12_000
    assert plan.spans == ((0, 4000), (6000, 12_000)) and plan.duration_ms == 10_000 and plan.frames == 10 * SR
    chunks = Chunks(store, mid, plan.rate)
    out = np.concatenate(list(ma.blocks(plan, chunks, block_ms=3000)))
    assert len(out) == plan.frames and np.allclose(out, 0.8, atol=1e-3)
    assert ma.measure_peak(plan, chunks, lambda _s, _f: False) == pytest.approx(0.8, abs=1e-3)
    assert ma.plan_audio(store, store.create_meeting("vazia", None, None, None)) is None


def test_gain_never_amplifies_and_brings_a_loud_sum_to_minus_1_dbfs():
    assert ma.gain_for(0.0) == 1.0 and ma.gain_for(0.5) == 1.0 and ma.gain_for(ma.CEILING) == 1.0
    assert ma.gain_for(1.6) * 1.6 == pytest.approx(ma.CEILING)


@pytest.mark.parametrize("key", ["mp3", "flac", "wav"])
def test_the_audio_file_reads_back_with_the_meeting_length_minus_pauses(store, tmp_path, key):
    if key == "mp3" and not HAS_MP3:
        pytest.skip("o libsndfile desta máquina não grava MP3")
    mid = _audio_meeting(store, left=0.5, right=0.7, tone=True)             # a soma (1,2) passaria do teto
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    fmt = next(f for f in ma.available_formats() if f.key == key)
    steps: list[tuple[str, float]] = []

    def record(stage: str, fraction: float) -> bool:
        steps.append((stage, fraction))
        return False

    folder = tmp_path / "saida"
    folder.mkdir()
    out = ma.write_audio(store, plan, folder / f"reuniao{fmt.ext}", fmt, record)
    data, rate = sf.read(str(out), dtype="float32")
    assert rate == SR and abs(len(data) / rate - 10.0) < 0.05 + (0.1 if key == "mp3" else 0)   # o MP3 tem o atraso do codificador
    top = float(np.max(np.abs(data)))
    assert ma.CEILING - 0.01 < top <= (0.95 if key == "mp3" else ma.CEILING + 0.001)   # com perda, o MP3 passa um pouco; nunca estoura
    assert steps[-1] == (ma.WRITE, 1.0) and any(s == ma.PEAK for s, _f in steps)
    assert [p.name for p in folder.iterdir()] == [out.name]                   # o temporário foi trocado, nada sobrou


def test_cancel_and_write_errors_leave_no_temporary_and_keep_the_previous_file(store, tmp_path, monkeypatch):
    mid = _audio_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    fmt = next(f for f in ma.available_formats() if f.key == "wav")
    folder = tmp_path / "saida"
    folder.mkdir()
    target = folder / "reuniao.wav"
    target.write_bytes(b"anterior")
    with pytest.raises(ma.ExportCancelledError):
        ma.write_audio(store, plan, target, fmt, lambda stage, _f: stage == ma.WRITE)
    assert target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["reuniao.wav"]

    def locked(_tmp: Path, _target: Path) -> Path:                          # o destino aberto num player: a troca falha (U-030)
        raise PermissionError(13, "acesso negado")

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError):
        ma.write_audio(store, plan, target, fmt, lambda _s, _f: False)
    assert target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["reuniao.wav"]


def test_format_for_honours_a_typed_extension_and_adds_the_chosen_one():
    formats = ma.FORMATS
    mp3, flac = formats[0], formats[1]
    assert ma.format_for("C:/x/ata.flac", mp3, formats) == ("C:/x/ata.flac", flac)
    assert ma.format_for("C:/x/ata", mp3, formats) == ("C:/x/ata.mp3", mp3)
    assert ma.format_for("C:/x/ata.v1", flac, formats) == ("C:/x/ata.v1.flac", flac)


# ---------------------------------------------------------------- vídeo
def _video(store, tmp_path: Path, qapp) -> tuple[Path, list[tuple[int, mc.Screen]], ma.AudioPlan]:
    from listener.ui.media_frames import FrameRenderer
    from listener.ui.media_save import speeches_for
    from listener.ui.media_video import write_video

    mid = _speech_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    cues = mc.build_cues(speeches_for(store, mid, True), plan.pauses, plan.duration_ms)
    timeline = mc.screens(cues, plan.duration_ms)
    renderer = FrameRenderer("Orçamento 2027", "07/11/2026", cues)
    result: dict[str, object] = {}
    folder = tmp_path / "saida"
    folder.mkdir()

    def run() -> None:                                                      # fora da thread da interface, como a tarefa
        try:
            result["out"] = write_video(store, plan, timeline, renderer.render, folder / "reuniao.mp4", lambda _s, _f: False,
                                        (renderer.width, renderer.height))
        finally:
            store.close_thread()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(60)
    written = result["out"]
    assert isinstance(written, tuple)
    out, frames = written
    assert isinstance(out, Path) and frames > len(timeline)
    return out, timeline, plan


def test_the_mp4_has_h264_and_aac_the_file_length_and_a_frame_at_every_screen_change(store, tmp_path, qapp):
    av = pytest.importorskip("av")
    out, timeline, plan = _video(store, tmp_path, qapp)
    with av.open(str(out)) as c:
        video, audio = c.streams.video[0], c.streams.audio[0]
        assert video.codec_context.name in ("h264", "mpeg4") and audio.codec_context.name == "aac"
        assert (video.codec_context.width, video.codec_context.height) == (1280, 720)
        times = [round(float(f.time) * 1000) for f in c.decode(video=0)]
    with av.open(str(out)) as c:
        heard = sum(f.samples for f in c.decode(audio=0)) / c.streams.audio[0].rate
    assert abs(heard - plan.duration_ms / 1000) < 0.15                     # 10 s: os 12 s menos a pausa (o AAC acolchoa o fim)
    for t, _screen in timeline:
        assert any(abs(t - x) <= 1 for x in times), (t, times[:40])        # um quadro em cada troca, no tempo dela
    assert times == sorted(times) and max(np.diff(times)) <= 1001           # e um por segundo entre as trocas
    assert [p.name for p in out.parent.iterdir()] == ["reuniao.mp4"]


def test_frame_renderer_draws_text_off_the_ui_thread_and_shrinks_long_text(qapp):
    from listener.ui.media_frames import FrameRenderer
    from listener.ui.tokens import DARK

    cues = [mc.Cue(0, 1000, "Ana", "CFO", "Bom dia a todos.", 0), mc.Cue(1000, 2000, "Bia", "", "palavra " * 60, 1)]
    renderer = FrameRenderer("Orçamento 2027", "07/11/2026", cues)
    frames: dict[str, np.ndarray] = {}

    def run() -> None:
        frames["idle"] = renderer.render(mc.IDLE)
        frames["both"] = renderer.render(mc.Screen(1, 0, False))

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    background = np.array([int(DARK.window[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)
    idle, both = frames["idle"], frames["both"]
    assert idle.shape == (720, 1280, 3) and both.dtype == np.uint8
    lower = slice(130, 720)
    assert np.all(idle[lower] == background)                                # sem legenda: só o fundo embaixo do título
    assert np.any(idle[:104] != background)                                 # o título e a data no topo
    assert np.mean(np.any(both[lower] != background, axis=2)) > 0.005       # o texto desenhado
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontMetrics

    def wrapped_height(text: str, fitted: Any) -> int:                     # a altura de verdade, sem o teto do `fit` (R-04)
        return QFontMetrics(fitted.font).boundingRect(QRect(0, 0, renderer.text_w, 100_000), fitted.flags, text).height()

    longest = (LONG + " " + LONG)[: mc.MAX_CHARS + 1].rsplit(" ", 1)[0]     # uma legenda do tamanho máximo, inteira
    assert 100 <= len(longest) <= mc.MAX_CHARS
    crowded = "palavra " * 25                                               # 200 caracteres: encolhe mais
    sizes = []
    for text in (longest, crowded):                                         # a fonte do offscreen não é a do Windows: o
        fitted = renderer.fit(text, renderer.look.text_px)                  # tamanho varia, a regra do `fit` não
        lines = renderer.look.lines * QFontMetrics(fitted.font).lineSpacing()
        assert wrapped_height(text, fitted) <= lines or fitted.font.pixelSize() == renderer.look.min_text_px
        sizes.append(fitted.font.pixelSize())
    assert renderer.look.min_text_px <= sizes[1] <= sizes[0] <= renderer.look.text_px and sizes[1] < renderer.look.text_px


def test_frame_times_put_one_frame_per_change_and_one_per_second_between():
    from listener.ui.media_video import frame_times

    timeline = [(0, mc.IDLE), (2500, mc.Screen(0)), (2600, mc.Screen(1))]
    assert list(frame_times(timeline, 4100)) == [(0, 0), (1000, 0), (2000, 0), (2500, 1), (2600, 2), (3600, 2)]
    assert list(frame_times([], 4100)) == []


# ---------------------------------------------------------------- revisão de arquitetura (A-02, A-03, A-07, A-09, A-19)
def test_the_previous_cue_is_the_one_still_speaking_before_the_last_that_ended():
    """A-09: uma fala longa num canal e duas curtas no outro; na segunda curta, a longa (que ainda fala) fica acima dela."""
    cues = [mc.Cue(0, 8000, "Ana", "CFO", "longa"), mc.Cue(2000, 2500, "Bia", "", "sim"), mc.Cue(4000, 4400, "Bia", "", "ok")]
    index = mc.ScreenIndex(cues)
    assert index.screen_at(4000) == mc.Screen(2, 0, True)
    assert index.screen_at(2000) == mc.Screen(1, 0, True)
    assert index.screen_at(6000) == mc.Screen(0)                               # as curtas saíram: a longa embaixo, sozinha


def test_progress_tick_waits_while_recording_and_reports_only_changes():
    from listener.ui import media_job as mj

    reports: list[tuple[str, int]] = []
    state = {"recording": True, "cancel": False}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            state["recording"] = False

    tick = mj.progress_tick(lambda: state["cancel"], reports.append, lambda: state["recording"], sleep)
    assert tick(ma.PEAK, 0.0) is False and len(sleeps) == 3
    assert reports == [(mj.WAIT, 0), (ma.PEAK, 0)]
    assert tick(ma.PEAK, 0.004) is False and reports[-1] == (ma.PEAK, 0) and len(reports) == 2   # o mesmo inteiro não repete
    state["recording"], state["cancel"] = True, True
    assert tick(ma.WRITE, 0.5) is True and len(sleeps) == 3                     # cancelado: não espera a gravação
    assert len(reports) == 2                                                    # R-02: nem manda progresso por cima


def test_run_job_classifies_cancel_write_errors_and_bugs_without_the_path_in_the_log(tmp_path, caplog):
    import logging

    from listener.ui import media_job as mj

    target = tmp_path / "2026-11-07 Comitê sigiloso.mp4"
    noop = lambda _s, _f: False  # noqa: E731

    def boom(error: BaseException):
        def make(_tick: ma.Tick) -> tuple[Path, int, int]:
            raise error
        return make

    with caplog.at_level(logging.INFO, logger="listener.ui.media"):
        assert mj.run_job("video", "mp4", 7, target, boom(AssertionError()), lambda: "sem arquivo", noop, 1.0).notice == "sem arquivo"
        assert mj.run_job("video", "mp4", 7, target, boom(ma.ExportCancelledError()), lambda: None, noop, 1.0) is mj.CANCELLED
        locked = mj.run_job("audio", "mp3", 7, target, boom(PermissionError(13, f"negado: '{target}.tmp'")), lambda: None, noop, 1.0)
        full = mj.run_job("audio", "wav", 7, target, boom(ma.MediaWriteError()), lambda: None, noop, 1.0)
        bug = mj.run_job("video", "mp4", 7, target, boom(ValueError(f"quebrou em {target}")), lambda: None, noop, 1.0)
    assert isinstance(locked, mj.JobFailure) and "player de áudio ou de vídeo" in locked.dialog and "Word" not in locked.dialog
    assert isinstance(full, mj.JobFailure) and ma.WRITE_FAILED in full.dialog
    assert isinstance(bug, mj.JobFailure) and bug.dialog.startswith("Não consegui gerar o vídeo") and "ValueError" in bug.dialog
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name == "listener.ui.media")
    assert "reunião 7" in logged and "Comitê" not in logged and "ValueError" in logged and "cancelada" in logged


def test_space_and_destination_checks_before_generating(tmp_path, monkeypatch):
    from listener.ui import media_job as mj

    plan = ma.AudioPlan(1, SR, ("L", "R"), ((0, 3_600_000),), (), 3_600_000)
    assert mj.estimate_bytes(False, "wav", plan) == plan.frames * 2 + 44
    assert mj.estimate_bytes(True, "mp4", plan) > mj.estimate_bytes(False, "mp3", plan) > 0
    usage = SimpleNamespace(free=10 * 1024 * 1024)
    monkeypatch.setattr(mj.shutil, "disk_usage", lambda _p: usage)
    problem = mj.space_problem(tmp_path / "x.wav", mj.estimate_bytes(False, "wav", plan))
    assert problem is not None and "Pouco espaço livre" in problem
    usage.free = 10 * 1024 ** 3
    assert mj.space_problem(tmp_path / "x.wav", mj.estimate_bytes(False, "wav", plan)) is None
    existing = tmp_path / "antigo.mp3"
    existing.write_bytes(b"x")
    assert not mj.held_by_another(tmp_path / "novo.mp3") and not mj.held_by_another(existing)

    def denied(*_a: object, **_k: object) -> object:
        raise PermissionError(13, "em uso")

    monkeypatch.setattr(Path, "open", denied)
    assert mj.held_by_another(existing)


def test_a_failed_soundfile_write_becomes_a_write_error_and_leaves_nothing(store, tmp_path, monkeypatch):
    """A-02: o soundfile 0.14 confere o gravado por `assert` (o disco cheio); vira `MediaWriteError`, um `OSError` sem o
    caminho, e o temporário sai."""
    mid = _audio_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    fmt = next(f for f in ma.available_formats() if f.key == "wav")

    def full(_self: object, _data: object) -> None:
        raise AssertionError

    monkeypatch.setattr(sf.SoundFile, "write", full)
    folder = tmp_path / "saida"
    folder.mkdir()
    with pytest.raises(ma.MediaWriteError) as caught:
        ma.write_audio(store, plan, folder / "reuniao.wav", fmt, lambda _s, _f: False)
    assert isinstance(caught.value, OSError) and str(folder) not in str(caught.value) and list(folder.iterdir()) == []


def test_pick_codec_skips_codecs_that_do_not_exist_or_do_not_open():
    """A-03: só codificadores de CPU, na ordem, e o que não abre passa ao próximo."""
    from listener.ui.media_video import CODECS, pick_codec

    class FakeContext:
        def __init__(self, name: str):
            self.name = name

        def open(self) -> None:
            if self.name == "libx264":
                raise RuntimeError("não abre")

    class FakeAv:
        class CodecContext:
            @staticmethod
            def create(name: str, _mode: str) -> FakeContext:
                if name == "libopenh264":
                    raise ValueError("desconhecido")
                return FakeContext(name)

    assert CODECS == ("libx264", "libopenh264", "mpeg4") and pick_codec(FakeAv, (1280, 720)) == "mpeg4"

    class NoCodecs:
        class CodecContext:
            @staticmethod
            def create(_name: str, _mode: str) -> FakeContext:
                raise ValueError("nada")

    with pytest.raises(RuntimeError):
        pick_codec(NoCodecs, (1280, 720))


# ---------------------------------------------------------------- revisão do diff (R-01, R-03, R-04)
def test_overall_percent_maps_both_passes_to_one_number():
    from listener.ui.media_save import AUDIO, VIDEO, overall_percent

    assert overall_percent(VIDEO, ma.PEAK, 100) == 5 and overall_percent(VIDEO, ma.WRITE, 0) == 5
    assert overall_percent(VIDEO, ma.WRITE, 100) == 100
    assert overall_percent(AUDIO, ma.PEAK, 50) == 15 and overall_percent(AUDIO, ma.WRITE, 50) == 65


def _second_full_write() -> tuple[ma.Tick, list[float]]:
    """Um `tick` que deixa passar o último bloco (WRITE em 1,0) e cancela na conferência seguinte, a do fecho (R-01)."""
    full: list[float] = []

    def tick(stage: str, fraction: float) -> bool:
        if stage == ma.WRITE and fraction >= 1.0:
            full.append(fraction)
        return len(full) >= 2

    return tick, full


def test_cancel_after_the_audio_file_closes_keeps_the_previous_one(store, tmp_path):
    mid = _audio_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    folder = tmp_path / "saida"
    folder.mkdir()
    target = folder / "reuniao.wav"
    target.write_bytes(b"anterior")
    tick, full = _second_full_write()
    with pytest.raises(ma.ExportCancelledError):
        ma.write_audio(store, plan, target, next(f for f in ma.available_formats() if f.key == "wav"), tick)
    assert len(full) == 2 and target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["reuniao.wav"]


def test_cancelling_the_real_mp4_leaves_nothing_open_or_behind(store, tmp_path, qapp):
    """R-04 (b) e R-01: o `write_video` com o contêiner de verdade, cancelado no primeiro bloco (o PyAV fecha antes de o
    temporário sair, A-21) e depois do trailer com o `+faststart`; nos dois, o anterior fica e nada sobra."""
    pytest.importorskip("av")
    from listener.ui.media_frames import FrameRenderer
    from listener.ui.media_save import speeches_for
    from listener.ui.media_video import write_video

    mid = _speech_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None
    cues = mc.build_cues(speeches_for(store, mid, True), plan.pauses, plan.duration_ms)
    timeline = mc.screens(cues, plan.duration_ms)
    renderer = FrameRenderer("Orçamento 2027", "", cues)
    folder = tmp_path / "saida"
    folder.mkdir()
    target = folder / "reuniao.mp4"
    target.write_bytes(b"anterior")

    def run(tick: ma.Tick) -> list[str]:
        seen: list[str] = []

        def body() -> None:
            try:
                write_video(store, plan, timeline, renderer.render, target, tick, (renderer.width, renderer.height))
            except ma.ExportCancelledError:
                seen.append("cancelado")
            finally:
                store.close_thread()

        thread = threading.Thread(target=body)
        thread.start()
        thread.join(60)
        return seen

    assert run(lambda stage, _f: stage == ma.WRITE) == ["cancelado"]
    assert target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["reuniao.mp4"]
    tick, full = _second_full_write()
    assert run(tick) == ["cancelado"] and len(full) == 2
    assert target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["reuniao.mp4"]
