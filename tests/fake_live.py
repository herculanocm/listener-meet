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

from listener.live.proc import NamesUnavailableError, Segment, Transcript
from listener.live.vad import FRAME
from listener.store import Store
from tests.fake_pipeline import ToneEmbedder


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
    def __init__(self, model: str, counter: Iterator[int], delay_s: float = 0.0, texts: list[str] | None = None,
                 before: Callable[[int], None] | None = None):
        self.model = model
        self.name = f"fake:{model}"
        self.counter = counter
        self.delay_s = delay_s
        self.texts = texts                      # roteiro opcional (alucinação, vazio...), consumido em ordem
        self.before = before                    # chamado com o número da chamada (1, 2...) antes de transcrever
        self.calls: list[tuple[int, str | None, str]] = []

    def transcribe(self, samples: np.ndarray, *, language: str | None, prompt: str) -> Transcript:
        self.calls.append((len(samples), language, prompt))
        if self.before is not None:
            self.before(len(self.calls))
        if self.delay_s:
            time.sleep(self.delay_s)
        n = next(self.counter)
        text = self.texts.pop(0) if self.texts else f"fala ao vivo {n}"
        return Transcript((Segment(text, 0.01, -0.2, 1.1),), "pt", 0.99)


class FakeEmbedder(ToneEmbedder):
    """O `ToneEmbedder` da F2 (one-hot suavizado na frequência dominante: 220 Hz no app, 440 Hz no microfone) com a carga
    do contrato do ao vivo; `fail_load` simula o torch que não carrega."""

    def __init__(self, fail_load: str = "", load_event: threading.Event | None = None):
        super().__init__()
        self.fail_load = fail_load
        self.load_event = load_event              # a carga espera este evento (testa legendas antes dos nomes)
        self.loaded = False

    def load(self) -> None:
        if self.load_event is not None:
            self.load_event.wait(5.0)
        if self.fail_load:
            raise RuntimeError(self.fail_load)
        self.loaded = True


class FakeEngines:
    """Os motores do filho no modo fake; guarda o que carregou (modelo, threads) e os transcritores criados."""
    name = "fake"

    def __init__(self, delay_s: float = 0.0, texts: list[str] | None = None, names_error: str = "", fail_load: str = "",
                 load_event: threading.Event | None = None, before: Callable[[int], None] | None = None):
        self.delay_s = delay_s
        self.texts = texts
        self.before = before
        self.names_error = names_error            # `NamesUnavailableError` (sem token, sem modelos)
        self.fail_load = fail_load
        self.load_event = load_event
        self.counter = itertools.count(1)
        self.loads: list[tuple[str, int]] = []
        self.transcribers: list[FakeTranscriber] = []
        self.embedders: list[FakeEmbedder] = []

    def vad(self) -> EnergyVad:
        return EnergyVad()

    def transcriber(self, model: str, threads: int) -> FakeTranscriber:
        self.loads.append((model, threads))
        t = FakeTranscriber(model, self.counter, self.delay_s, self.texts, self.before)
        self.transcribers.append(t)
        return t

    def embedder(self, store: Store, threads: int) -> FakeEmbedder:
        if self.names_error:
            raise NamesUnavailableError(self.names_error)
        e = FakeEmbedder(self.fail_load, self.load_event)
        self.embedders.append(e)
        return e


def tone(seconds: float, freq: float = 220.0, amplitude: float = 0.3, rate: int = 16000, phase: int = 0) -> np.ndarray:
    n = np.arange(phase, phase + round(seconds * rate))
    return (amplitude * np.sin(2 * np.pi * freq * n / rate)).astype(np.float32)


def session_input(hello: Any, plan: list[tuple[str, np.ndarray | int, int]], block: int = 1600, dropped: int = 4) -> bytes:
    """O stdin de uma sessão: `hello`, depois `plan` = [(canal, áudio ou quadros de silêncio, flags)] em sequência (cada
    canal com a própria linha do tempo, a 16 kHz) e o `stats` do app antes do EOF."""
    from listener.live import protocol as pr

    parts = [pr.encode_control(hello.to_msg())]
    pos = {"L": 0, "R": 0}
    for ch, data, flags in plan:
        if isinstance(data, int):
            parts.append(pr.encode_audio(ch, pos[ch], 16000, data, flags))
            pos[ch] += data
            continue
        for i in range(0, len(data), block):
            b = data[i:i + block]
            parts.append(pr.encode_audio(ch, pos[ch], 16000, len(b), 0, np.rint(b * 32767).astype("<i2").tobytes()))
            pos[ch] += len(b)
    parts.append(pr.encode_control({"type": pr.STATS, "dropped": dropped}))
    return b"".join(parts)


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
