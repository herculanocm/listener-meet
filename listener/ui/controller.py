"""Controlador: estado (ocioso, gravando, pausado, salvando), warm-up, gravador, widget, bandeja, atalhos, mudo global.
Nada bloqueante na thread da interface (M-26): a parada roda numa QThread com estado "salvando…", o mudo do sistema é
lido por um poller próprio e os atalhos registram fora da thread do Qt. `emergency_stop` cobre logoff e desligamento (M-05).
F2: ao parar cria o job e lança o worker (subprocesso solto) quando couber; lease da pausa enquanto grava (Q7); polling da
fila a cada 2 s com job ativo e 30 s sem; aviso "pronta para revisão" e de reuniões processadas sem revisão.
F3: duas classes de worker (`launch_worker(light)`): o pesado só fora da gravação; o leve (jobs `llm`) a qualquer hora;
`_on_job_done` ramifica por `kind` (ata pronta x pronta para revisão)."""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from contextlib import suppress

from PySide6.QtCore import QObject, QPoint, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from ..capture.clock import Clock
from ..capture.recorder import RecordConfig, Recorder
from ..config.defaults import SPECS
from ..log import get_logger
from ..model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, AppState, Health, JobKind, JobStatus, JobStep, MeetingStatus
from ..store.jobs import HELD
from ..pipeline import review
from ..platform.common import process
from .queue import QueueDialog
from .review import ReviewDialog
from .settings_min import SettingsDialog
from .sources import SourceTable
from .summary import SummaryDialog
from .tasks import Task, live_threads, release, run_task
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


class Controller(QObject):
    hotkey_pressed = Signal(str)

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
        self._review_offered: set[int] = set()
        self._reviews: dict[int, ReviewDialog] = {}
        self._review_sigs: dict[int, tuple[int, int]] = {}
        self._transcripts: dict[int, TranscriptDialog] = {}
        self._queue_dialog: QueueDialog | None = None
        self._queue_timer = QTimer(self)
        self._queue_timer.setInterval(QUEUE_POLL_IDLE_MS)
        self._queue_timer.timeout.connect(self._poll_queue)

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
        self._seen_done = {int(j["id"]) for j in self.store.jobs.list_jobs(limit=500) if j["status"] == JobStatus.DONE}
        waiting = self.store.meetings.list_by_status(MeetingStatus.PROCESSED)
        if waiting:
            self.notify(f"{len(waiting)} reunião(ões) processada(s) aguardando revisão de falantes.")
        self._clear_lease()                                   # um app que caiu gravando não pode deixar a fila presa
        self._enqueue_leftovers()
        self._poll_queue()
        self._queue_timer.start()
        log.info("app iniciado; %d gravação(ões) interrompida(s); %d reunião(ões) sem revisão", interrupted, len(waiting))

    def notify(self, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage("listener-meet", message)

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
            dlg = WarmupDialog(self.platform, self.store, on_settings=self.open_settings)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                cfg, fields = dlg.result_config()
                self.start_recording(cfg, fields)
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
        if fields.get("glossary_call_id"):                                   # a chamada do glossário nasceu sem reunião (F3A-11)
            self.store.llm_calls.set_meeting(int(fields["glossary_call_id"]), meeting_id)
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
        self.widget = RecordingWidget(size=self._saved_widget_size(), pos=self._saved_widget_pos())
        w = self.widget
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

    def stop_recording(self) -> None:
        """Parada assíncrona: o widget mostra "salvando…" enquanto a thread de trabalho esvazia a fila."""
        rec = self.recorder
        if rec is None or self.state == AppState.SAVING:
            return
        self.state = AppState.SAVING
        if self.tray:
            self.tray.set_state(AppState.SAVING)
        self._stop_mute_poller()
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
        self._queue_timer.stop()
        try:
            self.platform.hotkeys().stop()
        except Exception as e:
            log.debug("parar atalhos: %s", e)
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
        """O worker lê entre janelas e espera; vencida (app caiu), segue sozinho (F2A-05)."""
        if not self.store.get(SPECS["pipeline.pause_on_record"]):
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
        """Lê a fila (um SELECT): tooltip da bandeja, "pronta para revisão", "ata pronta" e relançamento dos workers."""
        try:
            jobs = self.store.jobs.list_jobs(limit=50)
            active_jobs = self.store.jobs.active()
        except Exception as e:
            log.debug("fila: %s", e)
            return
        done_now = {int(j["id"]): j for j in jobs if j["status"] == JobStatus.DONE}
        active = len(active_jobs)
        running = next((j for j in active_jobs if j["status"] in HELD and str(j.get("kind")) in HEAVY_JOB_KINDS), None)
        if self.tray is not None:
            if running is not None:
                pct = float(running["progress"] or 0) * 100
                self.tray.set_queue_tip(f"processando {len(done_now) + 1}/{len(done_now) + active} · {pct:.0f} %")
            elif active:
                self.tray.set_queue_tip(f"{active} na fila")
            else:
                self.tray.set_queue_tip("")
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
        self._queue_timer.setInterval(QUEUE_POLL_ACTIVE_MS if active else QUEUE_POLL_IDLE_MS)

    def _on_job_done(self, job: Any) -> None:
        mid = int(job["meeting_id"])
        m = self.store.get_meeting(mid) or {}
        title = m.get("title") or f"reunião {mid}"
        if str(job.get("kind")) == JobKind.LLM:
            self.notify(f"{title}: {self._llm_done_text(job)}")
            if mid in self._reviews:
                self._reviews[mid].reload()
        else:
            self.notify(f"{title}: pronta para revisão de falantes.")
            self._review_offered.add(mid)
            self.open_review(mid)
        if mid in self._transcripts:
            self._transcripts[mid].reload()

    def _llm_done_text(self, job: Any) -> str:
        """"ata pronta · US$ 0,32" (U-020); o custo entra quando a camada de LLM grava `llm_calls`."""
        return "ata pronta."

    def open_review(self, meeting_id: int) -> ReviewDialog:
        """Revisão mínima de falantes (F2 item 13): não modal, uma por reunião; sem voz pendente mostra "tudo confirmado"."""
        dlg = self._reviews.get(meeting_id)
        if dlg is None:
            dlg = ReviewDialog(self.store, meeting_id, None, on_transcript=self.open_transcript)
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

    def _clusters_signature(self, meeting_id: int) -> tuple[int, int]:
        row = self.store._q("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM clusters WHERE meeting_id = ?", (meeting_id,))[0]
        return int(row[0]), int(row[1])

    def _review_closed(self, meeting_id: int, _result: int) -> None:
        dlg = self._reviews.pop(meeting_id, None)
        self._review_sigs.pop(meeting_id, None)
        if dlg is not None:
            dlg.deleteLater()

    def open_transcript(self, meeting_id: int) -> TranscriptDialog:
        dlg = self._transcripts.get(meeting_id)
        if dlg is None:
            dlg = TranscriptDialog(self.store, meeting_id, None, on_review=self.open_review)
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

    # ---------------------------------------------------------------- outros
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.store, self.platform, self.hotkey_errors, self.widget, on_launch=self.launch_worker)
        if dlg.exec() == QDialog.DialogCode.Accepted:
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
