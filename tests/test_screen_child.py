"""F5.27 (lote 2): o processo `python -m listener --screen` de verdade, com a tela de mentira (`LISTENER_SCREEN=fake`): o hello,
os pedaços no banco e o `bye` depois do EOF, com o stdout do protocolo limpo."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("av")

from listener.screen_rec import client as sc
from listener.store import Store

pytestmark = pytest.mark.xdist_group("subprocesso")      # D-091: com os outros arquivos que lançam um filho de verdade
ROOT = Path(__file__).resolve().parent.parent


def test_the_real_screen_process_records_chunks_in_fake_mode_and_says_bye(tmp_path):
    db = tmp_path / "db"
    with Store(db) as store:
        store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
        mid = store.create_meeting("subprocesso da tela", None, None, None)
        store.screen.ensure_file(mid)
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_SCREEN"} | {"LISTENER_SCREEN": "fake", "PYTHONUTF8": "1"}
    argv = [sys.executable, "-m", "listener", "--screen", "--db", str(db), "--meeting", str(mid)]
    p = subprocess.Popen(argv, env=env, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603
                         stderr=subprocess.DEVNULL)
    assert p.stdin is not None and p.stdout is not None
    p.stdin.write(sc.encode_line(sc.ScreenHello(time.perf_counter(), "FAKE1", fps=5, chunk_s=1).to_msg()))
    p.stdin.flush()
    first = sc.decode_line(p.stdout.readline())
    assert first["type"] == sc.STARTED and first["monitor"] == 1
    time.sleep(1.5)
    out, _ = p.communicate(timeout=60)                    # EOF: fecha o último pedaço, grava e sai
    lines = [sc.decode_line(line) for line in out.splitlines()]      # toda linha é do protocolo
    assert p.returncode == 0 and lines[-1]["type"] == sc.BYE and lines[-1]["unwritten"] == 0
    with Store.open_readonly(db) as store:
        chunks = store.screen.list_chunks(mid)
    assert len(chunks) == lines[-1]["written"] >= 2 and chunks[0]["width"] == 64
    assert (tmp_path / "logs" / "listener-screen.log").exists()
