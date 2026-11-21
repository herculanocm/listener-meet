"""F5.27 lote 0: o núcleo da gravação da tela (`listener/screen_rec`): o tamanho, o pedaço codificado e lido de volta, e o laço
do `ScreenRecorder` com relógio e print falsos (nenhuma espera de verdade)."""
from __future__ import annotations

import threading

import numpy as np
import pytest

pytest.importorskip("av")

from listener.platform.screen_types import Frame, ScreenRect
from listener.screen_rec.recorder import FAIL_RETRY_MS, FAILING, OK, ScreenRecorder, percentile
from listener.screen_rec.segment import Segment, SegmentEncoder, decode_segment, fit_size

RECT = ScreenRect(0, 0, 64, 36)


def _bgra(w: int, h: int, seed: int = 0) -> np.ndarray:
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = 40 + seed * 10 % 200, 120, 200   # B, G, R
    arr[: h // 2, : w // 2, 1] = 250
    return arr


def _frame(w: int, h: int, seed: int = 0) -> Frame:
    arr = _bgra(w, h, seed)
    return Frame(memoryview(arr.reshape(-1)), w, h, w * 4)


class FakeClock:
    def __init__(self) -> None:
        self.ms = 0

    def __call__(self) -> int:
        return self.ms


class FakeGrab:
    """O print falso: o tamanho do retângulo pedido (ou `size`), None enquanto `failing`, e um custo em ms no relógio."""

    def __init__(self, clock: FakeClock, cost_ms: int = 0) -> None:
        self.clock, self.cost_ms = clock, cost_ms
        self.failing = False
        self.size: tuple[int, int] | None = None
        self.calls: list[int] = []
        self.rects: list[ScreenRect] = []

    def __call__(self, rect: ScreenRect) -> Frame | None:
        self.calls.append(self.clock.ms)
        self.rects.append(rect)
        self.clock.ms += self.cost_ms
        if self.failing:
            return None
        w, h = self.size or (rect.width, rect.height)
        return _frame(w, h, len(self.calls))


def _recorder(cost_ms: int = 0, **kw: object) -> tuple[ScreenRecorder, FakeClock, FakeGrab, list[Segment], list[tuple[int, str]]]:
    clock = FakeClock()
    grab = FakeGrab(clock, cost_ms)
    segments: list[Segment] = []
    status: list[tuple[int, str]] = []
    rec = ScreenRecorder(grab, RECT, clock, on_segment=segments.append, on_status=lambda s: status.append((clock.ms, s)),
                         **kw)  # type: ignore[arg-type]
    return rec, clock, grab, segments, status


def _run_until(rec: ScreenRecorder, clock: FakeClock, end_ms: int) -> None:
    """O laço do `run`, com o relógio pulando para o próximo compromisso em vez de dormir."""
    while clock.ms < end_ms:
        delay = rec.step()
        clock.ms = min(end_ms, clock.ms + delay)


# ---------------------------------------------------------------- tamanho
def test_fit_size_keeps_ratio_even_dimensions_and_never_enlarges():
    assert fit_size(3840, 2160, 1920) == (1920, 1080)
    assert fit_size(1920, 1080, 1920) == (1920, 1080)
    assert fit_size(1280, 720, 1920) == (1280, 720)            # nunca amplia
    assert fit_size(2160, 3840, 1920) == (1080, 1920)          # retrato: o lado maior é a altura
    assert fit_size(1366, 768, 1280) == (1280, 720)
    assert fit_size(1921, 1081, 4000) == (1920, 1080)          # ímpar desce ao par
    w, h = fit_size(2560, 1600, 1920)
    assert (w, h) == (1920, 1200) and w % 2 == 0 and h % 2 == 0
    with pytest.raises(ValueError):
        fit_size(0, 100, 1920)


# ---------------------------------------------------------------- pedaço
def test_segment_round_trip_keeps_frames_size_times_and_colors():
    times = [5000, 5000 + 1003, 5000 + 2017, 5000 + 3999, 5000 + 5000]
    enc = SegmentEncoder(128, 72, 64, 1)
    for k, t in enumerate(times):
        enc.add(_frame(128, 72, k), t)
    seg = enc.close()
    assert seg is not None and seg.data[4:8] == b"ftyp"
    assert (seg.frames, seg.width, seg.height, seg.codec) == (5, 64, 36, "libx264")
    assert seg.t_start_ms == 5000 and seg.t_end_ms == 5000 + 5000 + 1000    # o último quadro vale um intervalo
    decoded = list(decode_segment(seg.data))
    assert len(decoded) == 5
    for (rel, rgb), t in zip(decoded, times, strict=True):
        assert abs(rel - (t - 5000)) <= 1
        assert rgb.shape == (36, 64, 3)
    rgb = decoded[0][1]
    assert np.abs(rgb[30, 50].astype(int) - [200, 120, 40]).max() <= 12     # BGRA (40, 120, 200) volta como RGB
    assert np.abs(rgb[5, 5].astype(int) - [200, 250, 40]).max() <= 12


def test_segment_encoder_accepts_ndarray_bytes_and_padded_rows():
    enc = SegmentEncoder(64, 36, 1920, 2)
    enc.add(_bgra(64, 36), 0)                                              # ndarray
    enc.add(Frame(_bgra(64, 36).tobytes(), 64, 36, 64 * 4), 500)           # bytes imutáveis
    padded = np.zeros((36, 72, 4), np.uint8)
    padded[:, :64] = _bgra(64, 36)
    enc.add(Frame(padded.tobytes(), 64, 36, 72 * 4), 1000)                 # linhas com folga no fim
    with pytest.raises(ValueError):
        enc.add(_bgra(32, 18), 1500)                                       # outro tamanho é de outro pedaço
    seg = enc.close(1200)
    assert seg is not None and seg.frames == 3 and seg.t_end_ms == 1200   # o fim pedido, dentro do intervalo do último
    assert [t for t, _ in decode_segment(seg.data)] == [0, 500, 1000]


def test_segment_encoder_without_frames_gives_none():
    assert SegmentEncoder(64, 36, 1920, 1).close() is None


# ---------------------------------------------------------------- o laço
def test_late_frames_are_skipped_never_queued():
    rec, clock, grab, segments, _ = _recorder(cost_ms=2500)               # o print leva 2,5 s a 1 quadro por segundo
    _run_until(rec, clock, 10_000)
    rec.stop()
    assert grab.calls == [0, 2501, 5002, 7503]                            # atrasado, tira um já e não repõe os perdidos
    assert rec.stats.frames == 4 and rec.stats.skipped == 4               # 1, 3, 4 e 6 s ficaram sem quadro
    assert sum(s.frames for s in segments) == 4


def test_segment_closes_every_60_seconds():
    rec, clock, _grab, segments, _ = _recorder()
    _run_until(rec, clock, 130_500)
    assert [(s.t_start_ms, s.t_end_ms, s.frames) for s in segments] == [(0, 60_000, 60), (60_000, 120_000, 60)]
    rec.stop()
    assert segments[-1].t_start_ms == 120_000 and segments[-1].frames == 11 and segments[-1].t_end_ms == 130_500
    assert rec.stats.segments == 3 and rec.stats.skipped == 0


def test_segment_closes_on_time_even_without_new_frames():
    rec, clock, grab, segments, _ = _recorder()
    _run_until(rec, clock, 10_500)
    grab.failing = True                                                    # a tela bloqueada: nenhum quadro novo
    _run_until(rec, clock, 60_100)
    assert len(segments) == 1 and segments[0].frames == 11 and segments[0].t_end_ms == 11_000


def test_frame_size_change_closes_the_segment():
    rec, clock, grab, segments, _ = _recorder()
    _run_until(rec, clock, 3_500)
    grab.size = (48, 30)                                                   # o monitor mudou de resolução
    _run_until(rec, clock, 6_500)
    rec.stop()
    assert [(s.frames, s.width, s.height) for s in segments] == [(4, 64, 36), (3, 48, 30)]
    assert segments[1].t_start_ms == 4_000


def test_grab_failure_retries_every_5_s_and_reports_failing_then_ok():
    rec, clock, grab, segments, status = _recorder()
    _run_until(rec, clock, 2_500)                                          # quadros em 0, 1 e 2 s
    grab.failing = True
    _run_until(rec, clock, 70_000)
    fails = [t for t in grab.calls if t >= 3_000]
    assert fails == list(range(3_000, 70_000, FAIL_RETRY_MS))              # de novo a cada 5 s, sem parar
    assert status == [(63_000, FAILING)]                                   # 60 s depois da primeira falha, uma vez
    assert rec.stats.skipped == 0 and rec.stats.failures == len(fails)
    grab.failing = False
    _run_until(rec, clock, 75_500)
    assert status == [(63_000, FAILING), (73_000, OK)]                     # o primeiro quadro bom
    assert grab.calls[-3:] == [73_000, 74_000, 75_000]                     # e a grade volta a 1 por segundo
    rec.stop()
    assert sum(s.frames for s in segments) == 3 + 3


def test_short_grab_failure_gives_no_status():
    rec, clock, grab, _segments, status = _recorder()
    grab.failing = True
    _run_until(rec, clock, 30_000)
    grab.failing = False
    _run_until(rec, clock, 40_000)
    assert status == [] and rec.stats.frames > 0


def test_pause_closes_the_segment_and_resume_captures_again():
    rec, clock, grab, segments, status = _recorder()
    _run_until(rec, clock, 5_500)
    rec.pause()
    _run_until(rec, clock, 100_000)
    assert [(s.frames, s.t_end_ms) for s in segments] == [(6, 5_500)] and grab.calls[-1] == 5_000
    assert rec.paused
    rec.resume()
    _run_until(rec, clock, 102_500)
    assert grab.calls[-3:] == [100_000, 101_000, 102_000]
    rec.stop()
    assert segments[-1].t_start_ms == 100_000 and segments[-1].frames == 3 and status == []


def test_pause_does_not_count_toward_failing():
    rec, clock, grab, _segments, status = _recorder()
    grab.failing = True
    _run_until(rec, clock, 40_000)
    rec.pause()
    _run_until(rec, clock, 200_000)
    rec.resume()
    _run_until(rec, clock, 240_000)
    assert status == []                                                     # 40 s antes e 40 s depois da pausa


def test_set_params_applies_from_the_next_segment():
    rec, clock, grab, segments, _ = _recorder()
    _run_until(rec, clock, 30_500)
    rec.set_params(2, 32)
    _run_until(rec, clock, 63_000)
    assert rec.fps == 2 and rec.max_side == 32
    rec.stop()
    first, second = segments
    assert (first.frames, first.width, first.height) == (60, 64, 36)        # o pedaço em curso segue como estava
    assert (second.frames, second.width, second.height) == (6, 32, 18)      # 60,0 a 62,5 s a 2 por segundo
    assert grab.calls[-6:] == [60_000, 60_500, 61_000, 61_500, 62_000, 62_500]
    with pytest.raises(ValueError):
        rec.set_params(6, 1920)


def test_switch_closes_the_segment_and_records_the_new_rect():
    rec, clock, grab, segments, _ = _recorder()
    _run_until(rec, clock, 5_500)
    other = ScreenRect(1920, 0, 80, 40)
    rec.switch(other)
    _run_until(rec, clock, 8_000)
    rec.stop()
    assert rec.rect == other and grab.rects[-1] == other
    assert [(s.frames, s.width, s.height, s.t_end_ms) for s in segments] == [(6, 64, 36, 5_500), (3, 80, 40, 8_000)]
    assert grab.calls[6] == 5_500                                          # o monitor novo já no passo seguinte


def test_stop_closes_the_last_segment_once():
    rec, clock, _grab, segments, _ = _recorder()
    _run_until(rec, clock, 2_500)
    rec.stop()
    rec.stop()
    assert len(segments) == 1 and segments[0].frames == 3
    assert rec.step() > 0 and len(segments) == 1


def test_run_returns_on_stop_event_and_closes_the_segment():
    rec, clock, grab, segments, _ = _recorder()
    stop = threading.Event()

    def grab_then_stop(rect: ScreenRect) -> Frame | None:
        frame = FakeGrab.__call__(grab, rect)
        if len(grab.calls) == 3:
            stop.set()
        clock.ms += 1000
        return frame

    rec._grab = grab_then_stop                                             # três quadros, um por segundo do relógio falso
    rec.run(stop)
    assert len(segments) == 1 and segments[0].frames == 3


def test_stats_summary_has_percentiles():
    assert percentile([], 50) is None
    assert percentile([5, 1, 3, 2, 4], 50) == 3 and percentile(range(1, 101), 95) == 95
    rec, clock, _grab, _segments, _ = _recorder()
    _run_until(rec, clock, 3_500)
    summary = rec.stats.summary()
    assert summary["frames"] == 4 and summary["grab_ms_p95"] is not None and summary["cycle_ms_p50"] is not None
