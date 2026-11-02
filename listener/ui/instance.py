"""F5.13 (L1, U-129, U-130): uma instância só. Em `ui/app.py:run`, logo depois do `QApplication`, antes do tema, do banco e
do assistente, `claim` pede a trava à plataforma (`claim_instance`; no Windows, um mutex da sessão) com o nome do canal:
"listener-meet-" e os 12 primeiros hex do sha256 da pasta de configuração (o nome do usuário que ela traz nunca vai no
nome). Quem pega abre o canal, um `QLocalServer` só do usuário (`UserAccessOption`; o `removeServer` antes do `listen` limpa
o arquivo de uma queda fora do Windows, onde ele faz algo: isso só é seguro com trava de verdade, e nas outras plataformas,
até a F7, a base sempre pega a trava e ele apagaria o soquete de um app aberto), e segue; quem não pega passa o foco
adiante (`hand_over_foreground`), entrega o pedido e sai com 0, sem abrir um segundo app.

Protocolo: uma linha JSON de até 256 bytes, `{"open": X}` com X em `OpenTarget`; a resposta, "ok" ou "fechando" (o app
saindo). Qualquer outra coisa é ignorada com uma linha de log e sem resposta. O app fecha o canal no começo da saída (R-01:
o "Sair" confirmado e a parada de emergência, pelo `Controller.leaving`). O cliente bloqueia até 5 s no total: sem canal,
tenta a trava de novo a cada volta, e se o app já saiu ele a pega e vira o app, com o pedido dele; com a trava presa sem
canal até o prazo (o app saindo) ou com "fechando", a mensagem do passo 3 da spec; conectou e ninguém respondeu (o app
travado, ou ainda abrindo), a do passo 4.

Até o controlador existir (o assistente, o banco que não abre), o pedido só traz à frente o diálogo modal aberto. Depois
(`attach`): com o app saindo, "fechando"; com um diálogo modal aberto, só ele vem à frente; senão a ação sai no próximo giro
do laço (as Configurações e o "Antes de gravar" seguram o laço no `exec`, e a resposta não espera por eles): "history",
"queue" e "settings" pelo caminho do clique no aviso (`Controller.open_target`, que traz a janela à frente pela
plataforma) e "record" pelo do clique esquerdo na bandeja (`TrayIcon.left_click`, com a reunião pendente da F5.7), com a
janela que ele abrir trazida à frente do mesmo jeito (R-02). Log sem caminho e sem nome (D-053): o alvo, o motivo da recusa
e o tempo."""
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
REPLIES = frozenset({REPLY_OK, REPLY_CLOSING})
CLAIMED, LOCKED = "trava livre", "trava presa"   # o desfecho da espera sem canal (nunca uma resposta: só as de `REPLIES` valem)
CLIENT_TIMEOUT_S = 5.0                 # o processo novo desiste depois disto (os passos 3 e 4 da spec)
RETRY_S = 0.05                         # entre as tentativas, enquanto não há canal (o app abrindo ou saindo)
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
    """A mensagem do processo novo: nenhuma com "ok" (ou com a trava pega na espera); a do passo 3 com "fechando" ou com a
    trava presa sem canal até o prazo (o app saindo); a do passo 4 com o resto (conectou e ninguém respondeu)."""
    if reply in (REPLY_OK, CLAIMED):
        return None
    return CLOSING_TEXT if reply in (REPLY_CLOSING, LOCKED) else NO_REPLY_TEXT


# ---------------------------------------------------------------- o processo novo
def _left_ms(deadline: float) -> int:
    return max(0, int((deadline - time.monotonic()) * 1000))


def _reply(sock: QLocalSocket, deadline: float) -> str | None:
    data = bytearray()
    while b"\n" not in data and len(data) <= MAX_LINE:
        if not sock.bytesAvailable() and not sock.waitForReadyRead(_left_ms(deadline)):
            data += bytes(sock.readAll().data())         # o que chegou junto com o fim da conexão
            break
        data += bytes(sock.readAll().data())
    line, newline, _rest = bytes(data).partition(b"\n")
    return line.decode("utf-8", "replace").strip() if newline else None


def _ask(sock: QLocalSocket, target: OpenTarget, deadline: float) -> str | None:
    """A linha do pedido e a resposta dela; None quando a conexão caiu antes ou o prazo venceu."""
    sock.write(request_line(target))
    if not sock.waitForBytesWritten(_left_ms(deadline)):
        return None
    reply = _reply(sock, deadline)
    return reply if reply in REPLIES else None           # fora do protocolo vale como nenhuma


