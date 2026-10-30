"""F5.9 (item 3), só no Windows (o COM de mentira pede o pycaw): o loopback do app que acompanha os processos novos. A
função pura `new_targets`; os taps somados no mixer, que existe mesmo com um alvo; a vigia (`AppWatch`) com o snapshot e
o `FlowSessions` injetados, como `test_win32_sessions.py`: uma releitura soma o WebView2 que ganhou sessão depois do
"Gravar" (a chamada que entra) e para o tap do que saiu, sem tirá-lo do mixer; a thread nasce no `start()`, morre no
`stop()` e fecha o `FlowSessions` nela, sem ponteiro sobrando. Nenhum proc-tap de verdade: os taps são `_StreamHandle`
de mentira."""
from __future__ import annotations

import sys
import threading
import time
from typing import Any

import numpy as np
import pytest

from tests.fake_com import FakeManager, FakeSession, Outputs

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="as sessões do Core Audio só existem no Windows")


def _tree() -> dict[int, Any]:
    """O Teams novo (D-055): a raiz 20, o filho 21, os WebView2 22 e 24 netos (sob o 21), o 23 sob o 22 e o 30 filho
    direto."""
    from listener.platform.win32.processes import ProcInfo

    return {p.pid: p for p in [
        ProcInfo(1, "explorer.exe", 0), ProcInfo(20, "ms-teams.exe", 1), ProcInfo(21, "ms-teams.exe", 20),
        ProcInfo(22, "msedgewebview2.exe", 21), ProcInfo(23, "msedgewebview2.exe", 22), ProcInfo(24, "msedgewebview2.exe", 21),
        ProcInfo(30, "msedgewebview2.exe", 20),
    ]}


def _flow(outputs: Outputs) -> Any:
    from listener.platform.win32 import endpoints, sessions

    return sessions.FlowSessions(endpoints.FLOW_RENDER, outputs.list, outputs.activate, keep=True)


class _Taps:
    """Os taps de mentira: o callback de cada pid, as chamadas de `start`/`stop` e quem já saiu (a prova de vida)."""

    def __init__(self, formats: dict[int, tuple[int, int]] | None = None) -> None:
        self.feeds: dict[int, Any] = {}
        self.calls: list[tuple[str, int]] = []
        self.exited: dict[int, bool] = {}
        self.formats = formats or {}

    def open(self, pid: int, on_audio: Any, on_error: Any = None) -> Any:
        from listener.platform.win32.stream import _StreamHandle

        self.feeds[pid] = on_audio
        sr, ch = self.formats.get(pid, (48000, 2))
        return _StreamHandle(lambda: self.calls.append(("start", pid)), lambda: self.calls.append(("stop", pid)), sr, ch,
                             on_error, alive_fn=lambda: not self.exited.get(pid, False))


def test_new_targets_adds_only_the_deeper_processes_that_have_no_tap():
    from listener.platform.win32.app_loopback import new_targets

    tree = _tree()
    assert new_targets([20], 20, {21}, tree) == []                     # filho direto da raiz: já vem no loopback dela
    assert new_targets([20], 20, {30}, tree) == []
    assert new_targets([20], 20, {22}, tree) == [22]                   # o neto da chamada: tap novo
    assert new_targets([20, 22], 20, {22, 23}, tree) == []             # filho direto de um tap: já vem nele
    assert new_targets([20, 22], 20, {23}, tree) == []                 # idem, mesmo com o 22 sem sessão agora
    assert new_targets([20], 20, {23}, tree) == [23]                   # sem tap no 22: o 23 precisa do seu
    assert new_targets([20], 20, {22, 23, 999}, tree) == [22]          # o 23 cabe no tap novo do 22; o 999 não existe


