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
    LIVE_STARTED = "live_started"  # F4: processo ao vivo (gravados por ele, salvo `live_cpu`, que o app grava)
    LIVE_DEGRADED = "live_degraded"
    LIVE_CPU = "live_cpu"
    LIVE_STOPPED = "live_stopped"


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
    LLM = "llm"                    # tarefas de LLM da reunião (F3): worker leve, sem torch
    INDEX = "index"                # índice da busca da reunião (F5): trechos, FTS e vetores; worker leve, depois dos `llm`


# Classes de fila (F3 item 2): um worker por classe; o pesado carrega torch, o leve só faz rede e JSON (F5: e o índice)
HEAVY_JOB_KINDS: frozenset[str] = frozenset({JobKind.MEETING, JobKind.REPROCESS, JobKind.EMBED_RANGE})
LIGHT_JOB_KINDS: frozenset[str] = frozenset({JobKind.LLM, JobKind.INDEX})
# jobs que escrevem `clusters` e `segments`: só eles definem o job vigente da reunião (`current_job_id`, F3A-04); o `index`
# nunca entra aqui (F5)
PIPELINE_JOB_KINDS: tuple[str, ...] = (str(JobKind.MEETING), str(JobKind.REPROCESS))


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
    IDENTIFY = "identify"          # F3: tarefas do job `llm`, na ordem do item 3 da spec
    MINUTES = "minutes"
    EXECUTIVE = "executive"
    PRE_SUMMARY = "pre_summary"
    TITLE = "title"
    REVISE = "revise"
    TEST = "test"                  # "testar pelo worker" (F3 item 12)
    INDEX_TEXT = "index_text"      # F5: trechos e FTS da reunião (`rag.index.sync_text`)
    INDEX_VECTORS = "index_vectors"   # F5: vetores que faltam do modelo ativo (`rag.index.fill_vectors`)


JOB_STEP_LABELS: dict[str, str] = {
    JobStep.DOWNLOAD: "baixando modelo…", JobStep.WAITING: "aguardando fim da gravação", JobStep.DIARIZE: "separando vozes",
    JobStep.EMBED: "extraindo vozes", JobStep.MATCH: "casando perfis", JobStep.TRANSCRIBE_R: "transcrevendo a reunião",
    JobStep.TRANSCRIBE_L: "transcrevendo o microfone", JobStep.SEGMENTS: "montando as falas",
    JobStep.IDENTIFY: "LLM: identidade por contexto", JobStep.MINUTES: "LLM: ata", JobStep.EXECUTIVE: "LLM: resumo executivo",
    JobStep.PRE_SUMMARY: "LLM: pré-resumo", JobStep.TITLE: "LLM: título", JobStep.REVISE: "LLM: transcrição revisada",
    JobStep.TEST: "LLM: teste de conexão", JobStep.INDEX_TEXT: "índice: trechos", JobStep.INDEX_VECTORS: "índice: vetores",
}
LLM_STEPS: tuple[JobStep, ...] = (JobStep.IDENTIFY, JobStep.MINUTES, JobStep.EXECUTIVE, JobStep.PRE_SUMMARY, JobStep.TITLE,
                                  JobStep.REVISE, JobStep.TEST)


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
    INVALID = "invalidado"         # confirmação corrigida pelo usuário: fica gravado, fora do centroide (R2-09, CP3)


class SegmentSource(StrEnum):
    FINAL = "final"
    LIVE = "live"                  # F4


class Attribution(StrEnum):
    PYANNOTE = "pyannote"
    USER = "usuario"
    LLM = "llm"
    CHANNEL = "canal"              # canal L = o usuário (F2 item 8)


class SummaryKind(StrEnum):
    PRE_SUMMARY = "pre_summary"    # F3: título vai em `meetings.title_llm`, não aqui (F3A-05)
    MINUTES = "minutes"
    EXECUTIVE = "executive"
    PARTIAL = "partial"            # F4: resumo parcial ao vivo


class SummaryStatus(StrEnum):
    CURRENT = "atual"
    SUPERSEDED = "superada"        # "gerar de novo" acrescenta e marca; nada se apaga (CP3)
    ERROR = "erro"


class ActionStatus(StrEnum):
    OPEN = "aberta"
    DONE = "feita"
    DISCARDED = "descartada"
    SUPERSEDED = "superada"        # a ata que a gerou foi superada


class DecisionSource(StrEnum):
    """O que o usuário aceitou ao confirmar uma voz (F3 item 5, mede o G3)."""
    AUTO = "auto"                  # acima do limiar, sem revisão
    USER = "usuario"               # sem sugestão: escolheu ou criou a pessoa
    USER_PYANNOTE = "usuario_pyannote"
    USER_LLM = "usuario_llm"
    USER_OTHER = "usuario_outro"   # havia sugestão e escolheu outra


class LlmCallStatus(StrEnum):
    OK = "ok"
    ERROR = "erro"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class StoreRole(StrEnum):
    APP = "app"                    # main em escrita, migra, VACUUM de legado
    WORKER = "worker"              # main em escrita sem migrar; áudio só leitura com mmap (F2)
    READONLY = "readonly"          # tudo `mode=ro` (`--check`)
