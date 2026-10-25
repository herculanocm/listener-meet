"""Estados e tipos do domínio como `StrEnum` (F1.2 M-21): entram direto no SQLite e no JSON, comparam com a string
e validam na leitura (`MeetingStatus("gravada")`). Nada de literal solto fora daqui."""
from __future__ import annotations

from enum import StrEnum


class MeetingStatus(StrEnum):
    RECORDING = "gravando"
    PAUSED = "pausada"
    RECORDED = "gravada"
    CANCELLED = "cancelada"
    INTERRUPTED = "interrompida"
    PROCESSED = "processada"      # F2
    REVIEWED = "revisada"         # F2


class EventType(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    MUTE = "mute"
    UNMUTE = "unmute"
    MARKER = "marker"
    SOURCE_CHANGE = "source_change"
    DEVICE_LOST = "device_lost"
    DEVICE_BACK = "device_back"
    ALERT = "alert"
    WARNING = "warning"
    STOP = "stop"
    CANCEL = "cancel"
    ERROR = "error"


class Health(StrEnum):
    OK = "ok"
    WARN = "warn"
    ALERT = "alert"
    MUTED = "muted"
    PAUSED = "paused"
    OFF = "off"
    LOST = "lost"          # dispositivo sumiu (M-09); o gravador tenta reabrir


class SourceKind(StrEnum):
    APP = "app"
    SYSTEM = "system"
    NONE = "none"


class SettingScope(StrEnum):
    NOW = "now"
    NEXT_RECORDING = "next_recording"


class AppState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    SAVING = "saving"


class JobStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "rodando"
    PAUSED = "pausado"
    DONE = "concluído"
    ERROR = "erro"
    CANCELLED = "cancelado"


class Attribution(StrEnum):
    PYANNOTE = "pyannote"
    USER = "usuario"
    LLM = "llm"
