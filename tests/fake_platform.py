"""Plataforma simulada para os testes (roda em qualquer sistema): streams dirigidas por um relógio manual.

`FakePlatform.pump(seconds)` avança o `ManualClock` e entrega os blocos de todas as streams iniciadas, em ordem de
tempo, de forma síncrona: com `Recorder(inline_worker=True)` tudo acontece na thread do teste (F1.2 M-27).
F5.3 L4: `endpoints` é o que o Core Audio vê agora (`next_endpoints` entra na leitura seguinte) e `devices` o que o
PortAudio leu da última vez (só muda no reinício, com o padrão dessa leitura); `open_mic(strict=True)` recusa o pedido
fora da lista; o loopback do sistema abre na saída pedida, resolvida contra a lista do primeiro aberto enquanto algum
segue aberto (um `PyAudio()` vivo mantém a lista antiga, A-15), e `loopbacks_at_open` conta os abertos em cada abertura.
"""
from __future__ import annotations

import math
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

import numpy as np

from listener.capture.clock import ManualClock
from listener.platform.base import (AudioCallback, AudioDevice, ChildProcess, Endpoint, Endpoints, ErrorCallback, Platform,
                                    PlayingApp)

MIC = AudioDevice("fake-mic-0", "Mic Fake", "fake", 1, 16000, index=0)
SPEAKERS = Endpoint("fake-out-0", "Alto-falantes Fake")
BASE_ENDPOINTS = Endpoints(capture=(Endpoint(MIC.id, MIC.name),), render=(SPEAKERS,), capture_default=MIC.id,
                           capture_comm=MIC.id, render_default=SPEAKERS.id, render_comm=SPEAKERS.id)


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

    def fail(self, reason: str = "dispositivo removido") -> None:
        """Simula o driver encerrando a stream sozinho (headset desplugado)."""
        self.stopped = True
        if self.on_error is not None:
            self.on_error(reason)

    def pump(self, until_rel: float) -> None:
        """Entrega todos os blocos cujo fim é anterior a `until_rel` (tempo da sessão)."""
        if not self.started or self.stopped or self.t_next is None:
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


