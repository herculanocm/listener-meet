"""O "Testar" do token do Hugging Face (F5.11: U-113, A-29), nas Chaves de acesso das Configurações e no assistente.

`HfTokenTest` roda `python -m listener --hf-check [--db DIR]` num filho (`Platform.spawn_child`, o ambiente do worker), escreve
o token digitado no stdin dele (nunca na linha de comando nem no log) e lê a linha do resultado numa thread, entregue à da
interface por sinal enfileirado. Um teste por vez no app (`hf_tester()`); o prazo de 20 s, o Cancelar e a saída do app
(`live_prepare.at_shutdown`) matam o filho. Recusada (A-29): uma `Task` no processo da interface, que uma chamada presa na
verificação de certificado (D-058) prenderia, e a saída do app esperaria por ela.

`HfTestControl` liga o campo do token e o botão: "Testar" vira "Cancelar" durante o teste; o resultado vira o selo
("Conectado", "Falta aceitar os termos", "Token inválido", "Sem rede", "Sem resposta") com a mensagem de `pipeline/hf_check.py`
ao lado (o sinal `shown(selo, tom, mensagem)`; o selo vazio pede o do campo, informado ou não); mexer no token cancela o
teste em curso (o resultado seria de outro token). Sem `huggingface_hub` instalado, o botão diz que o teste não está
disponível. Sinais, não métodos do diálogo guardados aqui (R-06: o ciclo prenderia o diálogo)."""
from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..log import get_logger
from ..pipeline.hf_check import Status, describe, result
from ..platform.common import process
from .live_prepare import at_shutdown
from .theme import Tone

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLineEdit, QPushButton

    from ..platform.base import ChildProcess

log = get_logger("ui.hf_test")

type Spawn = Callable[[list[str], Mapping[str, str] | None], ChildProcess]
DEADLINE_MS = 20_000                         # comportamento 5: [chute] 20 s no total; o filho desiste aos 15
EXIT_WAIT_S = 5.0
TEST_TEXT, CANCEL_TEXT, UNAVAILABLE_TEXT = "Testar", "Cancelar", "Teste indisponível"
UNAVAILABLE_TIP = "O teste do token precisa do huggingface-hub, que vem com o processamento (pip install -e .[pipeline])."
TEST_TIP = "Confere o token digitado e o aceite dos termos dos dois modelos de voz, antes de salvar (até 20 s)."
TIMEOUT_MESSAGE = "o teste passou de 20 s sem resposta do Hugging Face; tente de novo"
TESTING = "Testando…"
BADGES: dict[str, tuple[str, Tone]] = {
    Status.OK: ("Conectado", Tone.SUCCESS), Status.GATED: ("Falta aceitar os termos", Tone.WARNING),
    Status.INVALID: ("Token inválido", Tone.DANGER), Status.NETWORK: ("Sem rede", Tone.WARNING),
    Status.OFFLINE: ("Sem rede", Tone.WARNING), Status.TIMEOUT: ("Sem resposta", Tone.WARNING),
    Status.UNAVAILABLE: (UNAVAILABLE_TEXT, Tone.NEUTRAL), Status.ERROR: ("Falhou", Tone.DANGER)}


def available() -> bool:
    """`huggingface_hub` instalado: o `find_spec` só procura, não importa."""
    return importlib.util.find_spec("huggingface_hub") is not None


def badge_of(found: Mapping[str, Any]) -> tuple[str, Tone, str]:
    """O selo, o tom e a mensagem de um resultado do filho."""
    text, value = BADGES.get(str(found.get("status") or ""), BADGES[Status.ERROR])
    return text, value, describe(found)


def _kill(proc: ChildProcess) -> None:
    with suppress(OSError):
        proc.kill()


