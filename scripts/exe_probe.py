"""Sonda do executável (F5.8, Aceite 5). No `.exe`: `listener-meet.exe --probe > sonda.json` (o lançador do build a chama;
sem console, o JSON só sai com o stdout redirecionado). No venv, para comparar: `.venv\\Scripts\\python.exe
scripts\\exe_probe.py`.

Uma linha JSON com:
- as importações da pilha na ordem do processo ao vivo (torch antes do faster_whisper, depois ctranslate2 e onnxruntime),
  com o tempo e um uso mínimo de cada (o VAD do faster-whisper carrega o onnx dos dados do pacote);
- as classes do pyannote community-1 achadas pelo nome, como o `from_pretrained` acha (o pipeline pelo YAML; a
  segmentação, o wespeaker e o PLDA pelo módulo gravado no modelo), e a conferência de dependências dele (lê a metadata);
- o `sys.flags.utf8_mode` aqui e num filho lançado como o app lança os seus (o mesmo executável, `spawn_child` com as flags
  de criação e o `worker_env`);
- o estilo efetivo do Qt por baixo do tema (um plugin ausente cai calado no padrão), a tradução pt-BR e se `tests` (os
  fakes) entrou no build.
Sai com 0 quando tudo bate com o Aceite 5. Não abre microfone, não toca som, não abre janela (Qt offscreen) nem lê o perfil
do usuário (só o caminho de quem chama decide `APPDATA`; nada aqui o lê).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # no venv, o pacote deste checkout

CHILD_FLAG = "--child"
PIPELINE_CLASS = "pyannote.audio.pipelines.SpeakerDiarization"        # `pipeline: name:` do config.yaml do community-1
MODEL_CLASSES = (("pyannote.audio.models.segmentation.PyanNet", "PyanNet"),           # a segmentação
                 ("pyannote.audio.models.embedding.wespeaker", "WeSpeakerResNet34"),  # o wespeaker (também o EMBEDDING_REPO)
                 ("pyannote.audio.core.plda", "PLDA"))                                 # o PLDA do agrupamento VBx
CHILD_TIMEOUT_S = 120.0
# a pasta do app: a do `.exe` empacotado, a do checkout no venv
APP_DIR = str(Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent)


def scrub(text: str, app_dir: str, home: str) -> str:
    """Tira os caminhos da mensagem de erro (o JSON é para ser colado): a pasta do app vira `<app>` e a do usuário `~`,
    nessa ordem (a do app pode estar dentro da do usuário); sem diferença de maiúsculas, como no Windows."""
    for path, mark in ((app_dir, "<app>"), (home, "~")):
        if path:
            text = re.sub(re.escape(path), mark, text, flags=re.IGNORECASE)    # as marcas não têm barra invertida
    return text


def _step(fn: Callable[[], str]) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        value = fn()
    except Exception as e:
        error = scrub(f"{type(e).__name__}: {e}", APP_DIR, str(Path.home()))
        return {"ok": False, "ms": round((time.perf_counter() - t0) * 1000, 1), "error": error[:500]}
    return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1), "value": value}


def _torch() -> str:
    import torch

    torch.set_num_threads(1)                         # como o embedder do ao vivo
    return f"{torch.__version__} soma={float(torch.arange(10.0).sum())}"


def _faster_whisper() -> str:
    import faster_whisper
    from faster_whisper.vad import get_vad_model

    return f"{faster_whisper.__version__} vad={type(get_vad_model()).__name__}"


def _ctranslate2() -> str:
    import ctranslate2

    return f"{ctranslate2.__version__} {sorted(ctranslate2.get_supported_compute_types('cpu'))}"


def _onnxruntime() -> str:
    import onnxruntime

    return f"{onnxruntime.__version__} {onnxruntime.get_available_providers()}"


def stack() -> dict[str, Any]:
    """A pilha na ordem do processo ao vivo (o torch antes do Whisper, F4)."""
    return {"torch": _step(_torch), "faster_whisper": _step(_faster_whisper), "ctranslate2": _step(_ctranslate2),
            "onnxruntime": _step(_onnxruntime)}


def _pyannote_names() -> str:
    from pyannote.audio.utils.dependencies import check_dependencies
    from pyannote.core.utils.helper import get_class_by_name

    found = [get_class_by_name(PIPELINE_CLASS).__name__]
    found += [getattr(importlib.import_module(module), name).__name__ for module, name in MODEL_CLASSES]
    check_dependencies({"pyannote.audio": metadata.version("pyannote.audio")}, "Pipeline")
    return " ".join(found)


def pyannote() -> dict[str, Any]:
    return _step(_pyannote_names)


def child_command() -> list[str]:
    """O próprio executável com a flag do filho (empacotado) ou este arquivo no Python do venv."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--probe", CHILD_FLAG]
    return [sys.executable, str(Path(__file__).resolve()), CHILD_FLAG]


