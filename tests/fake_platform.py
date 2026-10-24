"""Plataforma simulada para os testes (roda no Linux): gera áudio em threads como os drivers fariam."""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np

from listener.platform.base import AudioDevice, PlayingApp


class _FakeStream(threading.Thread):
    def __init__(self, sample_rate: int, channels: int, on_audio, block_ms: int = 100, freq: float = 440.0,
                 amplitude: float = 0.3, silent_windows: list[tuple[float, float]] | None = None):
        super().__init__(daemon=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_audio = on_audio
        self.block = int(sample_rate * block_ms / 1000)
        self.freq = freq
        self.amplitude = amplitude
        self.silent_windows = silent_windows or []   # (início, fim) em segundos: driver não entrega nada
        self._stop_event = threading.Event()
        self.t0 = time.perf_counter()
        self.phase = 0

    def run(self) -> None:
        next_t = self.t0
        period = self.block / self.sample_rate
        while not self._stop_event.is_set():
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            rel = next_t - self.t0 - period
            if any(a <= rel < b for a, b in self.silent_windows):
                self.phase += self.block
                continue
            n = np.arange(self.phase, self.phase + self.block)
            self.phase += self.block
            tone = (self.amplitude * np.sin(2 * math.pi * self.freq * n / self.sample_rate)).astype(np.float32)
            data = np.repeat(tone[:, None], self.channels, axis=1)
            self.on_audio(data, next_t - period)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2)


class FakeMute:
    def __init__(self):
        self.muted = False

    def get(self):
        return self.muted

    def set(self, muted: bool) -> bool:
        self.muted = muted
        return True


class FakeHotkeys:
    def __init__(self):
        self.registered = {}

    def register(self, combo, callback):
        self.registered[combo] = callback
        return True

    def unregister_all(self):
        self.registered.clear()

    def stop(self):
        pass


class FakePlatform:
    name = "fake"

    def __init__(self, tmp: Path, r_silent_windows=None, mic_fail: bool = False, app_fail: bool = False):
        self.tmp = Path(tmp)
        self.r_silent_windows = r_silent_windows or []
        self.mic_fail = mic_fail
        self.app_fail = app_fail
        self._mute = FakeMute()
        self._hk = FakeHotkeys()
        self.streams = []

    def list_input_devices(self):
        return [AudioDevice("0", "Mic Fake", "fake", 1, 16000)]

    def default_input_device(self):
        return self.list_input_devices()[0]

    def list_playing_apps(self):
        return [PlayingApp(pid=4242, exe="meeting.exe", title="Reunião", peak=0.5, is_meeting_app=True)]

    def open_mic(self, device, sample_rate, on_audio):
        if self.mic_fail:
            raise RuntimeError("sem microfone")
        s = _FakeStream(sample_rate, 1, on_audio, freq=440.0)
        s.start()
        self.streams.append(s)
        return s

    def open_app_loopback(self, pid, on_audio):
        if self.app_fail:
            raise RuntimeError("app sem áudio")
        s = _FakeStream(48000, 2, on_audio, freq=220.0, silent_windows=self.r_silent_windows)
        s.start()
        self.streams.append(s)
        return s

    def open_system_loopback(self, on_audio):
        s = _FakeStream(48000, 2, on_audio, freq=330.0, silent_windows=self.r_silent_windows)
        s.start()
        self.streams.append(s)
        return s

    def mic_mute(self):
        return self._mute

    def hotkeys(self):
        return self._hk

    def root_pid(self, exe):
        return 4242 if exe == "meeting.exe" else None

    def config_dir(self):
        return self.tmp / "config"

    def data_dir_suggestion(self):
        return self.tmp / "data"

    def synced_folder_name(self, path):
        from listener.platform.common.paths import synced_folder_name
        return synced_folder_name(path)
