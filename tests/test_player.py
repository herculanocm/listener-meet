"""F5 L4, player único (item 10, F5A-25): funções puras (pausas da gravação, mistura com ganho ½, posição pelas âncoras), o
motor com um fluxo de saída falso (L e R misturados, pausa pulada, fim da reunião, reamostragem para a taxa do dispositivo,
um canal num intervalo), posição pelos quadros escritos menos a latência, troca de ponto, pausar, ±10 s, parar, e os
avisos (sem dispositivo, sem o arquivo de áudio do mês sem criá-lo, reunião sem áudio); a barra do player. R7-02: um
dispositivo em tempo real de 0,18 s de buffer com a decodificação atrasada não fica sem som, o `underflowed` é contado e
vai para o log, e o comando que sobra numa queda relança a thread. R7-04: ±10 s em tempo de áudio através das pausas da
gravação. Nada de áudio real: a fábrica do fluxo é injetada (`tests/fake_player.py`)."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.model import EventType
from listener.ui import player as pl
from listener.ui.player import PAUSED, PLAYING, STOPPED, PlaybackEngine, PlayRequest
from tests.fake_player import FakeOutput, FakeOutputs, RealtimeOutput

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


def test_prewarm_audio_imports_in_the_background_and_never_raises(monkeypatch):
    """U-031: o `sounddevice` (PortAudio) na thread de quem chama, a da interface, a mesma que abre o microfone, e o
    `scipy.signal` numa thread de fundo; sem PortAudio nada levanta (o player avisa quando tocar)."""
    seen: list[tuple[str, str]] = []

    def fake_import(name: str) -> None:
        seen.append((name, threading.current_thread().name))
        if name == "sounddevice":
            raise OSError("PortAudio library not found")

    monkeypatch.setattr(pl.importlib, "import_module", fake_import)
    thread = pl.prewarm_audio()
    thread.join(5)
    assert not thread.is_alive() and thread.daemon
    assert seen == [("sounddevice", threading.current_thread().name), ("scipy.signal", "prewarm-audio")]


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


def test_the_warmup_pause_releases_the_device_and_resume_reopens_it(store):
    """U-043 (A-04): a pausa do warm-up fecha o fluxo (o PortAudio pode reler os dispositivos com o fone que chegou) e o
    player segue pausado no mesmo ponto; a retomada abre outro fluxo pela fábrica e toca dali até o fim."""
    mid = _meeting(store, seconds=8, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 1000))
    permits.release(4)
    _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) == 4)
    _wait(lambda: outs.opened[0].entered == 5)
    eng.pause("pausado para o warm-up", release=True)
    permits.release(1)
    _wait(lambda: outs.opened[0].calls[-1] == "close")
    st = eng.snapshot()
    assert st.state == PAUSED and st.message == "pausado para o warm-up" and len(outs.opened) == 1
    permits.release(1000)
    eng.resume()
    _wait(lambda: not eng.alive)
    assert len(outs.opened) == 2 and eng.snapshot().state == STOPPED
    assert abs(len(outs.opened[1].frames) - (8000 - st.t_ms) * SR // 1000) <= SR // 50     # tocou do ponto pausado ao fim
    eng.play(PlayRequest(mid, 0))
    eng.pause(release=True)
    _wait(lambda: outs.opened[-1].calls[-1] == "close")
    eng.stop()                                                         # parar depois de soltar: o estado vira parado
    _wait(lambda: eng.snapshot().state == STOPPED)
    assert eng.shutdown() is True


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
    _wait(lambda: outs.opened[0].entered == 5)                         # o 5º bloco já veio da alimentadora: write em voo
    eng.play(PlayRequest(mid, 6000))                                   # outro ponto: troca o som, no mesmo fluxo
    permits.release(1)                                                 # o write em voo termina; o comando vem em seguida
    _wait(lambda: "abort" in outs.opened[0].calls and eng.snapshot().t_ms == 6000)
    permits.release(4)
    _wait(lambda: len(outs.opened[0].chunks) == 9)
    _wait(lambda: eng.snapshot().t_ms > 6000)
    assert len(outs.opened) == 1 and eng.snapshot().t_ms == 6950
    _wait(lambda: outs.opened[0].entered == 10)
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

    class Refuses(FakeOutput):                                         # some entre abrir e começar
        def start(self) -> None:
            raise OSError("Invalid device")

    refused = Refuses()
    eng = PlaybackEngine(store, lambda: refused)
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: not eng.alive)
    assert eng.snapshot().message == "sem dispositivo de saída de áudio (Invalid device)" and refused.calls[-1] == "close"


def test_player_signals_guard_and_bar(qtbot, store):
    from listener.ui.player import Player
    from listener.ui.player_bar import PlayerBar                  # F5.4 L4: a barra saiu do `ui/player.py`

    mid = _meeting(store, seconds=4, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits)
    player = Player(store, outs)
    asked: list[PlayRequest] = []

    def refuse(req: PlayRequest) -> bool:                              # Q9: recusou
        asked.append(req)
        return False

    player.guard = refuse
    reread: list[int] = []
    player.before_play = lambda: reread.append(1)                      # U-043: relê os dispositivos só se vai tocar
    assert player.play_from(mid, 0) is False and not player.requests and len(asked) == 1 and reread == []
    player.guard = None
    bar = PlayerBar(player, mid, title=lambda: "Sintética", start=lambda: 1000)
    qtbot.addWidget(bar)
    states: list[str] = []
    player.state_changed.connect(lambda st: states.append(st.state))
    bar.toggle()                                                       # nada carregado: começa esta reunião em `start()`
    assert player.requests[-1] == PlayRequest(mid, 1000, None, None, "Sintética") and reread == [1]
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


@pytest.mark.lento                                           # D-050: tempo real
def test_slow_decoding_never_starves_a_small_realtime_device_and_underflows_are_logged(store, monkeypatch, caplog):
    """R7-02: um dispositivo de 0,18 s de buffer tocando em tempo real e cada janela (0,5 s pelo `decode_s`) decodificada
    300 ms mais devagar: nenhum buraco no som, porque a alimentadora decodifica à frente numa fila limitada e o `write` nunca
    espera decodificação (ela nem roda na thread que escreve). O `underflowed` que o dispositivo devolve é contado e vai
    para o log quando o som para."""
    mid = _meeting(store, seconds=2, pause=None)
    real = PlaybackEngine._decode
    threads: list[str] = []

    def slow(self, *args, **kwargs):
        threads.append(threading.current_thread().name)
        time.sleep(0.3)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PlaybackEngine, "_decode", slow)
    out = RealtimeOutput(rate=SR, buffer_s=0.18)
    eng = PlaybackEngine(store, lambda: out, decode_s=0.5)
    with caplog.at_level(logging.INFO, logger="listener.ui.player"):
        eng.play(PlayRequest(mid, 0))
        _wait(lambda: not eng.alive, timeout=10.0)
        assert len(out.frames) == 2 * SR and np.allclose(out.frames, 0.4, atol=1e-3)
        assert out.gaps == [] and (eng.underflows, eng.snapshot().state) == (0, STOPPED)
        assert threads == ["player-feed"] * 4                         # 2 s em janelas de 0,5 s, todas fora da escritora
        assert "2.0 s tocados, 0 buraco(s) na saída (underflow)" in caplog.text

        class Holes(FakeOutput):                                       # um dispositivo que diz ter ficado sem som
            def write(self, data: np.ndarray) -> object:
                super().write(data)
                return len(self.chunks) % 2 == 0

        holes = Holes()
        counted = PlaybackEngine(store, lambda: holes)
        counted.play(PlayRequest(mid, 0))
        _wait(lambda: not counted.alive)
    assert len(holes.chunks) == 8 and counted.underflows == 4 and "4 buraco(s) na saída (underflow)" in caplog.text


def test_a_command_left_when_the_writer_fails_starts_a_new_writer(store, monkeypatch):
    """R7-02: um comando que chega enquanto a escritora cai não se perde: o `finally` dela relança a thread, que o trata."""
    mid = _meeting(store, seconds=2, pause=None)
    outs = FakeOutputs(rate=SR)
    eng = PlaybackEngine(store, outs)
    real = PlaybackEngine._step
    failed = threading.Event()

    def failing(self, sess):
        if not failed.is_set():
            failed.set()
            self.play(PlayRequest(mid, 1000))                          # o clique em outro ponto chega durante a queda
            raise RuntimeError("falha simulada")
        return real(self, sess)

    monkeypatch.setattr(PlaybackEngine, "_step", failing)
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: failed.is_set() and not eng.alive)
    st = eng.snapshot()
    assert (st.state, st.t_ms) == (STOPPED, 2000) and len(outs.opened) == 2 and len(outs.opened[1].frames) == SR
    assert outs.opened[0].calls[-1] == "close" and eng.shutdown() is True


@pytest.mark.lento                                           # D-050: tempo real
def test_shutdown_waits_for_a_feeder_slower_than_the_writer_join(store, monkeypatch):
    """R8-03: uma janela da alimentadora mais lenta que a espera da escritora por ela (`FEED_JOIN_S`): o `shutdown` não
    devolve True com a alimentadora viva (o banco fecharia embaixo dela); vencido o prazo diz False, e com prazo bastante
    espera a escritora e a alimentadora e só então devolve True, sem thread do player viva."""
    mid = _meeting(store, seconds=4, pause=None)
    real = PlaybackEngine._decode
    entered = threading.Event()
    monkeypatch.setattr(pl, "FEED_JOIN_S", 0.3)                                          # a espera da escritora, encurtada

    def slow(self, *args, **kwargs):
        entered.set()
        time.sleep(pl.FEED_JOIN_S + 0.5)                                                 # mais que a espera da escritora
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PlaybackEngine, "_decode", slow)
    eng = PlaybackEngine(store, FakeOutputs(rate=SR))
    eng.play(PlayRequest(mid, 0))
    assert entered.wait(3.0)
    threads = [eng._thread, *eng._feeders]                                               # a escritora e a alimentadora
    t0 = time.monotonic()
    assert eng.shutdown(timeout_s=0.2) is False and time.monotonic() - t0 >= 0.15        # o prazo venceu: ainda há thread
    assert eng.shutdown(timeout_s=10.0) is True
    assert len(threads) == 2 and not any(t is not None and t.is_alive() for t in threads) and not eng._feeders


def test_the_pause_notice_survives_commands_handled_late(store):
    """R7-01: a pausa com aviso (o warm-up, a gravação do sistema inteiro) leva o aviso no comando: a thread, que trata os
    comandos depois do bloco em voo, reescreve estado e aviso ao tratar um "retomar" antigo, e a pausa seguinte os devolve."""
    mid = _meeting(store, seconds=4, pause=None)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 0))
    _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 1)                 # um bloco em voo: os comandos esperam
    eng.pause("pausado para o warm-up")
    eng.resume()
    eng.pause("pausado: sistema inteiro")
    assert (eng.snapshot().state, eng.snapshot().message) == (PAUSED, "pausado: sistema inteiro")
    permits.release(1)                                                               # pausa, retoma (reescreve) e pausa
    _wait(lambda: not eng._cmds and eng._feed is None)                               # os três tratados: nada a produzir
    assert outs.opened[0].calls.count("abort") == 1
    assert (eng.snapshot().state, eng.snapshot().message) == (PAUSED, "pausado: sistema inteiro")
    eng.resume()
    assert (eng.snapshot().state, eng.snapshot().message) == (PLAYING, "")           # retomar tira o aviso
    permits.release(1000)
    assert eng.shutdown() is True


def test_seek_target_moves_in_audio_time_across_recording_pauses():
    """R7-04: ±10 s em tempo de áudio. Para trás, alvo em [a, b) vira a - (b - alvo), repetindo, piso 0; para frente,
    b + (alvo - a); teto no fim do áudio; uma pausa que vai até o fim não tem áudio depois."""
    pauses = [(10_000, 70_000)]
    assert (pl.audio_ms(73_000, pauses), pl.audio_ms(40_000, pauses), pl.audio_ms(5_000, pauses)) == (13_000, 10_000, 5_000)
    assert (pl.meeting_ms(13_000, pauses), pl.meeting_ms(10_000, pauses), pl.meeting_ms(3_000, pauses)) == (73_000, 70_000, 3_000)
    assert pl.seek_target(73_000, -10_000, pauses, 90_000) == 3_000           # 10 - (70 - 63): antes, preso nos 70 s
    assert pl.seek_target(3_000, -10_000, pauses, 90_000) == 0                 # piso 0
    assert pl.seek_target(3_000, 10_000, pauses, 90_000) == 73_000             # 70 + (13 - 10)
    assert pl.seek_target(0, 10_000, pauses, 90_000) == 70_000                 # o começo da pausa: o fim dela
    assert pl.seek_target(85_000, 10_000, pauses, 90_000) == 89_999            # teto no fim do áudio
    two = [(10_000, 20_000), (25_000, 40_000)]
    assert pl.seek_target(45_000, -15_000, two, 60_000) == 5_000               # 30 s cai na 2ª, 15 s na 1ª: 10 - (20 - 15)
    assert pl.seek_target(5_000, 15_000, two, 60_000) == 45_000                # 5 s até a 1ª, 5 s entre as duas, 5 s depois
    assert pl.seek_target(45_000, 10_000, [(50_000, None)], 60_000) == 50_000  # pausa até o fim: o player para ali
    assert pl.seek_target(8_000, -10_000, [(0, 5_000)], 60_000) == 5_000       # pausa no começo: o primeiro áudio


def test_minus_ten_seconds_crosses_a_recording_pause_while_playing_and_paused(store):
    """R7-04, o caso do revisor: pausa da gravação de 10 a 70 s, tocando de 73 s, três vezes -10 s: 3,25 s (o bloco em voo
    andou 0,25 s), 0 e 0, sem ficar preso nos 70 s; pausado, a posição segue a mesma regra (0 -> 70 -> 80 -> 70 -> 0 s)."""
    mid = _meeting(store, seconds=90, pause=(10_000, 70_000))
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits)
    eng = PlaybackEngine(store, outs)
    eng.play(PlayRequest(mid, 73_000))
    try:
        for n, expected in enumerate((3_250, 0, 0), start=1):
            _wait(lambda n=n: bool(outs.opened) and outs.opened[0].entered == n)     # um bloco em voo
            eng.seek(-pl.SEEK_MS)
            permits.release(1)                                                     # o bloco sai; o -10 s vem em seguida
            _wait(lambda n=n, t=expected: outs.opened[0].calls.count("abort") == n and eng.snapshot().t_ms == t)
        _wait(lambda: outs.opened[0].entered == 4)
        eng.pause()
        permits.release(1)
        _wait(lambda: outs.opened[0].calls.count("abort") == 4)
        assert (eng.snapshot().state, eng.snapshot().t_ms) == (PAUSED, 0)
        for delta, expected in ((pl.SEEK_MS, 70_000), (pl.SEEK_MS, 80_000), (-pl.SEEK_MS, 70_000), (-pl.SEEK_MS, 0)):
            eng.seek(delta)
            _wait(lambda t=expected: eng.snapshot().t_ms == t)
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_release_output_closes_the_stream_and_waits_for_the_writer(qtbot, store):
    """F5.3 (A-17): a troca de microfone que relê o PortAudio pede ao player que solte a saída e espera a escritora: o som
    que toca pausa com o aviso e o fluxo já está fechado na volta; o pausado pela gravação do sistema inteiro (R8-01, fluxo
    aberto) fecha também e guarda o aviso dele; sem nada tocando, nada a esperar."""
    mid = _meeting(store, seconds=30, pause=None)
    outs: list[RealtimeOutput] = []

    def factory() -> RealtimeOutput:
        outs.append(RealtimeOutput(rate=SR))
        return outs[-1]

    player = pl.Player(store, factory)
    notice = "pausado para trocar o microfone"
    try:
        assert player.release_output(notice, 1.0) is True and outs == []
        assert player.play_from(mid, 0)
        _wait(lambda: bool(outs) and len(outs[0].chunks) >= 2)
        assert player.release_output(notice, 1.0) is True
        assert outs[0].calls[-1] == "close" and (player.state().state, player.state().message) == (PAUSED, notice)
        assert player.toggle()                                         # retomar abre outro fluxo
        _wait(lambda: len(outs) == 2 and len(outs[1].chunks) >= 2)
        assert player.pause_and_wait("pausado: sistema inteiro", 1.0) == (True, True)
        assert outs[1].calls[-1] == "abort"                            # R8-01: pausado, com o fluxo aberto
        assert player.release_output(notice, 1.0) is True
        assert outs[1].calls[-1] == "close" and player.state().message == "pausado: sistema inteiro"
    finally:
        assert player.shutdown() is True
