"""Pastas do sistema por plataforma, sem dependências externas."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "listener-meet"
SYNC_MARKERS = {
    "onedrive": "OneDrive", "dropbox": "Dropbox", "google drive": "Google Drive", "googledrive": "Google Drive",
    "icloud": "iCloud", "mobile documents": "iCloud", "nextcloud": "Nextcloud", "box sync": "Box",
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / APP


def data_dir_suggestion() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / APP


def synced_folder_name(path: Path | str) -> str | None:
    """Nome do serviço de sincronização se o caminho estiver dentro de uma pasta sincronizada."""
    for part in Path(path).parts:
        low = part.lower()
        for marker, name in SYNC_MARKERS.items():
            if marker in low:
                return name
    return None