def child() -> dict[str, Any]:
    """O filho lançado como o app lança os seus: `spawn_child` (sem janela, abaixo do normal, sem o cursor de carregando) e
    o `worker_env` (que pede o modo UTF-8 pelo `PYTHONUTF8`)."""
    from listener.platform import get_platform
    from listener.platform.common import process

    def run() -> str:
        proc = get_platform().spawn_child(child_command(), process.worker_env(1))
        if not isinstance(proc, subprocess.Popen):
            raise TypeError("o filho da plataforma não é um subprocess.Popen")
        try:
            out, _err = proc.communicate(timeout=CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"o filho não respondeu em {CHILD_TIMEOUT_S:.0f} s") from None
        if proc.returncode != 0:
            raise RuntimeError(f"o filho saiu com {proc.returncode}")
        return str(json.loads(out.decode("utf-8").strip().splitlines()[-1])["utf8_mode"])

    return _step(run)


def qt() -> dict[str, Any]:
    """O estilo efetivo por baixo do `ThemeStyle` (o que o Qt de fato criou), o esperado pela plataforma e a tradução."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QProxyStyle, QStyleFactory

    from listener.platform import get_platform
    from listener.ui import theme
    from listener.ui.common import install_translations

    platform = get_platform()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    translated = install_translations(app) is not None
    theme.apply(app, platform)
    style = app.style()
    base = style.baseStyle() if isinstance(style, QProxyStyle) else style
    available = QStyleFactory.keys()                           # os plugins de estilo que o Qt achou
    out = {"effective_style": base.name().lower(), "expected_style": platform.ui_style().lower(), "pt_BR": translated,
           "styles": [k.lower() for k in available], "qpa": QApplication.platformName()}
    theme.release(app)
    return out


def verdict(report: dict[str, Any]) -> bool:
    """O Aceite 5: tudo importou, o pyannote achou as classes, UTF-8 no filho, o estilo esperado, pt-BR e sem `tests`."""
    return (all(step["ok"] for step in report["stack"].values()) and report["pyannote"]["ok"]
            and report["child_utf8_mode"].get("value") == "1" and report["qt"]["effective_style"] == report["qt"]["expected_style"]
            and report["qt"]["pt_BR"] and not report["tests_in_build"])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if CHILD_FLAG in args:
        print(json.dumps({"utf8_mode": sys.flags.utf8_mode, "frozen": bool(getattr(sys, "frozen", False))}), flush=True)
        return 0
    report: dict[str, Any] = {"frozen": bool(getattr(sys, "frozen", False)), "executable": Path(sys.executable).name,
                              "utf8_mode": sys.flags.utf8_mode, "stack": stack(), "pyannote": pyannote(),
                              "child_utf8_mode": child(), "qt": qt(),
                              "tests_in_build": bool(getattr(sys, "frozen", False)) and importlib.util.find_spec("tests") is not None}
    report["ok"] = verdict(report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
