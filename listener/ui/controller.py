"""Controlador: estado (ocioso, gravando, pausado, salvando), warm-up, gravador, widget, bandeja, atalhos, mudo global.
Nada bloqueante na thread da interface (M-26): a parada roda numa QThread com estado "salvando…", o mudo do sistema é
lido por um poller próprio e os atalhos registram fora da thread do Qt. `emergency_stop` cobre logoff e desligamento (M-05).
F2: ao parar cria o job e lança o worker (subprocesso solto) quando couber; lease da pausa enquanto grava (Q7); polling da
fila a cada 2 s com job ativo e 30 s sem; aviso "pronta para revisão" e de reuniões processadas sem revisão.
F3: duas classes de worker (`launch_worker(light)`): o pesado só fora da gravação; o leve (jobs `llm`) a qualquer hora;
`_on_job_done` ramifica por `kind` (ata pronta x pronta para revisão).
F5: o leve roda também os `index` (índice da busca); a detecção de job concluído e a dica da bandeja os ignoram (a dica
acrescenta "· índice N"), e um `index` concluído nunca abre a revisão (F5A-05). Na abertura, a varredura do índice roda numa
`Task` e lança o leve se enfileirou algo (F5A-12); as edições da revisão e das abas da LLM (`on_edited`) reindexam a
reunião 30 s depois da última, e "Concluir", fechar a revisão e sair do app disparam na hora.
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
gravação só atualiza a janela dela."""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from contextlib import suppress

from PySide6.QtCore import QObject, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from ..capture.clock import Clock, SessionClock
from ..capture.recorder import RecordConfig, Recorder
from ..config.defaults import SPECS
from ..live.client import EXIT, LiveClient, live_models_dir, models_ready
from ..live.protocol import BYE, CAPTION, CPU, ERROR, READY, STATE, Hello
from ..llm import partial_task
from ..llm.prompts.common import stamp
from ..log import get_logger
from ..model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, AppState, EventType, Health, JobKind, JobStatus, JobStep, MeetingStatus
from ..store.jobs import HELD
from ..pipeline import review
from ..platform.common import process
from .live import LiveWindow, caption_key, caption_note, item_key, item_note
from .live_prepare import preparer, shutdown_preparer
from .queue import QueueDialog
from .review import ReviewDialog
from .settings_min import SettingsDialog
from .sources import SourceTable
from .summary import SummaryDialog
from .tasks import StreamTask, Task, live_threads, release, run_stream, run_task
from .transcript import TranscriptDialog
from .tray import TrayIcon
from .warmup import WarmupDialog
from .widget import MarkerPopup, RecordingWidget

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui")
HOTKEY_ACTIONS = ("start", "pause", "stop", "mark", "mute")
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
INDEX_DEBOUNCE_MS = 30_000           # F5: edição de falantes, título, ata ou ações: reindexa 30 s depois da última
SWEEP_WAIT_MS = 5000                 # sair do app: espera a varredura do índice antes de fechar o banco
LIVE_MODELS_MISSING = "modelos do ao vivo ausentes: prepare nas Configurações (Ao vivo)"
LIVE_STOPPED_TEXT = "A legenda ao vivo parou; a gravação continua normalmente."
PREPARE_INTERRUPTED = "interrompido pela gravação: prepare de novo depois dela (continua de onde parou)"
LIVE_RETRIES = 1                     # F4: uma nova tentativa por gravação quando o filho cai
LIVE_CLOSE_GRACE_S = 10.0            # Parar: o filho fecha a janela em curso em até 10 s; depois, kill
LIVE_EOF_GRACE_S = 8.0               # ...e transcreve a fila até 8 s depois do EOF (sobra folga para o bye)
PARTIAL_TICK_MS = 15_000             # F4 L5: confere se o resumo parcial venceu (o intervalo é relido a cada disparo, CP8)
PARTIAL_QUIT_WAIT_MS = 2500          # sair do app: espera a chamada cancelada do parcial terminar (cancelamento a cada 0,2 s)
_Task = Task                          # nome antigo mantido para quem ainda o cita (scripts)


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


