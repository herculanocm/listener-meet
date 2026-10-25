"""Ao vivo simulado (F4): `LISTENER_LIVE=fake` faz o filho real (`python -m listener --live`) usar estes motores, sem ML:
VAD por energia, transcritor determinístico ("fala ao vivo N") e embedder determinístico pelo tom dominante (o mesmo
esquema do `ToneEmbedder` da F2). `ThreadChild` roda o filho de verdade (`proc.serve`) numa thread com pipes do sistema
nos dois sentidos: o cliente e o controlador são testados sem subprocesso."""
from __future__ import annotations

import itertools
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from listener.live.proc import Segment, Transcript
from listener.live.vad import FRAME
from listener.store import Store


class EnergyVad:
    """Quadro de 512 amostras com RMS acima do limiar é fala (1,0); senão 0,0. Mesmo contrato do `SileroStream`."""

    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold
        self._rest = np.zeros(0, np.float32)
        self.resets = 0

    @property
    def pending(self) -> int:
        return len(self._rest)

    def reset(self) -> None:
        self._rest = np.zeros(0, np.float32)
        self.resets += 1

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.concatenate([self._rest, np.asarray(x, dtype=np.float32).reshape(-1)])
        n = (len(x) // FRAME) * FRAME
        self._rest = x[n:].copy()
        if n == 0:
            return np.zeros(0, np.float32)
        frames = x[:n].reshape(-1, FRAME)
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        return (rms > self.threshold).astype(np.float32)


class FakeTranscriber:
    def __init__(self, model: str, counter: Iterator[int], delay_s: float = 0.0, texts: list[str] | None = None):
        self.model = model
        self.name = f"fake:{model}"
        self.counter = counter
        self.delay_s = delay_s
        self.texts = texts                      # roteiro opcional (alucinação, vazio...), consumido em ordem
        self.calls: list[tuple[int, str | None, str]] = []

    def transcribe(self, samples: np.ndarray, *, language: str | None, prompt: str) -> Transcript:
        self.calls.append((len(samples), language, prompt))
        if self.delay_s:
            time.sleep(self.delay_s)
        n = next(self.counter)
        text = self.texts.pop(0) if self.texts else f"fala ao vivo {n}"
        return Transcript((Segment(text, 0.01, -0.2, 1.1),), "pt", 0.99)


class FakeEngines:
    """Os motores do filho no modo fake; guarda o que carregou (modelo, threads) e os transcritores criados."""
    name = "fake"

    def __init__(self, delay_s: float = 0.0, texts: list[str] | None = None):
        self.delay_s = delay_s
        self.texts = texts
        self.counter = itertools.count(1)
        self.loads: list[tuple[str, int]] = []
        self.transcribers: list[FakeTranscriber] = []

    def vad(self) -> EnergyVad:
        return EnergyVad()

    def transcriber(self, model: str, threads: int) -> FakeTranscriber:
        self.loads.append((model, threads))
        t = FakeTranscriber(model, self.counter, self.delay_s, self.texts)
        self.transcribers.append(t)
        return t


def tone(seconds: float, freq: float = 220.0, amplitude: float = 0.3, rate: int = 16000, phase: int = 0) -> np.ndarray:
    n = np.arange(phase, phase + round(seconds * rate))
    return (amplitude * np.sin(2 * np.pi * freq * n / rate)).astype(np.float32)


class ThreadChild:
    """`subprocess.Popen` de mentira para `spawn_child`: o filho real numa thread, com `os.pipe()` nos dois sentidos.
    `kill()` fecha o stdin (o filho fake termina em milissegundos no EOF); `wait()` espera a thread."""

    def __init__(self, target: Callable[[BinaryIO, BinaryIO], int], pid: int = 9000, argv: list[str] | None = None):
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()
        self.stdin = os.fdopen(w_in, "wb")
        self.stdout = os.fdopen(r_out, "rb")
        self._child_in = os.fdopen(r_in, "rb")
        self._child_out = os.fdopen(w_out, "wb")
        self.pid = pid
        self.argv = list(argv or [])
        self.returncode: int | None = None
        self.killed = False
        self._thread = threading.Thread(target=self._run, args=(target,), name=f"fake-live-{pid}", daemon=True)
        self._thread.start()

    def _run(self, target: Callable[[BinaryIO, BinaryIO], int]) -> None:
        try:
            self.returncode = target(self._child_in, self._child_out)
        except BaseException:
            self.returncode = 1
            raise
        finally:
            for f in (self._child_out, self._child_in):
                with suppress(Exception):
                    f.close()

    def poll(self) -> int | None:
        return None if self._thread.is_alive() else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired(self.argv or ["fake-live"], timeout or 0.0)
        return self.returncode if self.returncode is not None else 1

    def kill(self) -> None:
        self.killed = True
        with suppress(Exception):
            self.stdin.close()

    def terminate(self) -> None:
        self.kill()


def serve_target(db: Path, meeting_id: int, engines: Any, **kw: Any) -> Callable[[BinaryIO, BinaryIO], int]:
    """O alvo do `ThreadChild`: abre o banco no papel WORKER na thread do filho, como o processo de verdade."""
    from listener.live import proc

    def run(stdin: BinaryIO, stdout: BinaryIO) -> int:
        with Store.open_worker(db) as store:
            return proc.serve(stdin, stdout, store, meeting_id, engines, **kw)
    return run


def thread_spawner(db: Path, engines: Any, children: list[ThreadChild], **kw: Any) -> Callable[..., ThreadChild]:
    """`Platform.spawn_child` de teste: lê `--meeting` do argv e liga um `ThreadChild` ao filho real com `engines`."""
    def spawn(argv: list[str], env: Any = None) -> ThreadChild:
        meeting_id = int(argv[argv.index("--meeting") + 1])
        child = ThreadChild(serve_target(db, meeting_id, engines, **kw), pid=9000 + len(children), argv=argv)
        children.append(child)
        return child
    return spawn
