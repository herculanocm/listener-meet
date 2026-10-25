"""Controlador: estado (ocioso, gravando, pausado, salvando), warm-up, gravador, widget, bandeja, atalhos, mudo global.
Nada bloqueante na thread da interface (M-26): a parada roda numa QThread com estado "salvando…", o mudo do sistema é
lido por um poller próprio e os atalhos registram fora da thread do Qt. `emergency_stop` cobre logoff e desligamento (M-05)."""
from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QObject, QPoint, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from ..capture.clock import Clock
from ..capture.recorder import RecordConfig, Recorder
from ..log import get_logger
from ..model import AppState, Health
from .settings_min import SettingsDialog
from .sources import SourceTable
from .summary import SummaryDialog
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
_live_threads: set[QThread] = set()     # QThreads sem parent ficam referenciadas até `finished` (senão o Qt aborta)


def _release(thread: QThread) -> None:
    _live_threads.discard(thread)
    thread.deleteLater()


class _Task(QThread):
    """Roda uma função fora da thread da interface e devolve o resultado (ou a exceção) por sinal."""
    done = Signal(object)

    def __init__(self, fn: Callable[[], Any], store: Store | None):
        super().__init__(None)
        self.fn = fn
        self.store = store

    @override
    def run(self) -> None:
        result: Any
        try:
            result = self.fn()
        except BaseException as e:  # a exceção é entregue a quem pediu a tarefa
            log.exception("tarefa em segundo plano falhou")
            result = e
        finally:
            if self.store is not None:
                self.store.close_thread()
        self.done.emit(result)


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
        log.info("app iniciado; %d gravação(ões) interrompida(s)", interrupted)

    def notify(self, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage("listener-meet", message)

    def _run_task(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        task = _Task(fn, self.store)
        _live_threads.add(task)
        task.done.connect(on_done)
        task.finished.connect(functools.partial(_release, task))
        task.start()

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
            dlg = WarmupDialog(self.platform, self.store)
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
                                               fields.get("participants"), fields.get("is_in_room", False))
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
        self._quitting = True
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
            poller.finished.connect(functools.partial(_release, poller))
            _live_threads.add(poller)                     # referência viva até `finished` (sem parent, M-12)
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

    # ---------------------------------------------------------------- outros
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.store, self.platform, self.hotkey_errors, self.widget)
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
