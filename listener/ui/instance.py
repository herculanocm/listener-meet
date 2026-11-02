"""F5.13 (L1, U-129, U-130): uma instância só. Em `ui/app.py:run`, logo depois do `QApplication`, antes do tema, do banco e
do assistente, `claim` pede a trava à plataforma (`claim_instance`; no Windows, um mutex da sessão) com o nome do canal:
"listener-meet-" e os 12 primeiros hex do sha256 da pasta de configuração (o nome do usuário que ela traz nunca vai no
nome). Quem pega abre o canal, um `QLocalServer` só do usuário (`UserAccessOption`; o `removeServer` antes do `listen` limpa
o arquivo de uma queda fora do Windows, e a trava garante que ninguém mais o usa), e segue; quem não pega passa o foco
adiante (`hand_over_foreground`), entrega o pedido e sai com 0, sem abrir um segundo app.

Protocolo: uma linha JSON de até 256 bytes, `{"open": X}` com X em `OpenTarget`; a resposta, "ok" ou "fechando" (o app
saindo). Qualquer outra coisa é ignorada com uma linha de log e sem resposta. O cliente bloqueia até 5 s no total, tentando
conectar enquanto o servidor não existe (o app que tem a trava pode estar abrindo); com "fechando", a mensagem do passo 3
da spec; sem resposta, a do passo 4.

Até o controlador existir (o assistente, o banco que não abre), o pedido só traz à frente o diálogo modal aberto. Depois
(`attach`): com o app saindo, "fechando"; com um diálogo modal aberto, só ele vem à frente; senão a ação sai no próximo giro
do laço (as Configurações e o "Antes de gravar" seguram o laço no `exec`, e a resposta não espera por eles): "history",
"queue" e "settings" pelo caminho do clique no aviso (`Controller.open_target`, que traz a janela à frente pela
plataforma) e "record" pelo do clique esquerdo na bandeja (`TrayIcon.left_click`, com a reunião pendente da F5.7). Log sem
caminho e sem nome (D-053): o alvo, o motivo da recusa e o tempo."""
from __future__ import annotations

import functools
import hashlib
import json
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from ..log import get_logger
from ..model import OpenTarget

if TYPE_CHECKING:
    from ..platform.base import Platform
    from .controller import Controller

log = get_logger("ui.instance")

APP_NAME = "listener-meet"
NAME_PREFIX, NAME_HEX = f"{APP_NAME}-", 12
MAX_LINE = 256                         # bytes da linha do pedido, sem o fim de linha
REPLY_OK, REPLY_CLOSING = "ok", "fechando"
CLIENT_TIMEOUT_S = 5.0                 # o processo novo desiste depois disto (o passo 4 da spec)
RETRY_S = 0.05                         # entre as tentativas de conectar enquanto o servidor não existe
READ_TIMEOUT_MS = 2000                 # o servidor fecha a conexão que não mandou a linha inteira nisto
VALUES = frozenset(str(t) for t in OpenTarget)
CLOSING_TEXT = "O listener-meet está fechando. Abra de novo em alguns segundos."
NO_REPLY_TEXT = "O listener-meet já está aberto, mas não respondeu. Espere um pouco ou feche-o pelo Gerenciador de Tarefas."


def channel_name(config_dir: Path) -> str:
    """O nome da trava e do canal: o do app e um resumo da pasta de configuração. A mesma pasta, sem diferença de maiúsculas,
    dá o mesmo nome; outro perfil (outra pasta, como a das sondas) é outra instância."""
    digest = hashlib.sha256(str(config_dir).casefold().encode("utf-8")).hexdigest()
    return NAME_PREFIX + digest[:NAME_HEX]


def request_line(target: OpenTarget) -> bytes:
    return json.dumps({"open": str(target)}).encode("utf-8") + b"\n"


def parse_request(line: bytes) -> OpenTarget:
    """O alvo da linha do pedido (sem o fim de linha); `ValueError` com o motivo, para o log (nunca o conteúdo da linha)."""
    if len(line) > MAX_LINE:
        raise ValueError(f"linha acima de {MAX_LINE} bytes")
    try:
        data = json.loads(line.decode("utf-8"))
    except ValueError:                                   # UTF-8 ou JSON inválido
        raise ValueError("JSON inválido") from None
    value = data.get("open") if isinstance(data, dict) and len(data) == 1 else None
    if not isinstance(value, str) or value not in VALUES:
        raise ValueError("pedido desconhecido")
    return OpenTarget(value)


