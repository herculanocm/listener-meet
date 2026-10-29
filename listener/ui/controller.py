"""Controlador: estado (ocioso, gravando, pausado, salvando), warm-up, gravador, widget, bandeja, atalhos, mudo global.
Nada bloqueante na thread da interface (M-26): a parada roda numa QThread com estado "salvando…", o mudo do sistema é
lido por um poller próprio e os atalhos registram fora da thread do Qt. `emergency_stop` cobre logoff e desligamento (M-05).
F2: ao parar cria o job e lança o worker (subprocesso solto) quando couber; lease da pausa enquanto grava (Q7); polling da
fila a cada 2 s com job ativo e 30 s sem; aviso "pronta para revisão" e de reuniões processadas sem revisão.
F3: duas classes de worker (`launch_worker(light)`): o pesado só fora da gravação; o leve (jobs `llm`) a qualquer hora;
`_on_job_done` ramifica por `kind` (ata pronta x pronta para revisão).
F5: o leve roda também os `index` (índice da busca); a detecção de job concluído e a dica da bandeja os ignoram (a dica
acrescenta "· índice N"), e um `index` concluído nunca abre a revisão (F5A-05). Na abertura, a varredura do índice roda numa
`Task` interrompível e lança o leve se enfileirou algo (F5A-12, R5-02); a saída a interrompe e espera antes de fechar o
banco. As edições da revisão, das abas da LLM e do título em "Gravação salva" (`on_edited`) põem o `index` da reunião na
fila na hora, com uma espera durável de 30 s renovada a cada edição ("não antes de" no job, R5-07); "Concluir" e fechar a
revisão tiram a espera.
F5 L4: um player só no app (`self.player`), passado às janelas; a reunião aberta (`open_meeting`, uma janela por reunião)
substitui a janela "Transcrição" (`open_transcript` vira apelido de `open_meeting(mid, "transcricao")`), abre num ponto e
toca; o Histórico (`open_history`) pela bandeja e pelo atalho U-028 (`hotkeys.history`, com o cursor na busca e a janela
trazida à frente pela plataforma); o clique no balão abre a reunião do balão da tela (Q6: um balão sem reunião zera a
guardada, R7-06); os jobs `llm` e `index` concluídos recarregam a reunião aberta sem roubar o foco. Q9: gravando com a fonte
"sistema inteiro", o player pede confirmação antes de o som começar; abrir o warm-up pausa o som que já toca (R7-01), e o
gravador chama o gancho `_before_system_loopback` antes de abrir o loopback do sistema por qualquer caminho (a fonte pedida,
a troca automática quando o do app não abre, no começo, na troca de fonte e na reabertura), que pausa o player e espera a
confirmação do motor (R8-01); o aviso fica na barra do player e no log, sem balão, e sai quando a gravação não começa ou
acaba; retomar passa pela confirmação. A saída para o player e espera as threads dele, cancela o chat global e os das
reuniões, o vetor da consulta e o glossário, mesmo presos na rede, e espera todas as threads das tarefas antes de fechar o
banco (F5A-32, R7-03, R8-04).
F4 (ao vivo): ao gravar com o ao vivo ligado (caixa do warm-up ou `live.enabled`) e os modelos prontos, lança o processo
`--live` (`LiveClient`) e liga o desvio do gravador; Parar desliga o desvio e fecha o filho sem bloquear; Cancelar o mata
antes de apagar a reunião; uma queda tem uma nova tentativa por gravação. A interface lê `live_caption` (última legenda) e
`live_state` (estado) por sinais; com o ao vivo, a lease pede pausa ao pesado mesmo sem `pipeline.pause_on_record`.
F4 L4: o widget mostra o selo do ao vivo e, com `live.in_widget`, a última legenda; o clique nela (ou "Ao vivo…" na
bandeja) abre a janela "Ao vivo" (U-021), uma por vez, nunca sozinha (Q5); marcar uma legenda (U-022) ou um item do resumo
parcial (U-023) passa pelo `Recorder.mark` com o instante dele, uma vez por legenda ou item na gravação.
F4 L5, resumo parcial: com o ao vivo lançado, um timer confere a cada 15 s se `live.summary_interval_min` (relido a cada
disparo, vale em uso: CP8; 0 desliga o automático) venceu e, havendo legendas novas e nenhuma chamada em voo, roda
`llm.partial_task` numa `StreamTask`; "atualizar resumo agora" dispara já. Sem chave ou sem rede: um aviso por gravação e
nova tentativa no próximo intervalo; no teto do orçamento (Q6), um aviso único e o parcial para nessa reunião. Parar e
Cancelar param o timer; Cancelar e sair cancelam a chamada em voo (sair espera um pouco por ela); resultado de outra
gravação só atualiza a janela dela.
F5.1: o embedder local calcula no app com 1 thread (A-03); parar e cancelar a gravação rodam a varredura do índice, que
completa os vetores que o local adiou durante a gravação (A-07); terminado o preparo da busca local com o local ativo, o
app oferece "Reindexar agora/Depois" com os minutos (A-12), com as Configurações abertas ou não.
F5.3 L1: na abertura, o `llm.output_language = en` antigo vira `app.language = en` uma vez (A-03); a reunião nasce com o
idioma do warm-up (A-01); um processamento de troca de idioma não promete a ata adiada (A-06).
F5.3 L3 (U-048): todo `notify` vira o aviso do próprio app no canto da tela (`ui/toast.py`, `ui/notices.py`; com
`ui.toast_seconds` = 0, o balão de antes, e o clique nele segue abrindo a reunião do último balão), sempre na thread da
interface: de outra thread só emite um sinal enfileirado (o notificador de exceções roda na thread que falhou, A-10), com
trava de reentrada, limite por minuto e nada novo com `_quitting`. Um aviso por job, com o que ficou pronto: processamento e
LLM (lista e custo, U-020), só a identidade (revê a A-14 da F5.2), job com erro (por job e `finished_at`, semeado na
abertura, A-11), o fim e o erro do download de modelos e a resposta do chat com a janela fora de foco (A-12); índice,
glossário e resumo parcial não avisam. O clique faz a ação do aviso e traz o alvo à frente; job lançado põe o polling em 2 s.
F5.3 L4 (U-049, U-050): os dispositivos da gravação moram em `ui/recording_devices.py` (a vigia de 2 s, as trocas, a linha
de alerta e o painel do botão 🎧); aqui só o fio: o gravador chama `_release_player_for_mic` antes de reler o PortAudio
(o player solta a saída, "pausado para trocar o microfone", A-17) e a primeira leitura do poller do mudo depois de uma
troca de microfone é ignorada (A-18). O log da gravação leva o id do microfone, nunca o nome do aparelho (A-22).
F5.6 L3 (U-093): excluir reuniões e o espaço de volta em `ui/meeting_delete.py` (o Histórico pede; gravar interrompe)."""
from __future__ import annotations

import functools
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from contextlib import suppress

from PySide6.QtCore import QObject, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon, QWidget

from ..capture.clock import Clock, SessionClock
from ..capture.recorder import RecordConfig, Recorder
from ..config import language
from ..config.defaults import SPECS
from ..live.client import EXIT, LiveClient, live_models_dir, models_ready
from ..live.protocol import BYE, CAPTION, CPU, ERROR, READY, STATE, Hello
from ..llm import local_embed, partial_task
from ..llm.prompts.common import stamp
from ..log import get_logger
from ..model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, AppState, EventType, JobKind, JobStatus, JobStep, MeetingStatus, SourceKind
from ..store.jobs import HELD
from ..pipeline import llm_requests, review
from ..platform.base import log_id
from ..platform.common import process
from .glossary_link import LateGlossaryLink
from .history import HistoryWindow
from .live import LiveWindow, caption_key, caption_note, item_key, item_note
from .live_prepare import RAG, preparer, shutdown_preparer
from .meeting import MeetingWindow
from .meeting_delete import MeetingDeleter
from .notices import (CHAT_FAILED, CHAT_READY, CRASH_SHORT, HISTORY_FAILED, HISTORY_READY, IDENTITY_READY, Notice, NoticeAction,
                      NoticeKind, NoticeMeta, RateLimit, Target, download_text, job_error_text)
from .player import Player, PlayRequest, prewarm_audio
from .queue import QueueDialog
from .recording_devices import RecordingDevices
from .review import ReviewDialog
from .settings_min import SettingsDialog
from .sources import SourceTable
from .summary import SummaryDialog
from .tasks import StreamTask, Task, live_threads, release, run_stream, run_task, wait_before_close
from .toast import ToastManager
from .tray import TrayIcon
from .warmup import WarmupDialog
from .widget import MarkerPopup, RecordingWidget

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui")
HOTKEY_ACTIONS = ("start", "pause", "stop", "mark", "mute", "history")
SYSTEM_PLAY_TEXT = ("Você está gravando o som do sistema inteiro: o áudio do player entra na gravação. Tocar mesmo assim?")
PLAYER_PAUSED_WARMUP = "pausado para o warm-up da gravação"                                    # R7-01, na barra do player
PLAYER_PAUSED_SYSTEM = "pausado: a gravação capta o som do sistema inteiro (tocar pede confirmação)"
PLAYER_STOP_WAIT_S = 0.5             # R8-01: o gravador espera a pausa confirmada do player antes de abrir o loopback do sistema
PLAYER_PAUSED_MIC = "pausado para trocar o microfone"                                         # F5.3 (A-17)
PLAYER_RELEASE_WAIT_S = 1.0          # A-17: a troca de microfone espera o player soltar a saída antes de reler o PortAudio
TRAY_TIP = ("No Windows 11 o ícone da bandeja nasce no menu oculto (seta ao lado do relógio). Arraste-o para a barra "
            "para ver o estado da gravação.")
EMERGENCY_STOP_BUDGET_S = 10.0
MUTE_POLL_S = 2.0
LEASE_S = 600.0                      # pipeline.paused_until = agora + 10 min enquanto grava (Q7)
LEASE_RENEW_S = 60.0
QUEUE_POLL_ACTIVE_MS = 2000
QUEUE_POLL_IDLE_MS = 30000
WORKER_SPAWN_COOLDOWN_S = 60.0       # tempo para o worker recém-lançado carregar e escrever o primeiro heartbeat
REVIEW_STEPS = (str(JobStep.TRANSCRIBE_R), str(JobStep.TRANSCRIBE_L), str(JobStep.SEGMENTS))   # clusters já gravados (P1)
INDEX_KINDS: tuple[str, ...] = (str(JobKind.INDEX),)    # F5: fora da detecção de concluídos e da dica (seriam dezenas)
LIVE_MODELS_MISSING = "modelos do ao vivo ausentes: prepare nas Configurações (Ao vivo)"
LIVE_STOPPED_TEXT = "A legenda ao vivo parou; a gravação continua normalmente."
PREPARE_INTERRUPTED = "interrompido pela gravação: prepare de novo depois dela (continua de onde parou)"
LIVE_RETRIES = 1                     # F4: uma nova tentativa por gravação quando o filho cai
LIVE_CLOSE_GRACE_S = 10.0            # Parar: o filho fecha a janela em curso em até 10 s; depois, kill
LIVE_EOF_GRACE_S = 8.0               # ...e transcreve a fila até 8 s depois do EOF (sobra folga para o bye)
PARTIAL_TICK_MS = 15_000             # F4 L5: confere se o resumo parcial venceu (o intervalo é relido a cada disparo, CP8)
PARTIAL_QUIT_WAIT_MS = 2500          # sair do app: espera a chamada cancelada do parcial terminar (cancelamento a cada 0,2 s)
PREWARM_AUDIO_MS = 2000              # U-031: pré-aquece o áudio do player logo depois da bandeja, com o app ocioso
APP_LOCAL_THREADS = 1                # F5.1 (A-03): o e5 local no app (vetor da consulta); o worker leve usa 2
MEETING_TARGETS = (Target.MEETING, Target.MEETING_CHAT)   # F5.3: os avisos cujo balão (0 s) abre a reunião no clique (Q6)
ENDED_BADLY = (JobStatus.ERROR, JobStatus.CANCELLED)      # R-07: o fim que não recarrega a reunião inteira, só o cabeçalho
_Task = Task                          # nome antigo mantido para quem ainda o cita (scripts)


def _error_key(job: Any) -> tuple[int, str]:
    """A-11: um erro é o par (job, `finished_at`): o "Tentar de novo" que falha de novo é outro erro e avisa de novo."""
    return int(job["id"]), str(job.get("finished_at") or "")


