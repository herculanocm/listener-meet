"""F5.13 (L1, Aceite 1): o "segundo processo" dos testes do canal, num processo à parte de verdade: no mesmo processo do
servidor, o cliente bloqueante segura o GIL na espera e o servidor só responde depois do prazo. `tests/test_instance.py` o
roda contra o servidor aberto no processo do teste.

  python tests/instance_client.py request NOME TELA          `deliver` com a plataforma falsa: imprime {"reply": R}
  python tests/instance_client.py raw NOME HEX [ESPERA_MS]    manda os bytes como estão e imprime {"got": HEX} com o que
                                                              voltou até o servidor fechar (ou a espera)"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # o pacote deste checkout, não o instalado


class _Handover:
    """O que o `deliver` usa da plataforma (a falsa inteira custaria o import do numpy e dos fakes a cada processo)."""

    def __init__(self) -> None:
        self.handovers = 0

    def hand_over_foreground(self) -> None:
        self.handovers += 1


def request(name: str, target: str) -> dict[str, object]:
    from listener.model import OpenTarget
    from listener.ui.instance import deliver

    platform = _Handover()
    return {"reply": deliver(platform, name, OpenTarget(target)), "handovers": platform.handovers}   # type: ignore[arg-type]


def raw(name: str, data: bytes, wait_ms: int) -> dict[str, object]:
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(3000):
        return {"error": "não conectou"}
    sock.write(data)
    sock.waitForBytesWritten(3000)
    got = bytearray()
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline and sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
        if sock.waitForReadyRead(50):
            got += bytes(sock.readAll().data())
    got += bytes(sock.readAll().data())
    closed = sock.state() != QLocalSocket.LocalSocketState.ConnectedState
    sock.abort()
    return {"got": bytes(got).hex(), "closed": closed}


def main() -> int:
    from PySide6.QtCore import QCoreApplication

    _app = QCoreApplication(sys.argv[:1])
    mode, name = sys.argv[1], sys.argv[2]
    if mode == "request":
        out = request(name, sys.argv[3])
    else:
        out = raw(name, bytes.fromhex(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3000)
    sys.stdout.write(json.dumps(out) + chr(10))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