def refusal_text(reply: str | None) -> str | None:
    """A mensagem do processo novo: nenhuma com "ok"; a do passo 3 com "fechando"; a do passo 4 com o resto (sem resposta)."""
    if reply == REPLY_OK:
        return None
    return CLOSING_TEXT if reply == REPLY_CLOSING else NO_REPLY_TEXT


# ---------------------------------------------------------------- o processo novo
def _left_ms(deadline: float) -> int:
    return max(0, int((deadline - time.monotonic()) * 1000))


def _connect(sock: QLocalSocket, name: str, deadline: float) -> bool:
    while True:
        sock.connectToServer(name)
        if sock.waitForConnected(_left_ms(deadline)):
            return True
        sock.abort()
        if time.monotonic() + RETRY_S >= deadline:
            return False
        time.sleep(RETRY_S)                              # o servidor ainda não existe: o app que tem a trava está abrindo


def _reply(sock: QLocalSocket, deadline: float) -> str | None:
    data = bytearray()
    while b"\n" not in data and len(data) <= MAX_LINE:
        if not sock.bytesAvailable() and not sock.waitForReadyRead(_left_ms(deadline)):
            data += bytes(sock.readAll().data())         # o que chegou junto com o fim da conexão
            break
        data += bytes(sock.readAll().data())
    line, newline, _rest = bytes(data).partition(b"\n")
    return line.decode("utf-8", "replace").strip() if newline else None


def send_request(name: str, target: OpenTarget, timeout_s: float | None = None) -> str | None:
    """Conecta, manda a linha e espera a resposta, tudo em até `timeout_s` (padrão: 5 s); None quando nada voltou (o outro
    travado, ainda abrindo ou saindo, ou a linha recusada)."""
    deadline = time.monotonic() + (CLIENT_TIMEOUT_S if timeout_s is None else timeout_s)
    sock = QLocalSocket()
    try:
        if not _connect(sock, name, deadline):
            return None
        sock.write(request_line(target))
        if not sock.waitForBytesWritten(_left_ms(deadline)):
            return None
        return _reply(sock, deadline)
    finally:
        sock.abort()


def deliver(platform: Platform, name: str, target: OpenTarget) -> str | None:
    """O processo que não pegou a trava: passa o foco adiante e entrega o pedido; devolve a resposta (None: nenhuma)."""
    platform.hand_over_foreground()
    t0 = time.perf_counter()
    reply = send_request(name, target)
    ms = (time.perf_counter() - t0) * 1000
    if reply == REPLY_OK:
        log.info("outra instância já roda: pedido entregue (%s) em %.0f ms", target, ms)
    elif reply == REPLY_CLOSING:
        log.info("outra instância já roda e está fechando: pedido não entregue (%s)", target)
    else:
        log.warning("outra instância já roda e não respondeu em %.0f ms: pedido não entregue (%s)", ms, target)
    return reply


def show_refusal(app: QApplication, platform: Platform, text: str) -> None:
    """Os passos 3 e 4: a mensagem no tema do app, aplicado só para ela e solto antes de sair (R-06)."""
    from ..model import AppState
    from . import theme
    from .tray import make_icon

    theme.apply(app, platform)
    try:
        app.setWindowIcon(make_icon(AppState.IDLE))
        if text == CLOSING_TEXT:
            QMessageBox.information(None, APP_NAME, text)
        else:
            QMessageBox.warning(None, APP_NAME, text)
    finally:
        theme.release(app)


def claim(app: QApplication, platform: Platform, target: OpenTarget | None) -> InstanceServer | None:
    """U-129: a trava e o canal aberto (quem pega segue com eles) ou o pedido entregue ao app que já roda (None: este processo
    sai com 0). Sem `--open`, o pedido é o Histórico (U-130: o clique no ícone fixado)."""
    name = channel_name(platform.config_dir())
    if platform.claim_instance(name):
        server = InstanceServer(name, platform, parent=app)
        server.listen()
        return server
    text = refusal_text(deliver(platform, name, target or OpenTarget.HISTORY))
    if text is not None:
        show_refusal(app, platform, text)
    return None


