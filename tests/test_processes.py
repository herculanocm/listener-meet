from listener.platform.win32.processes import ProcInfo, root_of, root_pid_for


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
