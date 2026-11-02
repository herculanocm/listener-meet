"""F5.13 (L1, Aceite 3) só no Windows: a trava de verdade (`CreateMutexW` num nome deste teste, nunca o do app do dono) é
pega uma vez por nome e o foco passado adiante não falha; e dez pares de processos lançados em sequência rápida
(`tests/instance_pair.py`, com a trava e o canal reais) dão um dono só da trava em cada par, e o outro entrega o pedido.
Desvio do texto do Aceite 3 (que fala do `.exe`): os pares são processos Python, para não abrir o app do dono vinte vezes;
o tempo do processo novo até o "ok" sai na saída do teste. Revisão (R-01): com a trava de verdade presa sem o canal, o
processo novo diz que o app está fechando no prazo, sem guardar o handle das tentativas perdidas; e a trava solta no meio
da espera passa a ser dele."""
from __future__ import annotations

import ctypes
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from listener.model import OpenTarget
from listener.platform.common import paths

pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="a trava de verdade é do Windows"), pytest.mark.lento]

ROOT = Path(__file__).resolve().parent.parent
PAIRS = 10


def test_the_lock_is_taken_once_per_name_and_the_focus_is_handed_over():
    from listener.platform.win32.taskbar import Win32Taskbar
    from listener.ui.instance import channel_name

    real = channel_name(paths.config_dir())
    names = [f"listener-meet-teste-{uuid.uuid4().hex[:12]}" for _ in range(2)]
    assert real not in names
    taskbar = Win32Taskbar()
    assert taskbar.claim_instance(names[0]) is True
    assert taskbar.claim_instance(names[0]) is False                 # a trava já existe (aqui, deste mesmo processo)
    assert taskbar.claim_instance(names[1]) is True
    taskbar.hand_over_foreground()                                   # sem janela à frente, o Windows pode recusar: só não falha


# ---------------------------------------------------------------- R-01: a trava de verdade presa sem o canal
def _real_lock_platform(tmp_path: Path) -> Any:
    from listener.platform.win32.taskbar import Win32Taskbar
    from tests.fake_platform import FakePlatform

    class RealLock(FakePlatform):
        """A plataforma falsa com a trava de verdade (o mutex) e a pasta de configuração do teste: um nome só dele."""

        def claim_instance(self, name: str) -> bool:
            self.claims.append(name)
            return Win32Taskbar().claim_instance(name)

    return RealLock(tmp_path)


def _hold(name: str) -> int:
    """O app que sai: a trava presa e o canal já fechado (nenhum servidor com o nome)."""
    from listener.platform.win32 import taskbar
    from listener.ui.instance import channel_name

    assert name != channel_name(paths.config_dir())
    assert taskbar.Win32Taskbar().claim_instance(name)
    return taskbar._HELD[-1]


def _release(handle: int) -> None:
    from listener.platform.win32 import taskbar

    taskbar._HELD.remove(handle)
    taskbar._kernel32().CloseHandle(handle)


def _handles() -> int:
    """Os handles abertos deste processo (`GetProcessHandleCount`)."""
    if sys.platform != "win32":                                      # o mypy das outras plataformas não tem o `WinDLL`
        return 0
    count = ctypes.c_ulong()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessHandleCount(ctypes.c_void_p(-1), ctypes.byref(count))
    return int(count.value)


def test_with_the_real_lock_held_and_no_channel_the_new_process_says_closing(qapp, tmp_path, monkeypatch):
    from listener.platform.win32 import taskbar
    from listener.ui import instance

    platform = _real_lock_platform(tmp_path)
    holder = _hold(instance.channel_name(platform.config_dir()))
    shown: list[str] = []
    monkeypatch.setattr(instance, "show_refusal", lambda _app, _platform, text: shown.append(text))
    monkeypatch.setattr(instance, "CLIENT_TIMEOUT_S", 0.8)
    try:
        before = _handles()
        assert instance.claim(qapp, platform, OpenTarget.QUEUE) is None
        grew, attempts = _handles() - before, len(platform.claims)
        assert shown == [instance.CLOSING_TEXT] and attempts > 5
        assert taskbar._HELD[-1] == holder and grew < attempts // 2, (grew, attempts)   # cada tentativa perdida fechou o handle
    finally:
        _release(holder)


def test_the_real_lock_freed_during_the_wait_makes_the_new_process_the_app(qapp, tmp_path, monkeypatch):
    from listener.platform.win32 import taskbar
    from listener.ui import instance

    platform = _real_lock_platform(tmp_path)
    holder = _hold(instance.channel_name(platform.config_dir()))
    shown: list[str] = []
    monkeypatch.setattr(instance, "show_refusal", lambda _app, _platform, text: shown.append(text))
    monkeypatch.setattr(instance, "CLIENT_TIMEOUT_S", 4.0)
    leaves = threading.Timer(0.3, _release, args=(holder,))          # o app sai no meio da espera
    leaves.start()
    server = None
    try:
        server = instance.claim(qapp, platform, OpenTarget.QUEUE)
        assert server is not None and server.server.isListening() and shown == []
        assert holder not in taskbar._HELD and len(platform.claims) > 1   # a trava agora é deste "processo novo"
    finally:
        leaves.join()
        if server is not None:
            server.close()
            server.setParent(None)
            server.deleteLater()
        if holder not in taskbar._HELD and taskbar._HELD:
            _release(taskbar._HELD[-1])


def _pair(folder: Path) -> list[dict[str, object]]:
    """Os dois processos do par, lançados um logo depois do outro; a linha de cada um, com o log dele em `log`."""
    argv = [sys.executable, str(ROOT / "tests" / "instance_pair.py")]
    env = os.environ | {"PYTHONUTF8": "1"}
    procs = [subprocess.Popen([*argv, repr(time.time()), str(folder)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE,  # noqa: S603
                              stderr=subprocess.PIPE, text=True, encoding="utf-8") for _ in range(2)]
    lines: list[dict[str, object]] = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err[-2000:]
        lines.append(json.loads(out.strip().splitlines()[-1]) | {"log": err[-1500:]})
    return lines


def test_ten_pairs_of_processes_give_one_owner_of_the_lock_each_time(tmp_path, capsys):
    elapsed: list[float] = []
    claim: list[float] = []
    for i in range(PAIRS):
        lines = _pair(tmp_path / f"par-{i}")
        assert sorted(str(line["role"]) for line in lines) == ["primary", "secondary"], lines
        primary = next(line for line in lines if line["role"] == "primary")
        secondary = next(line for line in lines if line["role"] == "secondary")
        assert primary["request"] == "history" and secondary["refusal"] is None, lines
        elapsed.append(float(secondary["elapsed_s"]))                # type: ignore[arg-type]
        claim.append(float(secondary["claim_s"]))                    # type: ignore[arg-type]
    report = {"pares": PAIRS, "do_lancamento_ao_ok_s": {"mediana": round(statistics.median(elapsed), 3), "max": max(elapsed),
                                                         "min": min(elapsed)},
              "trava_e_entrega_s": {"mediana": round(statistics.median(claim), 3), "max": max(claim)}}
    with capsys.disabled():
        sys.stdout.write("\nF5.13 Aceite 3 (pares de processos Python): " + json.dumps(report, ensure_ascii=False) + "\n")
