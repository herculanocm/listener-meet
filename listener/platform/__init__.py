"""Camada de plataforma (D-027): tudo que depende do sistema operacional fica aqui.

Fora desta pasta não existe import de pycaw, PyAudioWPatch, ctypes.windll nem caminho com letra de unidade.
"""
from __future__ import annotations

import sys

from .base import AudioDevice, CaptureHandle, ChildProcess, Endpoint, Endpoints, Hotkeys, MicMute, Platform, PlayingApp

__all__ = ["AudioDevice", "CaptureHandle", "ChildProcess", "Endpoint", "Endpoints", "Hotkeys", "MicMute", "Platform",
           "PlayingApp", "get_platform"]


def get_platform() -> Platform:
    if sys.platform == "win32":
        from .win32.platform import Win32Platform

        return Win32Platform()
    raise NotImplementedError(
        f"plataforma {sys.platform!r} ainda não implementada (D-027: Linux e macOS entram depois)"
    )
