"""Lado do app do ao vivo (F4): `LiveClient` lança o filho (`Platform.spawn_child`), recebe o desvio da captura e conversa
pelo protocolo; mais a pasta dos modelos e a medida de CPU. Sem Qt e sem ML (regra estática de importações: nada de
torch, faster_whisper, pyannote nem onnxruntime aqui).

Threads: o desvio (`tap`) roda na thread de trabalho do gravador e só faz um append por referência num deque por canal,
limitado a ~30 s de blocos (cheio, sai o mais antigo e o descarte é contado); `live-feed` converte para int16, escreve
no stdin do filho e trata o pipe quebrado; `live-read` lê as linhas do stdout e chama `on_message` (o controlador o
transforma em sinal do Qt com conexão enfileirada); `close()` não bloqueia: EOF no stdin depois do que estava no deque e
uma thread espera o prazo e mata no fim; `terminate()` é síncrono e curto (Cancelar). Ao terminar, `on_message` recebe
`{"type": "exit", "code", "expected", "bye"}` (`expected` = parada pedida pelo app; senão é queda).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np

from ..capture.audio import to_int16
from ..config.defaults import LIVE_MODELS
from ..log import get_logger
from ..platform.base import ChildProcess
from ..platform.common import paths
from .protocol import BYE, CPU, GAP, SILENCED, STATS, Hello, decode_line, encode_audio, encode_control

log = get_logger("live.client")

DEGRADE_MODEL = "base"            # F4 item 9: a degradação troca para este
EXIT = "exit"                     # mensagem do próprio cliente: o filho terminou
MAX_BLOCKS = 300                  # por canal: ~30 s com os blocos de ~100 ms da captura
FEED_POLL_S = 0.02
BATCH_FRAMES = 64
CLOSE_GRACE_S = 10.0              # Parar: o filho fecha a janela em curso e sai em até 10 s; depois, kill
EXIT_WAIT_S = 5.0

type Spawn = Callable[[list[str], Mapping[str, str] | None], ChildProcess]
type OnMessage = Callable[[dict[str, Any]], None]


def is_fake() -> bool:
    """`LISTENER_LIVE=fake`: VAD por energia, transcritor e embedder determinísticos (`tests/fake_live.py`), sem modelos."""
    return os.environ.get("LISTENER_LIVE", "").strip().lower() == "fake"


def live_models_dir(models_dir_setting: str = "") -> Path:
    """A mesma pasta de modelos da F2 (`pipeline.models_dir`; vazio = `<dados do sistema>/models`), sem importar
    `pipeline.models` (que o app não carrega)."""
    return Path(models_dir_setting).expanduser() if models_dir_setting else paths.data_dir_suggestion() / "models"


def whisper_model_dir(models_dir: Path, model: str) -> Path:
    return models_dir / f"faster-whisper-{model}"


def required_models(model: str) -> tuple[str, ...]:
    """O modelo escolhido mais o da degradação (`--prepare` baixa os dois)."""
    return tuple(dict.fromkeys((model, DEGRADE_MODEL)))


def models_ready(models_dir: Path, model: str = LIVE_MODELS[0]) -> bool:
    """Os modelos do ao vivo estão na pasta (`model.bin` de cada um); no modo fake, sempre."""
    if is_fake():
        return True
    return all((whisper_model_dir(models_dir, m) / "model.bin").exists() for m in required_models(model))


class CpuMeter:
    """CPU do próprio processo em % da máquina inteira: 100 * Δ(user + sys) / (Δ parede * núcleos), por `os.times()`
    (psutil é lento com o EDR). A primeira amostra só marca o início (None)."""

    def __init__(self, times: Callable[[], Any] = os.times, clock: Callable[[], float] = time.monotonic, cpus: int | None = None):
        self.times = times
        self.clock = clock
        self.cpus = max(1, int(cpus or os.cpu_count() or 1))
        self._last: tuple[float, float] | None = None

    def sample(self) -> float | None:
        t = self.times()
        cpu, now = float(t.user) + float(t.system), self.clock()
        last, self._last = self._last, (cpu, now)
        if last is None or now <= last[1]:
            return None
        return round(100.0 * max(0.0, cpu - last[0]) / ((now - last[1]) * self.cpus), 1)


class LiveClient:
    def __init__(self, spawn: Spawn, argv: list[str], env: Mapping[str, str] | None, hello: Hello, on_message: OnMessage, *,
                 meeting_id: int | None = None, max_blocks: int = MAX_BLOCKS, poll_s: float = FEED_POLL_S, cpu: CpuMeter | None = None):
        self._spawn = spawn
        self.argv = list(argv)
        self.env = env
        self.hello = hello
        self.on_message = on_message
        self.meeting_id = meeting_id
        self.poll_s = poll_s
        self._queues: dict[str, deque[tuple[int, int, np.ndarray, int]]] = {"L": deque(maxlen=max_blocks), "R": deque(maxlen=max_blocks)}
        self.dropped = 0                 # blocos descartados com o deque cheio (o filho atrasou ou travou)
        self.proc: ChildProcess | None = None
        self.close_requested = False
        self.dead = False                # pipe quebrado ou filho encerrado: o desvio vira no-op
        self.saw_bye = False
        self.cpu = cpu or CpuMeter()
        self._closing = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        proc = self._spawn(self.argv, self.env)
        self.proc = proc
        if proc.stdin is None or proc.stdout is None:
            self._kill()
            raise RuntimeError("o processo ao vivo nasceu sem pipes")
        self.cpu.sample()                                           # marco inicial da CPU do app
        for target, name in ((self._feed, "live-feed"), (self._read, "live-read")):
            t = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(t)
            t.start()
        log.info("ao vivo lançado (pid %s): %s", proc.pid, " ".join(self.argv))

    # ---------------------------------------------------------------- desvio (thread de trabalho do gravador)
    def tap(self, channel: str, start_frame: int, rate: int, block: np.ndarray, flags: int) -> None:
        """O(1): só um append por referência; com o deque cheio sai o bloco mais antigo (contado em `dropped`)."""
        if self.dead or self.close_requested:
            return
        q = self._queues.get(channel)
        if q is None:
            return
        if len(q) == q.maxlen:
            self.dropped += 1
        q.append((int(start_frame), int(rate), block, int(flags)))

    # ---------------------------------------------------------------- `live-feed`
    def _feed(self) -> None:
        proc = self.proc
        stdin = proc.stdin if proc is not None else None
        if stdin is None:
            return
        try:
            stdin.write(encode_control(self.hello.to_msg()))
            stdin.flush()
            while not self.dead:                                    # filho morto: nada mais a escrever
                batch = self._take_batch()
                if batch:
                    stdin.write(b"".join(batch))
                    stdin.flush()
                    continue
                if self._closing.is_set():
                    break
                self._closing.wait(self.poll_s)
            if not self.dead:
                stdin.write(encode_control({"type": STATS, "dropped": self.dropped}))
                stdin.flush()
        except (OSError, ValueError) as e:                          # BrokenPipeError é OSError; arquivo fechado é ValueError
            self.dead = True
            if not self.close_requested:
                log.warning("pipe do ao vivo quebrou (%s): o filho deve ter caído", e)
        finally:
            with suppress(Exception):
                stdin.close()                                       # EOF: o filho fecha a janela em curso e sai

    def _take_batch(self) -> list[bytes]:
        """Os blocos pendentes em ordem de tempo entre os dois canais (até `BATCH_FRAMES` por escrita)."""
        out: list[bytes] = []
        ql, qr = self._queues["L"], self._queues["R"]
        while len(out) < BATCH_FRAMES:
            hl = ql[0] if ql else None                              # o desvio só acrescenta: espiar não esvazia
            hr = qr[0] if qr else None
            if hl is None and hr is None:
                break
            if hr is None or (hl is not None and hl[0] / hl[1] <= hr[0] / hr[1]):
                channel, q = "L", ql
            else:
                channel, q = "R", qr
            try:
                item = q.popleft()                                  # o que sair (o desvio pode ter descartado o espiado)
            except IndexError:
                continue
            out.append(self._encode(channel, item))
        return out

    @staticmethod
    def _encode(channel: str, item: tuple[int, int, np.ndarray, int]) -> bytes:
        start, rate, block, flags = item
        n = len(block)
        if flags & (SILENCED | GAP):
            return encode_audio(channel, start, rate, n, flags)    # só a duração: pausa, mudo e buraco não viram zeros no pipe
        return encode_audio(channel, start, rate, n, flags, to_int16(block).astype("<i2", copy=False).tobytes())

    # ---------------------------------------------------------------- `live-read`
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
                except ValueError:
                    log.debug("linha ilegível do ao vivo: %r", line[:200])
                    continue
                if msg["type"] == BYE:
                    self.saw_bye = True
                elif msg["type"] == CPU:
                    msg = {**msg, "app_pct": self.cpu.sample()}
                self._dispatch(msg)
        except (OSError, ValueError) as e:
            log.debug("leitura do ao vivo terminou: %s", e)
        code = self._wait_exit()
        self.dead = True
        log.log(logging.INFO if self.close_requested else logging.WARNING, "ao vivo terminou (código %s, %s)", code,
                "parada pedida" if self.close_requested else "queda inesperada")
        self._dispatch({"type": EXIT, "code": code, "expected": self.close_requested, "bye": self.saw_bye})

    def _wait_exit(self) -> int | None:
        proc = self.proc
        if proc is None:
            return None
        try:
            return proc.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            log.warning("o ao vivo fechou a saída mas não saiu: matando")
            self._kill()
        try:
            return proc.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            return None

    def _dispatch(self, msg: dict[str, Any]) -> None:
        try:
            self.on_message(msg)
        except Exception:
            log.exception("mensagem do ao vivo não tratada: %s", msg.get("type"))

    # ---------------------------------------------------------------- fim
    def close(self, grace_s: float = CLOSE_GRACE_S) -> None:
        """Parar sem bloquear a interface: EOF no stdin depois do que está no deque; uma thread espera até `grace_s` e mata."""
        if self.close_requested or self.proc is None:
            return
        self.close_requested = True
        self._closing.set()
        t = threading.Thread(target=self._reap, args=(grace_s,), name="live-close", daemon=True)
        self._threads.append(t)
        t.start()

    def _reap(self, grace_s: float) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            log.warning("o ao vivo não saiu em %.0f s depois do fim da gravação: matando", grace_s)
            self._kill()

    def terminate(self, timeout_s: float = 2.0) -> None:
        """Cancelar: mata já e espera pouco, para o filho não escrever numa reunião que vai ser apagada."""
        proc = self.proc
        if proc is None:
            return
        self.close_requested = True
        self._closing.set()
        self._kill()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("o ao vivo (pid %s) não morreu em %.0f s", proc.pid, timeout_s)

    def _kill(self) -> None:
        if self.proc is not None:
            with suppress(OSError):
                self.proc.kill()

    def join(self, timeout: float = 5.0) -> None:
        """Testes e encerramento: espera as threads do cliente terminarem."""
        deadline = time.monotonic() + timeout
        for t in list(self._threads):
            t.join(max(0.0, deadline - time.monotonic()))
