"""As peças da plataforma simulada (F5.9 L1a, de `fake_platform.py`, que estava no limite de 500 linhas): a stream dirigida
pelo relógio manual, o mudo, os atalhos e o filho ao vivo silencioso. `fake_platform.py` as reexporta."""
from __future__ import annotations

import math
import subprocess
import threading
from collections.abc import Mapping

import numpy as np

from listener.capture.clock import ManualClock
from listener.platform.base import AudioCallback, AudioDevice, ErrorCallback


class FakeStream:
    def __init__(self, clock: ManualClock, sample_rate: int, channels: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None, block_ms: int = 100, freq: float = 440.0, amplitude: float = 0.3,
                 silent_windows: list[tuple[float, float]] | None = None):
        self.clock = clock
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_audio = on_audio
        self.on_error = on_error
        self.block = int(sample_rate * block_ms / 1000)
        self.period = self.block / sample_rate
        self.freq = freq
        self.amplitude = amplitude
        self.silent_windows = silent_windows or []   # (início, fim) em segundos da sessão: o driver não entrega nada
        self.started = False
        self.stopped = False
        self.stop_calls = 0                          # U-043: quem fechou a stream (a morta também precisa ser fechada)
        self.device_id = ""                          # F5.3 (A-16): o endpoint aberto
        self.fail_start = False                      # o driver recusa o `start()` (revisão do L4, R-02 e R-03)
        self.dead = False                            # F5.9 (item 1): morreu calada (sem `on_error`); só `alive()` conta
        self.t_next: float | None = None             # início (sessão) do próximo bloco a entregar
        self.phase = 0
        self.delivered = 0

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("o dispositivo não começou")
        self.started = True
        self.t_next = self.clock.rel()

    def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1

    def alive(self) -> bool | None:
        """F5.9 (item 1): None por padrão (o driver não diz nada); False depois de `die()`."""
        return False if self.dead else None

    def fail(self, reason: str = "dispositivo removido") -> None:
        """Simula o driver encerrando a stream sozinho (headset desplugado)."""
        self.stopped = True
        if self.on_error is not None:
            self.on_error(reason)

    def die(self) -> None:
        """F5.9 (Aceite 1): o app saiu ou o endpoint foi invalidado sem o driver avisar: nada mais é entregue, `alive()` passa
        a False e a stream segue "aberta" até alguém a fechar (como no PortAudio)."""
        self.dead = True

    def pump(self, until_rel: float) -> None:
        """Entrega todos os blocos cujo fim é anterior a `until_rel` (tempo da sessão)."""
        if not self.started or self.stopped or self.dead or self.t_next is None:
            return
        while self.t_next + self.period <= until_rel + 1e-9:
            t0 = self.t_next
            self.t_next += self.period
            if any(a <= t0 < b for a, b in self.silent_windows):
                self.phase += self.block
                continue
            n = np.arange(self.phase, self.phase + self.block)
            self.phase += self.block
            tone = (self.amplitude * np.sin(2 * math.pi * self.freq * n / self.sample_rate)).astype(np.float32)
            data = np.repeat(tone[:, None], self.channels, axis=1)
            self.delivered += 1
            self.on_audio(data, self.clock.t0_perf + t0)


class FakeMute:
    """`muted` é o mudo do endpoint de agora; `by_device` guarda o de cada endpoint que já passou por aqui (A-18)."""

    def __init__(self) -> None:
        self.muted = False
        self.device: AudioDevice | None = None
        self.by_device: dict[str | None, bool] = {}
        self.switches: list[tuple[str | None, bool]] = []          # (endpoint novo, mudo passado junto)

    def set_device(self, device: AudioDevice | None, muted: bool = False) -> bool:
        self.by_device[self.device.id if self.device else None] = False if muted else self.muted
        self.device = device
        key = device.id if device else None
        self.switches.append((key, muted))
        self.muted = True if muted else self.by_device.get(key, False)
        self.by_device[key] = self.muted
        return True

    def get(self) -> bool | None:
        return self.muted

    def set(self, muted: bool) -> bool:
        self.muted = muted
        self.by_device[self.device.id if self.device else None] = muted
        return True


class FakeHotkeys:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self._errors: dict[str, str] = {}

    def validate(self, combo: str) -> str | None:
        from listener.platform.common.hotkeys import validate_combo
        return validate_combo(combo)

    def register(self, combo: str, callback: object) -> bool:
        err = self.validate(combo)
        if err:
            self._errors[combo] = err
            return False
        self.registered[combo] = callback
        return True

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def unregister_all(self) -> None:
        self.registered.clear()

    def stop(self) -> None:
        pass


class _ChildStdin:
    def __init__(self, child: FakeChild):
        self.child = child

    def write(self, data: bytes, /) -> int:
        if self.child.done.is_set():
            raise BrokenPipeError("filho encerrado")
        self.child.received.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if not self.child.ignore_eof:
            self.child.done.set()


class _ChildStdout:
    def __init__(self, child: FakeChild):
        self.child = child

    def readline(self, size: int = -1, /) -> bytes:
        self.child.done.wait()
        return b""

    def close(self) -> None:
        pass


class FakeChild:
    """Filho ao vivo silencioso (F4): guarda o que recebe no stdin, não escreve nada e sai com 0 no EOF ou no kill."""

    def __init__(self, argv: list[str], env: Mapping[str, str] | None, pid: int, ignore_eof: bool = False):
        self.argv = list(argv)
        self.env = dict(env or {})
        self.pid = pid
        self.ignore_eof = ignore_eof                 # filho travado: o EOF não o encerra (só o kill)
        self.received = bytearray()
        self.done = threading.Event()
        self.killed = False
        self.stdin = _ChildStdin(self)
        self.stdout = _ChildStdout(self)

    def poll(self) -> int | None:
        return 0 if self.done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired(self.argv, timeout or 0.0)
        return 0

    def kill(self) -> None:
        self.killed = True
        self.done.set()

    def terminate(self) -> None:
        self.kill()