def test_the_watch_sums_a_process_that_starts_playing_later_and_stops_the_one_that_left():
    from listener.capture.mixer import FrameMixer
    from listener.platform.win32.app_loopback import AppTaps, AppWatch

    tree, fake = _tree(), _Taps({24: (44100, 2)})
    out: list[tuple[np.ndarray, float]] = []
    mixer = FrameMixer(1, 48000, 2, lambda block, t: out.append((block.copy(), t)))
    taps = AppTaps(20, fake.open(20, lambda block, t: mixer.feed(0, block, t)), mixer, fake.open)
    outputs = Outputs({"ep-padrao": FakeManager([FakeSession(21, 0.1)])})
    watch = AppWatch(taps, lambda: tree, lambda: _flow(outputs))
    flow = _flow(outputs)
    taps.start()
    assert watch.rescan(flow) == [] and taps.pids() == [20] and fake.calls == [("start", 20)]
    outputs.managers["ep-fone"] = FakeManager([FakeSession(22, 0.5)])   # a chamada entra: o neto ganha sessão
    assert watch.rescan(flow) == [22] and taps.pids() == [20, 22] and fake.calls[-1] == ("start", 22)
    fake.feeds[22](np.full((2, 2), 0.25, np.float32), 5.0)
    fake.feeds[20](np.full((2, 2), 0.5, np.float32), 1.0)
    assert len(out) == 1 and np.allclose(out[0][0], 0.75) and out[0][1] == 1.0   # somado no bloco seguinte da raiz
    outputs.managers["ep-fone"].sessions = [FakeSession(24, 0.5)]        # outro neto, em outro formato: fica de fora
    assert watch.rescan(flow) == [] and taps.pids() == [20, 22] and fake.calls[-1] == ("stop", 24)
    assert flow.activations == 3                                         # `keep`: refeitos quando o fone entrou, depois guardados
    assert watch.rescan(flow) == [] and fake.calls.count(("stop", 24)) == 1 and taps.skipped == {24}   # uma tentativa só
    assert len(mixer._pending) == 1                                      # e nenhum slot no mixer para o que ficou de fora
    fake.exited[22] = True                                               # o WebView2 saiu: o tap para, sem sair do mixer
    assert watch.rescan(flow) == [] and ("stop", 22) in fake.calls and taps.pids() == [20, 22] and taps.dead == {22}
    outputs.managers["ep-fone"].sessions = [FakeSession(23, 0.5)]        # o filho do parado ganha sessão: tap próprio
    assert watch.rescan(flow) == [23] and taps.pids() == [20, 22, 23] and fake.calls[-1] == ("start", 23)
    outputs.down = True                                                  # o Core Audio fora: a releitura fica para a próxima
    assert watch.rescan(flow) == [] and watch.rescans == 7
    taps.close()
    assert fake.calls.count(("stop", 20)) == 1 and fake.calls.count(("stop", 22)) == 1 and fake.calls.count(("stop", 23)) == 1
    assert fake.calls.count(("stop", 24)) == 1 and not taps.add(25)
    flow.close()
    assert outputs.alive() == 0


def test_a_reused_pid_gets_a_new_tap_in_place_of_the_stopped_one():
    from listener.capture.mixer import FrameMixer
    from listener.platform.win32.app_loopback import AppTaps, AppWatch

    tree, fake = _tree(), _Taps()
    mixer = FrameMixer(1, 48000, 2, lambda block, t: None)
    taps = AppTaps(20, fake.open(20, lambda block, t: mixer.feed(0, block, t)), mixer, fake.open)
    outputs = Outputs({"ep-fone": FakeManager([FakeSession(22, 0.5)])})
    watch = AppWatch(taps, lambda: tree, lambda: _flow(outputs))
    flow = _flow(outputs)
    taps.start()
    assert watch.rescan(flow) == [22]
    fake.exited[22] = True
    gone = tree.pop(22)                                                  # saiu: some do snapshot (a sessão pode ficar)
    assert watch.rescan(flow) == [] and taps.dead == {22}
    assert watch.rescan(flow) == [] and fake.calls.count(("start", 22)) == 1   # a sessão que ficou não reabre o tap
    tree[22], _ = gone, fake.exited.pop(22)                              # o pid 22 reaproveitado por um processo novo
    assert watch.rescan(flow) == [22] and taps.dead == set() and fake.calls.count(("start", 22)) == 2
    taps.close()
    assert fake.calls.count(("stop", 22)) == 2
    flow.close()


