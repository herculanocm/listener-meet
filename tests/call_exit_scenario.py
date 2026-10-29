"""F5.7 L3 (R-01): a saída do app com a thread do sensor de chamada presa no sistema de áudio (como numa troca de dock)
termina com código 0. Roda num processo à parte (`tests/test_call_watch.py` confere o código de saída; também roda sozinho:
`python tests/call_exit_scenario.py [--sem-saida-imediata]`). O caminho é o do app (`listener.ui.app.run`: o banco, o
controlador, a bandeja, a cola do aviso, o `aboutToQuit` e o `theme.release`), com a plataforma falsa dos testes cuja leitura
do sensor nunca volta. Sem a saída imediata (`--sem-saida-imediata`), a derrubada do PySide destrói a QThread rodando e o Qt
aborta o processo (0xC0000409 no Windows). Nada de áudio, rede ou chave: `LISTENER_LLM=fake`, o pré-aquecimento do player
desligado e a interface `offscreen`."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LISTENER_LLM"] = "fake"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # o pacote deste checkout, não o instalado

POLL_MS = 20                     # a saída começa assim que a primeira leitura do sensor entra na plataforma e fica presa
SAFETY_MS = 20_000               # ...ou depois disto, se ela nunca entrar (o teste então falha pelo log)


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from listener.config import bootstrap
    from listener.platform.base import AudioActivity
    from listener.store import Store
    from listener.ui import app as ui_app
    from listener.ui import call_watch, controller
    from tests.fake_platform import FakePlatform

    never, entered = threading.Event(), threading.Event()

    class StuckPlatform(FakePlatform):
        def audio_activity(self, titles_for: frozenset[str] = frozenset(), degraded: bool = False) -> AudioActivity | None:
            entered.set()
            never.wait()                                         # presa como no COM numa troca de dock: nunca volta
            return None

    root = Path(tempfile.mkdtemp(prefix="listener-call-exit-"))
    platform = StuckPlatform(root)
    with Store(root / "db") as store:
        store.upsert_me("Eu")
    bootstrap.write_db_dir(platform.config_dir(), root / "db")
    controller.prewarm_audio = lambda: None                    # type: ignore[assignment,return-value]  # nada de áudio
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)   # type: ignore[method-assign]  # sem ela, o app abriria o "Antes de gravar"
    real_start = call_watch.CallWatch.start

    def start(self: call_watch.CallWatch) -> None:
        real_start(self)
        poll = QTimer(QApplication.instance())
        poll.setInterval(POLL_MS)
        poll.timeout.connect(lambda: entered.is_set() and QApplication.quit())
        poll.start()
        QTimer.singleShot(SAFETY_MS, QApplication.instance(), QApplication.quit)

    call_watch.CallWatch.start = start                         # type: ignore[method-assign]
    if "--sem-saida-imediata" in sys.argv:
        ui_app.leave_if_stuck = lambda _code: None             # type: ignore[assignment]  # a prova: sem a correção
    return ui_app.run(["listener"], platform)


if __name__ == "__main__":
    sys.exit(main())
