"""D-067, as falhas da saída que acompanha o padrão (a revisão de 30/10), com os falsos e o relógio manual: o fim do som
espera o buffer esvaziar antes do `stop`, que no WASAPI não espera (R-01); o buraco é o buffer vazio antes da escrita, que
o PortAudio não diz (R-07); o aparelho que cai no meio reabre uma vez no padrão de agora, do ponto que se ouvia, e o erro
só aparece se isso também falhar (R-03); a releitura solta a escritora fora do `portaudio.LOCK` (R-05) e tenta de novo
quando não aconteceu (R-06); o padrão que não há como identificar fica calado, sem aviso nem releitura (R-11)."""
from __future__ import annotations

import threading
import time

import numpy as np

from listener.capture.clock import ManualClock
from listener.platform.base import OutputUnknownError
from listener.platform.common import portaudio
from listener.ui import player as pl
from listener.ui import player_route
from listener.ui.player import PLAYING, STOPPED, PlaybackEngine, PlayRequest
from tests.fake_player import FakeOutput, FakeOutputs, RealtimeOutput
from tests.test_player_route import BLOCK_MS, SR, FakeDevices, _continuous, _engine, _ms, _ramp, _routed_player, _wait


def test_the_end_of_the_sound_waits_for_the_buffer_before_stop(store):
    """R-01: o `stop` do falso não espera (como o WASAPI): sem esperar o buffer, os últimos ~0,18 s seriam cortados."""
    mid = _ramp(store, seconds=2)
    outs: list[RealtimeOutput] = []

    def factory(_endpoint: str | None = None) -> RealtimeOutput:
        outs.append(RealtimeOutput(rate=SR))
        return outs[-1]

    eng = PlaybackEngine(store, factory)
    eng.play(PlayRequest(mid, 1000))
    _wait(lambda: not eng.alive)
    out = outs[0]
    assert out.calls[-2:] == ["stop", "close"] and out.cut_s is not None and out.cut_s <= 0.03
    assert eng.snapshot().state == STOPPED and abs(len(out.frames) - SR) <= SR // 100
    plain = FakeOutputs(rate=SR)                                         # sem a medida do buffer: para na hora, como antes
    eng2 = PlaybackEngine(store, plain)
    t0 = time.monotonic()
    eng2.play(PlayRequest(mid, 1000))
    _wait(lambda: not eng2.alive)
    assert time.monotonic() - t0 < 1.0 and plain.opened[0].calls == ["start", "stop", "close"]


class Dry(FakeOutput):
    """Um fluxo que diz o espaço livre: vazio (o buffer inteiro livre) antes da terceira escrita."""

    @property
    def write_available(self) -> int:
        return round(self.latency * self.samplerate) if len(self.chunks) == 2 else 0


def test_a_hole_is_counted_by_the_empty_buffer_before_the_write(store, caplog):
    """R-07: o `underflowed` do PortAudio não vem; o buffer vazio antes de escrever é o buraco, contado e no log."""
    mid = _ramp(store, seconds=2)
    dry = Dry(SR, latency=0.05)
    eng = PlaybackEngine(store, lambda _endpoint: dry)
    with caplog.at_level("INFO", logger="listener.ui.player"):
        eng.play(PlayRequest(mid, 0))
        _wait(lambda: not eng.alive)
    assert eng.underflows == 1 and "1 buraco(s) na saída" in caplog.text


def test_a_device_removed_mid_sound_reopens_once_on_the_default_from_the_point_heard(store):
    """R-03: o fone sai no meio (o `write` falha) e o Windows passa o padrão para o alto-falante: o som volta nele do ponto
    que se ouvia, sem erro; caindo de novo depois de uma escrita boa, reabre de novo; sem nada que abra, o erro aparece."""
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="fone")
    eng = _engine(store, outs, ManualClock())
    eng.play(PlayRequest(mid, 0))

    def dead(_data: np.ndarray) -> object:
        raise OSError("AUDCLNT_E_DEVICE_INVALIDATED")

    try:
        permits.release(2)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 3)
        outs.opened[0].write = dead                                     # type: ignore[method-assign, assignment]
        outs.system = "alto"
        permits.release(1)                                               # o bloco em voo sai; o seguinte falha
        _wait(lambda: len(outs.opened) == 2)
        permits.release(2)
        _wait(lambda: outs.opened[1].entered == 3)
        old, new = outs.opened
        assert new.device == "alto" and eng.snapshot().state == PLAYING and eng.snapshot().message == ""
        assert abs(_ms(new.frames[0]) - 3 * BLOCK_MS) <= 2 and _continuous(old, new) <= 1
        new.write = dead                                                 # type: ignore[method-assign, assignment]
        outs.fail = OSError("nenhuma saída")
        permits.release(1)
        _wait(lambda: not eng.alive)
        st = eng.snapshot()
        assert st.state == pl.ERROR and st.message == "a saída de áudio parou (AUDCLNT_E_DEVICE_INVALIDATED)"
        assert len(outs.requested) == 3 and new.calls[-1] == "close"
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_a_device_that_fails_twice_in_a_row_shows_the_error(store):
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="fone")
    eng = _engine(store, outs, ManualClock())

    class Broken(FakeOutput):
        def start(self) -> None:
            raise OSError("Invalid device")

    eng.factory = lambda _endpoint: Broken(SR)
    eng.play(PlayRequest(mid, 0))
    try:
        _wait(lambda: not eng.alive)
        assert eng.snapshot().message == "sem dispositivo de saída de áudio (Invalid device)"
    finally:
        permits.release(1000)
        assert eng.shutdown() is True


