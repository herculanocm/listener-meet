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
    JOB_START = "job_start"        # F2: marcos do worker
    JOB_STEP = "job_step"
    JOB_DONE = "job_done"
    JOB_ERROR = "job_error"
    JOB_CANCEL = "job_cancel"
    PROFILE_CONFIRM = "profile_confirm"


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

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED)


class JobKind(StrEnum):
    MEETING = "meeting"            # processar a reunião inteira (F2)
    REPROCESS = "reprocess"        # mesmo pipeline, troca atômica no fim (F2 item 12)
    EMBED_RANGE = "embed_range"    # perfil de um trecho livre (F6)


class JobStep(StrEnum):
    """Etapas do worker na ordem da spec F2 (item 9); `DOWNLOAD` e `WAITING` são estados de espera, não de cálculo."""
    DOWNLOAD = "download"
    WAITING = "waiting"
    DIARIZE = "diarize"
    EMBED = "embed"
    MATCH = "match"
    TRANSCRIBE_R = "transcribe_r"
    TRANSCRIBE_L = "transcribe_l"
    SEGMENTS = "segments"


JOB_STEP_LABELS: dict[str, str] = {
    JobStep.DOWNLOAD: "baixando modelo…", JobStep.WAITING: "aguardando fim da gravação", JobStep.DIARIZE: "separando vozes",
    JobStep.EMBED: "extraindo vozes", JobStep.MATCH: "casando perfis", JobStep.TRANSCRIBE_R: "transcrevendo a reunião",
    JobStep.TRANSCRIBE_L: "transcrevendo o microfone", JobStep.SEGMENTS: "montando as falas",
}


class ClusterStatus(StrEnum):
    AUTO = "auto"                  # acima do limiar: nome sem perguntar
    CONFIRMED = "confirmado"
    AMBIGUOUS = "ambíguo"          # margem pequena entre os dois melhores perfis
    UNNAMED = "sem_nome"
    IGNORED = "ignorado"


class EmbeddingSource(StrEnum):
    ENROLL = "cadastro"
    CONFIRM = "confirmacao"
    AUTO = "automatico"            # gravado, mas fora do centroide até uma confirmação (D-041 P3)


class SegmentSource(StrEnum):
    FINAL = "final"
    LIVE = "live"                  # F4


class Attribution(StrEnum):
    PYANNOTE = "pyannote"
    USER = "usuario"
    LLM = "llm"
    CHANNEL = "canal"              # canal L = o usuário (F2 item 8)


class StoreRole(StrEnum):
    APP = "app"                    # main em escrita, migra, VACUUM de legado
    WORKER = "worker"              # main em escrita sem migrar; áudio só leitura com mmap (F2)
    READONLY = "readonly"          # tudo `mode=ro` (`--check`)