class HfTokenTest(QObject):
    """O teste em curso (um por vez no app). `finished` leva o resultado (o `dict` de `hf_check.result`)."""
    finished = Signal(object)
    _line = Signal(object, object)          # (filho, mensagem): da thread `hf-check` para a da interface

    def __init__(self) -> None:
        super().__init__(None)
        self.proc: ChildProcess | None = None
        self.starts = 0                      # instrumentação dos testes
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEADLINE_MS)
        self._timer.timeout.connect(self._expire)
        self._line.connect(self._on_line, Qt.ConnectionType.QueuedConnection)

    @property
    def running(self) -> bool:
        return self.proc is not None

    def start(self, token: str, spawn: Spawn, db_dir: str | Path | None = None) -> bool:
        """Lança o filho com o token no stdin; False quando já há um teste ou o filho não nasceu (o `finished` diz o erro)."""
        if self.running or self._closed:
            return False
        try:
            proc = spawn(process.hf_check_command(db_dir), process.worker_env(1))
        except Exception as e:
            log.warning("teste do token: o filho não nasceu (%s)", type(e).__name__)
            self.finished.emit(result(Status.ERROR, f"não consegui iniciar o teste: {e}"))
            return False
        stdin = proc.stdin
        if stdin is None or proc.stdout is None:
            return self._refuse(proc, "sem os pipes")
        try:
            stdin.write((token.strip() + "\n").encode("utf-8"))
            stdin.flush()
            stdin.close()
        except OSError as e:
            return self._refuse(proc, type(e).__name__)
        self.proc = proc
        self.starts += 1
        log.info("teste do token do Hugging Face lançado (pid %s)", proc.pid)
        threading.Thread(target=self._read, args=(proc,), name="hf-check", daemon=True).start()
        self._timer.start()
        return True

    def _refuse(self, proc: ChildProcess, why: str) -> bool:
        _kill(proc)
        log.warning("teste do token: o filho não recebeu o token (%s)", why)
        self.finished.emit(result(Status.ERROR, "o teste não começou; tente de novo"))
        return False

    def _read(self, proc: ChildProcess) -> None:
        stdout = proc.stdout
        try:
            raw = stdout.readline() if stdout is not None else b""
            msg: Any = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except (OSError, ValueError) as e:
            log.debug("leitura do teste do token terminou: %s", type(e).__name__)
            msg = None
        if not isinstance(msg, dict):
            try:
                code: int | None = proc.wait(timeout=EXIT_WAIT_S)
            except Exception:
                _kill(proc)
                code = None
            msg = result(Status.ERROR, f"o teste saiu sem resposta (código {code})")
        if not self._closed:
            self._line.emit(proc, msg)

    def _on_line(self, proc: object, msg: object) -> None:
        if proc is self.proc and isinstance(msg, dict):
            self._finish(msg)

    def _finish(self, found: dict[str, Any]) -> None:
        proc, self.proc = self.proc, None
        self._timer.stop()
        if proc is not None:
            _kill(proc)                                   # o filho já saiu: matar de novo não faz nada
        log.info("teste do token do Hugging Face: %s", found.get("status"))
        self.finished.emit(dict(found))

    def _expire(self) -> None:
        if self.proc is not None:
            log.warning("teste do token do Hugging Face passou do prazo: o filho morre")
            self._finish(result(Status.TIMEOUT, TIMEOUT_MESSAGE))

    def cancel(self) -> bool:
        """O Cancelar (e o campo que mudou): o filho morre e nada é emitido. False sem teste em curso."""
        proc, self.proc = self.proc, None
        self._timer.stop()
        if proc is None:
            return False
        _kill(proc)
        log.info("teste do token do Hugging Face cancelado")
        return True

    def shutdown(self) -> None:
        """O app está saindo: o filho não fica órfão e a thread leitora não emite mais."""
        self._closed = True
        self.cancel()


_shared: dict[str, HfTokenTest] = {}


def hf_tester() -> HfTokenTest:
    """O teste único do app (criado na thread da interface no primeiro uso); morre com a saída do app."""
    if "t" not in _shared:
        _shared["t"] = HfTokenTest()
        at_shutdown(_shared["t"].shutdown)
    return _shared["t"]


class HfTestControl(QObject):
    """O botão "Testar" de um campo do token: `shown(selo, tom, mensagem)` leva o resultado (e o "Testando…")."""
    shown = Signal(str, object, str)

    def __init__(self, token: QLineEdit, button: QPushButton, spawn: Spawn, db_dir: str | Path | None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.token, self.button = token, button
        self.spawn, self.db_dir = spawn, db_dir
        self.testing = False
        self._token = ""
        if not available():
            button.setText(UNAVAILABLE_TEXT)
            button.setEnabled(False)
            button.setToolTip(UNAVAILABLE_TIP)
            return
        button.setText(TEST_TEXT)
        button.setToolTip(TEST_TIP)
        button.clicked.connect(self.toggle)
        token.textChanged.connect(self._token_changed)

    def toggle(self) -> None:
        if self.testing:
            self.cancel()
        else:
            self.start()

    def start(self) -> bool:
        token = self.token.text().strip()
        if not token:
            self.shown.emit("", Tone.NEUTRAL, "cole o token antes de testar")      # selo vazio: o do campo
            return False
        tester = hf_tester()
        tester.finished.connect(self._done)
        self._token, self.testing = token, True
        self.button.setText(CANCEL_TEXT)
        self.shown.emit(TESTING, Tone.INFO, "conferindo o token e os termos dos dois modelos de voz…")
        if not tester.start(token, self.spawn, self.db_dir):
            if self.testing:                             # outro teste em curso (o `finished` do erro já desligou)
                self._release()
                self.shown.emit(TESTING, Tone.INFO, "outro teste do token está em curso; espere ele terminar")
            return False
        return True

    def _done(self, found: object) -> None:
        self._release()
        if isinstance(found, dict) and self.token.text().strip() == self._token:
            self.shown.emit(*badge_of(found))

    def _release(self) -> None:
        with suppress(RuntimeError, TypeError):
            hf_tester().finished.disconnect(self._done)
        self.testing = False
        self.button.setText(TEST_TEXT)

    def cancel(self) -> None:
        """O Cancelar: o filho morre; o selo volta ao do campo."""
        if not self.testing:
            return
        hf_tester().cancel()
        self._release()
        self.shown.emit("", Tone.NEUTRAL, "")

    def _token_changed(self, _text: str) -> None:
        if self.testing:
            self.cancel()

    def close(self) -> None:
        """O diálogo fechou: o teste em curso morre com ele."""
        self.cancel()


__all__ = ["BADGES", "DEADLINE_MS", "TEST_TEXT", "UNAVAILABLE_TEXT", "HfTestControl", "HfTokenTest", "available", "badge_of",
           "hf_tester"]