class _MutePoller(QThread):
    """Lê o mudo do sistema a cada 2 s fora da thread do Qt (COM pode engasgar no dock/undock)."""
    changed = Signal(object)

    def __init__(self, platform: Platform):
        super().__init__(None)
        self.platform = platform
        self._wake = threading.Event()

    def interrupt(self) -> None:
        self.requestInterruption()
        self._wake.set()

    @override
    def run(self) -> None:
        mute = self.platform.mic_mute()
        while not self.isInterruptionRequested():
            try:
                self.changed.emit(mute.get())
            except Exception as e:
                log.debug("ler mudo do sistema: %s", e)
            self._wake.wait(MUTE_POLL_S)


class Controller(QObject):
    hotkey_pressed = Signal(str)
    live_caption = Signal(object)        # F4: dict da linha `caption` (channel, t_start_ms, t_end_ms, text, label, person_id...)
    live_state = Signal(object)          # F4: {"state": off | unavailable | starting | live | degraded | skipping | restarting |
                                         #      stopping | stopped | crashed, ...detalhes}; os nomes à parte, sem mudar
                                         #      `live_status`: names_ready | names_unavailable (com `reason`)
    partial_state = Signal(object)       # F4 L5: {"state": idle | running | done | error | stopped, "meeting_id", "message"}
    _live_message = Signal(object, object)   # (cliente, mensagem): da thread `live-read` para a da interface
    _notice_requested = Signal(str, object)  # F5.3 (A-10): (texto, NoticeMeta ou None) de outra thread para a da interface

    def __init__(self, app: QApplication, platform: Platform, store: Store, first_run: bool = False,
                 clock_factory: Callable[[], Clock] | None = None):
        super().__init__()
        self.app = app
        self.platform = platform
        self.store = store
        self.first_run = first_run
        self.clock_factory = clock_factory            # os testes injetam um relógio manual
        self.state = AppState.IDLE
        self.recorder: Recorder | None = None
        self.widget: RecordingWidget | None = None
        self.tray: TrayIcon | None = None
        self.hotkey_errors: dict[str, str] = {}
        self._warmup_open = False
        self._marker_open = False
        self._quitting = False
        self._mute_poller: _MutePoller | None = None
        self._mute_skip = 0                           # F5.3 (A-18): leituras do poller do mudo a ignorar depois de uma troca
        self.devices: RecordingDevices | None = None  # F5.3 L4: os dispositivos da gravação em curso
        self.hotkey_pressed.connect(self._on_hotkey)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        # F2: fila de processamento
        self.now: Callable[[], datetime] = lambda: datetime.now().astimezone()
        self.lease_renew_s = LEASE_RENEW_S
        self._lease_at: float | None = None
        self._worker_spawned_at: dict[bool, float | None] = {False: None, True: None}     # por classe: pesado, leve (F3)
        self._launch_reason: dict[bool, str | None] = {False: None, True: None}          # o último "não lançado" logado
        self._seen_done: set[int] = set()
        self._seen_errors: set[tuple[int, str]] = set()      # F5.3 (A-11): (job, finished_at) já vistos com erro
        self._seen_ended: set[tuple[int, str]] = set()       # R-07: (job, finished_at) em erro ou cancelado já relidos
        self._no_llm_warned = False
        self._review_offered: set[int] = set()
        self._reviews: dict[int, ReviewDialog] = {}
        self._review_sigs: dict[int, tuple[int, ...]] = {}
        self._queue_dialog: QueueDialog | None = None
        self._queue_timer = QTimer(self)
        self._queue_timer.setInterval(QUEUE_POLL_IDLE_MS)
        self._queue_timer.timeout.connect(self._poll_queue)
        # F4: ao vivo
        self.live: LiveClient | None = None
        self.live_status = "off"
        self.live_names_status = ""
        self.live_last_caption: dict[str, Any] | None = None
        self._live_hello: Hello | None = None
        self._live_retries = 0
        self._live_error: dict[str, Any] | None = None
        self._live_models_warned = False
        self._live_message.connect(self._on_live_message, Qt.ConnectionType.QueuedConnection)
        # F4 L4: widget, janela "Ao vivo" e marcadores das legendas e do parcial (chaves por gravação)
        self.live_window: LiveWindow | None = None
        self._marked_captions: set[tuple[Any, ...]] = set()
        self._marked_items: set[tuple[str, str]] = set()
        self.live_caption.connect(self._on_caption_ui)
        self.live_state.connect(self._on_live_state_ui)
        # F4 L5: resumo parcial (uma chamada por vez; `_partial_tasks` guarda as em voo, inclusive de gravações anteriores)
        self.monotonic: Callable[[], float] = time.monotonic
        self.partial_status: dict[str, Any] = {"state": "idle", "meeting_id": None}
        self._partial_meeting: int | None = None
        self._partial_auto = False                       # F5.6 (A-21, P-06): o automático desta gravação (`live.partial`)
        self._partial_last_at = 0.0
        self._partial_backlog = False                    # o último parcial deixou legendas para depois (corte da entrada)
        self._partial_last_caption = 0
        self._partial_stopped: str | None = None
        self._partial_warned = False
        self._partial_task: StreamTask | None = None
        self._partial_tasks: set[StreamTask] = set()
        self._partial_timer = QTimer(self)
        self._partial_timer.setInterval(PARTIAL_TICK_MS)
        self._partial_timer.timeout.connect(self._partial_tick)
        # F5: índice da busca (reindexação por edição com espera e varredura da abertura)
        self.index_delay_s: float | None = None             # espera das edições; None = `rag.index.EDIT_DELAY_S` (30 s)
        self._index_edited: set[int] = set()                 # reuniões com edição esperando nesta sessão (fechar a revisão)
        self._sweeps: set[Task] = set()
        self._sweep_stop = threading.Event()                 # a saída interrompe a varredura entre reuniões (R5-02)
        # F5 L4: player único, reuniões abertas, Histórico e a reunião da última notificação (Q6)
        self.player = Player(store, parent=self)
        self.player.guard = self._confirm_play
        self.player.before_play = platform.refresh_audio_devices          # U-043: o "Tocar" vai para o fone que chegou
        self._meetings: dict[int, MeetingWindow] = {}
        self._meeting_index: dict[int, int] = {}              # último `index` concluído visto por reunião aberta
        self._history: HistoryWindow | None = None
        self._notified_meeting: int | None = None
        self._windows_stopped = False
        # F5.1: as Configurações abertas (pai da pergunta "Reindexar agora/Depois" do fim do preparo da busca local, A-12)
        self._settings_dialog: SettingsDialog | None = None
        self._preparer_connected = False
        # F5.3 L3: o aviso do app (`notify`), só na thread da interface (A-10)
        self._ui_thread = threading.get_ident()
        self._notice_meta: NoticeMeta | None = None          # tipo, texto curto e ação do próximo `notify` (`_notify_with`)
        self._notifying = False                              # trava de reentrada
        self._notice_limit = RateLimit()                     # A-10: o relógio (`clock`) os testes trocam
        self.notices: deque[Notice] = deque(maxlen=100)      # instrumentação dos testes: os avisos mostrados
        self.toasts = ToastManager(platform, on_action=self._on_notice_action, recording=self._recording_now,
                                   anchor=self._toast_anchor, parent=self)
        self._notice_requested.connect(self._on_notice_requested, Qt.ConnectionType.QueuedConnection)

    @functools.cached_property
    def deleter(self) -> MeetingDeleter:
        """F5.6 L3: excluir pelo Histórico e o espaço de volta (`ui/meeting_delete.py`), criado no primeiro uso."""
        return MeetingDeleter(self)

    # ---------------------------------------------------------------- início
    def start(self) -> None:
        self._adopt_legacy_language()
        interrupted = self.store.mark_interrupted()
        orphans = self.store.orphan_report()
        missing = self.store.missing_audio_files()
        if orphans:
            log.warning("pedaços de áudio sem reunião (não apagados, CP3): %s", orphans)
        if missing:
            log.error("arquivos de áudio citados por reuniões e ausentes da pasta: %s", missing)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = TrayIcon(self)
            self.tray.show()
            if interrupted:
                self.notify(f"{interrupted} gravação(ões) interrompida(s) na última execução; os pedaços foram preservados.")
            if missing:
                self._notify_as(NoticeKind.ERROR, "Arquivo(s) de áudio ausente(s) na pasta do banco: " + ", ".join(missing))
            if self.first_run:
                self.notify(TRAY_TIP)
        self._run_task(self.register_hotkeys, self._on_hotkeys_registered)
        recent = self.store.jobs.list_jobs(limit=500, exclude_kinds=INDEX_KINDS)
        self._seen_done = {int(j["id"]) for j in recent if j["status"] == JobStatus.DONE}
        self._seen_errors = {_error_key(j) for j in recent if j["status"] == JobStatus.ERROR}    # A-11: os antigos não avisam
        self._seen_ended = {_error_key(j) for j in recent if j["status"] in ENDED_BADLY}
        waiting = self.store.meetings.list_by_status(MeetingStatus.PROCESSED)
        if waiting:
            self.notify(f"{len(waiting)} reunião(ões) processada(s) aguardando revisão de falantes.")
        self._clear_lease()                                   # um app que caiu gravando não pode deixar a fila presa
        self._scrub_orphan_people()
        self._enqueue_leftovers()
        local_embed.configure(APP_LOCAL_THREADS)              # F5.1 (A-03): a sessão do e5 no app abre só com o Histórico
        preparer().changed.connect(self._on_preparer_changed)
        self._preparer_connected = True
        self._start_sweep()
        self.deleter.resume_space()                           # F5.6 (A-39): o espaço que ficou pendente, na abertura ociosa
        self._poll_queue()
        self._queue_timer.start()
        QTimer.singleShot(PREWARM_AUDIO_MS, self, self._prewarm_audio)
        log.info("app iniciado; %d gravação(ões) interrompida(s); %d reunião(ões) sem revisão", interrupted, len(waiting))

    def _adopt_legacy_language(self) -> None:
        """A-03: o idioma das saídas da F3 em inglês vira o idioma padrão, uma vez; falhar não impede a abertura (fica o
        padrão pt-BR)."""
        try:
            language.adopt_legacy(self.store)
        except Exception as e:
            log.warning("idioma padrão: não adotei o antigo llm.output_language: %s", e)

    def _scrub_orphan_people(self) -> None:
        """U-041 (A-08): vínculo com pessoa apagada que um processo gravou depois (o ao vivo, o worker) sai na abertura."""
        try:
            n = self.store.people.scrub_orphans()
        except Exception as e:
            log.warning("vínculos com pessoas apagadas não limpos na abertura: %s", e)
            return
        if n:
            log.info("abertura: %d vínculo(s) com pessoa apagada limpo(s)", n)

    def _prewarm_audio(self) -> None:
        """U-031: o primeiro "Tocar" sai em ~0,5 s em vez de 2 a 5 s (`player.prewarm_audio`); nada depois da saída."""
        if not self._quitting:
            prewarm_audio()

    # ---------------------------------------------------------------- avisos (F5.3 L3, U-048)
    def notify(self, message: str) -> None:
        """O aviso ao usuário: o do próprio app, no canto da tela (`ui/toast.py`), ou, com `ui.toast_seconds` = 0, o balão da
        bandeja de antes. De qualquer thread (A-10): fora da thread da interface só emite um sinal enfileirado (o notificador
        de exceções roda na thread que falhou). Tipo, texto curto e ação chegam por `_notify_with` (o `notify` segue com um
        argumento: os testes o trocam por uma lista). Saindo, em reentrada ou acima do limite por minuto, fica só no log."""
        if threading.get_ident() != self._ui_thread:
            self._notice_requested.emit(message, None)
            return
        meta, self._notice_meta = self._notice_meta or NoticeMeta(), None
        if self._quitting or self._notifying or not self._notice_limit.allow():
            why = "saindo" if self._quitting else ("reentrada" if self._notifying else "limite por minuto")
            log.info("aviso fora da tela (%s)%s", why, meta.log_suffix(message))
            return
        self._notifying = True
        try:
            self._deliver(meta.notice(message), meta)
        finally:
            self._notifying = False

    def notify_error(self, message: str) -> None:
        """Um aviso de erro, de qualquer thread (A-10)."""
        self._notify_any_thread(message, NoticeMeta(NoticeKind.ERROR))

    def notify_crash(self, message: str) -> None:
        """O notificador de exceções (`log.set_crash_notifier`), chamado na thread que falhou (A-10): um aviso de erro que,
        gravando sem a exclusão da captura, diz só que houve um erro (os detalhes ficam no log)."""
        self._notify_any_thread(message, NoticeMeta(NoticeKind.ERROR, short_text=CRASH_SHORT))

    def _notify_any_thread(self, message: str, meta: NoticeMeta) -> None:
        if threading.get_ident() != self._ui_thread:
            self._notice_requested.emit(message, meta)
        else:
            self._notify_with(message, meta)

    def _on_notice_requested(self, message: str, meta: object) -> None:
        """O aviso pedido de outra thread, agora na da interface (A-10)."""
        self._notify_with(message, meta if isinstance(meta, NoticeMeta) else None)

    def _notify_with(self, message: str, meta: NoticeMeta | None) -> None:
        """Na thread da interface: o `notify` com o tipo, o texto curto e a ação do aviso."""
        self._notice_meta = meta
        try:
            self.notify(message)
        finally:
            self._notice_meta = None

    def _notify_as(self, kind: NoticeKind, message: str, action: NoticeAction | None = None) -> None:
        self._notify_with(message, NoticeMeta(kind, action))

    def _notify_meeting(self, meeting_id: int, text: str, kind: NoticeKind = NoticeKind.READY,
                        action: NoticeAction | None = None) -> None:
        """Aviso sobre uma reunião ("<título>: <texto>"): o clique abre a reunião (Q6) ou faz a `action`; o título vai só à
        tela, nunca ao log nem ao texto curto (R-03, A-13)."""
        mid = int(meeting_id)
        m = self.store.get_meeting(mid) or {}
        self._notify_with(f"{m.get('title') or f'reunião {mid}'}: {text}",
                          NoticeMeta(kind, action or NoticeAction(Target.MEETING, mid), (mid, text)))

    def _deliver(self, notice: Notice, meta: NoticeMeta) -> None:
        """Na tela ou, com 0 s, no balão (aí o clique abre a reunião do último balão: Q6; um balão sem reunião a esquece,
        R7-06). O log diz o que o usuário viu; o de uma reunião leva o id e o texto, nunca o título (pode ter nome de
        terceiro, R-03)."""
        seconds = self._toast_seconds()
        log.info("aviso %s (%s)%s", "na tela" if seconds > 0 else "na bandeja", notice.kind, meta.log_suffix(notice.text))
        self.notices.append(notice)
        if seconds > 0:
            self.toasts.show(notice, seconds)
            return
        action = notice.action
        self._notified_meeting = action.meeting_id if action is not None and action.target in MEETING_TARGETS else None
        if self.tray is not None:
            self.tray.showMessage("listener-meet", notice.text)

    def _toast_seconds(self) -> int:
        """`ui.toast_seconds` relido a cada aviso (CP8: vale já); banco ocupado: o padrão."""
        try:
            return int(self.store.get(SPECS["ui.toast_seconds"]))
        except Exception as e:
            log.debug("ui.toast_seconds não lido: %s", e)
            return int(SPECS["ui.toast_seconds"].default)

    def _recording_now(self) -> bool:
        return self.state in (AppState.RECORDING, AppState.PAUSED, AppState.SAVING)

    def _toast_anchor(self) -> tuple[int, int, int, int] | None:
        """Gravando, o retângulo do widget de gravação (A-14): o aviso vai para a tela dele e não o cobre."""
        w = self.widget
        if w is None or not w.isVisible() or not self._recording_now():
            return None
        g = w.frameGeometry()
        return g.x(), g.y(), g.width(), g.height()

    def _on_notice_action(self, action: NoticeAction) -> None:
        """O clique no aviso (A-12, A-14): abre o alvo e o traz à frente pela plataforma (o app está em segundo plano)."""
        if self._quitting:
            return
        log.info("clique no aviso: %s%s", action.target, f" {action.meeting_id}" if action.meeting_id is not None else "")
        match action.target:
            case Target.MEETING | Target.MEETING_CHAT:
                mid = action.meeting_id
                if mid is None or self.store.get_meeting(mid) is None:
                    self.notify("Esta reunião não existe mais.")
                    return
                self.open_meeting(mid, "chat" if action.target == Target.MEETING_CHAT else None, focus=True)
            case Target.QUEUE:
                self.open_queue()
                self._bring_to_front(self._queue_dialog)
            case Target.HISTORY:
                self._bring_to_front(self.open_history())
            case Target.SETTINGS:
                self.open_settings(focus=True)

    def _bring_to_front(self, win: QWidget | None) -> None:
        if win is not None:
            with suppress(Exception):
                self.platform.bring_to_front(int(win.winId()))

    def _chat_answered(self, meeting_id: int, ok: bool) -> None:
        """A-12: a resposta do chat da reunião chegou com a janela dela fora de foco; o clique abre a aba Chat."""
        self._notify_meeting(meeting_id, CHAT_READY if ok else CHAT_FAILED, NoticeKind.READY if ok else NoticeKind.ERROR,
                             NoticeAction(Target.MEETING_CHAT, int(meeting_id)))

    def _history_answered(self, ok: bool) -> None:
        """A-12: a do chat sobre todas as reuniões chegou com o Histórico fora de foco; o clique abre o Histórico."""
        self._notify_as(NoticeKind.READY if ok else NoticeKind.ERROR, HISTORY_READY if ok else HISTORY_FAILED,
                        NoticeAction(Target.HISTORY))

    def open_last_notified(self) -> MeetingWindow | None:
        """`messageClicked`: abre a reunião do último balão, quando ele era sobre uma reunião (Q6, R7-06). Só no caminho do
        balão (`ui.toast_seconds` = 0): o aviso do app faz a própria ação no clique (F5.3)."""
        mid = self._notified_meeting
        if mid is None or self._quitting or self.store.get_meeting(mid) is None:
            return None
        return self.open_meeting(mid, focus=True)

    # ---------------------------------------------------------------- índice da busca (F5)
    def _start_sweep(self) -> None:
        """Varredura do índice numa `Task` (F5A-12): reuniões processadas sem trechos, as de `index` com erro e, com o
        embedder pronto e o modelo confirmado, as que têm trecho sem vetor dele; com algo na fila, o worker leve sobe. A
        saída pede a interrupção (`_sweep_stop`) e espera a `Task` antes de fechar o banco (R5-02)."""
        from ..rag import index as rag_index

        self._sweep_stop.clear()
        task = run_task(lambda: rag_index.sweep(self.store, should_stop=self._sweep_stop.is_set), self.store, self._on_sweep)
        self._sweeps.add(task)
        task.finished.connect(functools.partial(self._sweeps.discard, task))   # o `release` destrói a QThread depois

    def _stop_sweeps(self) -> None:
        """Interrompe a varredura e espera a `Task` terminar: fechar o banco com a QThread rodando derruba o processo no
        Windows (F3.1)."""
        self._sweep_stop.set()
        for task in list(self._sweeps):
            with suppress(RuntimeError):                 # já destruída pelo `release`
                task.wait()

    def _on_sweep(self, result: Any) -> None:
        if self._quitting:
            return
        if isinstance(result, BaseException):
            log.warning("varredura do índice falhou: %s", result)
        elif result:
            self.launch_worker(light=True)

    def _on_preparer_changed(self, state: Any) -> None:
        """A-12: o preparo da busca local terminou; com o local ativo e pronto e algo a indexar, "Reindexar agora/Depois" com os
        minutos (Q3: a varredura não completa sozinha). A pergunta sai no próximo giro do laço: as Configurações abertas já
        mostram "pronta" antes dela (e ela sai sobre elas). F5.3 (A-11): o fim e o erro do download, do ao vivo e da busca,
        avisam com o clique nas Configurações; a interrupção pela gravação não (o começo da gravação já avisou)."""
        st = dict(state or {})
        if self._quitting:
            return
        found = download_text(st) if st.get("message") != PREPARE_INTERRUPTED else None
        if found is not None:
            kind, text = found
            self._notify_as(kind, text, NoticeAction(Target.SETTINGS))
        if st.get("what") == RAG and st.get("state") == "done":
            QTimer.singleShot(0, self, self._offer_local_reindex)

    def _offer_local_reindex(self) -> None:
        if self._quitting:
            return
        from .settings_min import offer_local_reindex

        try:
            offer_local_reindex(self.store, self._settings_dialog, self.launch_worker)
        except Exception as e:  # a pergunta nunca derruba o app; "Reindexar tudo…" segue nas Configurações
            log.warning("não ofereci reindexar na busca local: %s", e)

    def on_edited(self, meeting_id: int, reason: str = "edit", *, now: bool = False) -> None:
        """Uma edição que muda a busca (falantes, título, ata, executivo, ações): o `index` da reunião entra na fila já, com
        "não antes de" agora + 30 s renovado a cada edição (espera durável no job: sobrevive ao app fechar, R5-07); `now` tira
        a espera ("Concluir", fechar a revisão). O worker leve sobe agora e espera o job vencer. Com os vetores automáticos
        desligados o `index` entra do mesmo jeito: os trechos e o FTS são locais e grátis."""
        from ..rag.index import EDIT_DELAY_S, enqueue_index

        delay = 0.0 if now else (EDIT_DELAY_S if self.index_delay_s is None else self.index_delay_s)
        try:
            enqueue_index(self.store, meeting_id, reason, delay_s=delay)
        except Exception as e:  # a busca nunca atrapalha a edição
            log.warning("reunião %s: não enfileirei o índice da busca: %s", meeting_id, e)
            return
        if now:
            self._index_edited.discard(meeting_id)
        else:
            self._index_edited.add(meeting_id)
        self.launch_worker(light=True)

    def _run_task(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        run_task(fn, self.store, on_done)

    def register_hotkeys(self) -> dict[str, str]:
        """Registra os atalhos (pode rodar fora da thread do Qt; F5: mais o do Histórico, U-028); devolve combo -> motivo dos que
        falharam (A13)."""
        hk = self.platform.hotkeys()
        hk.unregister_all()
        errors: dict[str, str] = {}
        for name in HOTKEY_ACTIONS:
            combo = self.store.get_setting(f"hotkeys.{name}")
            if not combo:
                continue
            if not hk.register(combo, functools.partial(self.hotkey_pressed.emit, name)):
                errors[combo] = hk.errors().get(combo, "falhou")
        self.hotkey_errors = errors
        if errors:
            log.warning("atalhos não registrados: %s", errors)
        return errors

    def _on_hotkeys_registered(self, result: Any) -> None:
        if isinstance(result, BaseException):
            self._notify_as(NoticeKind.ERROR, f"Atalhos não registraram: {result}")
        elif result:
            self._notify_as(NoticeKind.ERROR, "Atalhos que não registraram:\n" + "\n".join(f"{k}: {v}" for k, v in result.items()))

    def _on_hotkey(self, name: str) -> None:
        log.debug("atalho %s", name)
        match name:
            case "start":
                self.open_warmup()
            case "pause":
                self.toggle_pause()
            case "stop":
                self.stop_recording()
            case "mark":
                self.mark()
            case "mute":
                self.toggle_mute()
            case "history":
                self.open_history(focus_search=True)

    # ---------------------------------------------------------------- gravação
    def open_warmup(self) -> None:
        if self.state != AppState.IDLE or self._warmup_open:
            log.info("warm-up não abriu: %s", "já está aberto" if self._warmup_open else f"o app está em {self.state.name}")
            self.show_widget()
            return
        self._warmup_open = True
        log.info("warm-up aberto")
        self._pause_player(PLAYER_PAUSED_WARMUP)          # o teste e os medidores do warm-up não ouvem o player (R7-01)
        try:
            link = LateGlossaryLink(self.store)
            dlg = WarmupDialog(self.platform, self.store, on_settings=self.open_settings, on_late_call=link.add)
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            cfg, fields = dlg.result_config() if accepted else (None, {})
            log.info("warm-up fechado: %s", "Gravar" if accepted else "cancelado")
            # destruído aqui, na thread da interface: se ficasse para o coletor de lixo, uma thread do gravador poderia
            # destruí-lo (segfault do Qt); a resposta tardia do glossário não toca em widgets (F3.1, revisão)
            dlg.deleteLater()
            if cfg is not None and self.start_recording(cfg, fields) and self.recorder is not None:
                link.bind(self.recorder.meeting_id)
        finally:
            self._warmup_open = False

    def start_recording(self, cfg: RecordConfig, fields: dict[str, Any]) -> bool:
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(None, "listener-meet", "Configuração de captura inválida:\n" + "\n".join(errors))
            return False
        lang = fields.get("language") or language.default_language(self.store)     # F5.3 (A-01): o combo do warm-up
        meeting_id = self.store.create_meeting(fields.get("title"), fields.get("platform"), fields.get("preset_id"),
                                               fields.get("participants"), fields.get("is_in_room", False),
                                               glossary=fields.get("glossary"), expected_people_json=fields.get("expected_people_json"),
                                               language=lang)
        for call_id in fields.get("glossary_call_ids") or ():               # as chamadas do glossário nascem sem reunião (F3A-11)
            self.store.llm_calls.set_meeting(int(call_id), meeting_id)
        log.info("gravação %s iniciada: mic=%s fonte=%s idioma=%s", meeting_id,       # A-22: o id, nunca o nome do aparelho
                 log_id(cfg.mic_device.id) if cfg.mic_device else "padrão (acompanha)", cfg.source.log_text(),
                 language.whisper_code(lang))
        mute_ok = self.platform.mic_mute().set_device(cfg.mic_device)
        self.platform.keep_awake(True)
        # `_before_system_loopback` vem antes de todo loopback do sistema inteiro (o pedido, a troca automática; R8-01)
        rec = Recorder(self.platform, self.store, meeting_id, cfg, clock=self.clock_factory() if self.clock_factory else None,
                       before_system_loopback=self._before_system_loopback, before_reread=self._release_player_for_mic)
        self.deleter.interrupt_space()                   # F5.6 (A-39): liberar espaço nunca disputa o arquivo com a gravação
        try:
            rec.start()
        except Exception as e:
            log.exception("gravação %s não iniciou", meeting_id)
            self.player.clear_notice()                   # a gravação não começou: o aviso da pausa sai (o som segue pausado)
            self.platform.keep_awake(False)
            self.store.delete_meeting(meeting_id)
            QMessageBox.critical(None, "listener-meet", f"A gravação não iniciou: {e}")
            return False
        self.recorder = rec
        self.state = AppState.RECORDING
        self.toasts.refresh_privacy()                    # R-10: o aviso à vista perde o título se a captura não o exclui
        self._marked_captions = set()                    # novos conjuntos: a janela da gravação anterior fica com os dela
        self._marked_items = set()
        prep = preparer()
        what = "da busca local" if prep.what == RAG else "dos modelos do ao vivo"
        if prep.running and prep.cancel(PREPARE_INTERRUPTED):   # F4 item 10: o download não disputa a rede com a reunião
            self.notify(f"Download {what} {PREPARE_INTERRUPTED}.")
        try:
            self._start_live(rec, fields)
        except Exception:                                # o ao vivo nunca impede a gravação
            log.exception("gravação %s segue sem ao vivo", meeting_id)
            self._set_live_status("unavailable", reason="erro")
        self._start_partial(meeting_id)
        live_line = self.live is not None and bool(self.store.get(SPECS["live.in_widget"]))
        self.widget = RecordingWidget(size=self._saved_widget_size(), pos=self._saved_widget_pos(), live_line=live_line,
                                      shortcuts=self._widget_shortcuts())
        w = self.widget
        w.height_changed.connect(self.toasts.relayout)    # F5.4 L2 (A-16): o widget cresceu para cima; os avisos sobem junto
        w.live_clicked.connect(self.open_live_window)
        w.set_live_state(self.live_status, self._live_badge_tip())
        w.pause_clicked.connect(self.toggle_pause)
        w.mark_clicked.connect(self.mark)
        w.mute_clicked.connect(self.toggle_mute)
        w.stop_clicked.connect(self.stop_recording)
        w.cancel_clicked.connect(self.cancel_recording)
        self.devices = RecordingDevices(self.platform, rec, panel_parent=w, on_mic_switched=self._mic_switched,
                                        open_other_app=self.switch_source, parent=self)
        w.devices_clicked.connect(self.devices.open_panel)
        w.alert_clicked.connect(self.devices.on_alert)
        self.devices.start()
        w.settings_requested.connect(self.open_settings)
        w.moved.connect(self._save_widget_pos)          # R-06: método, não lambda com `self` (o ciclo pelo C++)
        w.show()
        if self.tray:
            self.tray.set_state(AppState.RECORDING)
        self._timer.start()
        self._start_mute_poller()
        self._write_lease(rec)
        lines, plain = list(rec.warnings), list(rec.warnings)
        if not mute_ok and cfg.mic_device is not None:                  # R-05: o nome do aparelho só na tela (A-22)
            tail = "o mudo global vai no microfone padrão do sistema."
            lines.append(f"Não achei o endpoint de '{cfg.mic_device.name}': {tail}")
            plain.append(f"Não achei o endpoint {log_id(cfg.mic_device.id)}: {tail}")
        if lines:
            self._notify_with("\n".join(lines), NoticeMeta(short_text="\n".join(plain), log_text="\n".join(plain)))
        return True

    def show_widget(self) -> None:
        if self.widget:
            self.widget.show()
            self.widget.raise_()

    def toggle_pause(self) -> None:
        if not self.recorder or self.state == AppState.SAVING:
            return
        if self.recorder.paused:
            self.recorder.resume()
            self.state = AppState.RECORDING
        else:
            self.recorder.pause()
            self.state = AppState.PAUSED
        if self.tray:
            self.tray.set_state(self.state)

    def mark(self) -> None:
        if not self.recorder or self.state == AppState.SAVING or self._marker_open:
            return
        self._marker_open = True
        try:
            t_rel = self.recorder.elapsed()
            popup = MarkerPopup(t_rel, self.platform, self.widget)
            note = popup.note() if popup.exec() == QDialog.DialogCode.Accepted else None
            if note is not None and self.recorder is not None:
                self.recorder.mark(note)
        finally:
            self._marker_open = False

    def toggle_mute(self) -> None:
        if not self.recorder or self.state == AppState.SAVING:
            return
        muted = not self.recorder.muted
        ok = self.platform.mic_mute().set(muted)
        self.recorder.set_muted(muted, source="app")
        if not ok:
            self._notify_as(NoticeKind.ERROR, "Não consegui mutar o microfone no sistema; gravando silêncio mesmo assim.")

    def switch_source(self) -> None:
        if not self.recorder or self.state == AppState.SAVING:
            return
        from PySide6.QtWidgets import QDialogButtonBox, QVBoxLayout

        dlg = QDialog(self.widget)
        dlg.setWindowTitle("Trocar a fonte do canal R")
        table = SourceTable(self.platform, dlg)
        table.start()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(table)
        lay.addWidget(buttons)
        dlg.resize(560, 320)
        if dlg.exec() == QDialog.DialogCode.Accepted and self.recorder is not None:
            self.recorder.switch_source(table.selected_spec())   # o sistema inteiro pausa o player pelo gancho (R8-01)
        table.stop()
        dlg.deleteLater()

    def stop_recording(self) -> None:
        """Parada assíncrona: o widget mostra "salvando…" enquanto a thread de trabalho esvazia a fila."""
        rec = self.recorder
        if rec is None or self.state == AppState.SAVING:
            return
        self.state = AppState.SAVING
        if self.tray:
            self.tray.set_state(AppState.SAVING)
        self._stop_mute_poller()
        self._stop_devices()                             # F5.3: nenhuma troca de aparelho enquanto salva
        self._stop_live()
        self._stop_partial()
        self._end_live_window(rec.meeting_id, "gravação parada: só leitura")
        self._run_task(rec.stop, lambda result: self._on_stopped(rec, result))

    def _on_stopped(self, rec: Recorder, result: Any) -> None:
        self._timer.stop()
        if isinstance(result, BaseException):
            log.error("parada da gravação %s falhou: %s", rec.meeting_id, result)
            stats = rec.stats()
            self._notify_as(NoticeKind.ERROR, f"A gravação terminou com erro: {result}")
        else:
            stats = result
        if rec.muted:
            self.platform.mic_mute().set(False)
        self._finish(rec)
        self._clear_lease()
        self._enqueue_processing(rec.meeting_id)
        if not self._quitting:
            self._start_sweep()                          # F5.1 (A-07): os vetores que o local adiou durante a gravação
            SummaryDialog(self.store, rec.meeting_id, stats, on_edited=self.on_edited).exec()

    def cancel_recording(self) -> None:
        if not self.recorder or self.state == AppState.SAVING:
            return
        r = QMessageBox.question(self.widget, "Cancelar gravação", "Descartar esta gravação? Isso apaga o áudio gravado até agora.")
        if r != QMessageBox.StandardButton.Yes or not self.recorder:
            return
        rec = self.recorder
        self._timer.stop()
        self._stop_mute_poller()
        self._terminate_live()                           # antes de apagar: o filho não pode gravar numa reunião apagada
        self._stop_partial(cancel=True)                  # a resposta que vier depois não grava (a reunião some)
        self._end_live_window(rec.meeting_id, "gravação cancelada (apagada): só leitura")
        rec.cancel()
        if rec.muted:
            self.platform.mic_mute().set(False)
        log.info("gravação %s cancelada pelo usuário", rec.meeting_id)
        self._finish(rec)
        self._clear_lease()
        if not self._quitting:
            self._start_sweep()                          # F5.1 (A-07): os vetores que o local adiou durante a gravação

    def emergency_stop(self) -> None:
        """Logoff, desligamento ou `quit()` por fora: para e salva sem diálogo (M-05); o resumo fica para a próxima vez."""
        rec = self.recorder
        if rec is not None and not rec.stopped:
            log.warning("encerramento durante a gravação %s: parando sem diálogo", rec.meeting_id)
            rec.cfg.stop_timeout_s = min(rec.cfg.stop_timeout_s, EMERGENCY_STOP_BUDGET_S)
            self._timer.stop()
            self._stop_mute_poller()
            self._stop_devices()
            self._stop_live(detach=True)                 # EOF: o filho termina sozinho, mesmo com o app saindo
            self._stop_partial()
            try:
                rec.stop()
            except Exception:
                log.exception("parada de emergência da gravação %s falhou", rec.meeting_id)
            self.platform.keep_awake(False)
            self.recorder = None
            self.state = AppState.IDLE
            with suppress(Exception):
                self._clear_lease()
                self._enqueue_processing(rec.meeting_id)
        self._quitting = True
        self.toasts.shutdown()                           # F5.3: os avisos da tela e os da fila somem; nada novo aparece
        self._queue_timer.stop()
        try:
            self.platform.hotkeys().stop()
        except Exception as e:
            log.debug("parar atalhos: %s", e)
        if self._preparer_connected:
            self._preparer_connected = False
            with suppress(RuntimeError, TypeError):
                preparer().changed.disconnect(self._on_preparer_changed)
        shutdown_preparer()                              # F4: o download dos modelos não segue órfão
        self._wait_partials()                            # a chamada do parcial em voo é cancelada antes de o banco fechar
        self._stop_sweeps()                              # F5: a varredura usa o banco numa QThread; termina antes do close
        self._stop_windows()                             # F5 L4: player, chat global e buscas do Histórico, chats das reuniões
        if self.live_window is not None:                 # guarda a posição antes de o banco fechar
            with suppress(Exception):
                self.live_window.close()
        self.store.close()

    def _stop_windows(self) -> None:
        """Saída (F5A-32): para o player e espera as threads dele, cancela o chat global e o das reuniões e o vetor da consulta
        (presos na rede também, R7-03), cancela as `Task`s que sabem parar (o glossário do warm-up) e espera todas as threads
        vivas das tarefas, passado o prazo sem teto (R8-04: testar conexão e a lista de modelos têm 30 s no adaptador):
        fechar o banco com uma QThread viva derruba o processo; as janelas guardam a posição antes de o banco fechar.
        Idempotente (o `quit` e o `emergency_stop`)."""
        if self._windows_stopped:
            return
        self._windows_stopped = True
        with suppress(Exception):
            self.player.shutdown()
        if self._history is not None:
            with suppress(Exception):
                self._history.page.shutdown()
                self._history.save_geometry()
        for win in list(self._meetings.values()):
            with suppress(Exception):
                win.shutdown()
                win.save_geometry()
        # R8-04: as `Task`s comuns também, não só as `StreamTask`s (o chat de uma reunião já fechada, o glossário do warm-up,
        # testar conexão, a lista de modelos); todas canceladas primeiro, para pararem ao mesmo tempo
        tasks = [t for t in list(live_threads) if isinstance(t, Task | StreamTask)]
        for task in tasks:
            with suppress(RuntimeError):                 # já destruída pelo `release`
                task.cancel()
        for task in tasks:
            wait_before_close(task, PARTIAL_QUIT_WAIT_MS, "uma tarefa em voo")
        self._delete_windows()

    def _delete_windows(self) -> None:
        """R-06: as janelas sem dono que o controlador criou (o widget, a fila, as revisões, as reuniões e o Histórico) são
        apagadas na saída, ainda no laço do Qt (o `aboutToQuit` despacha os `deleteLater`); vivas até a derrubada do PySide,
        seriam destruídas fora de ordem (0xC0000409 na saída). As referências ficam: só o lado C++ some."""
        self._teardown_widget()
        for win in (self._queue_dialog, self._history, *self._reviews.values(), *self._meetings.values()):
            if win is not None:
                with suppress(RuntimeError):             # já destruída
                    win.deleteLater()

    def _finish(self, rec: Recorder) -> None:
        self._stop_devices()
        self.platform.keep_awake(False)
        self.platform.mic_mute().set_device(None)
        self.player.clear_notice()                       # a gravação acabou: "a gravação capta..." não vale mais (R8-01)
        self._teardown_widget()
        if self.recorder is rec:
            self.recorder = None
        self.state = AppState.IDLE
        self.toasts.refresh_privacy()                    # R-10: fora da gravação o título volta
        if self.tray:
            self.tray.set_state(AppState.IDLE)

    def _stop_devices(self) -> None:
        if self.devices is not None:
            devices, self.devices = self.devices, None
            devices.stop()
            devices.deleteLater()

    def _release_player_for_mic(self) -> bool:
        """F5.3 (A-17), o gancho do gravador antes de reler o PortAudio (que só reinicia sem stream aberto): o player solta a
        saída e o gravador espera a confirmação até `PLAYER_RELEASE_WAIT_S`; o som que tocava fica pausado com o aviso na
        barra."""
        released = self.player.release_output(PLAYER_PAUSED_MIC, PLAYER_RELEASE_WAIT_S)
        if not released:
            log.warning("o player não soltou a saída em %.1f s; a lista de dispositivos pode não ser relida", PLAYER_RELEASE_WAIT_S)
        return released

    def _mic_switched(self) -> None:
        """A-18: a leitura do poller do mudo que vier logo depois da troca pode ser do endpoint antigo: ignorada."""
        self._mute_skip = 1

    def _teardown_widget(self) -> None:
        if self.widget:
            self.widget.close()
            self.widget.deleteLater()
            self.widget = None

    # ---------------------------------------------------------------- mudo do sistema
    def _start_mute_poller(self) -> None:
        self._mute_skip = 0
        if self._mute_poller is None:
            poller = _MutePoller(self.platform)
            poller.changed.connect(self._on_os_mute)
            poller.finished.connect(functools.partial(release, poller))
            live_threads.add(poller)                      # referência viva até `finished` (sem parent, M-12)
            self._mute_poller = poller
            poller.start()

    def _stop_mute_poller(self) -> None:
        if self._mute_poller is not None:
            poller, self._mute_poller = self._mute_poller, None
            poller.changed.disconnect(self._on_os_mute)
            poller.interrupt()

    def _on_os_mute(self, os_muted: object) -> None:
        if self._mute_skip:
            self._mute_skip -= 1                         # F5.3 (A-18): a primeira depois de uma troca de microfone
            return
        rec = self.recorder
        if rec is not None and self.state != AppState.SAVING and os_muted is not None and os_muted != rec.muted:
            rec.set_muted(bool(os_muted), source="dispositivo")

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        rec, w = self.recorder, self.widget
        if not rec or not w:
            return
        saving = self.state == AppState.SAVING
        health = rec.health()
        w.update_state(rec.elapsed(), rec.levels(), health, rec.paused, rec.muted, rec.markers, saving=saving)
        if saving:
            return
        rec.check_devices()
        if self._lease_at is not None and rec.elapsed() - self._lease_at >= self.lease_renew_s:
            self._write_lease(rec)
        alert = self.devices.alert(health) if self.devices is not None else None    # F5.3 (A-21): com prioridade
        if alert is None:
            w.show_alert(None)
        else:
            w.show_alert(alert.text, alert.button, alert.action, alert.tip, alert.kind)

    # ---------------------------------------------------------------- fila de processamento (F2)
    def _write_lease(self, rec: Recorder) -> None:
        """O worker lê entre janelas e espera; vencida (app caiu), segue sozinho (F2A-05). Com o ao vivo, a lease pede pausa
        ao pesado mesmo sem `pipeline.pause_on_record` (F4: os dois juntos estouram a CPU)."""
        if not self.store.get(SPECS["pipeline.pause_on_record"]) and self.live is None:
            return
        until = (self.now() + timedelta(seconds=LEASE_S)).isoformat(timespec="milliseconds")
        self.store.set_setting("pipeline.paused_until", until)
        self._lease_at = rec.elapsed()

    def _clear_lease(self) -> None:
        self._lease_at = None
        if self.store.get_setting("pipeline.paused_until"):
            self.store.set_setting("pipeline.paused_until", "")

    def _enqueue_processing(self, meeting_id: int) -> int | None:
        """Parou: a reunião `gravada` entra na fila (D-014) e o worker é lançado se couber. Devolve o job, a que o pedido de
        ata ao parar fica preso (F5.6 A-18; None = o próximo processamento)."""
        m = self.store.get_meeting(meeting_id)
        if m is None or m.get("status") not in (MeetingStatus.RECORDED, MeetingStatus.INTERRUPTED):
            return None
        if not self.store.get(SPECS["pipeline.auto_start"]):
            log.info("reunião %s gravada; processamento automático desligado", meeting_id)
            return None
        job_id = self.store.jobs.enqueue(meeting_id)
        log.info("reunião %s na fila de processamento (job %s)", meeting_id, job_id)
        self.launch_worker()
        self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS)
        return job_id

    def _enqueue_leftovers(self) -> int:
        """Reuniões `gravada` ou `interrompida` sem nenhum job (Sair gravando, queda, versão anterior): entram na fila (D-014, R2-24)."""
        if not self.store.get(SPECS["pipeline.auto_start"]):
            return 0
        n = 0
        for m in self.store.meetings.list_by_status(MeetingStatus.RECORDED, MeetingStatus.INTERRUPTED):
            if not self.store.jobs.for_meeting(int(m["id"])):
                self.store.jobs.enqueue(int(m["id"]))
                n += 1
        if n:
            log.info("%d reunião(ões) sem processamento entraram na fila", n)
        return n

    def launch_worker(self, light: bool = False) -> bool:
        """Só se há job ativo da classe (pendente, ou rodando/pausado de um worker que morreu: heartbeat vencido), nenhum
        heartbeat fresco dessa classe e o último lançamento já teve tempo de se anunciar; o claim do worker novo devolve o job
        morto à fila (R2-01). O leve (`light`, jobs `llm`) é lançado mesmo durante a gravação (F3A-13). Um `index` que ainda
        espera a edição assentar não conta: o polling da fila lança o leve quando ele vence (R5-07; um leve já vivo espera)."""
        kinds = LIGHT_JOB_KINDS if light else HEAVY_JOB_KINDS
        active = self.store.jobs.active(kinds=kinds, due_only=True)
        if not active:
            self._launch_reason[light] = None                      # nada a fazer: não é motivo de log
            return False
        self._poll_fast()
        if not light and self.recorder is not None:
            return self._not_launching(light, active, "o pesado espera o fim da gravação")      # Q7
        held = self.store.jobs.held_fresh(kinds=kinds)
        if held:
            return self._not_launching(light, active, f"o job {held[0]['id']} está com um worker vivo (pid {held[0].get('worker_pid')})")
        if self.store.get(SPECS["pipeline.paused_manual"]):
            return self._not_launching(light, active, "a fila está pausada pelo usuário")
        spawned_at = self._worker_spawned_at[light]
        if spawned_at is not None and time.monotonic() - spawned_at < WORKER_SPAWN_COOLDOWN_S:
            return self._not_launching(light, active, "outro foi lançado há pouco e ainda não deu o primeiro heartbeat")
        self._launch_reason[light] = None
        argv = process.worker_command(light=light)
        env = process.worker_env(1 if light else self.store.get(SPECS["pipeline.torch_threads"]))
        try:
            pid = self.platform.spawn_detached(argv, env)
        except Exception as e:
            log.exception("não consegui lançar o worker%s", " leve" if light else "")
            self._notify_as(NoticeKind.ERROR, f"Não consegui iniciar o processamento: {e}")
            return False
        self._worker_spawned_at[light] = time.monotonic()
        log.info("worker%s lançado (pid %s) para %d job(s) ativo(s) (%s): %s", " leve" if light else "", pid, len(active),
                 ", ".join(f"{j['id']}:{j.get('kind')}" for j in active[:6]), " ".join(argv))
        return True

    def _poll_fast(self) -> None:
        """F5.3 (A-11): com job ativo o polling vai a 2 s (um "Gerar de novo" avisava até 30 s depois); só quando muda, porque
        o `setInterval` reinicia o timer."""
        if self._queue_timer.interval() != QUEUE_POLL_ACTIVE_MS:
            self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS)

    def _not_launching(self, light: bool, active: list[Any], reason: str) -> bool:
        """Por que a fila tem job e nenhum worker sai (a "fila parada" do teste de 27/10): uma linha só quando o motivo muda,
        não a cada polling."""
        if self._launch_reason[light] != reason:
            self._launch_reason[light] = reason
            log.info("worker%s não lançado: %s; %d job(s) ativo(s) (%s)", " leve" if light else "", reason, len(active),
                     ", ".join(f"{j['id']}:{j.get('kind')}:{j.get('status')}" for j in active[:6]))
        return False

    def _poll_queue(self) -> None:
        """Lê a fila (um SELECT): tooltip da bandeja, "pronta para revisão", "ata pronta" e relançamento dos workers. Os
        `index` ficam fora da lista dos concluídos e da contagem (F5A-05): com dezenas deles ativos depois da varredura, o
        pesado concluído sumiria das 50 linhas e a revisão não abriria."""
        try:
            jobs = self.store.jobs.list_jobs(limit=50, exclude_kinds=INDEX_KINDS)
            active_jobs = self.store.jobs.active()
        except Exception as e:
            log.debug("fila: %s", e)
            return
        done_now = {int(j["id"]): j for j in jobs if j["status"] == JobStatus.DONE}
        shown = [j for j in active_jobs if str(j.get("kind")) not in INDEX_KINDS]
        indexing = len({int(j["meeting_id"]) for j in active_jobs if str(j.get("kind")) in INDEX_KINDS})
        active = len(shown)
        running = next((j for j in shown if j["status"] in HELD and str(j.get("kind")) in HEAVY_JOB_KINDS), None)
        if self.tray is not None:
            if running is not None:
                pct = float(running["progress"] or 0) * 100
                tip = f"processando {len(done_now) + 1}/{len(done_now) + active} · {pct:.0f} %"
            elif active:
                tip = f"{active} na fila"
            else:
                tip = ""
            if indexing:
                tip = f"{tip} · índice {indexing}" if tip else f"índice {indexing}"
            self.tray.set_queue_tip(tip)
        for jid, job in sorted(done_now.items()):
            if jid not in self._seen_done:
                self._seen_done.add(jid)
                self._on_job_done(job)
        for job in jobs:                                         # A-11: cada erro novo avisa uma vez
            if job["status"] == JobStatus.ERROR and _error_key(job) not in self._seen_errors:
                self._seen_errors.add(_error_key(job))
                self._on_job_error(job)
        for job in jobs:                                         # R-07: o fim sem sucesso também muda o cabeçalho (o idioma)
            if job["status"] in ENDED_BADLY and _error_key(job) not in self._seen_ended:
                self._seen_ended.add(_error_key(job))
                self._reload_meeting_header(int(job["meeting_id"]))
        if running is not None and str(running.get("step") or "") in REVIEW_STEPS:
            mid = int(running["meeting_id"])
            if mid not in self._review_offered:
                self._review_offered.add(mid)                         # P1: a revisão abre enquanto o Whisper roda
                if self._pending_voices(mid):                         # D-055: só com voz a confirmar
                    self.open_review(mid)
        for mid, dlg in list(self._reviews.items()):            # só quando os clusters mudaram: não rouba o foco (R2-26)
            sig = self._clusters_signature(mid)
            if sig != self._review_sigs.get(mid):
                self._review_sigs[mid] = sig
                dlg.reload()
        self._reload_indexed_meetings()
        if any(str(j.get("kind")) in HEAVY_JOB_KINDS for j in active_jobs):
            self.launch_worker()
        if any(str(j.get("kind")) in LIGHT_JOB_KINDS for j in active_jobs):
            self.launch_worker(light=True)                        # rede e JSON não disputam CPU com a gravação (F3A-13)
        self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS if active_jobs else QUEUE_POLL_IDLE_MS)

    def _on_job_done(self, job: Any) -> None:
        mid = int(job["meeting_id"])
        if str(job.get("kind")) in INDEX_KINDS:
            # F5A-05: o índice não avisa nem abre a revisão; só recarrega a reunião aberta (sem roubar o foco nem rolar)
            log.debug("reunião %s: índice concluído (job %s)", mid, job["id"])
            self._meeting_index[mid] = max(self._meeting_index.get(mid, 0), int(job["id"]))
            if mid in self._meetings:
                self._meetings[mid].reload()
            return
        if str(job.get("kind")) == JobKind.LLM:
            if self._identity_only(job, mid):                    # F5.3: avisa (revê a A-14 da F5.2, que não avisava)
                self._notify_meeting(mid, IDENTITY_READY)
            else:
                self._notify_meeting(mid, self._llm_done_text(job))
            if mid in self._reviews:
                self._reviews[mid].reload()
        else:
            todo = self._pending_voices(mid)
            waits = todo and self._summary_waits(mid)              # U-042, F5.6 (A-06): só com um pedido que espera
            text = ("pronta para revisão de falantes; a ata sai depois que as vozes forem identificadas" if waits else
                    "pronta para revisão de falantes" if todo else "processada, sem voz a confirmar")
            self._notify_meeting(mid, f"{text}.")
            self._warn_no_llm(mid, int(job["id"]))
            self._review_offered.add(mid)
            if todo:
                self.open_review(mid)
        if mid in self._meetings:
            self._meetings[mid].reload()

    def _reload_meeting_header(self, meeting_id: int) -> None:
        """R-07: um job da reunião acabou em erro ou cancelado: a janela aberta dela relê o cabeçalho; uma troca de idioma
        que não foi até o fim sai do "(reprocessando)" e o botão volta a oferecer o outro idioma."""
        win = self._meetings.get(meeting_id)
        if win is None:
            return
        try:
            win.reload_header()
        except Exception as e:  # a janela relê na próxima vez; o polling não para por ela
            log.debug("reunião %s: cabeçalho não relido: %s", meeting_id, e)

    def _on_job_error(self, job: Any) -> None:
        """F5.3 (A-11): processamento, reprocessamento ou LLM com erro: um aviso com a etapa; o clique abre a fila. O índice e
        o perfil de trecho não avisam."""
        text = job_error_text(job)
        if text is not None:
            self._notify_meeting(int(job["meeting_id"]), text, NoticeKind.ERROR, NoticeAction(Target.QUEUE))

    def _summary_waits(self, meeting_id: int) -> bool:
        """U-042, F5.6 (A-06): a ata espera a identificação só com um pedido "quando todos tiverem nome" (`llm_requests`)."""
        try:
            return llm_requests.summary_waits(self.store, meeting_id)
        except Exception as e:
            log.debug("critério da ata adiada: %s", e)
            return False

    def _identity_only(self, job: Any, meeting_id: int) -> bool:
        """O job `llm` só do núcleo enquanto um pedido espera as vozes (A-14: o balão "LLM concluída" enganaria)."""
        try:
            return llm_requests.identity_only(self.store, meeting_id, dict(job))
        except Exception as e:
            log.debug("reunião %s: job llm só de identidade? %s", meeting_id, e)
            return False

    def _pending_voices(self, meeting_id: int) -> bool:
        """D-055: a revisão só abre sozinha (P1 e o fim do job) com voz a confirmar; sem nenhuma, nada sobe por cima do que o
        usuário está fazendo (antes abria só para dizer "tudo confirmado"); ela segue a um clique, na reunião e na fila."""
        try:
            todo = bool(review.pending(review.review_items(self.store, meeting_id)))
        except Exception as e:  # na dúvida, abre como antes
            log.debug("vozes pendentes da reunião %s: %s", meeting_id, e)
            return True
        if not todo:
            log.info("reunião %s: nenhuma voz a confirmar; a revisão não abre sozinha", meeting_id)
        return todo

    def _reload_indexed_meetings(self) -> None:
        """O `index` de uma reunião aberta terminou (os `index` ficam fora da detecção dos concluídos, F5A-05): recarrega a
        janela dela, uma consulta só para as reuniões abertas."""
        if not self._meetings:
            return
        try:
            done = self.store.jobs.last_done(JobKind.INDEX, self._meetings)
        except Exception as e:
            log.debug("fila: índice das reuniões abertas: %s", e)
            return
        for mid, job_id in done.items():
            seen = self._meeting_index.get(mid)
            self._meeting_index[mid] = job_id
            if seen is not None and job_id != seen and mid in self._meetings:
                self._meetings[mid].reload()

    def _warn_no_llm(self, meeting_id: int, job_id: int) -> None:
        """Uma vez por sessão: o job pesado terminou e não haverá ata porque falta provedor com chave (F3.1 R3-13)."""
        from ..pipeline.llm_steps import NO_PROVIDER, auto_skip_reason

        if self._no_llm_warned or auto_skip_reason(self.store, meeting_id, job_id) != NO_PROVIDER:
            return
        self._no_llm_warned = True
        self.notify(NO_PROVIDER)

    def _llm_done_text(self, job: Any) -> str:
        """"ata pronta · US$ 0,32" (U-020) pelo `timings_json.llm` do job."""
        from .llm_tabs import llm_done_text

        return llm_done_text(dict(job))

    def open_review(self, meeting_id: int) -> ReviewDialog | None:
        """Revisão mínima de falantes (F2 item 13): não modal, uma por reunião; sem voz pendente mostra "tudo confirmado"."""
        if self.deleter.refuses(meeting_id):                     # F5.6 (R-07): sendo excluída
            return None
        dlg = self._reviews.get(meeting_id)
        if dlg is None:
            dlg = ReviewDialog(self.store, meeting_id, None, on_transcript=self.open_transcript, on_launch=self.launch_worker,
                               on_edited=self.on_edited, player=self.player)
            dlg.finished.connect(functools.partial(self._review_closed, meeting_id))
            self._reviews[meeting_id] = dlg
            self._review_sigs[meeting_id] = self._clusters_signature(meeting_id)
            log.info("revisão da reunião %s aberta: %d voz(es), %d pendente(s)", meeting_id, len(dlg.items),
                     len(review.pending(dlg.items)))
            if not review.pending(dlg.items):
                log.info("reunião %s: nenhuma voz pendente (tudo confirmado)", meeting_id)
        else:
            dlg.reload()
        dlg.show()
        dlg.raise_()
        return dlg

    def _clusters_signature(self, meeting_id: int) -> tuple[int, ...]:
        """Assinatura de recarga da revisão numa só consulta (F3A-08): clusters (quantos, último id, quantos com sugestão da
        LLM), resumos (último id) e título da LLM; muda -> `ReviewDialog.reload()` sem roubar o foco (R2-26)."""
        row = self.store._q(
            "SELECT (SELECT COUNT(*) FROM clusters WHERE meeting_id = ?), "
            "(SELECT COALESCE(MAX(id), 0) FROM clusters WHERE meeting_id = ?), "
            "(SELECT COUNT(*) FROM clusters WHERE meeting_id = ? AND (llm_person_id IS NOT NULL OR llm_name IS NOT NULL)), "
            "(SELECT COALESCE(MAX(id), 0) FROM summaries WHERE meeting_id = ?), "
            "(SELECT COALESCE(LENGTH(title_llm), 0) FROM meetings WHERE id = ?), "
            "(SELECT COUNT(*) FROM processing_jobs WHERE meeting_id = ? AND kind = 'llm' AND status IN ('pendente', 'rodando', 'pausado'))",
            (meeting_id, meeting_id, meeting_id, meeting_id, meeting_id, meeting_id))[0]
        return tuple(int(v) for v in row)

    def _review_closed(self, meeting_id: int, result: int) -> None:
        dlg = self._reviews.pop(meeting_id, None)
        self._review_sigs.pop(meeting_id, None)
        log.info("revisão da reunião %s fechada (%s)", meeting_id, "concluída" if result == QDialog.DialogCode.Accepted else "sem concluir")
        if dlg is not None:
            dlg.deleteLater()
        if meeting_id in self._index_edited:                     # fechar a revisão tira a espera das edições (R5-07)
            self._index_edited.discard(meeting_id)
            try:
                expedited = self.store.jobs.expedite_index(meeting_id)
            except Exception as e:  # a busca nunca atrapalha fechar a janela
                log.warning("reunião %s: não tirei a espera do índice da busca: %s", meeting_id, e)
                expedited = False
            if expedited:
                self.launch_worker(light=True)

    # ---------------------------------------------------------------- reunião aberta e Histórico (F5 L4)
    def open_transcript(self, meeting_id: int) -> MeetingWindow | None:
        """Apelido de `open_meeting(mid, "transcricao")` (Q6: a fila e a revisão continuam chamando)."""
        return self.open_meeting(meeting_id, "transcricao")

    def open_meeting(self, meeting_id: int, tab: str | None = None, t_ms: int | None = None, play: bool = False, *,
                     focus: bool = False) -> MeetingWindow | None:
        """Abre (ou traz à frente) a janela da reunião; com `t_ms`, vai à fala de início mais perto, rola até ela e, com
        `play`, toca dela; senão mostra a aba `tab`. `focus` pede à plataforma para trazer a janela à frente (clique no
        balão: o app está em segundo plano)."""
        if self.deleter.refuses(meeting_id):                     # F5.6 (R-07): sendo excluída (Histórico, fila, aviso)
            return None
        win = self._meetings.get(meeting_id)
        log.info("reunião %s %s (aba %s%s%s)", meeting_id, "aberta" if win is None else "trazida à frente", tab or "padrão",
                 f", em {t_ms} ms" if t_ms is not None else "", ", tocando" if play else "")
        if win is None:
            win = MeetingWindow(self.store, meeting_id, player=self.player, on_review=self.open_review, on_launch=self.launch_worker,
                                on_edited=self.on_edited, cascade=len(self._meetings),
                                on_chat_answer=functools.partial(self._chat_answered, meeting_id))
            win.closed.connect(self._meeting_closed)
            self._meetings[meeting_id] = win
            with suppress(Exception):
                self._meeting_index[meeting_id] = self.store.jobs.last_done(JobKind.INDEX, [meeting_id]).get(meeting_id, 0)
        win.show()
        win.raise_()
        win.activateWindow()
        if focus:
            with suppress(Exception):
                self.platform.bring_to_front(int(win.winId()))
        if t_ms is not None:
            win.open_at(int(t_ms), play)
        elif tab is not None:
            win.show_tab(tab)
        return win

    def _meeting_closed(self, meeting_id: int) -> None:
        log.info("reunião %s fechada", meeting_id)
        win = self._meetings.pop(meeting_id, None)
        self._meeting_index.pop(meeting_id, None)
        if win is not None:
            win.deleteLater()

    def open_history(self, focus_search: bool = False) -> HistoryWindow:
        """O Histórico (uma janela só, fechar só esconde): pela bandeja e pelo atalho U-028, que põe o cursor na busca com o
        texto selecionado e traz a janela à frente pela plataforma (no Windows, um atalho global não dá o foco sozinho)."""
        win = self._history
        log.info("histórico aberto%s", " pelo atalho da busca" if focus_search else "")
        if win is None:
            win = HistoryWindow(self.store, on_open=self.deleter.open_from_history, on_edited=self.on_edited,
                                on_answer=self._history_answered, on_delete=self.deleter.request)
            self._history = win
        win.show()
        win.raise_()
        win.activateWindow()
        if focus_search:
            with suppress(Exception):
                self.platform.bring_to_front(int(win.winId()))
            win.page.focus_search()
        return win

    def _pause_player(self, notice: str) -> None:
        """R7-01: o som que o player toca não entra no teste do warm-up: pausa, com o aviso na barra do player e no log (sem
        balão); retomar passa pela confirmação do Q9 (`_confirm_play`). A pausa solta o fluxo de saída (U-043, A-04): o
        warm-up pode reler os dispositivos com o fone que chegou; a retomada abre outro."""
        try:
            if self.player.pause(notice, release=True):
                log.info("player pausado: %s", notice)
        except Exception as e:  # o player nunca impede o warm-up nem a gravação
            log.warning("não consegui pausar o player: %s", e)

    def _before_system_loopback(self) -> None:
        """R8-01, o gancho do gravador: o loopback do sistema inteiro vai abrir (a fonte pedida ou a troca automática quando o
        do app não abre, no começo, na troca de fonte e na reabertura). O player pausa e o gravador espera a confirmação do
        motor (o bloco em voo saiu e o buffer do dispositivo foi descartado) até `PLAYER_STOP_WAIT_S`: nenhum bloco do player
        depois da abertura. Pode rodar fora da thread da interface: o motor é seguro entre threads e a barra é refeita na
        thread dela (`Player.pause_and_wait`); o log também é seguro."""
        playing, confirmed = self.player.pause_and_wait(PLAYER_PAUSED_SYSTEM, PLAYER_STOP_WAIT_S)
        if playing:
            log.info("player pausado: %s", PLAYER_PAUSED_SYSTEM)
        if not confirmed:
            log.warning("o player não confirmou a pausa em %.1f s; o loopback do sistema abre assim mesmo", PLAYER_STOP_WAIT_S)

    def _confirm_play(self, _req: PlayRequest) -> bool:
        """Q9: gravando com a fonte "sistema inteiro", o som do player entraria no canal R: pede confirmação antes de tocar;
        com a fonte "app" ou sem gravação, toca direto."""
        rec = self.recorder
        if rec is None or self.state not in (AppState.RECORDING, AppState.PAUSED) or str(rec.source.kind) != SourceKind.SYSTEM:
            return True
        answer = QMessageBox.question(None, "Tocar durante a gravação", SYSTEM_PLAY_TEXT,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def open_queue(self) -> None:
        log.info("fila de processamento aberta")
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self.store, None, on_launch=self.launch_worker, on_open=self.open_transcript,
                                             on_review=self.open_review)
            self._queue_dialog.finished.connect(self._queue_closed)
        self._queue_dialog.show()
        self._queue_dialog.raise_()

    def _queue_closed(self, _result: int) -> None:
        if self._queue_dialog is not None:
            self._queue_dialog.deleteLater()
            self._queue_dialog = None

    # ---------------------------------------------------------------- ao vivo (F4)
    def _start_live(self, rec: Recorder, fields: dict[str, Any]) -> bool:
        """Depois de `rec.start()`: caixas do warm-up (`live_enabled`, `live_names`, `live_partial`; ausentes = padrão das
        Configurações), modelos prontos, filho lançado e desvio ligado. Qualquer falha só deixa a gravação sem ao vivo."""
        partial = fields.get("live_partial")             # F5.6 (A-21): fixo na gravação; o "Hello" o leva ao `--check`
        self._partial_auto = bool(self.store.get(SPECS["live.partial"]) if partial is None else partial)
        old, self.live = self.live, None                 # o filho da gravação anterior ainda fechando (até 10 s) termina
        if old is not None:                              # sozinho: sem lease, legendas nem queda nesta (revisão do L0 a L3)
            old.on_message = lambda _msg: None
        self.live_last_caption = None
        self.live_names_status = ""
        self._live_retries = 0
        enabled = fields.get("live_enabled")
        if enabled is None:
            enabled = self.store.get(SPECS["live.enabled"])
        if not enabled:
            self._set_live_status("off")
            return False
        names = fields.get("live_names")
        if names is None:
            names = self.store.get(SPECS["live.names"])
        model = self.store.get(SPECS["live.model"])
        models_dir = live_models_dir(self.store.get_setting("pipeline.models_dir"))
        if not models_ready(models_dir, model):
            log.warning("gravação %s sem ao vivo: modelos ausentes em %s", rec.meeting_id, models_dir)
            if not self._live_models_warned:
                self._live_models_warned = True
                self.notify(LIVE_MODELS_MISSING)
            self._set_live_status("unavailable", reason="models")
            return False
        t0_perf = rec.clock.t0_perf if isinstance(rec.clock, SessionClock) else time.perf_counter() - rec.clock.rel()
        self._live_hello = Hello(t0_perf, names=bool(names), model=model, threads=self.store.get(SPECS["live.threads"]),
                                 eof_grace_s=LIVE_EOF_GRACE_S, partial=self._partial_auto)
        return self._spawn_live(rec)

    def _spawn_live(self, rec: Recorder) -> bool:
        hello = self._live_hello
        if hello is None:
            return False
        argv = process.live_command(self.store.base_dir, rec.meeting_id)
        client = LiveClient(self.platform.spawn_child, argv, process.live_env(hello.threads), hello, lambda _msg: None,
                            meeting_id=rec.meeting_id)
        client.on_message = functools.partial(self._live_message.emit, client)
        self._live_error = None
        try:
            client.start()
        except Exception as e:
            log.exception("ao vivo da gravação %s não iniciou", rec.meeting_id)
            self._notify_as(NoticeKind.ERROR, f"A legenda ao vivo não iniciou: {e}")
            self._set_live_status("unavailable", reason=str(e))
            return False
        self.live = client
        rec.set_live_tap(client.tap)
        self._set_live_status("starting")
        return True

    def _stop_live(self, detach: bool = False) -> None:
        """Parar: desliga o desvio e fecha o filho sem bloquear (EOF; ele transcreve a fila e sai; kill no fim do prazo).
        `detach` (o app está saindo): as mensagens que ainda vierem são descartadas."""
        if self.recorder is not None:
            self.recorder.set_live_tap(None)
        client = self.live
        if client is None:
            return
        if detach:
            client.on_message = lambda _msg: None
            self.live = None
        client.close(LIVE_CLOSE_GRACE_S)
        self._set_live_status("stopping")

    def _terminate_live(self) -> None:
        """Cancelar: mata o filho e espera (curto) antes de a reunião ser apagada (a FK em cascata limpa as legendas)."""
        if self.recorder is not None:
            self.recorder.set_live_tap(None)
        client, self.live = self.live, None
        if client is not None:
            client.terminate()
            self._set_live_status("stopped")

    def _set_live_status(self, state: str, **details: Any) -> None:
        self.live_status = state
        self.live_state.emit({"state": state, **details})

    def _on_live_message(self, client: object, msg: object) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if client is not self.live:
            if kind == EXIT:
                log.debug("ao vivo anterior terminou: %s", msg)
            return
        if kind == READY:
            self._set_live_status("live", model=msg.get("model"), names=msg.get("names"))
        elif kind == CAPTION:
            self.live_last_caption = msg
            self.live_caption.emit(msg)
        elif kind == STATE:
            state = str(msg.get("state") or "")
            details = {k: v for k, v in msg.items() if k not in ("type", "state")}
            if state in ("names_ready", "names_unavailable"):    # só os nomes: a legenda segue (sem nome, no segundo caso)
                self.live_names_status = "ok" if state == "names_ready" else str(msg.get("reason") or "indisponível")
                self.live_state.emit({"state": state, **details})
            else:
                self._set_live_status(state, **details)
        elif kind == CPU:
            self._record_live_cpu(msg)
        elif kind == ERROR:
            log.warning("ao vivo: %s", msg.get("message"))
            self._live_error = msg
        elif kind == BYE:
            log.info("ao vivo encerrado: %s", {k: v for k, v in msg.items() if k != "type"})
        elif kind == EXIT:
            self._on_live_exit(msg)

    def _record_live_cpu(self, msg: dict[str, Any]) -> None:
        """`live_cpu` a cada ~10 s com a CPU do app e a do filho, em % da máquina inteira (aceite do G4)."""
        rec, client = self.recorder, self.live
        if rec is None or client is None or client.meeting_id != rec.meeting_id:
            return
        try:
            self.store.add_event(rec.meeting_id, round(rec.elapsed() * 1000), EventType.LIVE_CPU,
                                 {"app_pct": msg.get("app_pct"), "live_pct": msg.get("live_pct")})
        except Exception as e:
            log.debug("evento live_cpu não gravado: %s", e)

    def _on_live_exit(self, msg: dict[str, Any]) -> None:
        client, self.live = self.live, None
        rec = self.recorder
        if msg.get("expected"):
            self._set_live_status("stopped")
            return
        if rec is not None:
            rec.set_live_tap(None)
        permanent = self._live_error is not None and self._live_error.get("retry") is False
        recording = rec is not None and client is not None and rec.meeting_id == client.meeting_id and self.state in (
            AppState.RECORDING, AppState.PAUSED)
        log.warning("ao vivo caiu (código %s)%s", msg.get("code"), f": {self._live_error.get('message')}" if self._live_error else "")
        if recording and rec is not None and not permanent and self._live_retries < LIVE_RETRIES:
            self._live_retries += 1
            self._set_live_status("restarting", code=msg.get("code"))
            if self._spawn_live(rec):
                return
        reason = str((self._live_error or {}).get("message") or "")
        self._set_live_status("crashed", code=msg.get("code"), reason=reason)
        if recording:
            self._notify_as(NoticeKind.ERROR, LIVE_STOPPED_TEXT + (f"\n{reason}" if reason else ""))

    # ---------------------------------------------------------------- interface do ao vivo (F4 L4)
    def _live_badge_tip(self) -> str:
        """Tooltip do selo: o estado em texto e o dos nomes ("nomes indisponíveis: <motivo>")."""
        names = self.live_names_status
        tip = {"starting": "carregando o modelo da legenda…", "restarting": "a legenda caiu: reiniciando…",
               "live": "legenda ao vivo", "degraded": "legenda atrasada: trocou para o modelo base",
               "skipping": "legenda atrasada: pulando trechos antigos", "crashed": "a legenda ao vivo parou; a gravação segue"}
        text = tip.get(self.live_status, self.live_status)
        if names and names != "ok":
            text += f"\nnomes indisponíveis: {names}"
        return text

    def _on_caption_ui(self, msg: object) -> None:
        if self.widget is not None and isinstance(msg, dict):
            self.widget.set_caption(msg.get("label"), str(msg.get("text") or ""))

    def _on_live_state_ui(self, _state: object) -> None:
        if self.widget is not None:
            self.widget.set_live_state(self.live_status, self._live_badge_tip())
        win = self.live_window
        if win is not None and self.recorder is not None and win.meeting_id == self.recorder.meeting_id:
            win.set_live_state(self.live_status)

    def open_live_window(self) -> LiveWindow | None:
        """U-021: abre (ou traz à frente) a janela da gravação em curso; a de uma gravação anterior, só leitura, é trocada.
        Fora da gravação, só mostra a que estiver aberta."""
        rec, win = self.recorder, self.live_window
        if rec is None or self.state == AppState.SAVING:
            if win is not None:
                win.show()
                win.raise_()
            return win
        if win is not None and win.meeting_id != rec.meeting_id:
            win.close()                                   # `closed` solta a referência
            win = self.live_window = None
        if win is None:
            win = LiveWindow(self.store, rec.meeting_id, on_mark_caption=self.mark_caption, on_mark_item=self.mark_partial_item,
                             on_refresh=self.request_partial_now, marked_captions=self._marked_captions,
                             marked_items=self._marked_items, partial_auto=self._partial_auto)
            self.live_caption.connect(win.on_caption)
            self.partial_state.connect(win.on_partial_state)
            win.closed.connect(functools.partial(self._live_window_closed, win))
            self.live_window = win
        win.set_live_state(self.live_status)
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def _live_window_closed(self, win: LiveWindow) -> None:
        with suppress(RuntimeError, TypeError):
            self.live_caption.disconnect(win.on_caption)
        with suppress(RuntimeError, TypeError):
            self.partial_state.disconnect(win.on_partial_state)
        if self.live_window is win:
            self.live_window = None
        win.deleteLater()

    def _end_live_window(self, meeting_id: int, text: str) -> None:
        """Parar ou Cancelar: a janela da gravação fica aberta só para leitura."""
        win = self.live_window
        if win is not None and win.meeting_id == meeting_id:
            win.set_finished(text)

    def _can_mark(self, meeting_id: object) -> bool:
        rec = self.recorder
        return rec is not None and self.state in (AppState.RECORDING, AppState.PAUSED) and meeting_id in (None, rec.meeting_id)

    def mark_caption(self, row: Any) -> tuple[bool, str]:
        """U-022: marcador no início da legenda com "rótulo: texto" (~300 caracteres), uma vez por legenda na gravação."""
        caption = dict(row)
        if not self._can_mark(caption.get("meeting_id")) or self.recorder is None:
            return False, "fora da gravação: a janela é só para leitura"
        key = caption_key(caption)
        if key in self._marked_captions:
            return False, "esta legenda já virou marcador"
        try:
            t_ms = self.recorder.mark(caption_note(caption), t_ms=int(caption.get("t_start_ms") or 0), origin="legenda")
        except Exception as e:                            # banco preso: a gravação segue, o clique só avisa
            log.warning("marcador da legenda não gravado: %s", e)
            return False, f"não consegui criar o marcador: {e}"
        self._marked_captions.add(key)
        log.info("reunião %s: marcador de legenda em %d ms", self.recorder.meeting_id, t_ms)
        return True, f"marcador criado em [{stamp(t_ms)}]"

    def mark_partial_item(self, kind: str, item: Any, t_until_ms: int | None = None) -> tuple[bool, str]:
        """U-023: "Decisão: ..." ou "Ação: ..." no `t_ms` do item (sem ele, no fim do parcial), uma vez por item."""
        data = dict(item)
        if not self._can_mark(None) or self.recorder is None:
            return False, "fora da gravação: a janela é só para leitura"
        key = item_key(kind, data)
        if key in self._marked_items:
            return False, "este item já virou marcador"
        t = data.get("t_ms")
        t_ms = int(t) if isinstance(t, int | float) else (int(t_until_ms) if t_until_ms is not None else None)
        try:
            t_ms = self.recorder.mark(item_note(kind, data), t_ms=t_ms, origin="parcial")
        except Exception as e:
            log.warning("marcador do resumo parcial não gravado: %s", e)
            return False, f"não consegui criar o marcador: {e}"
        self._marked_items.add(key)
        return True, f"marcador criado em [{stamp(t_ms)}]"

    # ---------------------------------------------------------------- resumo parcial (F4 L5)
    def _start_partial(self, meeting_id: int) -> None:
        """Só com o ao vivo lançado; o primeiro parcial sai um intervalo depois do começo."""
        self._partial_task = None                        # a chamada de uma gravação anterior termina sozinha e é ignorada
        self._partial_stopped = None
        self._partial_warned = False
        self._partial_backlog = False
        self._partial_last_at = self.monotonic()
        self._partial_meeting = meeting_id if self.live is not None else None
        if self._partial_meeting is None:
            return
        try:
            self._partial_last_caption = partial_task.partial_state(self.store, meeting_id)[1]
        except Exception as e:
            log.debug("parcial anterior ilegível: %s", e)
            self._partial_last_caption = 0
        self._partial_timer.start()
        self._set_partial({"state": "idle"})

    def _stop_partial(self, cancel: bool = False) -> None:
        """Parar e Cancelar param o timer; Cancelar também cancela a chamada em voo desta gravação."""
        self._partial_timer.stop()
        self._partial_meeting = None
        task, self._partial_task = self._partial_task, None
        if cancel and task is not None:
            task.cancel()

    def _wait_partials(self) -> None:
        """Saída do app: cancela as chamadas do parcial em voo e espera (pouco) cada uma gravar o cancelamento."""
        for task in list(self._partial_tasks):
            task.cancel()
            if not task.wait(PARTIAL_QUIT_WAIT_MS):
                log.warning("a chamada do resumo parcial não terminou em %d ms", PARTIAL_QUIT_WAIT_MS)
        self._partial_tasks.clear()

    def _set_partial(self, state: dict[str, Any], meeting_id: int | None = None) -> None:
        st = {"meeting_id": meeting_id if meeting_id is not None else self._partial_meeting, **state}
        self.partial_status = st
        self.partial_state.emit(st)

    def _partial_tick(self) -> None:
        """A cada 15 s: o intervalo relido agora (CP8; 0 desliga o automático); vencido, roda se houver legenda nova. F5.6
        (A-22): sem "Resumo parcial" nesta gravação, nunca (o "Atualizar resumo agora" segue)."""
        rec = self.recorder
        if (self._partial_meeting is None or rec is None or rec.meeting_id != self._partial_meeting or not self._partial_auto
                or self.state not in (AppState.RECORDING, AppState.PAUSED) or self._partial_stopped):
            return
        try:
            interval = int(self.store.get(SPECS["live.summary_interval_min"]))
        except Exception as e:                            # banco ocupado: confere de novo no próximo disparo
            log.debug("intervalo do resumo parcial não lido: %s", e)
            return
        now = self.monotonic()
        if self._partial_backlog:                         # o corte da entrada deixou legendas: seguem já, sem esperar o intervalo
            self._partial_last_at = now
        elif interval <= 0 or now - self._partial_last_at < interval * 60:
            return
        else:                                              # cadência fixa: o disparo de 15 s não empurra o próximo intervalo
            due = self._partial_last_at + interval * 60
            self._partial_last_at = due if now - due < interval * 60 else now
        self._run_partial(manual=False)

    def request_partial_now(self) -> str:
        """"Atualizar resumo agora" (U-021): dispara já, se não houver chamada em voo; devolve o texto para a janela."""
        return self._run_partial(manual=True)

    def _run_partial(self, manual: bool) -> str:
        mid = self._partial_meeting
        if mid is None or self.recorder is None or self.state not in (AppState.RECORDING, AppState.PAUSED):
            return "o resumo parcial só roda durante a gravação com a legenda ao vivo"
        if self._partial_stopped:
            return self._partial_stopped
        if self._partial_tasks:
            return "o resumo parcial já está sendo atualizado"
        if manual:                                         # o automático já acertou a cadência no `_partial_tick`
            self._partial_last_at = self.monotonic()
        self._partial_backlog = False
        try:
            fresh = self.store.captions.last_id(mid) > self._partial_last_caption
            keyed = self.store.providers.active_with_key("llm") is not None
        except Exception as e:
            log.debug("resumo parcial não conferido: %s", e)
            return "resumo parcial indisponível agora"
        if not fresh:
            if manual:
                self._set_partial({"state": "idle", "message": "nada novo desde o último resumo parcial"})
            return "nada novo desde o último resumo parcial"
        if not keyed:
            self._partial_failed(partial_task.NO_PROVIDER, no_provider=True)
            return partial_task.NO_PROVIDER
        box: list[StreamTask] = []
        store = self.store

        def job(_emit: Callable[[str], None], cancelled: Callable[[], bool]) -> Any:
            return partial_task.run_partial(store, mid, cancelled=cancelled, manual=manual)

        task = run_stream(job, store, lambda _chunk: None, functools.partial(self._on_partial_done, mid, box))
        box.append(task)
        self._partial_tasks.add(task)
        self._partial_task = task
        self._set_partial({"state": "running"})
        log.info("reunião %s: resumo parcial pedido (%s)", mid, "agora" if manual else "intervalo")
        return "atualizando o resumo parcial…"

    def _on_partial_done(self, meeting_id: int, box: list[StreamTask], result: Any) -> None:
        for task in box:
            self._partial_tasks.discard(task)
            if self._partial_task is task:
                self._partial_task = None
        if self._quitting:
            return
        current = meeting_id == self._partial_meeting
        if isinstance(result, BaseException):
            if current:
                self._partial_failed(f"{type(result).__name__}: {result}")
            return
        res: partial_task.PartialResult = result
        match res.outcome:
            case partial_task.Outcome.DONE:
                if current:
                    self._partial_last_caption = max(self._partial_last_caption, res.last_caption_id)
                    self._partial_backlog = res.left > 0
                self._set_partial({"state": "done", "summary_id": res.summary_id, "t_until_ms": res.t_until_ms,
                                   "left": res.left}, meeting_id)       # a janela da reunião recarrega, mesmo depois de Parar
            case partial_task.Outcome.BUDGET if current:
                self._partial_stopped = res.error or partial_task.budget_text(res.spent_usd, res.limit_usd)
                self._partial_timer.stop()
                log.warning("reunião %s: %s", meeting_id, self._partial_stopped)
                self.notify(self._partial_stopped[:1].upper() + self._partial_stopped[1:] + ".")
                self._set_partial({"state": "stopped", "message": self._partial_stopped})
            case partial_task.Outcome.NO_PROVIDER if current:
                self._partial_failed(res.error or partial_task.NO_PROVIDER, no_provider=True)
            case partial_task.Outcome.ERROR if current:
                self._partial_failed(res.error or "falha")
            case partial_task.Outcome.NOTHING if current:
                self._set_partial({"state": "idle", "message": "nada novo desde o último resumo parcial"})
            case _:
                log.debug("resumo parcial da reunião %s: %s (fora da gravação atual)", meeting_id, res.outcome)

    def _partial_failed(self, message: str, no_provider: bool = False) -> None:
        """Sem chave ou sem rede: um aviso por gravação; a próxima tentativa é no próximo intervalo e as legendas seguem."""
        text = message if no_provider else f"resumo parcial indisponível agora ({message}); tento de novo no próximo intervalo"
        if not self._partial_warned:
            self._partial_warned = True
            self._notify_as(NoticeKind.INFO if no_provider else NoticeKind.ERROR, text[:1].upper() + text[1:] + ".")
        self._set_partial({"state": "error", "message": text})

    # ---------------------------------------------------------------- outros
    def open_settings(self, *, focus: bool = False) -> None:
        """As Configurações (modal); `focus` (o clique no aviso, F5.3) as traz à frente pela plataforma ao aparecer."""
        dlg = SettingsDialog(self.store, self.platform, self.hotkey_errors, self.widget, on_launch=self.launch_worker,
                             recording=self.recorder is not None)
        self._settings_dialog = dlg
        log.info("configurações abertas%s", " (gravando)" if self.recorder is not None else "")
        if focus:
            QTimer.singleShot(0, dlg, functools.partial(self._bring_to_front, dlg))
        try:
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
        finally:
            self._settings_dialog = None
        dlg.deleteLater()
        log.info("configurações fechadas (%s; as mudanças de valor estão no DEBUG de store.settings)",
                 "salvas" if accepted else "canceladas")
        if accepted:
            self._run_task(self.register_hotkeys, self._on_hotkeys_registered)
            from .app import attach_log

            err = attach_log(self.store)
            if err:
                self._notify_as(NoticeKind.ERROR, f"Log em arquivo indisponível: {err}")
            if self.recorder:
                self.recorder.cfg.apply_live(self.store)

    def quit(self) -> None:
        if self.recorder:
            r = QMessageBox.question(self.widget, "Sair", "Há uma gravação em andamento. Parar e salvar antes de sair?")
            if r != QMessageBox.StandardButton.Yes:
                return
            self._quitting = True
            rec = self.recorder
            self._timer.stop()
            self._stop_mute_poller()
            self._stop_live(detach=True)
            self._stop_partial()
            rec.stop()                                   # síncrono aqui: o usuário mandou sair
            if rec.muted:
                self.platform.mic_mute().set(False)
            self._finish(rec)
            self._clear_lease()
            self._enqueue_processing(rec.meeting_id)     # o worker é solto do app e termina a fila (P2, R2-12)
        log.info("app encerrado pelo usuário")
        self.player.stop()                               # o som para já; o resto da saída fica com o emergency_stop
        self.app.quit()                                  # aboutToQuit -> emergency_stop fecha atalhos e banco

    def _save_widget_pos(self, pos: QPoint) -> None:
        self.store.set_setting("ui.widget_pos", f"{pos.x()},{pos.y()}")

    def _saved_widget_pos(self) -> QPoint | None:
        raw = self.store.get_setting("ui.widget_pos")
        try:
            x, y = (int(v) for v in raw.split(","))
            return QPoint(x, y)
        except ValueError:
            return None

    def _widget_shortcuts(self) -> dict[str, str]:
        """F5.4 L2 (U-061): os atalhos globais das ações do widget, para a dica dos botões."""
        return {name: self.store.get_setting(f"hotkeys.{name}") or "" for name in ("pause", "mark", "mute", "stop")}

    def _saved_widget_size(self) -> tuple[int, int]:
        raw = self.store.get_setting("ui.widget_size") or "320x96"
        try:
            w, h = (int(v) for v in raw.lower().split("x"))
        except ValueError:
            return (320, 96)
        return (w, h)