class FakePlatform(Platform):
    name = "fake"

    def __init__(self, tmp: Path, r_silent_windows: list[tuple[float, float]] | None = None, mic_fail: bool = False,
                 app_fail: bool = False, clock: ManualClock | None = None, r_format: tuple[int, int] = (48000, 2),
                 system_format: tuple[int, int] = (48000, 2)):
        self.tmp = Path(tmp)
        self.clock = clock or ManualClock()
        self.r_silent_windows = r_silent_windows or []
        self.mic_fail = mic_fail
        self.app_fail = app_fail
        self.r_format = r_format
        self.system_format = system_format
        self._mute = FakeMute()
        self._hk = FakeHotkeys()
        self.streams: list[FakeStream] = []
        self.awake: bool | None = None
        self.spawned: list[tuple[list[str], dict[str, str]]] = []   # F2: o worker nunca é lançado de verdade
        self.children: list[FakeChild] = []                          # F4: filhos ao vivo silenciosos
        self.lowered = 0
        self.next_pid = 5000
        self.devices = [MIC]
        self.next_devices: list[AudioDevice] | None = None    # U-043: o teste "conecta" um dispositivo aqui
        self.refreshes = 0
        self.mic_streams: list[FakeStream] = []
        # F5.3 L4: o Core Audio (agora e na próxima leitura), o que o PortAudio leu da última vez e quem mais o segura
        self.endpoints: Endpoints | None = BASE_ENDPOINTS
        self.next_endpoints: Endpoints | None = None
        self.endpoint_reads = 0
        self._pa_signature = BASE_ENDPOINTS.signature
        self._pa_default: str | None = None                   # o padrão da última leitura do PortAudio (None = o primeiro)
        self.other_streams: Callable[[], int] = lambda: 0     # o player nos testes do controlador
        self.fail_ids: set[str] = set()                       # microfones que recusam abrir
        self.start_fail_ids: set[str] = set()                 # microfones que abrem e recusam o `start()`
        self.system_fail = False                              # o loopback do sistema recusa abrir
        self.system_start_fail = False                        # ...ou abre e recusa o `start()`
        self.mics_at_open: list[int] = []                     # quantos microfones seguiam abertos em cada abertura (A-15)
        self.loopbacks: list[FakeStream] = []
        self.loopbacks_at_open: list[int] = []                # quantos loopbacks do sistema seguiam abertos em cada abertura
        self._lb_view: Endpoints | None = None                # a lista do PyAudio vivo (A-15)
        self.capture_exclusion = True                         # F5.3 (A-13): o que `exclude_from_capture` devolve
        self.excluded: list[int] = []                         # as janelas que pediram para sair da captura
        self.text_scale_factor = 1.0                          # F5.4 (A-08): o "Tamanho do texto" do sistema simulado

    # ---- condução dos testes ----
    def pump(self, seconds: float, step: float = 0.1) -> None:
        """Avança o relógio em passos de `step` entregando os blocos de todas as streams em ordem de tempo."""
        target = self.clock.rel() + seconds
        while self.clock.rel() + 1e-9 < target:
            self.clock.advance(min(step, target - self.clock.rel()))
            now = self.clock.rel()
            for s in list(self.streams):
                s.pump(now)

    def live_streams(self) -> list[FakeStream]:
        return [s for s in self.streams if s.started and not s.stopped]

    def settle(self) -> None:
        """F5.3: o PortAudio leu agora (o app abriu assim): a lista e o padrão dele são os do Core Audio de agora."""
        if self.endpoints is not None:
            self._pa_signature = self.endpoints.signature
            self._pa_default = self.endpoints.capture_default or None

    # ---- Platform ----
    @override
    def app_init(self) -> None:
        pass

    @override
    def keep_awake(self, on: bool) -> bool:
        self.awake = on
        return True

    @override
    def bring_to_front(self, window_id: int) -> bool:
        return True

    @override
    def exclude_from_capture(self, window_id: int) -> bool:
        self.excluded.append(int(window_id))
        return self.capture_exclusion

    @override
    def text_scale(self) -> float:
        return self.text_scale_factor

    @override
    def ui_style(self) -> str:
        return "Fusion"                                       # F5.4 (A-03): o offscreen não tem o `windows11`

    @override
    def list_input_devices(self) -> list[AudioDevice]:
        return list(self.devices)

    @override
    def refresh_audio_devices(self, own_streams: int = 0, release: Callable[[], None] | None = None) -> bool:
        """U-043: `next_devices` é o que o "sistema" passou a ter; relê só sem stream de microfone aberto além dos de quem
        pede. Aberto é o que ninguém fechou (A-03): a stream que o driver encerrou (`fail`) segura, como no PortAudio.
        F5.3: também relê quando o Core Audio mudou desde a última leitura (o padrão novo entra aqui)."""
        if not self.audio_devices_changed() or self._held() > own_streams:
            return False
        if release is not None:
            release()
        if self.next_devices is not None:
            self.devices, self.next_devices = list(self.next_devices), None
        if self.endpoints is not None:
            self._pa_signature = self.endpoints.signature
            self._pa_default = self.endpoints.capture_default or None
        self.refreshes += 1
        return True

    def _held(self) -> int:
        return sum(1 for s in self.mic_streams if s.stop_calls == 0) + self.other_streams()

    @override
    def audio_devices_changed(self) -> bool:
        return self.next_devices is not None or (self.endpoints is not None and self.endpoints.signature != self._pa_signature)

    @override
    def audio_endpoints(self, refresh_names: bool = False) -> Endpoints | None:
        if self.next_endpoints is not None:
            self.endpoints, self.next_endpoints = self.next_endpoints, None
        self.endpoint_reads += 1
        return self.endpoints

    @override
    def default_input_device(self) -> AudioDevice | None:
        devices = self.list_input_devices()
        return next((d for d in devices if d.id == self._pa_default), devices[0] if devices else None)

    @override
    def list_playing_apps(self) -> list[PlayingApp]:
        return [PlayingApp(pid=4242, exe="meeting.exe", title="Reunião", peak=0.5, is_meeting_app=True)]

    def _stream(self, sample_rate: int, channels: int, on_audio: AudioCallback, on_error: ErrorCallback | None,
                freq: float, silent: list[tuple[float, float]] | None = None) -> FakeStream:
        s = FakeStream(self.clock, sample_rate, channels, on_audio, on_error, freq=freq, silent_windows=silent)
        self.streams.append(s)
        return s

    @override
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None, strict: bool = False) -> FakeStream:
        if self.mic_fail:
            raise RuntimeError("sem microfone")
        found = self.default_input_device() if device is None else next((d for d in self.devices if d.id == device.id), None)
        if found is None:
            if strict and device is not None:
                raise LookupError(f"o microfone {device.id} não está na lista")
            found = self.default_input_device()
        if found is not None and found.id in self.fail_ids:
            raise RuntimeError("o microfone recusou abrir")
        self.mics_at_open.append(sum(1 for s in self.mic_streams if s.stop_calls == 0))
        stream = self._stream(sample_rate, 1, on_audio, on_error, freq=440.0)
        stream.device_id = found.id if found else ""
        stream.fail_start = stream.device_id in self.start_fail_ids
        self.mic_streams.append(stream)                  # U-043: o que o PortAudio do sounddevice abriria
        return stream

    @override
    def open_app_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> FakeStream:
        if self.app_fail:
            raise RuntimeError("app sem áudio")
        return self._stream(*self.r_format, on_audio, on_error, freq=220.0, silent=self.r_silent_windows)

    @override
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None,
                             output_id: str | None = None) -> FakeStream:
        if self.system_fail:
            raise RuntimeError("loopback do sistema indisponível")
        still_open = sum(1 for s in self.loopbacks if s.stop_calls == 0)
        self.loopbacks_at_open.append(still_open)
        if not still_open or self._lb_view is None:
            self._lb_view = self.endpoints                     # PyAudio novo: a lista de agora (com outro vivo, a antiga)
        view = self._lb_view
        stream = self._stream(*self.system_format, on_audio, on_error, freq=330.0, silent=self.r_silent_windows)
        if view is not None:
            stream.device_id = output_id if output_id and view.output(output_id) is not None else view.render_default
        stream.fail_start = self.system_start_fail
        self.loopbacks.append(stream)
        return stream

    @override
    def mic_mute(self) -> FakeMute:
        return self._mute

    @override
    def hotkeys(self) -> FakeHotkeys:
        return self._hk

    @override
    def root_pid(self, exe: str) -> int | None:
        return 4242 if exe == "meeting.exe" else None

    @override
    def spawn_detached(self, argv: list[str], env: Mapping[str, str] | None = None) -> int:
        self.spawned.append((list(argv), dict(env or {})))
        self.next_pid += 1
        return self.next_pid

    @override
    def spawn_child(self, argv: list[str], env: Mapping[str, str] | None = None) -> ChildProcess:
        self.next_pid += 1
        child = FakeChild(argv, env, self.next_pid)
        self.children.append(child)
        return child

    @override
    def disable_own_power_throttling(self) -> bool:
        return False

    @override
    def sync_stdout_handle(self) -> None:
        pass

    @override
    def lower_own_priority(self) -> bool:
        self.lowered += 1
        return True

    @override
    def memory_rss(self) -> int | None:
        return 123_000_000

    @override
    def config_dir(self) -> Path:
        return self.tmp / "config"

    @override
    def data_dir_suggestion(self) -> Path:
        return self.tmp / "data"

    @override
    def synced_folder_name(self, path: Path) -> str | None:
        from listener.platform.common.paths import synced_folder_name
        return synced_folder_name(path)
