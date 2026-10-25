"""F5 L4, player único (item 10, F5A-25): funções puras (pausas da gravação, mistura com ganho ½, posição pelas âncoras), o
motor com um fluxo de saída falso (L e R misturados, pausa pulada, fim da reunião, reamostragem para a taxa do dispositivo,
um canal num intervalo), posição pelos quadros escritos menos a latência, troca de ponto, pausar, ±10 s, parar, e os
avisos (sem dispositivo, sem o arquivo de áudio do mês sem criá-lo, reunião sem áudio); a barra do player. Nada de áudio
real: a fábrica do fluxo é injetada (`tests/fake_player.py`)."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.model import EventType
from listener.ui import player as pl
from listener.ui.player import PAUSED, PLAYING, STOPPED, PlaybackEngine, PlayRequest
from tests.fake_player import FakeOutput, FakeOutputs

pytest.importorskip("PySide6")
SR = 16000


def _meeting(store, seconds: int = 6, left: float = 0.2, right: float = 0.6, pause: tuple[int, int] | None = (1000, 3000)) -> int:
    """Reunião sintética: L e R constantes em pedaços de 2 s; uma pausa da gravação (eventos `pause` e `resume`)."""
    mid = store.create_meeting("Sintética", None, None, None)
    for ch, value in (("L", left), ("R", right)):
        for idx in range(seconds // 2):
            x = np.full(SR * 2, value, np.float32)
            store.insert_chunk(mid, ch, idx, idx * 2000, len(x), SR, 0, encode_flac(x, SR))
    if pause is not None:
        store.add_event(mid, pause[0], EventType.PAUSE)
        store.add_event(mid, pause[1], EventType.RESUME)
    store.finish_meeting(mid, seconds * 1000)
    return mid


def _wait(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("tempo esgotado esperando o player")
        time.sleep(0.005)


def test_pure_helpers_pauses_mix_and_position():
    ev = [{"type": "pause", "t_ms": 1000}, {"type": "pause", "t_ms": 1500}, {"type": "resume", "t_ms": 3000},
          {"type": "marker", "t_ms": 3500}, {"type": "resume", "t_ms": 3600}, {"type": "pause", "t_ms": 5000}]
    spans = pl.pause_intervals(ev)
    assert spans == [(1000, 3000), (5000, None)]                       # a pausa repetida vale a primeira; sem retomada: até o fim
    assert pl.skip_paused(500, spans) == 500 and pl.skip_paused(1000, spans) == 3000 and pl.skip_paused(2999, spans) == 3000
    assert pl.skip_paused(6000, spans) is None and pl.next_pause(500, spans) == 1000 and pl.next_pause(3000, spans) == 5000
    mixed = pl.mix(np.full(4, 0.2, np.float32), np.full(2, 0.6, np.float32))
    assert mixed.dtype == np.float32 and np.allclose(mixed, [0.4, 0.4, 0.1, 0.1])     # ganho ½; o canal curto completa com silêncio
    anchors = [(0, 0), (16000, 3000)]                                  # 1 s tocado, depois a pausa de 2 s pulada
    assert pl.position_ms(anchors, 8000, SR) == 500 and pl.position_ms(anchors, 24000, SR) == 3500
    assert pl.position_ms([(0, 1450)], 0, SR) == 1450


def test_engine_mixes_left_and_right_skips_the_pause_and_stops_at_the_end(store):
    mid = _meeting(store)
    outs = FakeOutputs(rate=SR)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 0, title="Sintética"))
    _wait(lambda: not eng.alive)
    out = outs.opened[0]
    y = out.frames
    assert len(y) == 4 * SR and np.allclose(y, 0.4, atol=1e-3)         # 6 s menos a pausa de 2 s, (0,2 + 0,6) / 2
    assert out.calls == ["start", "stop", "close"]                     # o fim espera o buffer tocar (stop), depois fecha
    st = eng.snapshot()
    assert (st.state, st.meeting_id, st.t_ms, st.duration_ms, st.title) == (STOPPED, mid, 6000, 6000, "Sintética")
    # começando dentro da pausa: o som sai do fim dela; um canal só, num intervalo (os 5 s da revisão)
    eng.play(PlayRequest(mid, 1500, end_ms=4000, channel="R"))
    _wait(lambda: not eng.alive)
    clip = outs.opened[1].frames
    assert len(clip) == SR and np.allclose(clip, 0.6, atol=1e-3)       # de 3000 a 4000 ms, só o R, ganho 1


def test_engine_resamples_to_the_device_rate(store):
    mid = _meeting(store, seconds=4, pause=None)
    outs = FakeOutputs(rate=48000)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: not eng.alive)
    y = outs.opened[0].frames
    assert abs(len(y) - 4 * 48000) <= 48 and np.allclose(y[4800:-4800], 0.4, atol=2e-3)   # estado entre as janelas: sem emenda


def test_position_switch_point_pause_seek_and_stop(store):
    mid = _meeting(store, seconds=8, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, latency=0.05, permits=permits)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 1000))
    assert eng.snapshot().state == PLAYING and eng.snapshot().t_ms == 1000            # na hora, antes da thread
    permits.release(4)                                                 # 4 blocos de 0,25 s escritos
    _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) == 4)
    _wait(lambda: eng.snapshot().t_ms > 1000)
    assert eng.snapshot().t_ms == 1000 + (4 * SR // 4 - 0.05 * SR) * 1000 // SR       # 1 s escrito menos 50 ms de latência
    eng.play(PlayRequest(mid, 6000))                                   # outro ponto: troca o som, no mesmo fluxo
    permits.release(1)                                                 # o write em voo termina; o comando vem em seguida
    _wait(lambda: "abort" in outs.opened[0].calls and eng.snapshot().t_ms == 6000)
    permits.release(4)
    _wait(lambda: len(outs.opened[0].chunks) == 9)
    _wait(lambda: eng.snapshot().t_ms > 6000)
    assert len(outs.opened) == 1 and eng.snapshot().t_ms == 6950
    eng.pause()
    permits.release(1)
    _wait(lambda: outs.opened[0].calls.count("abort") == 2)
    st = eng.snapshot()
    assert st.state == PAUSED and st.t_ms == 6950
    eng.seek(+10_000)                                                  # em pausa: só a posição anda, presa ao fim
    _wait(lambda: eng.snapshot().t_ms == 7999)
    eng.seek(-3000)
    _wait(lambda: eng.snapshot().t_ms == 4999)
    permits.release(1000)
    eng.resume()
    _wait(lambda: not eng.alive)
    st = eng.snapshot()
    assert st.state == STOPPED and st.t_ms == 8000                     # tocou de 4999 até o fim
    eng.play(PlayRequest(mid, 0))
    eng.stop()
    _wait(lambda: not eng.alive)
    assert eng.snapshot().state == STOPPED and outs.opened[-1].calls[-1] == "close"
    assert eng.shutdown() is True


def test_errors_without_device_without_audio_file_and_without_audio(store):
    mid = _meeting(store, seconds=2, pause=None)
    eng = PlaybackEngine(store, FakeOutputs(fail=OSError("PortAudio library not found")))
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: not eng.alive)
    st = eng.snapshot()
    assert st.state == pl.ERROR and "sem dispositivo de saída" in st.message and "PortAudio" in st.message
    silent = store.create_meeting("sem áudio", None, None, None)
    ok = PlaybackEngine(store, FakeOutputs())
    ok.play(PlayRequest(silent, 0))
    _wait(lambda: not ok.alive)
    assert ok.snapshot().state == pl.ERROR and "não tem áudio gravado" in ok.snapshot().message
    gone = store.create_meeting("arquivo sumiu", None, None, None)
    store._x("UPDATE meetings SET audio_file = 'listener-audio-1999-01.db' WHERE id = ?", (gone,))
    ok.play(PlayRequest(gone, 0))
    _wait(lambda: not ok.alive)
    assert "listener-audio-1999-01.db" in ok.snapshot().message
    assert not (store.base_dir / "listener-audio-1999-01.db").exists()   # o player não cria o arquivo do mês vazio
    ok.play(PlayRequest(9999, 0))
    _wait(lambda: not ok.alive)
    assert "não existe" in ok.snapshot().message

    class Unplugged(FakeOutput):                                       # o fone sai no meio do som
        def write(self, data: np.ndarray) -> object:
            if self.chunks:
                raise OSError("dispositivo removido")
            return super().write(data)

    dying = Unplugged()
    eng = PlaybackEngine(store, lambda: dying)
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: not eng.alive)
    assert eng.snapshot().message == "a saída de áudio parou (dispositivo removido)" and dying.calls[-1] == "close"


def test_player_signals_guard_and_bar(qtbot, store):
    from listener.ui.player import Player, PlayerBar

    mid = _meeting(store, seconds=4, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits)
    player = Player(store, outs)
    asked: list[PlayRequest] = []

    def refuse(req: PlayRequest) -> bool:                              # Q9: recusou
        asked.append(req)
        return False

    player.guard = refuse
    assert player.play_from(mid, 0) is False and player.requests == [] and len(asked) == 1
    player.guard = None
    bar = PlayerBar(player, mid, title=lambda: "Sintética", start=lambda: 1000)
    qtbot.addWidget(bar)
    states: list[str] = []
    player.state_changed.connect(lambda st: states.append(st.state))
    bar.toggle()                                                       # nada carregado: começa esta reunião em `start()`
    assert player.requests[-1] == PlayRequest(mid, 1000, None, None, "Sintética")
    assert states[-1] == PLAYING and bar.btn_play.text() == "Pausar" and "Sintética" in bar.what.text()
    permits.release(2)
    qtbot.waitUntil(lambda: bool(outs.opened) and len(outs.opened[0].chunks) == 2, timeout=3000)
    bar.toggle()
    permits.release(1000)
    assert player.state().state == PAUSED and bar.btn_play.text() == "Tocar" and bar.btn_back.isEnabled()
    other = PlayerBar(player, mid + 1)
    qtbot.addWidget(other)
    assert other.time.text() == "--:-- / --:--" and "outra reunião" in other.what.text() and not other.btn_fwd.isEnabled()
    player.stop(meeting_id=mid + 1)                                    # fechar outra reunião não para este som
    assert player.state().state == PAUSED
    player.stop(meeting_id=mid)
    qtbot.waitUntil(lambda: player.state().state == STOPPED and not player.engine.alive, timeout=3000)
    assert states[-1] == STOPPED and bar.btn_play.text() == "Tocar"
    assert player.shutdown() is True
