from listener.platform.win32.processes import ProcInfo, app_root, group_by_app, loopback_targets, root_of, root_pid_for


def _procs():
    return {p.pid: p for p in [
        ProcInfo(1, "explorer.exe", 0), ProcInfo(10, "chrome.exe", 1), ProcInfo(11, "chrome.exe", 10),
        ProcInfo(12, "chrome.exe", 10), ProcInfo(13, "chrome.exe", 12), ProcInfo(20, "ms-teams.exe", 1),
        ProcInfo(21, "msedgewebview2.exe", 20),
    ]}


def test_root_of_climbs_same_name_parents_only():
    procs = _procs()
    assert root_of(13, procs) == 10
    assert root_of(11, procs) == 10
    assert root_of(21, procs) == 21          # pai tem outro nome: não sobe
    assert root_of(999, procs) == 999        # desconhecido: devolve o próprio


def test_root_pid_for_picks_the_tree_root():
    procs = _procs()
    assert root_pid_for("chrome.exe", procs) == 10
    assert root_pid_for("CHROME.EXE", procs) == 10
    assert root_pid_for("zoom.exe", procs) is None


def _teams_machine():
    """A árvore vista no Windows do dono em 26/10 (D-055): o Teams novo e o WhatsApp, os dois com WebView2."""
    return {p.pid: p for p in [
        ProcInfo(1648, "wininit.exe", 1), ProcInfo(1728, "services.exe", 1648), ProcInfo(2336, "svchost.exe", 1728),
        ProcInfo(12012, "sihost.exe", 2336), ProcInfo(11516, "explorer.exe", 9),
        ProcInfo(33820, "ms-teams.exe", 11516), ProcInfo(36964, "ms-teams.exe", 33820),
        ProcInfo(35660, "msedgewebview2.exe", 33820), ProcInfo(32348, "msedgewebview2.exe", 35660),
        ProcInfo(24760, "WhatsApp.Root.exe", 12012), ProcInfo(30284, "msedgewebview2.exe", 24760),
        ProcInfo(31984, "msedgewebview2.exe", 30284), ProcInfo(40, "chrome.exe", 11516), ProcInfo(41, "chrome.exe", 40),
        ProcInfo(50, "Zoom.exe", 11516), ProcInfo(51, "CptHost.exe", 50),
    ]}


def test_app_root_climbs_to_the_app_that_hosts_the_webview2():
    procs = _teams_machine()
    assert app_root(36964, procs) == 33820          # o ms-teams.exe filho sobe para a raiz
    assert app_root(32348, procs) == 33820          # o WebView2 da chamada também: a raiz captura os dois
    assert app_root(31984, procs) == 24760          # o WhatsApp: para antes do sihost.exe (sistema)
    assert app_root(41, procs) == 40                # Chromium: pais de mesmo nome
    assert app_root(51, procs) == 51                # outro executável que não é WebView2: a sessão fica com ele
    assert app_root(999, procs) == 999              # desconhecido: o próprio


def test_loopback_targets_add_the_sessions_deeper_than_the_direct_children():
    """D-055: o loopback do Windows pega o processo e os filhos diretos (medido: o neto ficava mudo). O Teams toca a chamada
    no WebView2 neto da raiz; o ms-teams.exe filho direto já está na captura da raiz."""
    procs = _teams_machine() | {77: ProcInfo(77, "msedgewebview2.exe", 32348)}
    sessions = {36964, 32348, 77, 31984, 41}
    assert loopback_targets(33820, sessions, procs) == [33820, 32348]      # 77 é filho do alvo 32348: já está nele
    assert loopback_targets(24760, sessions, procs) == [24760, 31984]
    assert loopback_targets(40, sessions, procs) == [40]                   # o áudio do Chromium é filho direto
    assert loopback_targets(51, {51}, procs) == [51]
    assert loopback_targets(999, sessions, procs) == [999]                 # pid de fora: só ele, como antes


def test_group_by_app_joins_the_sessions_of_one_app_with_the_highest_peak():
    procs = _teams_machine()
    groups = group_by_app({36964: 0.0, 32348: 0.4, 31984: 0.1, 41: 0.2}, procs)
    assert groups == {33820: (0.4, (36964, 32348)), 24760: (0.1, (31984,)), 40: (0.2, (41,))}
    assert group_by_app({7: 0.3}, {}) == {7: (0.3, (7,))}                        # sem o snapshot: a própria sessão
