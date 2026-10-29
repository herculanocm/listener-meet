"""F5.7 (item 10), só no Windows: o "Apps tocando agora" lê as sessões de todas as saídas ativas, não só a padrão
multimídia do `GetAllSessions` do pycaw, com os gerenciadores criados e soltos dentro de cada leitura (guardar é opção do
sensor do L1, que solta na própria thread). Objetos falsos no lugar do COM (endpoint, gerenciador, sessão e medidor), novos
a cada enumeração e a cada Activate, com `weakref` para provar que nada sobra; um teste lê esta máquina, só leitura (o
gerenciador de sessões não abre stream)."""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any

import pytest

from tests.fake_com import FakeManager, FakeSession, Outputs

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="as sessões do Core Audio só existem no Windows")


def _procs() -> dict[int, Any]:
    from listener.platform.win32.processes import ProcInfo

    return {p.pid: p for p in [
        ProcInfo(1, "explorer.exe", 0), ProcInfo(100, "ms-teams.exe", 1), ProcInfo(101, "msedgewebview2.exe", 100),
        ProcInfo(200, "chrome.exe", 1), ProcInfo(300, "Spotify.exe", 1),
    ]}


def _platform(monkeypatch: pytest.MonkeyPatch, outputs: Outputs) -> Any:
    """A plataforma de verdade, com a leitura dela (e a forma dela, sem guardar) sobre as saídas de mentira."""
    from listener.platform.win32 import platform as wp

    plat = wp.Win32Platform()
    plat._render_sessions._list, plat._render_sessions._activate = outputs.list, outputs.activate
    monkeypatch.setattr(plat, "_processes", lambda: (_procs(), {}))
    return plat


def _flow(outputs: Outputs, keep: bool) -> Any:
    from listener.platform.win32 import endpoints, sessions

    return sessions.FlowSessions(endpoints.FLOW_RENDER, outputs.list, outputs.activate, keep=keep)


def test_two_outputs_with_one_session_each_both_appear(monkeypatch, caplog):
    """A chamada do Teams toca no fone de comunicação (o WebView2, neto da raiz) e o Chrome na saída padrão: as duas linhas,
    o app de reunião primeiro, pela raiz (D-055); o log conta as sessões das duas saídas."""
    outputs = Outputs({"ep-padrao": FakeManager([FakeSession(200, 0.3)]),
                       "ep-fone": FakeManager([FakeSession(101, 0.6)])})
    plat = _platform(monkeypatch, outputs)
    with caplog.at_level(logging.DEBUG, logger="listener"):
        apps = plat.list_playing_apps()
    assert [(a.pid, a.exe, a.is_meeting_app) for a in apps] == [(100, "ms-teams.exe", True), (200, "chrome.exe", False)]
    assert [a.peak for a in apps] == [0.6, 0.3] and plat.last_list_apps_ms > 0
    assert "(2 sessões" in caplog.text


@pytest.mark.parametrize("peaks", [(0.7, 0.2), (0.2, 0.7)])
def test_the_same_pid_on_two_outputs_is_one_row_with_the_highest_peak(monkeypatch, peaks):
    outputs = Outputs({"ep-a": FakeManager([FakeSession(300, peaks[0])]),
                       "ep-b": FakeManager([FakeSession(300, peaks[1])])})
    apps = _platform(monkeypatch, outputs).list_playing_apps()
    assert [(a.pid, a.exe, a.peak) for a in apps] == [(300, "Spotify.exe", 0.7)]


def test_an_output_that_fails_does_not_take_the_others_down(monkeypatch):
    """O endpoint invalidado (fone desconectado no meio) e o que nem ativa ficam de fora desta leitura; na seguinte, já
    bons, voltam."""
    outputs = Outputs({"ep-a": FakeManager([FakeSession(200, 0.3)], fail=True),
                       "ep-b": FakeManager([FakeSession(300, 0.5)]),
                       "ep-c": FakeManager([FakeSession(101, 0.4)])})
    outputs.broken.add("ep-c")
    plat = _platform(monkeypatch, outputs)
    assert [a.pid for a in plat.list_playing_apps()] == [300]
    outputs.managers["ep-a"].fail = False
    outputs.broken.clear()
    assert [a.pid for a in plat.list_playing_apps()] == [100, 300, 200]


