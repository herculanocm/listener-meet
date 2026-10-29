"""F5.7 (L1): o sensor do aviso de reunião sem gravação (`platform/win32/activity.py`). As funções puras rodam em qualquer
sistema (o agrupamento pela raiz, a própria árvore fora, o cache de raízes, o padrão estrito do título); a leitura com
objetos falsos no lugar do COM e a leitura real desta máquina (só leitura) só no Windows."""
from __future__ import annotations

import sys
from typing import Any

import pytest

from listener.platform.base import AppAudio, AudioActivity
from listener.platform.win32 import activity
from listener.platform.win32.processes import ProcInfo

win32_only = pytest.mark.skipif(sys.platform != "win32", reason="as sessões do Core Audio só existem no Windows")
OWN = 901                                          # o interpretador do próprio app nos retratos de mentira


def _tree() -> dict[int, ProcInfo]:
    """A árvore do Teams novo e do WhatsApp vista em 26/10 (D-055), o Chrome, e o próprio app sob o venv: o lançador
    `python.exe` (900), o interpretador (901, filho dele), o filho ao vivo (902) e um conhost (903); outro Python solto (950)."""
    return {p.pid: p for p in [
        ProcInfo(11516, "explorer.exe", 9), ProcInfo(33820, "ms-teams.exe", 11516), ProcInfo(36964, "ms-teams.exe", 33820),
        ProcInfo(35660, "msedgewebview2.exe", 33820), ProcInfo(32348, "msedgewebview2.exe", 35660),
        ProcInfo(24760, "WhatsApp.Root.exe", 11516), ProcInfo(30284, "msedgewebview2.exe", 24760),
        ProcInfo(31984, "msedgewebview2.exe", 30284), ProcInfo(40, "chrome.exe", 11516), ProcInfo(41, "chrome.exe", 40),
        ProcInfo(900, "python.exe", 11516), ProcInfo(OWN, "python.exe", 900), ProcInfo(902, "python.exe", OWN),
        ProcInfo(903, "conhost.exe", OWN), ProcInfo(950, "python.exe", 11516), ProcInfo(50, "Zoom.exe", 11516),
    ]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _roots(clock: Clock | None = None) -> tuple[activity.RootCache, list[int]]:
    taken: list[int] = []

    def snapshot() -> dict[int, ProcInfo]:
        taken.append(1)
        return _tree()

    return activity.RootCache(snapshot, clock or Clock(), own_pid=OWN), taken


def test_own_tree_is_the_venv_launcher_and_its_interpreter_descendants():
    """O lançador, o interpretador e o filho ao vivo (`python.exe`); o conhost não tem sessão de áudio e fica fora."""
    assert activity.own_tree(OWN, _tree()) == {900, OWN, 902}
    assert activity.own_tree(777, _tree()) == {777}                       # fora do retrato: só ele


def test_a_browser_started_by_the_app_is_not_part_of_the_own_tree():
    """R-02: o Chrome aberto por um link do app é filho do interpretador, mas é outro app."""
    tree = {p.pid: p for p in [
        ProcInfo(11516, "explorer.exe", 9), ProcInfo(900, "python.exe", 11516), ProcInfo(OWN, "python.exe", 900),
        ProcInfo(70, "chrome.exe", OWN), ProcInfo(71, "chrome.exe", 70), ProcInfo(72, "chrome.exe", 70),
        ProcInfo(73, "pythonw.exe", OWN),                                   # um worker (o executável sem console)
    ]}
    assert activity.own_tree(OWN, tree) == {900, OWN, 73}
    roots = activity.RootCache(lambda: tree, Clock(), own_pid=OWN)
    assert roots.resolve({71, 72, 73}) == {71: (70, "chrome.exe"), 72: (70, "chrome.exe")}


def test_a_stale_ppid_equal_to_the_launcher_does_not_pull_explorer_into_the_own_tree():
    """R-02: o explorer.exe nasceu de um userinit.exe que saiu; o pid dele (900) voltou como o lançador do venv. Pelo ppid,
    o explorer e o que ele abriu (o Teams) virariam "o próprio app"."""
    procs = {p.pid: p for p in [
        ProcInfo(4000, "explorer.exe", 900), ProcInfo(33820, "ms-teams.exe", 4000), ProcInfo(36964, "ms-teams.exe", 33820),
        ProcInfo(7000, "WindowsTerminal.exe", 4000), ProcInfo(7100, "pwsh.exe", 7000),
        ProcInfo(900, "python.exe", 7100), ProcInfo(OWN, "python.exe", 900),
    ]}
    roots = activity.RootCache(lambda: procs, Clock(), own_pid=OWN)
    assert roots.resolve({36964}) == {36964: (33820, "ms-teams.exe")}


def test_a_pid_missing_from_the_reading_leaves_the_cache_and_a_reused_pid_gets_its_new_root():
    """R-07: o WebView2 32348 do Teams sai; o pid volta em 20 s num Spotify, e a raiz é a do Spotify."""
    clock = Clock()
    trees = [{p.pid: p for p in [ProcInfo(1, "explorer.exe", 0), ProcInfo(33820, "ms-teams.exe", 1),
                                 ProcInfo(35660, "msedgewebview2.exe", 33820),
                                 ProcInfo(32348, "msedgewebview2.exe", 35660)]},
             {p.pid: p for p in [ProcInfo(1, "explorer.exe", 0), ProcInfo(32348, "Spotify.exe", 1)]}]
    taken: list[int] = []

    def snapshot() -> dict[int, ProcInfo]:
        taken.append(1)
        return trees[min(len(taken) - 1, 1)]

    roots = activity.RootCache(snapshot, clock, own_pid=999)
    assert roots.resolve({32348}) == {32348: (33820, "ms-teams.exe")}
    clock.now += 5
    assert roots.resolve(set()) == {}                                       # a leitura sem o pid (a sessão expirou)
    clock.now += 15
    assert roots.resolve({32348}) == {32348: (32348, "Spotify.exe")} and len(taken) == 2


def test_discord_is_not_in_the_factory_list():
    """R-01 (D-065, pergunta 3): o Discord é "outro app" e avisa pela R3; quem quiser a R1 acrescenta à mão."""
    assert "discord.exe" not in {w.exe for w in activity.WATCHED}
    assert {w.label for w in activity.WATCHED} == {"Teams", "Zoom", "Webex", "Slack", "Edge", "Chrome", "Firefox", "Brave",
                                                   "Opera", "Vivaldi"}


def test_the_teams_webview2_sums_into_the_root_and_the_own_tree_stays_out():
    """O WebView2 neto toca a chamada e o ms-teams.exe filho tem o microfone: um app só, o ms-teams.exe raiz, com `mic`
    (um OU) e o maior pico. O próprio app gravando (901, 902), o pid 0 e o processo que saiu (777) ficam de fora."""
    roots, _taken = _roots()
    mic = {36964, OWN, 902, 0, 30284}
    peaks = {32348: 0.6, 36964: 0.1, 41: 0.3, OWN: 0.9, 777: 0.5, 31984: 0.2}
    apps = activity.group_apps(mic, peaks, roots.resolve(mic | set(peaks)))
    assert set(apps) == {33820, 40, 24760}
    teams = apps[33820]
    assert (teams.exe, teams.mic, teams.peak, teams.members) == ("ms-teams.exe", True, 0.6, [32348, 36964])
    assert (apps[40].exe, apps[40].mic, apps[40].peak) == ("chrome.exe", False, 0.3)
    assert (apps[24760].exe, apps[24760].mic, apps[24760].peak) == ("WhatsApp.Root.exe", True, 0.2)   # o mic no menor pid
    out = activity.to_activity(apps, {40: "Google Meet"})
    assert [a.exe for a in out.apps] == ["chrome.exe", "ms-teams.exe", "WhatsApp.Root.exe"]
    assert out.apps[0] == AppAudio(40, "chrome.exe", False, 0.3, "Google Meet") and not out.degraded


def test_the_process_snapshot_runs_only_for_a_new_pid_and_the_root_lasts_60_s():
    clock = Clock()
    roots, taken = _roots(clock)
    assert roots.resolve({32348}) == {32348: (33820, "ms-teams.exe")} and len(taken) == 1
    assert roots.resolve({32348}) and len(taken) == 1                       # guardado: nenhum retrato
    clock.now += 30
    assert roots.resolve({32348, 41}) == {32348: (33820, "ms-teams.exe"), 41: (40, "chrome.exe")} and len(taken) == 2
    clock.now += 31                                                          # o 32348 venceu (61 s); o 41 não (31 s)
    roots.resolve({32348, 41})
    assert len(taken) == 3
    roots.resolve({32348, 41})
    assert len(taken) == 3 and roots.snapshots == 3
    assert roots.resolve({OWN, 902, 0, 777}) == {} and len(taken) == 4      # de fora também fica guardado
    assert roots.resolve({OWN, 902}) == {} and len(taken) == 4


@pytest.mark.parametrize("title", [
    "Meet - abc-defg-hij", "Meet \N{EN DASH} abc-defg-hij - Google Chrome",
    "Meet - abc-defg-hij and 2 more pages - Personal - Microsoft Edge",
])
def test_the_meet_title_is_recognized(title):
    assert activity.match_service(title) == "Google Meet"


@pytest.mark.parametrize("title", [
    "", "Call of Duty - YouTube", "Meet the Parents - Netflix", "Google Meet tutorial - YouTube", "Meet - abc-defg-hijk",
    "Meeting - abc-defg-hij", "Minha Meet - abc-defg-hij", "Chat | Microsoft Teams", "Microsoft Teams", "Zoom Meeting",
    "Reunião de equipe - Google Docs", "call", "meet", "Google Meet", "Google Meet - Google Chrome",   # R-06: a página inicial
])
def test_a_loose_title_is_not_a_meeting(title):
    assert activity.match_service(title) == ""


def test_the_titles_are_read_only_for_the_pids_of_the_apps_asked():
    """R-05: o texto da janela só para a raiz e os membros dos navegadores pedidos (o resto nem é lido)."""
    asked: list[set[int]] = []

    def titles(pids: set[int]) -> dict[int, str]:
        asked.append(set(pids))
        return {40: "Meet - abc-defg-hij - Google Chrome", 33820: "Meet - abc-defg-hij", 41: "Call of Duty - YouTube"}

    apps = {40: activity.AppSignals("chrome.exe", members=[41]), 33820: activity.AppSignals("ms-teams.exe")}
    assert activity.services_for(apps, frozenset(), titles) == {} and asked == []
    assert activity.services_for(apps, frozenset({"firefox.exe"}), titles) == {} and asked == []
    assert activity.services_for(apps, frozenset({"chrome.exe"}), titles) == {40: "Google Meet"} and asked == [{40, 41}]
    only_child = {40: activity.AppSignals("chrome.exe", members=[41, 42])}
    assert activity.services_for(only_child, frozenset({"chrome.exe"}),
                                 lambda pids: {42: "Meet - abc-defg-hij"}) == {40: "Google Meet"}

    def broken(pids: set[int]) -> dict[int, str]:
        raise OSError("EnumWindows")

    assert activity.services_for(apps, frozenset({"chrome.exe"}), broken) == {}


# ---------------------------------------------------------------- a leitura, com os falsos do COM (só no Windows)
def _no_titles(pids: set[int]) -> dict[int, str]:
    return {}


def _reader(capture: Any, render: Any, titles: Any = _no_titles) -> tuple[activity.ActivityReader, list[int]]:
    from listener.platform.win32 import endpoints, sessions

    roots, taken = _roots()
    reader = activity.ActivityReader(sessions.FlowSessions(endpoints.FLOW_CAPTURE, capture.list, capture.activate, keep=True),
                                     sessions.FlowSessions(endpoints.FLOW_RENDER, render.list, render.activate, keep=True),
                                     roots, titles)
    return reader, taken


def _mics_and_outputs() -> tuple[Any, Any]:
    from tests.fake_com import FakeManager, FakeSession, Outputs

    capture = Outputs({"mic-fone": FakeManager([FakeSession(36964, 0.0, 1), FakeSession(41, 0.0, 0)]),
                       "mic-interno": FakeManager([FakeSession(31984, 0.0, 2), FakeSession(50, 0.0),
                                                   FakeSession(OWN, 0.4, 1)])})
    render = Outputs({"saida-padrao": FakeManager([FakeSession(32348, 0.2), FakeSession(41, 0.4), FakeSession(0, 0.9)]),
                      "saida-fone": FakeManager([FakeSession(32348, 0.6), FakeSession(OWN, 0.8)])})
    return capture, render


@win32_only
def test_the_mic_is_an_active_capture_session_and_the_peak_the_highest_output():
    """ATIVA conta; inativa, expirada e o estado desconhecido não (e, sem som, o app nem aparece: WhatsApp e Zoom). O pico
    do Teams é o maior entre as duas saídas; o próprio app (gravando e tocando) e a sessão do sistema ficam de fora."""
    capture, render = _mics_and_outputs()
    reader, taken = _reader(capture, render)
    got = reader.read()
    assert got == AudioActivity((AppAudio(40, "chrome.exe", False, 0.4), AppAudio(33820, "ms-teams.exe", True, 0.6)))
    assert reader.read() == got and len(taken) == 1                          # raízes guardadas
    assert capture.alive() == 2 and render.alive() == 2                      # os gerenciadores, guardados (`keep`)
    reader.close()
    assert capture.alive() == 0 and render.alive() == 0


@win32_only
def test_an_endpoint_that_fails_is_skipped_and_a_whole_failure_closes_and_raises():
    capture, render = _mics_and_outputs()
    reader, _taken = _reader(capture, render)
    capture.managers["mic-interno"].fail = True
    render.broken.add("saida-padrao")
    got = {a.exe: (a.mic, a.peak) for a in reader.read().apps}
    assert got == {"ms-teams.exe": (True, 0.6)}                            # o Chrome só tocava na saída que falhou
    render.down = True
    with pytest.raises(OSError, match="Core Audio fora"):
        reader.read()
    assert capture.alive() == 0 and render.alive() == 0                      # tudo solto, nos dois fluxos
    render.down = False
    capture.managers["mic-fone"].fail = True                                 # todos os microfones falham
    with pytest.raises(OSError, match="nenhum endpoint do fluxo 1"):
        reader.read()
    assert capture.alive() == 0 and render.alive() == 0


@win32_only
def test_the_service_comes_from_the_title_only_for_the_browsers_asked():
    capture, render = _mics_and_outputs()
    reader, _taken = _reader(capture, render, lambda pids: {40: "Meet - abc-defg-hij - Google Chrome", 33820: "Meet - x"})
    by_exe = {a.exe: a.service for a in reader.read(frozenset({"chrome.exe"})).apps}
    assert by_exe["chrome.exe"] == "Google Meet" and by_exe["ms-teams.exe"] == ""
    assert {a.service for a in reader.read().apps} == {""}


# ---------------------------------------------------------------- a parte herdada pelo Win32Platform
@win32_only
def test_the_platform_reads_lazily_closes_in_the_reading_thread_and_degrades_by_the_registry(monkeypatch):
    from listener.platform.win32 import consent
    from listener.platform.win32.platform import Win32Platform

    made: list[Any] = []

    class Reader:
        def __init__(self) -> None:
            self.reads: list[frozenset[str]] = []
            self.closed = 0
            made.append(self)

        def read(self, titles_for: frozenset[str]) -> AudioActivity:
            self.reads.append(titles_for)
            return AudioActivity(())

        def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(activity, "ActivityReader", Reader)
    plat = Win32Platform()
    plat.close_audio_activity()                                              # antes da primeira leitura: nada
    assert made == [] and plat.watched_apps() is activity.WATCHED
    plat.audio_activity(frozenset({"chrome.exe"}))
    plat.audio_activity()
    plat.close_audio_activity()
    assert len(made) == 1 and made[0].reads == [frozenset({"chrome.exe"}), frozenset()] and made[0].closed == 1
    entries = [consent.ConsentEntry("MSTeams_8wekyb3d8bbwe", True, 5, 0)]
    monkeypatch.setattr(consent, "mic_entries", lambda: entries)
    assert plat.audio_activity(degraded=True) == AudioActivity((AppAudio(0, "ms-teams.exe", True, 0.0),), degraded=True)
    assert len(made[0].reads) == 2                                           # o modo só pelo registro não usa o COM
    monkeypatch.setattr(consent, "mic_entries", lambda: None)
    assert plat.audio_activity(degraded=True) is None


@win32_only
def test_the_sensor_reads_this_machine():
    """Só leitura: apps com raiz e nome, picos de 0 a 1, nenhum serviço fora do padrão, o próprio processo fora; a segunda
    leitura não tira retrato de processos de novo (nenhum pid novo, quase sempre) e o `close` solta na mesma thread."""
    import os

    from listener.platform.win32.platform import Win32Platform

    plat = Win32Platform()
    first = plat.audio_activity(frozenset({"chrome.exe", "msedge.exe", "firefox.exe"}))
    assert first is not None and not first.degraded
    assert all(a.pid > 0 and a.exe and 0.0 <= a.peak <= 1.0 and a.service in {"", "Google Meet"} for a in first.apps)
    assert os.getpid() not in {a.pid for a in first.apps}
    assert plat.audio_activity() is not None
    plat.close_audio_activity()


def test_no_log_nor_print_gets_a_title_a_path_or_a_device_name():
    """Aceite 4 (D-053): nos módulos novos do L1 e na sonda, nenhum `log.*` nem `print` recebe título, caminho nem nome de
    aparelho (por nome de variável e de atributo, também dentro de f-string)."""
    from tests.log_privacy import L1_MODULES, leaks

    assert {str(m): leaks(m) for m in L1_MODULES} == {str(m): [] for m in L1_MODULES}


def test_the_log_detector_catches_a_title_a_path_and_a_registry_name():
    """O controle do detector: ele acha o que é proibido e deixa passar o nome da classe da exceção."""
    from tests.log_privacy import leaks_in

    assert leaks_in("log.debug('x %s', entry.name)") == ["1: name"]
    assert leaks_in("print(f'{title} ok')") == ["1: title"]
    assert leaks_in("log.warning('falhou: %s', path=key_path)") == [] and leaks_in("logger.info(path)") == ["1: path"]
    assert leaks_in("log.debug('t: %s', type(e).__name__)") == [] and leaks_in("other(title)") == []
