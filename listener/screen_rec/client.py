"""F5.27 (lote 2): o protocolo do filho `--screen` e o lado do app que o conduz (`ScreenClient`), sem Qt e sem o PyAV (o app
importa este módulo; o filho, `proc.py`, importa daqui as mensagens, como o ao vivo).

Protocolo, uma linha JSON UTF-8 por mensagem nos dois sentidos (o JSON escapa as quebras de linha):
- Ida (stdin do filho): `hello` primeiro (`ScreenHello`: o `t0_perf` da sessão, a chave do monitor, a taxa, o lado máximo, a
  pausa da reunião e o tamanho do pedaço); depois `pause`, `resume`, `switch` (`monitor_key`), `params` (`fps`, `max_side`,
  valem no próximo pedaço) e `stop`. EOF no stdin = `stop`: o filho fecha o último pedaço, grava e sai com 0.
- Volta (stdout do filho): `started` (pid, monitor, tamanho, `t_ms`), `status` (`state` = "falhando" ou "ok"), `lost`
  (monitor, `t_ms`), `back` e `switched` (monitor, tamanho, `t_ms`), `chunk` (idx, tempos, quadros, bytes), `stats` (o
  `RecorderStats.summary()` depois de cada pedaço), `error` (`message`, `retry`) e `bye` (as estatísticas finais, os pedaços
  gravados e os não gravados, `t_ms`). O monitor vai pelo número ("Monitor 2" = 2), nunca pela chave do sistema.

`ScreenClient`: lança o filho (`Platform.spawn_child`); `screen-feed` escreve os comandos de uma fila (a interface nunca bloqueia
num pipe cheio); `screen-read` lê as linhas e chama `on_message` (o host as transforma em sinal do Qt); `close()` não bloqueia
(EOF depois dos comandos na fila; uma thread espera o prazo e mata no fim); `terminate()` mata já e espera pouco; `wait_closed`
espera o fim do processo (a parada da reunião, a saída do app). Ao terminar, `on_message` recebe `{"type": "exit", "code",
"expected", "bye"}` (`expected` = parada pedida pelo app; senão é queda)."""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Self

from ..log import argv_text, get_logger
from ..platform.base import ChildProcess

log = get_logger("screen_rec.client")

PROTOCOL_VERSION = 1
# ida
HELLO, PAUSE, RESUME, SWITCH, PARAMS, STOP = "hello", "pause", "resume", "switch", "params", "stop"
# volta
STARTED, STATUS, LOST, BACK, SWITCHED, CHUNK, STATS, ERROR, BYE = (
    "started", "status", "lost", "back", "switched", "chunk", "stats", "error", "bye")
EXIT = "exit"                     # mensagem do próprio cliente: o filho terminou
CLOSE_GRACE_S = 30.0              # depois do EOF o filho grava o último pedaço (com novas tentativas até 20 s) e sai; depois, kill
EXIT_WAIT_S = 5.0

type Spawn = Callable[[list[str], Mapping[str, str] | None], ChildProcess]
type OnMessage = Callable[[dict[str, Any]], None]


class ProtocolError(ValueError):
    """Linha que não é um objeto JSON com `type`, ou `hello` inválido."""


def encode_line(msg: dict[str, Any]) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def decode_line(line: bytes) -> dict[str, Any]:
    """Uma linha do protocolo; `ProtocolError` quando não é um objeto JSON com `type`."""
    try:
        msg = json.loads(line.decode("utf-8"))
    except ValueError as e:
        raise ProtocolError(f"linha ilegível: {e}") from e
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("linha sem objeto JSON com 'type'")
    return msg


@dataclass(frozen=True, slots=True)
class ScreenHello:
    """O primeiro comando: o relógio da sessão (`perf_counter` do início, comum aos processos no Windows) e a gravação."""
    t0_perf: float
    monitor_key: str
    fps: int = 1
    max_side: int = 1920
    paused: bool = False           # a reunião já pausada (o relançamento depois de uma queda, a tela começada na pausa)
    chunk_s: int = 60

    def to_msg(self) -> dict[str, Any]:
        return {"type": HELLO, "v": PROTOCOL_VERSION, "t0_perf": float(self.t0_perf), "monitor_key": str(self.monitor_key),
                "fps": int(self.fps), "max_side": int(self.max_side), "paused": bool(self.paused), "chunk_s": int(self.chunk_s)}

    @classmethod
    def from_msg(cls, msg: dict[str, Any]) -> Self:
        if msg.get("type") != HELLO:
            raise ProtocolError(f"esperava hello, veio {msg.get('type')!r}")
        if msg.get("v") != PROTOCOL_VERSION:            # app e filho de versões diferentes (código trocado com o app aberto)
            raise ProtocolError(f"protocolo {msg.get('v')!r} do app, o filho da tela fala o {PROTOCOL_VERSION}: reinicie o app")
        try:
            hello = cls(t0_perf=float(msg["t0_perf"]), monitor_key=str(msg["monitor_key"]), fps=int(msg.get("fps", 1)),
                        max_side=int(msg.get("max_side", 1920)), paused=bool(msg.get("paused", False)),
                        chunk_s=int(msg.get("chunk_s", 60)))
        except (KeyError, TypeError, ValueError) as e:
            raise ProtocolError(f"hello inválido: {e}") from e
        if not hello.monitor_key or hello.chunk_s < 1:
            raise ProtocolError("hello inválido: sem monitor ou pedaço menor que 1 s")
        return hello