class LateGlossaryLink:
    """Chamadas do glossário que voltam depois do "Gravar" de um warm-up (F3.1 R3-11): ficam guardadas até a reunião dele
    existir e então ganham o `meeting_id`; cada warm-up tem o seu (resposta atrasada não cai na reunião seguinte)."""

    def __init__(self, store: Store):
        self.store = store
        self.meeting_id: int | None = None
        self.pending: list[int] = []

    def add(self, call_id: int) -> None:
        if self.meeting_id is None:
            self.pending.append(call_id)
        else:
            self.store.llm_calls.set_meeting(call_id, self.meeting_id)

    def bind(self, meeting_id: int) -> None:
        self.meeting_id = meeting_id
        for call_id in self.pending:
            self.store.llm_calls.set_meeting(call_id, meeting_id)
        self.pending.clear()


class Controller(QObject):
    hotkey_pressed = Signal(str)
    live_caption = Signal(object)        # F4: dict da linha `caption` (channel, t_start_ms, t_end_ms, text, label, person_id...)
    live_state = Signal(object)          # F4: {"state": off | unavailable | starting | live | degraded | skipping | restarting |
                                         #      stopping | stopped | crashed, ...detalhes}; os nomes à parte, sem mudar
                                         #      `live_status`: names_ready | names_unavailable (com `reason`)
    partial_state = Signal(object)       # F4 L5: {"state": idle | running | done | error | stopped, "meeting_id", "message"}
    _live_message = Signal(object, object)   # (cliente, mensagem): da thread `live-read` para a da interface

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
        self.hotkey_pressed.connect(self._on_hotkey)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        # F2: fila de processamento
        self.now: Callable[[], datetime] = lambda: datetime.now().astimezone()
        self.lease_renew_s = LEASE_RENEW_S
        self._lease_at: float | None = None
        self._worker_spawned_at: dict[bool, float | None] = {False: None, True: None}     # por classe: pesado, leve (F3)
        self._seen_done: set[int] = set()
        self._no_llm_warned = False
        self._review_offered: set[int] = set()
        self._reviews: dict[int, ReviewDialog] = {}
        self._review_sigs: dict[int, tuple[int, ...]] = {}
        self._transcripts: dict[int, TranscriptDialog] = {}
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
        self.index_debounce_ms = INDEX_DEBOUNCE_MS
        self._index_timers: dict[int, QTimer] = {}
        self._index_reasons: dict[int, str] = {}
        self._sweeps: set[Task] = set()

    # ---------------------------------------------------------------- início
    def start(self) -> None:
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
                self.notify("Arquivo(s) de áudio ausente(s) na pasta do banco: " + ", ".join(missing))
            if self.first_run:
                self.notify(TRAY_TIP)
        self._run_task(self.register_hotkeys, self._on_hotkeys_registered)
        self._seen_done = {int(j["id"]) for j in self.store.jobs.list_jobs(limit=500, exclude_kinds=INDEX_KINDS)
                           if j["status"] == JobStatus.DONE}
        waiting = self.store.meetings.list_by_status(MeetingStatus.PROCESSED)
        if waiting:
            self.notify(f"{len(waiting)} reunião(ões) processada(s) aguardando revisão de falantes.")
        self._clear_lease()                                   # um app que caiu gravando não pode deixar a fila presa
        self._enqueue_leftovers()
        self._start_sweep()
        self._poll_queue()
        self._queue_timer.start()
        log.info("app iniciado; %d gravação(ões) interrompida(s); %d reunião(ões) sem revisão", interrupted, len(waiting))

    def notify(self, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage("listener-meet", message)

    # ---------------------------------------------------------------- índice da busca (F5)
    def _start_sweep(self) -> None:
        """Varredura do índice numa `Task` (F5A-12): reuniões processadas sem trechos e, com embedder pronto, as que têm trecho
        sem vetor do modelo ativo; com algo na fila, o worker leve sobe."""
        from ..rag import index as rag_index

        task = run_task(lambda: rag_index.sweep(self.store), self.store, self._on_sweep)
        self._sweeps.add(task)
        task.finished.connect(functools.partial(self._sweeps.discard, task))   # o `release` destrói a QThread depois

    def _on_sweep(self, result: Any) -> None:
        if self._quitting:
            return
        if isinstance(result, BaseException):
            log.warning("varredura do índice falhou: %s", result)
        elif result:
            self.launch_worker(light=True)

    def on_edited(self, meeting_id: int, reason: str = "edit", *, now: bool = False) -> None:
        """Uma edição que muda a busca (falantes, título, ata, executivo, ações): o `index` da reunião entra na fila
        `index_debounce_ms` depois da última edição (cada uma reinicia a espera); `now` enfileira já ("Concluir")."""
        self._index_reasons[meeting_id] = reason
        if now:
            self._flush_index(meeting_id)
            return
        timer = self._index_timers.get(meeting_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(functools.partial(self._flush_index, meeting_id))
            self._index_timers[meeting_id] = timer
        timer.setInterval(self.index_debounce_ms)
        timer.start()

    def _flush_index(self, meeting_id: int) -> None:
        from ..rag.index import enqueue_index

        timer = self._index_timers.pop(meeting_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        reason = self._index_reasons.pop(meeting_id, "edit")
        try:
            enqueue_index(self.store, meeting_id, reason)
        except Exception as e:  # a busca nunca atrapalha a edição
            log.warning("reunião %s: não enfileirei o índice da busca: %s", meeting_id, e)
            return
        self.launch_worker(light=True)

    def _flush_all_index(self) -> None:
        for mid in list(self._index_timers):
            self._flush_index(mid)

    def _run_task(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        run_task(fn, self.store, on_done)

    def register_hotkeys(self) -> dict[str, str]:
        """Registra os cinco atalhos (pode rodar fora da thread do Qt); devolve combo -> motivo dos que falharam (A13)."""
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
            self.notify(f"Atalhos não registraram: {result}")
        elif result:
            self.notify("Atalhos que não registraram:\n" + "\n".join(f"{k}: {v}" for k, v in result.items()))

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

    # ---------------------------------------------------------------- gravação
    def open_warmup(self) -> None:
        if self.state != AppState.IDLE or self._warmup_open:
            self.show_widget()
            return
        self._warmup_open = True
        try:
            link = LateGlossaryLink(self.store)
            dlg = WarmupDialog(self.platform, self.store, on_settings=self.open_settings, on_late_call=link.add)
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            cfg, fields = dlg.result_config() if accepted else (None, {})
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
        meeting_id = self.store.create_meeting(fields.get("title"), fields.get("platform"), fields.get("preset_id"),
                                               fields.get("participants"), fields.get("is_in_room", False),
                                               glossary=fields.get("glossary"), expected_people_json=fields.get("expected_people_json"))
        for call_id in fields.get("glossary_call_ids") or ():               # as chamadas do glossário nascem sem reunião (F3A-11)
            self.store.llm_calls.set_meeting(int(call_id), meeting_id)
        log.info("gravação %s iniciada: mic=%s fonte=%s", meeting_id,
                 cfg.mic_device.name if cfg.mic_device else "padrão", cfg.source.label or cfg.source.kind)
        mute_ok = self.platform.mic_mute().set_device(cfg.mic_device)
        self.platform.keep_awake(True)
        rec = Recorder(self.platform, self.store, meeting_id, cfg, clock=self.clock_factory() if self.clock_factory else None)
        try:
            rec.start()
        except Exception as e:
            log.exception("gravação %s não iniciou", meeting_id)
            self.platform.keep_awake(False)
            self.store.delete_meeting(meeting_id)
            QMessageBox.critical(None, "listener-meet", f"A gravação não iniciou: {e}")
            return False
        self.recorder = rec
        self.state = AppState.RECORDING
        self._marked_captions = set()                    # novos conjuntos: a janela da gravação anterior fica com os dela
        self._marked_items = set()
        prep = preparer()
        if prep.running and prep.cancel(PREPARE_INTERRUPTED):   # F4 item 10: o download não disputa a rede com a reunião
            self.notify(f"Download dos modelos do ao vivo {PREPARE_INTERRUPTED}.")
        try:
            self._start_live(rec, fields)
        except Exception:                                # o ao vivo nunca impede a gravação
            log.exception("gravação %s segue sem ao vivo", meeting_id)
            self._set_live_status("unavailable", reason="erro")
        self._start_partial(meeting_id)
        live_line = self.live is not None and bool(self.store.get(SPECS["live.in_widget"]))
        self.widget = RecordingWidget(size=self._saved_widget_size(), pos=self._saved_widget_pos(), live_line=live_line)
        w = self.widget
        w.live_clicked.connect(self.open_live_window)
        w.set_live_state(self.live_status, self._live_badge_tip())
        w.pause_clicked.connect(self.toggle_pause)
        w.mark_clicked.connect(self.mark)
        w.mute_clicked.connect(self.toggle_mute)
        w.stop_clicked.connect(self.stop_recording)
        w.cancel_clicked.connect(self.cancel_recording)
        w.switch_source_clicked.connect(self.switch_source)
        w.settings_requested.connect(self.open_settings)
        w.moved.connect(lambda p: self.store.set_setting("ui.widget_pos", f"{p.x()},{p.y()}"))
        w.show()
        if self.tray:
            self.tray.set_state(AppState.RECORDING)
        self._timer.start()
        self._start_mute_poller()
        self._write_lease(rec)
        notices = list(rec.warnings)
        if not mute_ok and cfg.mic_device is not None:
            notices.append(f"Não achei o endpoint de '{cfg.mic_device.name}': o mudo global vai no microfone padrão do sistema.")
        if notices:
            self.notify("\n".join(notices))
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
            self.notify("Não consegui mutar o microfone no sistema; gravando silêncio mesmo assim.")

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
            self.recorder.switch_source(table.selected_spec())
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
        self._stop_live()
        self._stop_partial()
        self._end_live_window(rec.meeting_id, "gravação parada: só leitura")
        self._run_task(rec.stop, lambda result: self._on_stopped(rec, result))

    def _on_stopped(self, rec: Recorder, result: Any) -> None:
        self._timer.stop()
        if isinstance(result, BaseException):
            log.error("parada da gravação %s falhou: %s", rec.meeting_id, result)
            stats = rec.stats()
            self.notify(f"A gravação terminou com erro: {result}")
        else:
            stats = result
        if rec.muted:
            self.platform.mic_mute().set(False)
        self._finish(rec)
        self._clear_lease()
        self._enqueue_processing(rec.meeting_id)
        if not self._quitting:
            SummaryDialog(self.store, rec.meeting_id, stats).exec()

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

    def emergency_stop(self) -> None:
        """Logoff, desligamento ou `quit()` por fora: para e salva sem diálogo (M-05); o resumo fica para a próxima vez."""
        rec = self.recorder
        if rec is not None and not rec.stopped:
            log.warning("encerramento durante a gravação %s: parando sem diálogo", rec.meeting_id)
            rec.cfg.stop_timeout_s = min(rec.cfg.stop_timeout_s, EMERGENCY_STOP_BUDGET_S)
            self._timer.stop()
            self._stop_mute_poller()
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
        with suppress(Exception):
            self._flush_all_index()                      # F5: edição recente não espera os 30 s para entrar na busca
        self._quitting = True
        self._queue_timer.stop()
        try:
            self.platform.hotkeys().stop()
        except Exception as e:
            log.debug("parar atalhos: %s", e)
        shutdown_preparer()                              # F4: o download dos modelos não segue órfão
        self._wait_partials()                            # a chamada do parcial em voo é cancelada antes de o banco fechar
        for task in list(self._sweeps):                  # F5: a varredura do índice usa o banco numa QThread
            with suppress(RuntimeError):                 # já destruída pelo `release`
                task.wait(SWEEP_WAIT_MS)
        if self.live_window is not None:                 # guarda a posição antes de o banco fechar
            with suppress(Exception):
                self.live_window.close()
        self.store.close()

    def _finish(self, rec: Recorder) -> None:
        self.platform.keep_awake(False)
        self.platform.mic_mute().set_device(None)
        self._teardown_widget()
        if self.recorder is rec:
            self.recorder = None
        self.state = AppState.IDLE
        if self.tray:
            self.tray.set_state(AppState.IDLE)

    def _teardown_widget(self) -> None:
        if self.widget:
            self.widget.close()
            self.widget.deleteLater()
            self.widget = None

    # ---------------------------------------------------------------- mudo do sistema
    def _start_mute_poller(self) -> None:
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
        snap = rec.snapshot
        db = health.get("db", Health.OK)
        if db == Health.ALERT:
            since = int(rec.elapsed() - (snap.pending_since or rec.elapsed()))
            w.show_alert(f"banco não responde há {since}s; {snap.pending} pedaço(s) retido(s) em memória")
        elif health.get("L") == Health.LOST:
            w.show_alert("microfone desconectado; tentando reabrir…")
        elif health.get("R") == Health.LOST:
            w.show_alert("saída desconectada; tentando reabrir…", can_switch=True)
        elif health.get("R") == Health.ALERT:
            w.show_alert("saída sem sinal: a reunião está tocando em outro app?", can_switch=True)
        elif health.get("L") == Health.ALERT:
            w.show_alert(f"microfone sem sinal há mais de {int(rec.cfg.l_alert_seconds)}s")
        elif db == Health.WARN:
            w.show_alert(f"banco lento: {snap.pending} pedaço(s) na fila")
        else:
            w.show_alert(None)

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

    def _enqueue_processing(self, meeting_id: int) -> None:
        """Parou: a reunião `gravada` entra na fila (D-014) e o worker é lançado se couber."""
        m = self.store.get_meeting(meeting_id)
        if m is None or m.get("status") not in (MeetingStatus.RECORDED, MeetingStatus.INTERRUPTED):
            return
        if not self.store.get(SPECS["pipeline.auto_start"]):
            log.info("reunião %s gravada; processamento automático desligado", meeting_id)
            return
        job_id = self.store.jobs.enqueue(meeting_id)
        log.info("reunião %s na fila de processamento (job %s)", meeting_id, job_id)
        self.launch_worker()
        self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS)

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
        morto à fila (R2-01). O leve (`light`, jobs `llm`) é lançado mesmo durante a gravação (F3A-13)."""
        kinds = LIGHT_JOB_KINDS if light else HEAVY_JOB_KINDS
        if not light and self.recorder is not None:
            return False                                           # o pesado espera o fim da gravação (Q7)
        if not self.store.jobs.active(kinds=kinds) or self.store.jobs.held_fresh(kinds=kinds):
            return False
        if self.store.get(SPECS["pipeline.paused_manual"]):
            return False
        spawned_at = self._worker_spawned_at[light]
        if spawned_at is not None and time.monotonic() - spawned_at < WORKER_SPAWN_COOLDOWN_S:
            return False
        argv = process.worker_command(light=light)
        env = process.worker_env(1 if light else self.store.get(SPECS["pipeline.torch_threads"]))
        try:
            pid = self.platform.spawn_detached(argv, env)
        except Exception as e:
            log.exception("não consegui lançar o worker%s", " leve" if light else "")
            self.notify(f"Não consegui iniciar o processamento: {e}")
            return False
        self._worker_spawned_at[light] = time.monotonic()
        log.info("worker%s lançado (pid %s): %s", " leve" if light else "", pid, " ".join(argv))
        return True

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
        if running is not None and str(running.get("step") or "") in REVIEW_STEPS:
            mid = int(running["meeting_id"])
            if mid not in self._review_offered:
                self._review_offered.add(mid)                         # P1: a revisão abre enquanto o Whisper roda
                self.open_review(mid)
        for mid, dlg in list(self._reviews.items()):            # só quando os clusters mudaram: não rouba o foco (R2-26)
            sig = self._clusters_signature(mid)
            if sig != self._review_sigs.get(mid):
                self._review_sigs[mid] = sig
                dlg.reload()
        if any(str(j.get("kind")) in HEAVY_JOB_KINDS for j in active_jobs):
            self.launch_worker()
        if any(str(j.get("kind")) in LIGHT_JOB_KINDS for j in active_jobs):
            self.launch_worker(light=True)                        # rede e JSON não disputam CPU com a gravação (F3A-13)
        self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS if active_jobs else QUEUE_POLL_IDLE_MS)

    def _on_job_done(self, job: Any) -> None:
        mid = int(job["meeting_id"])
        if str(job.get("kind")) in INDEX_KINDS:
            # F5A-05: o índice não avisa nem abre a revisão; a revisão e a transcrição não mostram nada dele (recarregar a
            # transcrição levaria a rolagem para o topo a cada edição), então não há janela aberta a recarregar ainda
            log.debug("reunião %s: índice concluído (job %s)", mid, job["id"])
            return
        m = self.store.get_meeting(mid) or {}
        title = m.get("title") or f"reunião {mid}"
        if str(job.get("kind")) == JobKind.LLM:
            self.notify(f"{title}: {self._llm_done_text(job)}")
            if mid in self._reviews:
                self._reviews[mid].reload()
        else:
            self.notify(f"{title}: pronta para revisão de falantes.")
            self._warn_no_llm(mid, int(job["id"]))
            self._review_offered.add(mid)
            self.open_review(mid)
        if mid in self._transcripts:
            self._transcripts[mid].reload()

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

    def open_review(self, meeting_id: int) -> ReviewDialog:
        """Revisão mínima de falantes (F2 item 13): não modal, uma por reunião; sem voz pendente mostra "tudo confirmado"."""
        dlg = self._reviews.get(meeting_id)
        if dlg is None:
            dlg = ReviewDialog(self.store, meeting_id, None, on_transcript=self.open_transcript, on_launch=self.launch_worker,
                               on_edited=self.on_edited)
            dlg.finished.connect(functools.partial(self._review_closed, meeting_id))
            self._reviews[meeting_id] = dlg
            self._review_sigs[meeting_id] = self._clusters_signature(meeting_id)
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

    def _review_closed(self, meeting_id: int, _result: int) -> None:
        dlg = self._reviews.pop(meeting_id, None)
        self._review_sigs.pop(meeting_id, None)
        if dlg is not None:
            dlg.deleteLater()
        if meeting_id in self._index_timers:                     # fechar a revisão não espera os 30 s
            self._flush_index(meeting_id)

    def open_transcript(self, meeting_id: int) -> TranscriptDialog:
        dlg = self._transcripts.get(meeting_id)
        if dlg is None:
            dlg = TranscriptDialog(self.store, meeting_id, None, on_review=self.open_review, on_launch=self.launch_worker)
            dlg.finished.connect(functools.partial(self._transcript_closed, meeting_id))
            self._transcripts[meeting_id] = dlg
        else:
            dlg.reload()
        dlg.show()
        dlg.raise_()
        return dlg

    def _transcript_closed(self, meeting_id: int, _result: int) -> None:
        dlg = self._transcripts.pop(meeting_id, None)
        if dlg is not None:
            dlg.deleteLater()

    def open_queue(self) -> None:
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
        """Depois de `rec.start()`: caixas do warm-up (`live_enabled`, `live_names`; ausentes = padrão das Configurações),
        modelos prontos, filho lançado e desvio ligado. Qualquer falha só deixa a gravação sem ao vivo."""
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
                                 eof_grace_s=LIVE_EOF_GRACE_S)
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
            self.notify(f"A legenda ao vivo não iniciou: {e}")
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
            self.notify(LIVE_STOPPED_TEXT + (f"\n{reason}" if reason else ""))

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
                             marked_items=self._marked_items)
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
        """A cada 15 s: o intervalo relido agora (CP8; 0 desliga o automático); vencido, roda se houver legenda nova."""
        rec = self.recorder
        if (self._partial_meeting is None or rec is None or rec.meeting_id != self._partial_meeting
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
            return partial_task.run_partial(store, mid, cancelled=cancelled)

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
            self.notify(text[:1].upper() + text[1:] + ".")
        self._set_partial({"state": "error", "message": text})

    # ---------------------------------------------------------------- outros
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.store, self.platform, self.hotkey_errors, self.widget, on_launch=self.launch_worker,
                             recording=self.recorder is not None)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        dlg.deleteLater()
        if accepted:
            self._run_task(self.register_hotkeys, self._on_hotkeys_registered)
            from .app import attach_log

            err = attach_log(self.store)
            if err:
                self.notify(f"Log em arquivo indisponível: {err}")
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
        self.app.quit()                                  # aboutToQuit -> emergency_stop fecha atalhos e banco

    def _saved_widget_pos(self) -> QPoint | None:
        raw = self.store.get_setting("ui.widget_pos")
        try:
            x, y = (int(v) for v in raw.split(","))
            return QPoint(x, y)
        except ValueError:
            return None

    def _saved_widget_size(self) -> tuple[int, int]:
        raw = self.store.get_setting("ui.widget_size") or "320x96"
        try:
            w, h = (int(v) for v in raw.lower().split("x"))
        except ValueError:
            return (320, 96)
        return (w, h)
