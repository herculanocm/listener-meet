"""F5.27 lote 4 (item 11): o vídeo exportado com a tela gravada. Os tempos puros (a sessão no tempo do arquivo sem as pausas,
as trocas da tela com os buracos, os quadros iguais que não trocam, a junção com as legendas e o quadro por segundo); o MP4 de
1920 x 1080 com vídeo, áudio e texto em que cada quadro gravado aparece no tempo certo (±1 ms, já sem a pausa), os trechos sem
tela com a tela escura e a pausa cortando a tela junto com o áudio; a reunião sem tela no vídeo da F5.19; o cancelamento no
meio sem sobrar o temporário. O PyAV com `importorskip`."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from listener.ui import media_audio as ma
from listener.ui import media_cues as mc
from listener.ui.media_screen import FileClock, Shot, moments, shots
from tests.test_media_export import _speech_meeting

W, H = 640, 360                                  # a tela gravada pequena: o vídeo a amplia para 1440 x 810
# a cor de cada segundo da sessão (o pedaço 0 cobre 1 a 9 s, com a pausa de 4 a 6 s; o pedaço 1, 10 a 12 s)
COLORS = {s: ((s * 53) % 200 + 40, (s * 97) % 200 + 40, (s * 31) % 200 + 40) for s in range(13)}
CHUNKS = ((1, 9), (10, 12))


# ---------------------------------------------------------------- tempos (puro)
def test_the_session_time_goes_to_the_file_time_without_pauses_and_a_paused_instant_is_the_resume():
    clock = FileClock(ma.sound_spans(12_000, [(4000, 6000)]))
    assert clock.duration_ms == 10_000
    assert [clock.at(t) for t in (0, 3999, 4000, 5000, 6000, 6001, 11_999)] == [0, 3999, 4000, 4000, 4000, 4001, 9999]
    assert clock.at(12_000) is None and clock.at(20_000) is None
    assert FileClock(ma.sound_spans(5000, [(0, 1000)])).at(500) == 0


def _frames(seconds: range, value: int = 0) -> bytes:
    """Um pedaço falso: "segundo:valor" de cada quadro (o valor 0 vale o próprio segundo)."""
    return ",".join(f"{s}:{value or s}" for s in seconds).encode()


def _decode(data: bytes) -> Any:
    for item in data.decode().split(","):
        s, value = (int(x) for x in item.split(":"))
        yield s * 1000, np.full((2, 2, 3), value, np.uint8)


def test_shots_mark_holes_skip_equal_frames_and_let_a_frame_after_the_pause_replace_the_paused_ones():
    chunks = [{"idx": 0, "t_start_ms": 1000, "t_end_ms": 9000}, {"idx": 1, "t_start_ms": 10_000, "t_end_ms": 12_000},
              {"idx": 2, "t_start_ms": 12_000, "t_end_ms": 13_000}]
    data = {0: _frames(range(8)), 1: _frames(range(2), 99)}           # o pedaço 2 sumiu (apagado no meio)
    clock = FileClock(ma.sound_spans(12_500, [(4000, 6000)]))
    out = [(t, s.key if isinstance(s, Shot) else None) for t, s in shots(chunks, data.get, _decode, clock)]
    assert out[0] == (0, None)
    assert (1000, (0, 0)) in out and (3000, (0, 2)) in out
    assert [k for t, k in out if t == 4000] == [(0, 3), (0, 4), (0, 5)]      # 4 e 5 s pausados: o de 6 s vale na volta
    assert (7000, None) in out and (8000, (1, 0)) in out                     # o buraco de 9 a 10 s: sem tela
    assert (1, 1) not in [k for _t, k in out]                                # igual ao anterior: não troca
    assert out[-1] == (10_000, None)                                         # o pedaço 2 sumiu: buraco (e o fim)
    times = [t for t, _k in out]
    assert times == sorted(times)


def test_moments_join_screen_and_caption_changes_once_per_ms_with_one_frame_per_second_between():
    captions = [(0, mc.IDLE), (1500, mc.Screen(0))]
    red, blue = Shot((0, 0), np.zeros((1, 1, 3), np.uint8)), Shot((0, 1), np.ones((1, 1, 3), np.uint8))
    screen = iter([(0, None), (1500, red), (1500, blue), (4200, None)])
    out = list(moments(captions, screen, 6000))
    assert [(t, ci, s.key if s else None) for t, ci, s in out] == [
        (0, 0, None), (1000, 0, None), (1500, 1, (0, 1)), (2500, 1, (0, 1)), (3500, 1, (0, 1)), (4200, 1, None), (5200, 1, None)]
    assert list(moments([], iter([]), 1000)) == []


# ---------------------------------------------------------------- o MP4
def _screen_meeting(store: Any) -> int:
    """A reunião de 12 s com a pausa de 4 a 6 s e as falas da F5.19, mais a tela: um quadro por segundo, cada segundo numa cor."""
    from listener.screen_rec.segment import SegmentEncoder

    mid = _speech_meeting(store)
    store.screen.ensure_file(mid)
    for idx, (a, b) in enumerate(CHUNKS):
        enc = SegmentEncoder(W, H, 1920, 1)
        for s in range(a, b):
            r, g, bl = COLORS[s]
            enc.add(np.full((H, W, 4), (bl, g, r, 255), np.uint8), s * 1000)
        seg = enc.close(b * 1000)
        assert seg is not None
        store.screen.insert_chunk(mid, idx=idx, t_start_ms=seg.t_start_ms, t_end_ms=seg.t_end_ms, frames=seg.frames, width=seg.width,
                                  height=seg.height, codec=seg.codec, data=seg.data)
    return mid


def _export(store: Any, mid: int, target: Path, tick: ma.Tick) -> dict[str, Any]:
    """O vídeo com a tela, como o `save_video` monta (na thread da interface) e a tarefa grava (noutra thread)."""
    from listener.ui.media_compose import screen_job
    from listener.ui.media_save import speeches_for

    plan = ma.plan_audio(store, mid)
    assert plan is not None
    cues = mc.build_cues(speeches_for(store, mid, True), plan.pauses, plan.duration_ms)
    job = screen_job(store, plan, "Orçamento 2027", "20/11/2026", cues)
    assert job is not None and job.extra_bytes > 0
    result: dict[str, Any] = {"plan": plan, "cues": cues}

    def run() -> None:
        try:
            result["out"] = job.write(target, tick, mc.screens(cues, plan.duration_ms), mc.text_track(cues, plan.duration_ms))
        except ma.ExportCancelledError:
            result["out"] = "cancelado"
        finally:
            store.close_thread()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(120)
    return result


def _read(out: Path) -> tuple[list[str], tuple[int, int], list[tuple[int, tuple[int, ...], tuple[int, ...]]]]:
    """As faixas, o tamanho e, de cada quadro, o tempo (ms), o pixel no meio da área da tela e o do topo, no meio."""
    import av

    with av.open(str(out)) as c:
        kinds = [str(s.type) for s in c.streams]
        size = (c.streams.video[0].codec_context.width, c.streams.video[0].codec_context.height)
        frames = []
        for f in c.decode(video=0):
            rgb = f.to_ndarray(format="rgb24")
            frames.append((round(float(f.time) * 1000), tuple(int(v) for v in rgb[405, 960]), tuple(int(v) for v in rgb[60, 960])))
    return kinds, size, frames


def _near(a: tuple[int, ...], b: tuple[int, ...], tol: int = 14) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _expected(t_file: int) -> tuple[int, int, int] | None:
    """A cor da tela no tempo do arquivo (None: a tela escura): a sessão é o arquivo até 4 s e mais 2 s depois da pausa."""
    session_s = (t_file if t_file < 4000 else t_file + 2000) // 1000
    if any(a <= session_s < b for a, b in CHUNKS):
        return COLORS[session_s]
    return None


def test_the_mp4_with_the_screen_is_1080p_with_its_tracks_and_each_recorded_frame_at_its_file_time(store, tmp_path, qapp):
    pytest.importorskip("av")
    mid = _screen_meeting(store)
    (tmp_path / "saida").mkdir()
    result = _export(store, mid, tmp_path / "saida" / "tela.mp4", lambda _s, _f: False)
    out: Path = result["out"][0]
    kinds, size, frames = _read(out)
    assert size == (1920, 1080) and kinds == ["video", "audio", "subtitle"]
    times = [t for t, _c, _top in frames]
    for t in (1000, 2000, 3000, 4000, 5000, 6000, 8000, 9000):               # cada troca da tela, sem os 2 s da pausa
        assert any(abs(t - x) <= 1 for x in times), (t, times)
    for t, center, _top in frames:
        want = _expected(t)
        if want is not None:
            assert _near(center, want), (t, center, want)
    assert times == sorted(times) and max(np.diff(times)) <= 1001 and times[-1] < 10_000
    assert [p.name for p in out.parent.iterdir()] == ["tela.mp4"]


def test_without_screen_the_video_shows_the_dark_screen_and_the_pause_cuts_the_screen_with_the_audio(store, tmp_path, qapp):
    pytest.importorskip("av")
    from listener.ui.tokens import DARK

    mid = _screen_meeting(store)
    result = _export(store, mid, tmp_path / "tela.mp4", lambda _s, _f: False)
    out: Path = result["out"][0]
    _kinds, _size, frames = _read(out)
    window = tuple(int(DARK.window[i: i + 2], 16) for i in (1, 3, 5))
    by_time = {t: (center, top) for t, center, top in frames}
    for t in (0, 7000):                                                      # antes do primeiro pedaço e no buraco de 9 a 10 s
        assert _near(by_time[t][1], window), (t, by_time[t])                 # a tela escura da F5.19 inteira (o topo sem tela)
    assert _near(by_time[4000][0], COLORS[6])                                # na volta da pausa, o quadro de 6 s
    shown = [c for _t, c, _top in frames]
    assert not any(_near(c, COLORS[4], 6) or _near(c, COLORS[5], 6) for c in shown)   # os quadros pausados não saem


def test_a_meeting_without_screen_keeps_the_f519_video(store):
    from listener.ui.media_compose import screen_job

    mid = _speech_meeting(store)
    plan = ma.plan_audio(store, mid)
    assert plan is not None and screen_job(store, plan, "t", "", []) is None


def test_cancelling_the_screen_video_in_the_middle_leaves_nothing_behind(store, tmp_path, qapp):
    pytest.importorskip("av")
    mid = _screen_meeting(store)
    folder = tmp_path / "saida"
    folder.mkdir()
    target = folder / "tela.mp4"
    target.write_bytes(b"anterior")
    calls: list[str] = []

    def tick(stage: str, _fraction: float) -> bool:
        calls.append(stage)
        return stage == ma.WRITE and calls.count(ma.WRITE) >= 3            # no meio dos quadros do primeiro bloco

    result = _export(store, mid, target, tick)
    assert result["out"] == "cancelado" and target.read_bytes() == b"anterior" and [p.name for p in folder.iterdir()] == ["tela.mp4"]


def test_save_video_from_the_menu_takes_the_screen_path_in_its_task(qtbot, store, tmp_path, monkeypatch):
    """O "Salvar vídeo com legendas…" da reunião com tela: a estimativa de espaço com a tela, a tarefa da F5.19 (a tela anexada
    na conexão dela enquanto a da interface também a tem) e o MP4 de 1920 x 1080."""
    pytest.importorskip("av")
    from listener.ui import media_save as msave
    from listener.ui.meeting import MeetingWindow

    mid = _screen_meeting(store)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    needs: list[int] = []
    monkeypatch.setattr(msave, "space_problem", lambda _target, need: needs.append(need))
    try:
        assert win.media.save_video(str(tmp_path / "reuniao"), prefer_llm=True) is not None
        qtbot.waitUntil(lambda: win.media.task is None, timeout=60_000)
    finally:
        msave._running.clear()
    out = tmp_path / "reuniao.mp4"
    _kinds, size, _frames = _read(out)
    plan = ma.plan_audio(store, mid)
    assert plan is not None and size == (1920, 1080) and win.message.text() == f"Salvo em {out}."
    assert needs == [msave.estimate_bytes(True, "mp4", plan) + 2 * store.screen.total_bytes(mid)]