class ScreenClient:
    def __init__(self, spawn: Spawn, argv: list[str], env: Mapping[str, str] | None, hello: ScreenHello, on_message: OnMessage, *,
                 meeting_id: int):
        self._spawn = spawn
        self.argv = list(argv)
        self.env = env
        self.hello = hello
        self.on_message = on_message
        self.meeting_id = meeting_id
        self.proc: ChildProcess | None = None
        self.close_requested = False
        self.dead = False                 # pipe quebrado ou filho encerrado: os comandos viram no-op
        self.bye: dict[str, Any] | None = None
        self.exit_code: int | None = None
        self._commands: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._exited = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def exited(self) -> bool:
        return self._exited.is_set()

    def start(self) -> None:
        proc = self._spawn(self.argv, self.env)
        self.proc = proc
        if proc.stdin is None or proc.stdout is None:
            self._kill()
            raise RuntimeError("o filho da tela nasceu sem pipes")
        self._commands.put(encode_line(self.hello.to_msg()))
        for target, name in ((self._feed, "screen-feed"), (self._read, "screen-read")):
            t = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(t)
            t.start()
        log.info("filho da tela lançado (pid %s): %s", proc.pid, argv_text(self.argv))

    def send(self, msg: dict[str, Any]) -> bool:
        """Põe um comando na fila do `screen-feed`; False com o filho morto ou já fechando."""
        if self.dead or self.close_requested or self.proc is None:
            return False
        self._commands.put(encode_line(msg))
        return True

    # ---------------------------------------------------------------- `screen-feed`
    def _feed(self) -> None:
        proc = self.proc
        stdin = proc.stdin if proc is not None else None
        if stdin is None:
            return
        try:
            while True:
                data = self._commands.get()
                if data is None or self.dead:
                    break
                stdin.write(data)
                stdin.flush()
        except (OSError, ValueError) as e:                          # BrokenPipeError é OSError; arquivo fechado é ValueError
            self.dead = True
            if not self.close_requested:
                log.warning("pipe do filho da tela quebrou (%s): ele deve ter caído", type(e).__name__)
        finally:
            with suppress(Exception):
                stdin.close()                                       # EOF: o filho fecha o último pedaço e sai

    # ---------------------------------------------------------------- `screen-read`
    def _read(self) -> None:
        proc = self.proc
        stdout = proc.stdout if proc is not None else None
        if stdout is None:
            return
        try:
            while True:
                line = stdout.readline()
                if not line:
                    break
                try:
                    msg = decode_line(line)
                except ProtocolError:
                    log.debug("linha ilegível do filho da tela (%d bytes)", len(line))
                    continue
                if msg["type"] == BYE:
                    self.bye = msg
                self._dispatch(msg)
        except (OSError, ValueError) as e:
            log.debug("leitura do filho da tela terminou: %s", type(e).__name__)
        self.exit_code = self._wait_exit()
        self.dead = True
        self._commands.put(None)                                    # o `screen-feed` sai, se ainda esperava
        self._exited.set()
        log.log(logging.INFO if self.close_requested else logging.WARNING, "filho da tela terminou (código %s, %s)", self.exit_code,
                "parada pedida" if self.close_requested else "queda inesperada")
        self._dispatch({"type": EXIT, "code": self.exit_code, "expected": self.close_requested, "bye": self.bye is not None})

    def _wait_exit(self) -> int | None:
        proc = self.proc
        if proc is None:
            return None
        try:
            return proc.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            log.warning("o filho da tela fechou a saída mas não saiu: matando")
            self._kill()
        try:
            return proc.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            return None

    def _dispatch(self, msg: dict[str, Any]) -> None:
        try:
            self.on_message(msg)
        except Exception:
            log.exception("mensagem do filho da tela não tratada: %s", msg.get("type"))

    # ---------------------------------------------------------------- fim
    def close(self, grace_s: float = CLOSE_GRACE_S) -> None:
        """Parar sem bloquear: EOF depois dos comandos na fila; uma thread espera até `grace_s` e mata."""
        if self.close_requested or self.proc is None:
            return
        self.close_requested = True
        self._commands.put(None)
        t = threading.Thread(target=self._reap, args=(grace_s,), name="screen-close", daemon=True)
        self._threads.append(t)
        t.start()

    def _reap(self, grace_s: float) -> None:
        if not self._exited.wait(grace_s):
            log.warning("o filho da tela não saiu em %.0f s depois do pedido de parada: matando", grace_s)
            self._kill()

    def terminate(self, timeout_s: float = 2.0) -> None:
        """Cancelar e a saída que passou do prazo: mata já e espera pouco."""
        proc = self.proc
        if proc is None:
            return
        self.close_requested = True
        self._commands.put(None)
        self._kill()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("o filho da tela (pid %s) não morreu em %.0f s", proc.pid, timeout_s)

    def wait_closed(self, timeout_s: float) -> bool:
        """Espera o fim do processo (o `exit` já calculado pela `screen-read`); True se terminou no prazo. Qualquer thread."""
        return self.proc is None or self._exited.wait(max(0.0, timeout_s))

    def _kill(self) -> None:
        if self.proc is not None:
            with suppress(OSError):
                self.proc.kill()


__all__ = ["BACK", "BYE", "CHUNK", "CLOSE_GRACE_S", "ERROR", "EXIT", "HELLO", "LOST", "PARAMS", "PAUSE", "PROTOCOL_VERSION", "RESUME",
           "STARTED", "STATS", "STATUS", "STOP", "SWITCH", "SWITCHED", "ProtocolError", "ScreenClient", "ScreenHello", "decode_line",
           "encode_line"]