def test_a_tap_opened_while_the_taps_close_is_stopped_and_the_lock_is_free_during_the_open():
    """A interface fecha os taps sob a trava enquanto a vigia abre um (o proc-tap espera a ativação por até 10 s): a
    abertura corre fora da trava, e o recém-aberto, com tudo já fechado, para na hora."""
    from listener.capture.mixer import FrameMixer
    from listener.platform.win32.app_loopback import AppTaps

    fake = _Taps()
    mixer = FrameMixer(1, 48000, 2, lambda block, t: None)

    def open_and_close(pid: int, on_audio: Any, on_error: Any = None) -> Any:
        handle = fake.open(pid, on_audio, on_error)
        taps.close()                                                     # travaria aqui se `add` segurasse a trava
        return handle

    taps = AppTaps(20, fake.open(20, lambda block, t: None), mixer, open_and_close)
    taps.start()
    assert not taps.add(22) and fake.calls.count(("stop", 22)) == 1 and taps.pids() == [20] and not taps.skipped


def test_the_watch_survives_a_failing_rescan_and_logs_once(monkeypatch, caplog):
    import logging

    from listener.capture.mixer import FrameMixer
    from listener.platform.win32.app_loopback import AppTaps, AppWatch

    outputs = Outputs({"ep-padrao": FakeManager([FakeSession(21, 0.1)])})
    fake = _Taps()
    taps = AppTaps(20, fake.open(20, lambda block, t: None), FrameMixer(1, 48000, 2, lambda block, t: None), fake.open)
    monkeypatch.setattr(taps, "stop_dead", lambda: 1 // 0)              # uma falha fora da leitura, a cada releitura
    watch = AppWatch(taps, _tree, lambda: _flow(outputs), every_s=0.01)
    with caplog.at_level(logging.WARNING, logger="listener"):
        watch.start()
        deadline = time.monotonic() + 3.0
        while watch.errors < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert watch.errors >= 3 and watch.is_alive()                     # segue viva
        watch.close()
    assert not watch.is_alive() and outputs.alive() == 0
    assert sum("releitura falhou" in r.getMessage() for r in caplog.records) == 1


def test_the_app_loopback_handle_runs_the_watch_between_start_and_stop(monkeypatch):
    """O handle composto: o mixer mesmo com um alvo, a thread da vigia nasce no `start()` e morre no `stop()` (o
    `FlowSessions` criado e fechado nela, sem ponteiro sobrando), e o processo novo é somado pela thread."""
    from listener.platform.win32 import app_loopback

    tree, fake = _tree(), _Taps()
    outputs = Outputs({"ep-padrao": FakeManager([FakeSession(21, 0.1)])})
    plat = app_loopback.Win32AppLoopback()
    plat.watch_every_s = 0.02
    monkeypatch.setattr(plat, "_processes", lambda: (tree, {}), raising=False)
    monkeypatch.setattr(plat, "_session_pids", lambda: {21})
    monkeypatch.setattr(plat, "_open_process_loopback", fake.open)
    monkeypatch.setattr(plat, "_watch_snapshot", lambda: tree)
    monkeypatch.setattr(plat, "_watch_flow", lambda: _flow(outputs))
    out: list[float] = []
    h = plat.open_app_loopback(20, lambda block, t: out.append(t))
    assert list(fake.feeds) == [20] and (h.sample_rate, h.channels) == (48000, 2)
    assert "app-loopback-20" not in {t.name for t in threading.enumerate()}      # a vigia só nasce no start
    h.start()
    assert fake.calls == [("start", 20)] and h.alive() is True
    outputs.managers["ep-fone"] = FakeManager([FakeSession(22, 0.5)])
    deadline = time.monotonic() + 3.0
    while 22 not in fake.feeds and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ("start", 22) in fake.calls                                          # somado pela thread
    fake.feeds[22](np.full((2, 2), 0.25, np.float32), 5.0)
    fake.feeds[20](np.full((2, 2), 0.5, np.float32), 1.0)
    assert out == [1.0]
    h.stop()
    assert "app-loopback-20" not in {t.name for t in threading.enumerate()}      # morreu no stop, com o flow fechado nela
    assert fake.calls.count(("stop", 20)) == 1 and fake.calls.count(("stop", 22)) == 1 and outputs.alive() == 0
    h.stop()
    assert fake.calls.count(("stop", 20)) == 1
