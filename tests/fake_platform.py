"""Plataforma simulada para os testes (roda em qualquer sistema): streams dirigidas por um relógio manual.

`FakePlatform.pump(seconds)` avança o `ManualClock` e entrega os blocos de todas as streams iniciadas, em ordem de
tempo, de forma síncrona: com `Recorder(inline_worker=True)` tudo acontece na thread do teste (F1.2 M-27).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import override

import numpy as np

from listener.capture.clock import ManualClock
from listener.platform.base import AudioCallback, AudioDevice, ErrorCallback, Platform, PlayingApp


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
        self.t_next: float | None = None             # início (sessão) do próximo bloco a entregar
        self.phase = 0
        self.delivered = 0

    def start(self) -> None:
        self.started = True
        self.t_next = self.clock.rel()

    def stop(self) -> None:
        self.stopped = True

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
    def __init__(self) -> None:
        self.muted = False
        self.device: AudioDevice | None = None

    def set_device(self, device: AudioDevice | None) -> bool:
        self.device = device
        return True

    def get(self) -> bool | None:
        return self.muted

    def set(self, muted: bool) -> bool:
        self.muted = muted
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
    def list_input_devices(self) -> list[AudioDevice]:
        return [AudioDevice("fake-mic-0", "Mic Fake", "fake", 1, 16000, index=0)]

    @override
    def default_input_device(self) -> AudioDevice | None:
        return self.list_input_devices()[0]

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
                 on_error: ErrorCallback | None = None) -> FakeStream:
        if self.mic_fail:
            raise RuntimeError("sem microfone")
        return self._stream(sample_rate, 1, on_audio, on_error, freq=440.0)

    @override
    def open_app_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> FakeStream:
        if self.app_fail:
            raise RuntimeError("app sem áudio")
        return self._stream(*self.r_format, on_audio, on_error, freq=220.0, silent=self.r_silent_windows)

    @override
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> FakeStream:
        return self._stream(*self.system_format, on_audio, on_error, freq=330.0, silent=self.r_silent_windows)

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
    def config_dir(self) -> Path:
        return self.tmp / "config"

    @override
    def data_dir_suggestion(self) -> Path:
        return self.tmp / "data"

    @override
    def synced_folder_name(self, path: Path) -> str | None:
        from listener.platform.common.paths import synced_folder_name
        return synced_folder_name(path)
