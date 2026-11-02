"""F5.13 (L1, Aceite 3) só no Windows: a trava de verdade (`CreateMutexW` num nome deste teste, nunca o do app do dono) é
pega uma vez por nome e o foco passado adiante não falha; e dez pares de processos lançados em sequência rápida
(`tests/instance_pair.py`, com a trava e o canal reais) dão um dono só da trava em cada par, e o outro entrega o pedido.
Desvio do texto do Aceite 3 (que fala do `.exe`): os pares são processos Python, para não abrir o app do dono vinte vezes;
o tempo do processo novo até o "ok" sai na saída do teste."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

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