def test_a_general_failure_gives_the_empty_list_as_before(monkeypatch, caplog):
    outputs = Outputs({"ep-a": FakeManager([FakeSession(200, 0.3)])})
    plat = _platform(monkeypatch, outputs)
    assert [a.pid for a in plat.list_playing_apps()] == [200]
    outputs.down = True
    with caplog.at_level(logging.WARNING, logger="listener"):
        assert plat.list_playing_apps() == []
    assert "sessões de áudio: Core Audio fora" in caplog.text
    outputs.down = False
    assert [a.pid for a in plat.list_playing_apps()] == [200]


def test_the_system_sounds_session_and_unreadable_sessions_stay_out(monkeypatch):
    """A sessão dos sons do sistema (pid 0) fica de fora, como antes; a de pid ilegível também; o pico que falha vale 0 e
    a sessão fica (como antes)."""
    outputs = Outputs({"ep-a": FakeManager([FakeSession(0, 0.9), None, FakeSession(OSError("saiu"), 0.8),
                                            FakeSession(200, OSError("medidor"))]),
                       "ep-b": FakeManager([FakeSession(0, 0.5)])})
    apps = _platform(monkeypatch, outputs).list_playing_apps()
    assert [(a.pid, a.peak) for a in apps] == [(200, 0.0)]


def test_the_platform_creates_and_releases_the_managers_inside_each_read(monkeypatch):
    """Sem guardar: cada leitura ativa os gerenciadores de novo e, ao sair dela, nenhum ponteiro sobra vivo (nem endpoint,
    nem gerenciador), também depois de um endpoint falhar e da falha geral. Soltos na thread de quem lê, sem coleta de
    lixo (0xC0000409)."""
    outputs = Outputs({"ep-a": FakeManager([FakeSession(200, 0.3)]), "ep-b": FakeManager([FakeSession(300, 0.5)])})
    plat = _platform(monkeypatch, outputs)
    flow = plat._render_sessions
    assert flow.keep is False
    plat.list_playing_apps()
    plat.list_playing_apps()
    assert flow.activations == 2 + 2 and outputs.alive() == 0 and len(outputs.handed) == 2 * 4
    assert flow._managers == {} and flow._key is None
    outputs.managers["ep-a"].fail = True
    outputs.broken.add("ep-b")
    assert plat.list_playing_apps() == [] and outputs.alive() == 0 and flow._managers == {}
    outputs.down = True
    assert plat.list_playing_apps() == [] and outputs.alive() == 0 and flow._managers == {}


def test_kept_managers_live_until_the_outputs_or_the_thread_change_or_close():
    """Com `keep` (o sensor do L1, numa thread só): a mesma leitura não ativa de novo; uma saída nova refaz todos; outra
    thread também (M-07); o que falhou é refeito na seguinte; a falha geral e o `close()` soltam tudo."""
    outputs = Outputs({"ep-a": FakeManager([FakeSession(200, 0.3)]), "ep-b": FakeManager([])})
    flow = _flow(outputs, keep=True)
    first = flow.read()
    from listener.platform.win32.sessions import Session

    assert first == {"ep-a": [Session(200, 0.3)], "ep-b": []} and flow.activations == 2
    assert flow.read() == first and flow.activations == 2 and outputs.alive() == 2      # os dois gerenciadores
    outputs.managers["ep-c"] = FakeManager([FakeSession(300, 0.1)])
    assert list(flow.read()) == ["ep-a", "ep-b", "ep-c"] and flow.activations == 2 + 3 and outputs.alive() == 3
    t = threading.Thread(target=flow.read)
    t.start()
    t.join(5)
    assert flow.activations == 5 + 3
    outputs.managers["ep-a"].fail = True
    assert list(flow.read()) == ["ep-b", "ep-c"] and flow.activations == 8 + 3          # de volta a esta thread
    outputs.managers["ep-a"].fail = False
    assert list(flow.read()) == ["ep-a", "ep-b", "ep-c"] and flow.activations == 11 + 1  # só o que falhou
    outputs.down = True
    with pytest.raises(OSError, match="Core Audio fora"):
        flow.read()
    assert outputs.alive() == 0
    outputs.down = False
    flow.read()
    assert outputs.alive() == 3
    flow.close()
    assert outputs.alive() == 0 and flow._key is None