def test_the_reread_releases_the_writer_outside_the_lock_and_retries_a_transient_failure(qtbot, store, monkeypatch):
    """R-05: a escritora solta o fluxo sem o `portaudio.LOCK` na mão da interface (o `open_output` de verdade o pega);
    dentro dele a contagem é conferida de novo. R-06: a releitura que não aconteceu (o PortAudio não voltou) tenta de novo
    depois do `REROUTE_RETRY_S`, e só a que aconteceu fica marcada."""
    mid = _ramp(store)
    outs = FakeOutputs(rate=SR, system="alto", known={"alto"}, realtime=True)
    clock = ManualClock()
    devices = FakeDevices(outs)
    opened_with_lock: list[bool] = []

    def open_output(endpoint_id: str | None = None) -> FakeOutput:
        with portaudio.LOCK:                                             # como o `Win32Output.open_output`
            return outs(endpoint_id)

    devices.open_output = open_output                                    # type: ignore[method-assign]
    player = _routed_player(store, devices, clock, monkeypatch)
    real_detach = player.engine.detach_and_wait

    def detach(timeout_s: float) -> bool:
        opened_with_lock.append(portaudio.LOCK._is_owned())             # type: ignore[attr-defined]
        return real_detach(timeout_s)

    monkeypatch.setattr(player.engine, "detach_and_wait", detach)
    transient = {"left": 1}
    real_refresh = devices.refresh_audio_devices

    def refresh(own_streams: int = 0, release=None) -> bool:
        if transient["left"]:
            transient["left"] -= 1
            devices.refreshes.append(-1)
            return False                                                 # o reinício falhou desta vez
        return real_refresh(own_streams, release)

    player.reroute.refresh = refresh                                     # type: ignore[union-attr]
    try:
        assert player.play_from(mid, 0)
        _wait(lambda: bool(outs.opened) and len(outs.opened[0].chunks) >= 2)
        outs.system = "fone-bt"
        clock.advance(pl.ROUTE_CHECK_S)
        _wait(lambda: player.state().note == pl.ROUTE_STUCK)
        assert player.reroute is not None and player.reroute.tick() is True and devices.refreshes == [-1]
        _wait(lambda: len(outs.opened) == 2)                             # reaberto no alto-falante (a reserva)
        clock.advance(pl.ROUTE_CHECK_S)
        _wait(lambda: player.state().note == pl.ROUTE_STUCK and outs.requested.count("fone-bt") == 2)
        assert player.reroute.tick() is False                            # antes do `REROUTE_RETRY_S`
        clock.advance(player_route.REROUTE_RETRY_S)
        assert player.reroute.tick() is True and devices.refreshes == [-1, 0]
        _wait(lambda: outs.opened[-1].device == "fone-bt" and len(outs.opened[-1].chunks) >= 2)
        assert opened_with_lock == [False, False] and player.state().note == ""
    finally:
        assert player.shutdown() is True


def test_an_output_that_cannot_be_identified_stays_quiet(store):
    """R-11: sem como saber qual dispositivo é o padrão novo, o som segue onde está, sem aviso e sem pedir releitura, e a
    mesma tentativa não se repete a cada leitura."""
    mid = _ramp(store)
    permits = threading.Semaphore(0)
    outs = FakeOutputs(rate=SR, permits=permits, system="alto")
    clock = ManualClock()
    tried: list[str | None] = []

    def factory(endpoint_id: str | None = None) -> FakeOutput:
        tried.append(endpoint_id)
        if endpoint_id:
            raise OutputUnknownError("sem como identificar")
        return outs(None)

    eng = PlaybackEngine(store, factory, probe=outs.probe, clock=clock.rel)
    eng.play(PlayRequest(mid, 0))
    try:
        permits.release(2)
        _wait(lambda: bool(outs.opened) and outs.opened[0].entered == 3)
        outs.system = "sem-nome"
        for _ in range(3):
            clock.advance(pl.ROUTE_CHECK_S)
            permits.release(1)
            time.sleep(0.05)
        assert tried == [None, "sem-nome"] and eng.snapshot().note == "" and eng.stuck_id() is None
        assert eng.snapshot().state == PLAYING and len(outs.opened) == 1
    finally:
        permits.release(1000)
        assert eng.shutdown() is True
