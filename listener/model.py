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
    LLM_DEFERRED = "llm_deferred"  # U-042 (F5.2): a ata espera a identificação das vozes (job pai e tarefas)
    LLM_RELEASED = "llm_released"  # U-042: as tarefas adiadas entraram na fila (job criado e o motivo)
    LLM_REQUESTED = "llm_requested"  # F5.6 (A-11): ata ou resumo pedido (tarefas, modo, origem, job a que fica preso)
    LLM_CHAINED = "llm_chained"    # F5.6 (A-12): o fim do processamento enfileirou o núcleo e consumiu os pedidos (job pai)
    DEVICE_CHANGE = "device_change"  # F5.3 (A-21): outro aparelho num canal (id do endpoint, acompanha, motivo; nunca o nome)


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


class DeviceReason(StrEnum):
    """F5.3 L4: por que o aparelho de um canal mudou (o motivo do evento `device_change`); F5.9 (item 4): também o do
    `source_change` (`user`, `fallback`, `back`)."""
    USER = "user"          # o usuário escolheu no painel ou no "Usar"
    DEFAULT = "default"    # acompanha o padrão do Windows, que mudou (A-20)
    MISSING = "missing"    # o escolhido não está: o padrão no lugar (A-16)
    BACK = "back"          # o escolhido voltou (pergunta 10); F5.9: a fonte de volta ao app depois do fallback
    FALLBACK = "fallback"  # F5.9 (item 4): o app sem loopback: gravando o sistema inteiro no lugar dele


class LostReason(StrEnum):
    """F5.9 (itens 1 e 2): por que um canal está perdido (o motivo do evento `device_lost` e do alerta do widget)."""
    PROCESS_EXIT = "process_exit"        # o app gravado saiu (o handle do processo sinalizou)
    STREAM_STOPPED = "stream_stopped"    # a stream parou: o driver avisou (`on_error`) ou a prova de vida disse
    NEVER_OPENED = "never_opened"        # não abriu no começo da gravação; tentado de novo a cada 5 s


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

# F5.3 (U-047, A-09): os textos da fila e da Ajuda dela, para quem usa o app (sem jargão); o teste exige todo tipo, estado e
# etapa. A ordem das etapas vem dos próprios runners (`ui/queue_help.py`), não daqui.
JOB_KIND_LABELS: dict[str, str] = {JobKind.MEETING: "processar", JobKind.REPROCESS: "reprocessar", JobKind.EMBED_RANGE: "perfil de trecho",
                                   JobKind.LLM: "LLM", JobKind.INDEX: "índice"}
JOB_KIND_HELP: dict[str, str] = {
    JobKind.MEETING: "transforma a gravação em falas com o nome de quem fala: separa as vozes, reconhece quem já tem perfil e "
                     "escreve o que cada um disse. Começa sozinho quando você para a gravação, roda neste computador com "
                     "prioridade baixa e espera a gravação seguinte terminar para não disputar o computador com ela.",
    JobKind.REPROCESS: "faz de novo o processamento de uma reunião (por exemplo, depois de trocar o idioma dela). A versão atual "
                       "continua valendo até o fim, e os nomes que você já confirmou passam para a nova. As etapas são as mesmas "
                       "de \"processar\".",
    JobKind.EMBED_RANGE: "vai criar um perfil de voz a partir de um trecho que você escolher. F6: ainda não roda.",
    JobKind.LLM: "a inteligência artificial escolhida nas Configurações (a LLM) escreve a partir da transcrição: quem é cada voz "
                 "ainda sem nome, a ata, o resumo executivo, o pré-resumo, o título e a transcrição revisada. Usa a internet e a "
                 "sua chave de acesso; o custo aparece em \"Detalhe\". Cada item faz só as tarefas pedidas.",
    JobKind.INDEX: "prepara a reunião para a busca do Histórico: primeiro os trechos (a busca por palavra já funciona), depois "
                   "os vetores (a busca pelo sentido). Todos aparecem numa linha só, no fim da tabela.",
}
JOB_STATUS_HELP: dict[str, str] = {
    JobStatus.PENDING: "na fila, esperando a vez.",
    JobStatus.RUNNING: "sendo feito agora.",
    JobStatus.PAUSED: "parado por enquanto: espera a gravação em curso terminar ou você retomar a fila, e continua sozinho.",
    JobStatus.DONE: "pronto.",
    JobStatus.ERROR: "parou por um problema; o motivo está em \"Detalhe\". \"Tentar de novo\" o põe de volta na fila.",
    JobStatus.CANCELLED: "você cancelou; \"Tentar de novo\" o põe de volta na fila.",
}
JOB_STEP_HELP: dict[str, str] = {
    JobStep.DOWNLOAD: "só na primeira vez ou ao trocar de modelo: baixa para este computador os modelos de voz e de transcrição.",
    JobStep.WAITING: "a fila espera a gravação terminar (ou você retomar a fila) e continua sozinha.",
    JobStep.DIARIZE: "descobre quantas pessoas falam no áudio da reunião e em que momentos cada uma fala.",
    JobStep.EMBED: "junto com a separação, tira a \"impressão\" de cada voz para comparar com os perfis.",
    JobStep.MATCH: "compara cada voz com os perfis salvos e põe o nome quando tem certeza; daqui em diante a revisão de falantes "
                   "já abre.",
    JobStep.TRANSCRIBE_R: "escreve o que foi dito no áudio da reunião (os outros participantes).",
    JobStep.TRANSCRIBE_L: "escreve o que foi dito no seu microfone.",
    JobStep.SEGMENTS: "junta o texto e as vozes em falas, com nome e horário.",
    JobStep.IDENTIFY: "sugere quem é cada voz ainda sem nome pelo que foi dito na conversa; você confirma na revisão.",
    JobStep.MINUTES: "escreve a ata: decisões, ações com dono e prazo, destaques, pendências e próximos passos.",
    JobStep.EXECUTIVE: "um resumo curto da ata, para quem não esteve na reunião.",
    JobStep.PRE_SUMMARY: "duas ou três frases com o assunto e o resultado, mostradas na lista de reuniões.",
    JobStep.TITLE: "sugere um título curto para a reunião.",
    JobStep.REVISE: "corrige pontuação, nomes e palavras mal entendidas, sem mudar o sentido.",
    JobStep.TEST: "só quando você testa a conexão nas Configurações: confere a chave e o provedor.",
    JobStep.INDEX_TEXT: "divide a reunião em trechos para a busca por palavra.",
    JobStep.INDEX_VECTORS: "calcula os vetores de cada trecho para a busca pelo sentido.",
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


class ChunkKind(StrEnum):
    """Tipo do trecho do índice (F5 item 1, `rag_chunks.kind`)."""
    TRANSCRIPT = "transcricao"     # 60 a 120 s de falas, cortado em fim de turno
    DECISION = "decisao"           # uma linha de lista da ata exibida, por seção
    HIGHLIGHT = "destaque"
    PENDING = "pendencia"
    NEXT_STEP = "proximo_passo"
    MINUTES = "ata"                # o contexto da ata e qualquer seção desconhecida
    ACTION = "acao"                # `action_items` vigentes, com dono e prazo
    EXECUTIVE = "executivo"        # o resumo executivo, sem tempo
    MEETING = "reuniao"            # título, data, participantes e duração: busca por título e marca de reunião indexada


class StoreRole(StrEnum):
    APP = "app"                    # main em escrita, migra, VACUUM de legado
    WORKER = "worker"              # main em escrita sem migrar; áudio só leitura com mmap (F2)
    READONLY = "readonly"          # tudo `mode=ro` (`--check`)
