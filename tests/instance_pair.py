"""F5.13 (Aceite 3, sem abrir o app de verdade): um dos dois processos de um par, com a trava real do Windows
(`Win32Platform.claim_instance`) e o canal real (`ui/instance.claim`), pelo caminho que o `run` do app faz antes do tema
(o log, a identidade do processo, o `QApplication` e a tradução). A pasta de configuração é a do par, temporária: o nome
nunca é o do app do dono. Quem pega a trava atende o pedido e sai 1 s depois; quem não pega entrega "history" e mede o
tempo desde o lançamento (`t0`, do pai). Uma linha JSON por processo; `tests/test_instance_win.py` roda os pares.

  python tests/instance_pair.py T0 PASTA"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # o pacote deste checkout, não o instalado

WAIT_MS = 15_000                 # o que pegou a trava sai sozinho se o pedido nunca chegar (o teste falha pela linha)
LINGER_MS = 1000                 # ...e espera isto depois do pedido: quem fecha a conexão é o processo novo


def emit(**kw: object) -> None:
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + chr(10))
    sys.stdout.flush()


def main() -> int:
    t0, folder = float(sys.argv[1]), Path(sys.argv[2])
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from listener.log import setup
    from listener.model import OpenTarget
    from listener.platform.win32.platform import Win32Platform
    from listener.ui import app as _ui_app  # noqa: F401  # o que o `run` do app já importou neste ponto
    from listener.ui import instance
    from listener.ui.common import install_translations

    class PairPlatform(Win32Platform):
        def config_dir(self) -> Path:                          # o par tem a sua pasta: nunca a do perfil do dono
            return folder

    setup()
    platform = PairPlatform()
    platform.app_init()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    install_translations(app)
    shown: list[str] = []
    instance.show_refusal = lambda _app, _platform, text: shown.append(text)   # type: ignore[assignment]  # nenhuma caixa
    got: list[str] = []
    real_answer = instance.InstanceServer.answer

    def answer(self: instance.InstanceServer, target: OpenTarget) -> str:
        got.append(str(target))
        QTimer.singleShot(LINGER_MS, app, app.quit)
        return real_answer(self, target)

    # na classe, antes do `claim`: o processo novo que conecta enquanto o `listen` cria o pipe entra já dentro dele
    instance.InstanceServer.answer = answer                    # type: ignore[method-assign]
    t_claim = time.time()
    server = instance.claim(app, platform, OpenTarget.HISTORY)
    if server is None:
        now = time.time()
        emit(role="secondary", refusal=shown[0] if shown else None, elapsed_s=round(now - t0, 3), claim_s=round(now - t_claim, 3))
        return 0
    QTimer.singleShot(WAIT_MS, app, app.quit)
    app.exec()
    server.close()
    emit(role="primary", request=got[0] if got else None, ready_s=round(t_claim - t0, 3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