# ---------------------------------------------------------------- o app que tem a trava
def open_target(controller: Controller, target: OpenTarget) -> None:
    """O pedido no controlador: "history", "queue" e "settings" pelo caminho do clique no aviso (abre e traz à frente pela
    plataforma); "record" pelo do clique esquerdo na bandeja (o "Antes de gravar", o da reunião pendente da F5.7 ou o widget
    da gravação em curso); sem bandeja, o "Antes de gravar"."""
    from .notices import NoticeAction, Target

    if controller.quitting:
        return
    if target == OpenTarget.RECORD:
        if controller.tray is not None:
            controller.tray.left_click()
        else:
            controller.open_warmup()
        return
    screens = {OpenTarget.HISTORY: Target.HISTORY, OpenTarget.QUEUE: Target.QUEUE, OpenTarget.SETTINGS: Target.SETTINGS}
    controller.open_target(NoticeAction(screens[target]))


class InstanceServer(QObject):
    """O canal do pedido no processo que tem a trava; vive com o `QApplication` e fecha na saída do `run`."""

    def __init__(self, name: str, platform: Platform, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._platform = platform
        self._controller: Controller | None = None
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        QLocalServer.removeServer(self.name)
        if self.server.listen(self.name):
            return True
        log.warning("canal do pedido indisponível: %s", self.server.errorString())
        return False

    def close(self) -> None:
        self.server.close()

    def attach(self, controller: Controller) -> None:
        """Depois do `controller.start()`: os pedidos vão ao controlador (antes, só o diálogo modal aberto vem à frente)."""
        self._controller = controller

    def open_later(self, target: OpenTarget) -> None:
        """A tela no próximo giro do laço, pelo controlador: a do pedido e a da abertura (U-130: o Histórico ou a do `--open`)."""
        if self._controller is not None:
            QTimer.singleShot(0, self._controller, functools.partial(open_target, self._controller, target))

    def answer(self, target: OpenTarget) -> str:
        """A resposta ao pedido válido, sem bloquear: a ação sai no próximo giro do laço."""
        controller = self._controller
        if controller is not None and controller.quitting:
            log.info("pedido recebido com o app saindo: %s", target)
            return REPLY_CLOSING
        modal = QApplication.activeModalWidget()
        if modal is not None:
            log.info("pedido recebido: %s (só o diálogo aberto vem à frente)", target)
            self._front(modal)
        else:
            log.info("pedido recebido: %s", target)
            self.open_later(target)
        return REPLY_OK

    def _front(self, win: QWidget) -> None:
        win.raise_()
        win.activateWindow()
        with suppress(Exception):
            self._platform.bring_to_front(int(win.winId()))

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if sock is not None:
                _Connection(sock, self.answer, self)


class _Connection(QObject):
    """Uma conexão: junta a linha (até 256 bytes), responde e fecha; o que não é pedido válido sai sem resposta, com uma linha
    de log, e a conexão que não manda a linha inteira em 2 s é fechada."""

    def __init__(self, sock: QLocalSocket, answer: Callable[[OpenTarget], str], parent: QObject) -> None:
        super().__init__(parent)
        self.sock = sock
        sock.setParent(self)
        self._answer = answer
        self._data = bytearray()
        self._done = False
        sock.readyRead.connect(self._read)
        sock.disconnected.connect(self._closed)
        QTimer.singleShot(READ_TIMEOUT_MS, self, self._expire)
        if sock.bytesAvailable():
            self._read()

    def _read(self) -> None:
        if self._done:
            return
        self._data += bytes(self.sock.readAll().data())
        line, newline, _rest = bytes(self._data).partition(b"\n")
        if not newline:
            if len(self._data) > MAX_LINE:
                self._finish(None, f"linha acima de {MAX_LINE} bytes")
            return
        try:
            target = parse_request(line.removesuffix(b"\r"))
        except ValueError as e:
            self._finish(None, str(e))
            return
        self._finish(self._answer(target), "")

    def _finish(self, reply: str | None, reason: str) -> None:
        self._done = True
        if reply is None:
            log.info("pedido ignorado: %s", reason)
            self.sock.abort()
            return
        # quem fecha é o cliente, depois de ler: no Windows, fechar aqui descarta o que ele ainda não leu (o `DisconnectNamedPipe`
        # do Qt), e a resposta se perdia às vezes; a conexão que ele não fechar sai no mesmo prazo da linha
        self.sock.write(reply.encode("utf-8") + b"\n")
        self.sock.flush()
        QTimer.singleShot(READ_TIMEOUT_MS, self, self.sock.abort)

    def _expire(self) -> None:
        if not self._done:
            self._finish(None, f"sem a linha inteira em {READ_TIMEOUT_MS} ms")

    def _closed(self) -> None:
        if not self._done:
            self._done = True
            log.info("pedido ignorado: a conexão fechou sem a linha inteira")
        self.deleteLater()
