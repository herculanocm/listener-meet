"""F5.13 (L1, Aceite 1): dois processos pelo caminho do app (`listener.ui.app.run`) com a plataforma falsa dos testes, cuja
trava de instância é simulada (`instance_free`); o canal é o de verdade. `tests/test_instance.py` roda os dois.

  python tests/instance_scenario.py primary PASTA            o app (banco com o usuário, bandeja simulada): imprime
                                                             {"ready": true} quando o canal passou ao controlador e
                                                             {"opened": X} a cada tela aberta pelo canal (a da abertura e a
                                                             do pedido); sai depois da segunda
  python tests/instance_scenario.py secondary PASTA TELA     o processo novo, sem a trava: entrega TELA e imprime {"code": N}

Nada de áudio, rede ou chave: `LISTENER_LLM=fake`, o pré-aquecimento do player desligado e a interface `offscreen`; a mensagem
do processo novo vira uma linha {"refusal": texto} em vez da caixa."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LISTENER_LLM"] = "fake"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # o pacote deste checkout, não o instalado

SAFETY_MS = 30_000               # o primeiro sai sozinho se o pedido nunca chegar (o teste então falha pela saída)


def emit(**kw: object) -> None:
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + chr(10))
    sys.stdout.flush()


def primary(root: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from listener.config import bootstrap
    from listener.store import Store
    from listener.ui import app as ui_app
    from listener.ui import controller as ctl
    from listener.ui import instance
    from tests.fake_platform import FakePlatform

    platform = FakePlatform(root)
    with Store(root / "db") as store:
        store.upsert_me("Eu")
    bootstrap.write_db_dir(platform.config_dir(), root / "db")
    ctl.prewarm_audio = lambda: None                    # type: ignore[assignment,return-value]  # nada de áudio
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)   # type: ignore[method-assign]
    real_attach, real_open = instance.InstanceServer.attach, instance.open_target
    opened: list[str] = []

    def attach(self: instance.InstanceServer, controller: ctl.Controller) -> None:
        real_attach(self, controller)
        QTimer.singleShot(SAFETY_MS, QApplication.instance(), QApplication.quit)
        emit(ready=True)

    def open_target(controller: ctl.Controller, target: instance.OpenTarget) -> None:
        real_open(controller, target)
        opened.append(str(target))
        emit(opened=str(target))
        if len(opened) == 2:
            QTimer.singleShot(0, QApplication.instance(), QApplication.quit)

    instance.InstanceServer.attach = attach                    # type: ignore[method-assign]
    instance.open_target = open_target                         # type: ignore[assignment]
    return ui_app.run(["listener"], platform)


def secondary(root: Path, target: str) -> int:
    from listener.model import OpenTarget
    from listener.ui import app as ui_app
    from listener.ui import instance
    from tests.fake_platform import FakePlatform

    platform = FakePlatform(root)
    platform.instance_free = False                             # a trava é do primeiro
    instance.show_refusal = lambda _app, _platform, text: emit(refusal=text)   # type: ignore[assignment]
    code = ui_app.run(["listener"], platform, OpenTarget(target))
    emit(code=code)
    return code


if __name__ == "__main__":
    role, folder = sys.argv[1], Path(sys.argv[2])
    sys.exit(primary(folder) if role == "primary" else secondary(folder, sys.argv[3]))
