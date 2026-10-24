"""Interfaces da camada de plataforma. Nada específico de sistema operacional aqui."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from collections.abc import Callable

import numpy as np


@dataclass(frozen=True)
class AudioDevice:
    id: str            # identificador estável (ID do endpoint no Windows; "name:<nome>" quando não há): é o que se persiste
    name: str
    api: str           # "WASAPI", "CoreAudio", "PulseAudio"...
    channels: int
    sample_rate: int
    index: int | None = None   # índice na biblioteca de áudio nesta sessão: muda ao plugar e desplugar, nunca persistir


def pick_device(devices: list[AudioDevice], device_id: str | None, name: str | None) -> AudioDevice | None:
    """Acha o dispositivo salvo: pelo id estável, depois pelo nome (exato, depois por prefixo). Índices antigos
    (só dígitos, F1) são ignorados. None = não está mais nesta máquina: quem chama avisa e cai no padrão."""
    if device_id and not device_id.isdigit():
        for d in devices:
            if d.id == device_id:
                return d
    if name:
        for d in devices:
            if d.name == name:
                return d
        low = name.lower()
        for d in devices:
            dn = d.name.lower()
            if len(low) >= 20 and (dn.startswith(low[:24]) or low.startswith(dn[:24])):
                return d
    return None


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
    def set_device(self, device: AudioDevice | None) -> bool: ...   # True se achou o endpoint; False = vai no padrão
    def get(self) -> bool | None: ...          # None = não sei
    def set(self, muted: bool) -> bool: ...    # True se conseguiu


class Hotkeys(Protocol):
    def register(self, combo: str, callback: Callable[[], None]) -> bool: ...
    def errors(self) -> dict[str, str]: ...    # combo -> motivo da falha (sintaxe, já usado por outro app...)
    def unregister_all(self) -> None: ...
    def stop(self) -> None: ...


class Platform(Protocol):
    name: str

    # processo
    def app_init(self) -> None: ...                      # antes de criar a interface: identidade do processo etc.
    def keep_awake(self, on: bool) -> bool: ...          # impede a suspensão do sistema enquanto grava
    def bring_to_front(self, window_id: int) -> bool: ...  # traz uma janela nossa para a frente mesmo em segundo plano

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
