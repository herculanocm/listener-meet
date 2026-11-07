"""F5.18: o vigia de travamentos da suíte (`tests/hang_guard.py`) despeja as pilhas e derruba um processo preso."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests import hang_guard

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, log: Path, hang_s: str) -> tuple[int, int, str]:
    """Roda `code` num Python filho com o vigia apontado para `log`; devolve (pid do interpretador, código de saída, stdout).
    O pid é o primeiro token impresso pelo filho: no Windows o `python.exe` da venv é um lançador, e o `Popen.pid` seria o
    dele, não o do interpretador que escreve o marcador."""
    env = os.environ | {"LISTENER_TEST_HANG_LOG": str(log), "LISTENER_TEST_HANG_S": hang_s, "PYTHONUTF8": "1"}
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid(), flush=True); " + code],  # noqa: S603
                          check=False, env=env, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          timeout=60)
    return int(done.stdout.split()[0]), done.returncode, done.stdout


def test_a_stuck_process_is_dumped_and_killed_within_the_deadline(tmp_path):
    log = tmp_path / "hang.log"
    code = ("import time; from tests import hang_guard\n"
            "def fica_preso():\n"
            "    time.sleep(30)\n"
            "hang_guard.note('fase de teste')\n"
            "hang_guard.arm(1.0, 'tests/x.py::test_preso')\n"
            "fica_preso()\n"
            "print('nunca chega aqui')\n")
    pid, code_out, out = _run(code, log, "1")
    assert code_out != 0 and "nunca chega aqui" not in out
    text = log.read_text(encoding="utf-8")
    assert "fase de teste" in text and "Timeout" in text and "in fica_preso" in text     # a pilha de quem travou
    marker = Path(tempfile.gettempdir()) / f"{hang_guard.MARKER_PREFIX}{pid}.txt"
    try:
        assert f"pid {pid}: tests/x.py::test_preso" in marker.read_text(encoding="utf-8")   # o marcador fica para trás
    finally:
        marker.unlink(missing_ok=True)


def test_a_process_that_finishes_in_time_is_left_alone_and_clears_its_marker(tmp_path):
    log = tmp_path / "hang.log"
    code = ("from tests import hang_guard\n"
            "hang_guard.arm(5.0, 'tests/x.py::test_rapido')\n"
            "hang_guard.disarm()\n"
            "hang_guard.arm(5.0, 'fim da sessão', marker=False)\n"
            "print('terminou')\n")
    pid, code_out, out = _run(code, log, "5")
    assert code_out == 0 and "terminou" in out
    assert not log.exists() or "Timeout" not in log.read_text(encoding="utf-8")
    assert not (Path(tempfile.gettempdir()) / f"{hang_guard.MARKER_PREFIX}{pid}.txt").exists()


def test_the_marker_names_the_test_in_progress_and_disarm_removes_it():
    hang_guard.arm(60.0, "tests/y.py::test_em_curso")
    try:
        assert "tests/y.py::test_em_curso" in hang_guard.MARKER.read_text(encoding="utf-8")
    finally:
        hang_guard.disarm()
    assert not hang_guard.MARKER.exists()
