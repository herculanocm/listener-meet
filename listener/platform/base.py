"""Interfaces da camada de plataforma. Nada específico de sistema operacional aqui."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


@dataclass(frozen=True)
class AudioDevice:
    id: str            # identificador na plataforma (ex.: índice PortAudio como texto)
    name: str
    api: str           # "WASAPI", "CoreAudio", "PulseAudio"...
    channels: int
    sample_rate: int


@dataclass
class PlayingApp:
    pid: int
    exe: str                   # nome do executável
    title: str = ""            # título da janela principal, se conhecido
    peak: float = 0.0          # pico de áudio recente, 0 a 1
    is_meeting_app: bool = False

    @property
    def label(self) -> str:
        base = self.exe.removesuffix(".exe")
        return f"{base} — {self.title}" if self.title else base


# Callback de áudio: (bloco float32 com shape (frames, canais), perf_counter do primeiro frame)
AudioCallback = Callable[[np.ndarray, float], None]


class CaptureHandle(Protocol):
    sample_rate: int
    channels: int

    def stop(self) -> None: ...


class MicMute(Protocol):
    def get(self) -> bool | None: ...          # None = não sei
    def set(self, muted: bool) -> bool: ...    # True se conseguiu


class Hotkeys(Protocol):
    def register(self, combo: str, callback: Callable[[], None]) -> bool: ...
    def unregister_all(self) -> None: ...
    def stop(self) -> None: ...


class Platform(Protocol):
    name: str

    # dispositivos e apps
    def list_input_devices(self) -> list[AudioDevice]: ...
    def default_input_device(self) -> AudioDevice | None: ...
    def list_playing_apps(self) -> list[PlayingApp]: ...

    # captura (o callback roda em thread de áudio: só copiar e enfileirar)
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback) -> CaptureHandle: ...
    def open_app_loopback(self, pid: int, on_audio: AudioCallback) -> CaptureHandle: ...
    def open_system_loopback(self, on_audio: AudioCallback) -> CaptureHandle: ...

    # controles
    def mic_mute(self) -> MicMute: ...
    def hotkeys(self) -> Hotkeys: ...

    # processos
    def root_pid(self, exe: str) -> int | None: ...

    # pastas do sistema
    def config_dir(self) -> Path: ...
    def data_dir_suggestion(self) -> Path: ...
    def synced_folder_name(self, path: Path) -> str | None: ...