def _exchange(platform: Platform, name: str, target: OpenTarget, deadline: float) -> str | None:
    """Até o prazo, conecta e pede; sem canal (ou com a conexão caída sem resposta), tenta a trava de novo, porque o app pode
    ter saído. Devolve a resposta, `CLAIMED` (a trava ficou livre: este processo vira o app), `LOCKED` (a trava seguiu presa
    sem canal: o app saindo) ou None (conectou e ninguém respondeu no prazo: o app travado)."""
    while True:
        sock = QLocalSocket()
        sock.connectToServer(name)
        connected = sock.waitForConnected(_left_ms(deadline))
        try:
            reply = _ask(sock, target, deadline) if connected else None
            if reply is not None:
                return reply
            if connected and sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                return None
        finally:
            sock.abort()
        if platform.claim_instance(name):                # o handle de cada tentativa perdida fecha na plataforma
            return CLAIMED
        if time.monotonic() + RETRY_S >= deadline:
            return None if connected else LOCKED
        time.sleep(RETRY_S)


def deliver(platform: Platform, name: str, target: OpenTarget) -> str | None:
    """O processo que não pegou a trava: passa o foco adiante e entrega o pedido; devolve o desfecho de `_exchange`."""
    platform.hand_over_foreground()
    t0 = time.perf_counter()
    outcome = _exchange(platform, name, target, time.monotonic() + CLIENT_TIMEOUT_S)
    ms = (time.perf_counter() - t0) * 1000
    if outcome == REPLY_OK:
        log.info("outra instância já roda: pedido entregue (%s) em %.0f ms", target, ms)
    elif outcome == CLAIMED:
        log.info("a outra instância saiu na espera: este processo abre o app (%s), depois de %.0f ms", target, ms)
    elif outcome in (REPLY_CLOSING, LOCKED):
        log.info("outra instância já roda e está fechando: pedido não entregue (%s)", target)
    else:
        log.warning("outra instância já roda e não respondeu em %.0f ms: pedido não entregue (%s)", ms, target)
    return outcome


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
    """U-129: a trava e o canal aberto (quem pega segue com eles, também se pegou na espera, R-01) ou o pedido entregue ao app
    que já roda (None: este processo sai com 0). Sem `--open`, o pedido é o Histórico (U-130: o clique no ícone fixado)."""
    name = channel_name(platform.config_dir())
    if not platform.claim_instance(name):
        outcome = deliver(platform, name, target or OpenTarget.HISTORY)
        if outcome != CLAIMED:
            if (text := refusal_text(outcome)) is not None:
                show_refusal(app, platform, text)
            return None
    server = InstanceServer(name, platform, parent=app)
    server.listen()
    return server


# ---------------------------------------------------------------- o app que tem a trava
def bring_forward(platform: Platform, win: QWidget) -> None:
    """A janela à frente, também pela plataforma (o app está em segundo plano quando o pedido chega)."""
    win.raise_()
    win.activateWindow()
    with suppress(Exception):
        platform.bring_to_front(int(win.winId()))


def _front_opened(controller: Controller) -> None:
    """R-02 (o passo 5 da spec): o que o "record" abriu vem à frente, como as outras telas: o diálogo modal (o "Antes de
    gravar", também o da reunião pendente da F5.7) ou, gravando, o widget. Roda dentro do `exec` do diálogo."""
    win = QApplication.activeModalWidget() or controller.widget
    if win is not None and win.isVisible():
        bring_forward(controller.platform, win)


def open_target(controller: Controller, target: OpenTarget) -> None:
    """O pedido no controlador: "history", "queue" e "settings" pelo caminho do clique no aviso (abre e traz à frente pela
    plataforma); "record" pelo do clique esquerdo na bandeja (o "Antes de gravar", o da reunião pendente da F5.7 ou o widget
    da gravação em curso), trazido à frente no primeiro giro do laço (R-02); sem bandeja, o "Antes de gravar"."""
    from .notices import NoticeAction, Target

    if controller.quitting:
        return
    if target == OpenTarget.RECORD:
        QTimer.singleShot(0, controller, functools.partial(_front_opened, controller))
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
        """Depois do `controller.start()`: os pedidos vão ao controlador (antes, só o diálogo modal aberto vem à frente), e o
        canal fecha no começo da saída (R-01: o processo novo não espera o salvar da gravação nem a parada)."""
        self._controller = controller
        controller.leaving.connect(self.close)

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
            bring_forward(self._platform, modal)
        else:
            log.info("pedido recebido: %s", target)
            self.open_later(target)
        return REPLY_OK

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if sock is not None:
                _Connection(sock, self.answer, self)


class _Connection(QObject):
    """Uma conexão: junta a linha (até 256 bytes) e responde, e quem fecha é o cliente, depois de ler; o que não é pedido
    válido sai sem resposta, com uma linha de log, e a conexão que não manda a linha inteira em 2 s é fechada."""

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
