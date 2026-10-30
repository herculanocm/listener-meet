"""D-067 (29/10, o dono: "quando trocar de dispositivo o som precisa ser direcionado para o dispositivo escolhido na troca"):
o player acompanha o padrão de saída do Windows. Com o fluxo falso (`tests/fake_player.py`) e o relógio manual: a troca no
meio do som move o fluxo do ponto que se ouvia (sem pular, repetindo no máximo o buffer); trocar e trocar de volta; várias
trocas seguidas valem a última; pausado e retomado; ±10 s e outro ponto logo depois da troca; o aparelho que chegou depois
da leitura do PortAudio, com uma gravação segurando a biblioteca e depois dela; a leitura do padrão falhando; nada lido com
o player parado ou pausado (R-09: o retomar confere); o "Tocar" logo depois de uma troca com o player parado. As falhas
(aparelho que cai, fim do som, buraco, releitura) estão em `test_player_route_faults.py`. Nada de áudio real."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.capture.clock import ManualClock
from listener.ui import player as pl
from listener.platform.common import portaudio
from listener.ui import player_route
from listener.ui.player import PAUSED, PLAYING, STOPPED, PlaybackEngine, PlayRequest
from tests.fake_player import FakeOutput, FakeOutputs

SR = 16000
SECONDS = 20
BLOCK_MS = int(pl.BLOCK_S * 1000)


def _ramp(store, seconds: int = SECONDS) -> int:
    """L e R iguais, uma rampa de -0,9 a 0,9: cada amostra diz o instante dela (a mistura com ganho ½ é a própria rampa)."""
    mid = store.create_meeting("Rampa", None, None, None)
    t = np.arange(seconds * SR, dtype=np.float64) / SR
    x = (-0.9 + 1.8 * t / seconds).astype(np.float32)
    for ch in ("L", "R"):
        for idx in range(seconds // 2):
            part = x[idx * 2 * SR:(idx + 1) * 2 * SR]
            store.insert_chunk(mid, ch, idx, idx * 2000, len(part), SR, 0, encode_flac(part, SR))
    store.finish_meeting(mid, seconds * 1000)
    return mid


def _ms(sample: float, seconds: int = SECONDS) -> float:
    """O instante (ms) de uma amostra da rampa."""
    return (float(sample) + 0.9) / 1.8 * seconds * 1000


def _wait(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("tempo esgotado esperando o player")
        time.sleep(0.005)


def _engine(store, outs: FakeOutputs, clock: ManualClock) -> PlaybackEngine:
    return PlaybackEngine(store, outs, probe=outs.probe, clock=clock.rel)


def _switch(outs: FakeOutputs, clock: ManualClock, permits: threading.Semaphore, endpoint: str) -> None:
    """O dono troca o padrão no Windows; a leitura seguinte vence; o bloco em voo sai e a escritora lê o padrão."""
    n = len(outs.opened)
    outs.system = endpoint
    clock.advance(pl.ROUTE_CHECK_S)
    permits.release(1)
    _wait(lambda: len(outs.opened) == n + 1)


def _continuous(old: FakeOutput, new: FakeOutput) -> float:
    """Quanto o novo repete do antigo (ms); nunca pula: o novo começa até onde o antigo foi escrito."""
    last_old, first_new = _ms(old.frames[-1]), _ms(new.frames[0])
    assert first_new <= last_old + 1.0, "pulou áudio na troca"
    return last_old - first_new


def test_a_switch_while_playing_moves_the_sound_from_the_point_heard(store):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, latency=0.05, permits=permits, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 1000))
    try:
        permits.release(4)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 5)    # 4 blocos tocados, o 5º em voo
        _switch(outs, clock, permits, "fone")
        heard = 1000 + 5 * BLOCK_MS - 50                                  # o que se ouvia quando o novo abriu
        permits.release(3)
        _wait(lambda: len(outs.opened) == 2 and outs.opened[1].entered == 4)   # R-13 (d): 3 escritos, o 4º em voo
        old, new = outs.opened
        assert old.calls[-2:] == ["abort", "close"] and (old.device, new.device) == ("alto", "fone")
        assert abs(_ms(new.frames[0]) - heard) <= 2 and _continuous(old, new) <= 500     # repete só o buffer (50 ms)
        assert outs.requested == [None, "fone"] and eng.route.moves == 1
        st = eng.snapshot()
        assert st.state == PLAYING and st.note == "" and abs(st.t_ms - (heard + 3 * BLOCK_MS - 50)) <= 2    # menos o buffer do novo
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_back_and_forth_and_several_quick_switches_go_to_the_last_one(store):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto", rates={"fone": 48000})
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 0))
    try:
        permits.release(2)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 3)
        outs.system = "monitor"                                          # três trocas antes da leitura: vale a última
        outs.system = "alto"
        _switch(outs, clock, permits, "fone")
        permits.release(2)
        _wait(lambda: outs.opened[1].entered == 3)
        _switch(outs, clock, permits, "alto")                            # e de volta
        permits.release(2)
        _wait(lambda: len(outs.opened[2].chunks) >= 2)
        first, fone, back = outs.opened
        assert [o.device for o in outs.opened] == ["alto", "fone", "alto"] and outs.requested == [None, "fone", "alto"]
        assert fone.samplerate == 48000 and len(fone.chunks[0]) == 48000 // 4   # a taxa do aparelho novo (reamostrado)
        assert _continuous(first, fone) <= 50 and back.calls == ["start"]          # o começo do reamostrador: ~6 ms
        assert _continuous(fone, back) <= 50 and abs(_ms(back.frames[0]) - 6 * BLOCK_MS) <= 2      # do ponto, sem pular
        assert eng.route.moves == 2 and "monitor" not in outs.requested
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_paused_nothing_is_read_and_resume_opens_the_new_default_from_the_point(store):
    """R-09: pausado com o fluxo aberto, o padrão não é lido (nem com o relógio andando); o retomar confere e vai para ele."""
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 2000))
    try:
        permits.release(4)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 5)
        eng.pause()
        permits.release(1)
        _wait(lambda: outs.opened[0].calls[-1] == "abort")              # pausado, com o fluxo aberto (R8-01)
        paused, reads = eng.snapshot().t_ms, outs.probes
        outs.system = "fone"
        for _ in range(3):
            clock.advance(10 * pl.ROUTE_CHECK_S)
            eng.kick()
            time.sleep(0.05)
        assert outs.probes == reads and outs.opened[0].calls[-1] == "abort" and len(outs.opened) == 1
        assert (eng.snapshot().state, eng.snapshot().t_ms) == (PAUSED, paused)
        eng.resume()
        permits.release(2)
        _wait(lambda: len(outs.opened) == 2 and len(outs.opened[1].chunks) == 2)
        assert outs.probes == reads + 1 and outs.opened[0].calls[-1] == "close" and len(outs.opened[0].chunks) == 5
        assert outs.opened[1].device == "fone" and abs(_ms(outs.opened[1].frames[0]) - paused) <= 2
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_resume_right_after_a_switch_goes_to_the_new_default_without_a_block_on_the_old(store):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 0))
    try:
        permits.release(3)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 4)
        eng.pause()
        permits.release(1)
        _wait(lambda: outs.opened[0].calls[-1] == "abort")
        paused, written = eng.snapshot().t_ms, len(outs.opened[0].chunks)
        outs.system = "fone"                                             # troca e retoma antes da leitura de 1 s
        eng.resume()
        permits.release(2)
        _wait(lambda: len(outs.opened) == 2 and len(outs.opened[1].chunks) == 2)
        assert len(outs.opened[0].chunks) == written and outs.opened[0].calls[-1] == "close"
        assert outs.opened[1].device == "fone" and abs(_ms(outs.opened[1].frames[0]) - paused) <= 2
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


@pytest.mark.parametrize("action", ["seek", "point"])
def test_seek_or_another_point_right_after_a_switch_plays_there_on_the_new_default(store, action):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 1000))
    try:
        permits.release(3)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 4)
        outs.system = "fone"
        if action == "seek":
            eng.seek(pl.SEEK_MS)
            target = 1000 + 4 * BLOCK_MS + pl.SEEK_MS                     # o bloco em voo sai antes do +10 s
        else:
            eng.play(PlayRequest(mid, 7000))
            target = 7000
        permits.release(3)
        _wait(lambda: len(outs.opened) == 2 and len(outs.opened[1].chunks) == 2)
        old, new = outs.opened
        assert len(old.chunks) == 4 and new.device == "fone" and abs(_ms(new.frames[0]) - target) <= 2
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_a_failing_read_keeps_the_sound_where_it_is_and_the_next_one_moves_it(store, caplog):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 0))
    try:
        permits.release(2)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 3)
        outs.system, outs.probe_error = "fone", OSError("Core Audio fora")
        reads = outs.probes
        clock.advance(pl.ROUTE_CHECK_S)
        permits.release(3)
        _wait(lambda: outs.opened[0].entered == 6)
        assert outs.probes == reads + 1 and len(outs.opened) == 1 and eng.snapshot().state == PLAYING
        outs.probe_error = None
        _switch(outs, clock, permits, "fone")
        assert outs.opened[1].device == "fone"
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_nothing_is_read_with_the_player_stopped_or_paused_without_a_stream(store):
    mid = _ramp(store, seconds=4)
    outs = FakeOutputs(rate=SR, system="alto")
    clock = ManualClock()
    eng = _engine(store, outs, clock)
    eng.play(PlayRequest(mid, 2000))
    _wait(lambda: not eng.alive)                                         # tocou até o fim: a escritora terminou
    reads = outs.probes
    assert reads >= 1 and eng.snapshot().state == STOPPED and not eng.has_stream
    for _ in range(3):
        clock.advance(10 * pl.ROUTE_CHECK_S)
        eng.kick()
        time.sleep(0.05)
    assert outs.probes == reads and not eng.alive
    permits = threading.Semaphore(0)
    held = FakeOutputs(rate=SR, permits=permits, system="alto")
    eng2 = _engine(store, held, clock)
    eng2.play(PlayRequest(_ramp(store), 0))
    try:
        permits.release(1)
        _wait(lambda: bool(held.opened) and held.opened[0].entered == 2)
        eng2.pause(release=True)                                         # o warm-up: o fluxo fecha
        permits.release(1)
        _wait(lambda: held.opened[0].calls[-1] == "close")
        reads = held.probes
        for _ in range(3):
            clock.advance(10 * pl.ROUTE_CHECK_S)
            eng2.kick()
            time.sleep(0.05)
        assert held.probes == reads and eng2.snapshot().state == PAUSED
    finally:
        permits.release(1000)
        assert eng2.shutdown() is True


class FakeDevices:
    """A plataforma do player, fiel no que importa: a releitura confere a contagem de streams sob o `portaudio.LOCK` (com o
    `release` dentro, como o `refresh_audio_devices`), acha o aparelho novo (`known`) ou não, e depois dela a assinatura é
    a atual (`changed` falso); `held` são os streams de fora do player (uma gravação)."""

    def __init__(self, outs: FakeOutputs, finds: bool = True):
        self.outs, self.finds = outs, finds
        self.refreshes: list[int] = []
        self.changed = True
        self.held = 0
        self.engine: PlaybackEngine | None = None

    def open_streams(self) -> int:
        return self.held + (1 if self.engine is not None and self.engine.has_stream else 0)

    def default_output_id(self) -> str | None:
        return self.outs.probe()

    def open_output(self, endpoint_id: str | None = None) -> FakeOutput:
        return self.outs(endpoint_id)

    def audio_devices_changed(self) -> bool:
        return self.changed

    def refresh_audio_devices(self, own_streams: int = 0, release=None) -> bool:
        with portaudio.LOCK:
            if self.open_streams() > own_streams:
                return False
            self.refreshes.append(own_streams)
            if release is not None:
                release()
            if self.finds and self.outs.known is not None:
                self.outs.known.add(self.outs.system)
            self.changed = False
            return self.finds


def _routed_player(store, devices: FakeDevices, clock: ManualClock, monkeypatch) -> pl.Player:
    player = pl.Player(store, devices=devices, clock=clock.rel)
    devices.engine = player.engine
    monkeypatch.setattr(player_route.portaudio, "open_streams", devices.open_streams)
    return player


def test_a_headset_that_arrived_after_the_reading_waits_for_the_recording_and_then_plays_there(qtbot, store, monkeypatch):
    """O fone conectado com o app aberto não está na lista do PortAudio: o som segue no alto-falante com o aviso na barra;
    com uma gravação segurando a biblioteca, nada é relido; quando ela acaba, a lista é relida uma vez e o som volta do mesmo
    ponto no fone."""
    mid = _ramp(store)
    outs = FakeOutputs(rate=SR, system="alto", known={"alto"}, realtime=True)
    clock = ManualClock()
    devices = FakeDevices(outs)
    devices.held = 1                                                     # a gravação segura o microfone
    player = _routed_player(store, devices, clock, monkeypatch)
    try:
        assert player.play_from(mid, 1000)
        _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) >= 2)
        outs.system = "fone-bt"
        clock.advance(pl.ROUTE_CHECK_S)
        _wait(lambda: player.state().note == pl.ROUTE_STUCK)
        assert outs.requested == [None, "fone-bt"] and len(outs.opened) == 1
        assert player.reroute is not None and player.reroute.tick() is False and devices.refreshes == []   # a gravação segura
        n = len(outs.opened[0].chunks)
        _wait(lambda: len(outs.opened[0].chunks) >= n + 2)              # o som segue no alto-falante
        devices.held = 0                                                 # a gravação acabou: só o player
        clock.advance(player_route.REROUTE_RETRY_S)
        assert player.reroute.tick() is True and devices.refreshes == [0]   # R-05: solto antes, conferido dentro
        _wait(lambda: len(outs.opened) == 2 and len(outs.opened[1].chunks) >= 2)
        old, new = outs.opened
        assert old.calls[-1] == "close" and new.device == "fone-bt" and _continuous(old, new) <= 500
        _wait(lambda: player.state().note == "")
        assert player.state().state == PLAYING and player.reroute.tick() is False and devices.refreshes == [0]
    finally:
        assert player.shutdown() is True


def test_a_reread_that_does_not_find_it_is_not_repeated_and_the_note_stays(qtbot, store, monkeypatch):
    mid = _ramp(store)
    outs = FakeOutputs(rate=SR, system="alto", known={"alto"}, realtime=True)
    clock = ManualClock()
    devices = FakeDevices(outs, finds=False)
    player = _routed_player(store, devices, clock, monkeypatch)
    try:
        assert player.play_from(mid, 0)
        _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) >= 2)
        outs.system = "fone-bt"
        clock.advance(pl.ROUTE_CHECK_S)
        _wait(lambda: player.state().note == pl.ROUTE_STUCK)
        assert player.reroute is not None and player.reroute.tick() is True
        _wait(lambda: len(outs.opened) == 2)                             # o `reattach` reabriu (na reserva)
        clock.advance(pl.ROUTE_CHECK_S)
        _wait(lambda: player.state().note == pl.ROUTE_STUCK and outs.requested.count("fone-bt") == 2)
        clock.advance(player_route.REROUTE_RETRY_S)
        assert player.reroute.tick() is True and devices.refreshes == [0]   # R-06: nada mudou desde a leitura: marcada
        clock.advance(10 * player_route.REROUTE_RETRY_S)
        assert player.reroute.tick() is False and devices.refreshes == [0] and len(outs.opened) == 2
        assert player.state().state == PLAYING and outs.opened[1].device == "alto-falante"
    finally:
        assert player.shutdown() is True


@pytest.mark.parametrize("case", ["known", "reread", "held"])
def test_play_right_after_a_switch_with_the_player_stopped_and_an_old_list(qtbot, store, case):
    """Trocado com o player parado: o "Tocar" abre o padrão de agora pelo endpoint (o padrão do PortAudio é o da leitura
    velha); o aparelho fora da lista entra com a releitura do "Tocar" (U-043) ou, sem ela, o som sai na reserva com o aviso."""
    mid = _ramp(store)
    outs = FakeOutputs(rate=SR, system="fone", known={"alto", "fone"} if case == "known" else {"alto"}, realtime=True)
    clock = ManualClock()
    devices = FakeDevices(outs, finds=case == "reread")
    player = pl.Player(store, devices=devices, clock=clock.rel)
    player.before_play = devices.refresh_audio_devices
    try:
        assert player.play_from(mid, 0)
        _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) >= 2)
        if case == "held":
            assert outs.opened[0].device == "alto-falante"
            _wait(lambda: player.state().note == pl.ROUTE_STUCK)
        else:
            assert outs.opened[0].device == "fone" and outs.requested == [None] and player.state().note == ""
    finally:
        assert player.shutdown() is True


def test_the_bar_shows_the_note_while_playing(qtbot, store):
    from listener.ui.player_bar import PlayerBar

    player = pl.Player(store, FakeOutputs())
    bar = PlayerBar(player, 7)
    qtbot.addWidget(bar)
    bar.show_state(pl.PlayerState(PLAYING, 7, 0, 1000, "t", "", pl.ROUTE_STUCK))
    assert bar.note.text() == pl.ROUTE_STUCK
    bar.show_state(pl.PlayerState(PLAYING, 8, 0, 1000, "t", "", pl.ROUTE_STUCK))      # outra reunião: não é desta barra
    assert bar.note.text() == ""
    bar.show_state(pl.PlayerState(STOPPED, 7, 0, 1000, "t", "", pl.ROUTE_STUCK))
    assert bar.note.text() == ""
    assert player.shutdown() is True


def test_the_new_modules_keep_device_names_out_of_the_log():
    """D-053 e A-22: o log leva o id do endpoint, nunca o nome do aparelho."""
    from tests.log_privacy import D067_MODULES, leaks

    assert {str(m): leaks(m) for m in D067_MODULES} == {str(m): [] for m in D067_MODULES}