def test_a_read_waits_for_the_one_in_progress():
    """Dois `_Poller` do "Antes de gravar" podem ler ao mesmo tempo (o novo nasce antes de o anterior sair): a trava os
    põe em fila."""
    from listener.platform.win32.sessions import Session

    outputs = Outputs({"ep-a": FakeManager([FakeSession(200, 0.3)])})
    flow = _flow(outputs, keep=False)
    got: list[object] = []
    with flow._lock:
        t = threading.Thread(target=lambda: got.append(flow.read()))
        t.start()
        t.join(0.2)
        assert t.is_alive() and got == []
    t.join(5)
    assert got == [{"ep-a": [Session(200, 0.3)]}]


def test_the_render_sessions_read_this_machine():
    """Só leitura: as saídas lidas são as ativas da enumeração, nenhuma sessão do sistema; sem guardar, cada leitura ativa
    de novo; guardando, a segunda não ativa, e o `close()` solta na mesma thread."""
    from listener.platform.win32 import endpoints, sessions

    scan = endpoints.scan()
    if scan is None:
        pytest.skip("o Core Audio não respondeu nesta máquina")
    ids = {eid for eid, _ in scan.render}
    del scan                                                                  # os ponteiros da enumeração, soltos aqui
    for keep in (False, True):
        flow = sessions.FlowSessions(endpoints.FLOW_RENDER, keep=keep)
        reading = flow.read()
        assert set(reading) <= ids
        assert all(s.pid > 0 and 0.0 <= s.peak <= 1.0 for group in reading.values() for s in group)
        first = flow.activations
        flow.read()
        assert flow.activations == (first if keep else 2 * first)
        flow.close()
        assert flow._managers == {}


# ---------------------------------------------------------------- os alvos do loopback do app, em todas as saídas
def _teams_tree() -> dict[int, Any]:
    """A árvore do Teams novo vista em 26/10 (D-055): o WebView2 da chamada é neto da raiz."""
    from listener.platform.win32.processes import ProcInfo

    return {p.pid: p for p in [
        ProcInfo(11516, "explorer.exe", 9), ProcInfo(33820, "ms-teams.exe", 11516), ProcInfo(36964, "ms-teams.exe", 33820),
        ProcInfo(35660, "msedgewebview2.exe", 33820), ProcInfo(32348, "msedgewebview2.exe", 35660),
    ]}


def _loopback_platform(monkeypatch: pytest.MonkeyPatch, outputs: Outputs) -> tuple[Any, list[int]]:
    """A plataforma com a árvore do Teams e um loopback de processo de mentira que anota os pids abertos."""
    from types import SimpleNamespace

    plat = _platform(monkeypatch, outputs)
    monkeypatch.setattr(plat, "_processes", lambda: (_teams_tree(), {}))
    opened: list[int] = []

    def open_one(pid: int, on_audio: Any, on_error: Any = None) -> Any:
        opened.append(pid)
        return SimpleNamespace(sample_rate=48000, channels=2, start=lambda: None, stop=lambda: None)

    monkeypatch.setattr(plat, "_open_process_loopback", open_one)
    return plat, opened


def test_a_grandchild_that_plays_only_on_the_communications_output_is_captured(monkeypatch):
    """O ms-teams.exe filho toca na saída padrão e o WebView2 neto, a chamada, só no fone de comunicação: o neto entra
    entre os alvos do loopback do app (o filho direto já vem no da raiz). Antes só a saída padrão era vista, e a captura
    ficava só com a raiz. Nada sobra guardado depois."""
    outputs = Outputs({"ep-padrao": FakeManager([FakeSession(36964, 0.1), FakeSession(0, 0.2)]),
                       "ep-fone": FakeManager([FakeSession(32348, 0.5)])})
    plat, opened = _loopback_platform(monkeypatch, outputs)
    plat.open_app_loopback(33820, lambda block, t: None)
    assert opened == [33820, 32348]
    assert plat._session_pids() == {36964, 32348} and outputs.alive() == 0


def test_the_loopback_falls_back_to_the_root_when_the_sessions_fail(monkeypatch):
    """Sem as sessões (o Core Audio fora), o loopback fica só com a raiz, como antes."""
    outputs = Outputs({"ep-fone": FakeManager([FakeSession(32348, 0.5)])})
    outputs.down = True
    plat, opened = _loopback_platform(monkeypatch, outputs)
    assert plat._session_pids() == set()
    plat.open_app_loopback(33820, lambda block, t: None)
    assert opened == [33820]
