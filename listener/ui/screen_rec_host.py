"""F5.27 (lote 2): a gravação da tela do lado da interface (`ScreenRecHost`, em `controller.screen`); o controlador só liga os
ganchos. `start` confere o monitor (fora do Windows não há monitores: False, o motivo no log), cria o arquivo da tela do mês
(`store.screen.ensure_file`, papel APP, fora de transação) e lança o filho `--screen` (`ScreenClient`) com o `t0_perf` da sessão,
a taxa, o lado máximo e a pausa da reunião; com a tela já gravando nesta reunião vira troca, e com um filho anterior dela ainda
gravando o último pedaço, o lançamento espera ele sair (o `idx` segue a ordem do tempo). Eventos, no tempo da sessão que o filho
informa: `screen_start` (número e tamanho; `back` quando o mesmo monitor volta), `screen_switch`, `screen_lost` (o número) e
`screen_stop` (as estatísticas, os pedaços gravados e não gravados e `reason`: "usuario", "reuniao", "perdido" = a reunião parou
com o monitor fora, ou "queda"); nunca a chave do monitor. Avisos na linha de alerta do widget (`alert()`, lida pelo `_tick` do
controlador sem alerta dos dispositivos): U-183 até o monitor voltar e a falha de leitura, apagada no primeiro quadro bom; a
primeira queda relança, a segunda para a tela com o aviso (`on_error`). `stop` (a reunião parou): EOF no filho, que grava o
último pedaço; `then_wait(rec.stop)` faz a tarefa da parada esperar o filho sair (até `STOP_WAIT_S`) antes da fila; passado o
prazo, segue (o filho grava até o kill do `client.CLOSE_GRACE_S`). `shutdown` (a saída) para, espera, mata o que sobrou e grava
o `screen_stop` antes de o banco fechar; `cancel` mata o filho antes de a reunião ser apagada.
`settings_changed` leva a taxa e o lado (gravados no OK das Configurações) ao próximo pedaço; ao começar a tela, o widget sai da
captura. As mensagens do filho viram o sinal `_message`, enfileirado para a interface; só `wait_closed` roda na tarefa da parada."""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from ..capture.clock import SessionClock
from ..config.defaults import SPECS
from ..log import get_logger
from ..model import EventType
from ..platform.base import Platform
from ..platform.common import process
from ..platform.screen_types import Monitor
from ..screen_rec import client as sc
from ..screen_rec import recorder
from ..store import Store

log = get_logger("ui.screen_rec_host")
OFF, STARTING, RECORDING, PAUSED, LOST, FAILING, STOPPED = "off", "starting", "recording", "paused", "lost", "failing", "stopped"
LOST_TEXT = "A gravação da tela parou: o {label} saiu. O áudio segue."     # U-183
FAILING_TEXT = "A gravação da tela não está conseguindo ler o monitor."
CRASHED_TEXT = "A gravação da tela parou por um erro. O áudio segue."
NOT_STARTED_TEXT = "A gravação da tela não começou (o monitor escolhido ou o arquivo da tela). O áudio segue."
STOP_WAIT_S, SHUTDOWN_WAIT_S, RETRIES = 10.0, 10.0, 1   # a espera do último pedaço na parada e na saída; um relançamento (pergunta 8)


def session_t0(rec: Any) -> float:
    """O `perf_counter` do início da sessão do gravador (o relógio manual dos testes vira o equivalente)."""
    return rec.clock.t0_perf if isinstance(rec.clock, SessionClock) else time.perf_counter() - rec.clock.rel()


class ScreenRecHost(QObject):
    state_changed = Signal(str, str)             # (estado, "Monitor 2" ou "")
    _message = Signal(object, object)            # (cliente, mensagem): da `screen-read` para a thread da interface

    def __init__(self, platform: Platform, store: Store, *, on_error: Callable[[str], None] = lambda _t: None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.platform, self.store, self.on_error = platform, store, on_error
        self._state, self._label, self._shown = OFF, "", (OFF, "")
        self._session: sc.ChildRun | None = None
        self._sessions: list[sc.ChildRun] = []      # vivos: o atual e os que fecham
        self._lock = threading.Lock()
        self._meeting: tuple[int, float] | None = None
        self._paused, self._retries = False, 0
        self._alert: str | None = None
        self._pending: tuple[int, float, str] | None = None
        self._params, self._closed = (0, 0), False
        self._widget: Any = None
        self._message.connect(self._on_message, Qt.ConnectionType.QueuedConnection)

    @property
    def state(self) -> str:
        return self._state

    @property
    def monitor_label(self) -> str:
        return self._label if self._state != OFF else ""

    def alert(self) -> str | None:
        return self._alert

    # ---------------------------------------------------------------- começar
    def start(self, meeting_id: int, t0_perf: float, monitor_key: str) -> bool:
        if self._closed:
            return False
        found = self._find(monitor_key)
        if found is None:
            self.on_error(NOT_STARTED_TEXT)
            return False
        self._meeting = (meeting_id, t0_perf)
        cur = self._session
        if cur is not None and cur.meeting_id == meeting_id:
            self.switch(monitor_key)
            return True
        try:
            self.store.screen.ensure_file(meeting_id)
        except Exception:
            log.exception("reunião %s: arquivo da tela não criado; a tela não será gravada", meeting_id)
            self.on_error(NOT_STARTED_TEXT)
            return False
        label = sc.short_label(found[1])
        if any(s.meeting_id == meeting_id for s in self._snapshot()):   # o filho anterior grava o último pedaço: espera ele
            self._pending = (meeting_id, t0_perf, monitor_key)
            self._label = label
            self._set_state(STARTING)
            return True
        return self._launch(meeting_id, t0_perf, monitor_key, label)

    def _find(self, key: str) -> tuple[int, Monitor] | None:
        monitors = self.platform.monitors()
        if not monitors:
            log.info("tela não gravada: sem monitores nesta plataforma")
            return None
        found = sc.find_monitor(monitors, key)
        if found is None:
            log.info("tela não gravada: o monitor escolhido não está conectado (%d monitor(es))", len(monitors))
        return found

    def _launch(self, meeting_id: int, t0_perf: float, key: str, label: str) -> bool:
        fps, max_side = self._read_params()
        hello = sc.ScreenHello(t0_perf, key, fps=fps, max_side=max_side, paused=self._paused)
        argv = process.screen_command(self.store.base_dir, meeting_id)
        client = sc.ScreenClient(self.platform.spawn_child, argv, None, hello, lambda _m: None, meeting_id=meeting_id)
        client.on_message = functools.partial(self._message.emit, client)
        try:
            client.start()
        except Exception as e:
            log.exception("reunião %s: o filho da tela não iniciou", meeting_id)
            self._set_state(STOPPED)
            self.on_error(f"A gravação da tela não iniciou ({type(e).__name__}).")   # sem a mensagem: pode trazer um caminho
            return False
        session = sc.ChildRun(client, meeting_id, t0_perf, key, label)
        with self._lock:
            self._sessions.append(session)
        self._session, self._params, self._label = session, (fps, max_side), label
        self._set_state(STARTING)
        self._exclude_widget()
        log.info("reunião %s: tela começando no %s (%d quadro(s) por segundo, lado até %d px)", meeting_id, label, fps, max_side)
        return True

    def _read_params(self) -> tuple[int, int]:
        try:
            return int(self.store.get(SPECS["screen_rec.fps"])), int(self.store.get(SPECS["screen_rec.max_side"]))
        except Exception as e:                    # banco ocupado: os padrões
            log.debug("parâmetros da tela não lidos: %s", e)
            return int(SPECS["screen_rec.fps"].default), int(SPECS["screen_rec.max_side"].default)

    # ---------------------------------------------------------------- durante a reunião
    def pause(self) -> None:
        self._paused = True
        if self._session is not None:
            self._session.client.send({"type": sc.PAUSE})
        if self._state in (RECORDING, FAILING):
            self._alert = None if self._alert == FAILING_TEXT else self._alert
            self._set_state(PAUSED)

    def resume(self) -> None:
        self._paused = False
        if self._session is not None:
            self._session.client.send({"type": sc.RESUME})
        if self._state == PAUSED:
            self._set_state(RECORDING)

    def switch(self, monitor_key: str) -> None:
        cur = self._session
        if cur is None:
            if self._meeting is not None:
                self.start(*self._meeting, monitor_key)
            return
        if (monitor_key == cur.key and self._state != LOST) or self._find(monitor_key) is None:
            return
        cur.key = monitor_key
        cur.client.send({"type": sc.SWITCH, "monitor_key": monitor_key})

    def settings_changed(self) -> None:
        params = self._read_params()
        if self._session is not None and params != self._params:
            self._params = params
            self._session.client.send({"type": sc.PARAMS, "fps": params[0], "max_side": params[1]})
            log.info("tela: %d quadro(s) por segundo, lado até %d px a partir do próximo pedaço", *params)

    def stop_screen(self) -> None:
        """O usuário parou só a tela; a reunião segue (e pode recomeçar a tela pelo widget)."""
        self._close_current("usuario")
        self._pending, self._alert = None, None
        self._set_state(STOPPED)

    # ---------------------------------------------------------------- fim da reunião
    def stop(self) -> None:
        """A reunião parou: EOF no filho (o último pedaço é fechado e gravado); a espera é do `then_wait`."""
        self._close_current("perdido" if self._state == LOST else "reuniao")
        self._pending, self._meeting, self._paused, self._retries, self._alert = None, None, False, 0, None
        self._set_state(OFF)

    def then_wait(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        """`fn` (a parada do gravador, na tarefa da parada) seguida da espera do filho da tela, antes do processamento."""
        def run() -> Any:
            try:
                return fn()
            finally:
                self.wait_closed(STOP_WAIT_S)
        return run

    def wait_closed(self, timeout_s: float) -> bool:
        """Espera os filhos que fecham saírem (qualquer thread); True se todos saíram no prazo."""
        deadline = time.monotonic() + timeout_s
        ok = all(s.client.wait_closed(deadline - time.monotonic()) for s in self._snapshot())
        if not ok:
            log.warning("o filho da tela ainda grava depois de %.0f s; segue sem esperar (o kill vem no prazo dele)", timeout_s)
        return ok

    def cancel(self) -> None:
        """Cancelar: mata os filhos desta reunião antes de ela ser apagada; nenhum evento (a reunião some)."""
        meeting = self._session.meeting_id if self._session is not None else (self._meeting[0] if self._meeting else None)
        for s in self._snapshot():
            if s.meeting_id == meeting:
                s.cancelled = True
                s.client.terminate()
                self._drop(s)
        self._session = None
        self.stop()

    def shutdown(self) -> None:
        """Saída do app: para, espera (até `SHUTDOWN_WAIT_S`, depois kill) e grava o `screen_stop` antes de o banco fechar."""
        if self._closed:
            return
        self.stop()
        self.wait_closed(SHUTDOWN_WAIT_S)
        for s in self._snapshot():
            if not s.client.exited:
                s.client.terminate()
            if s.client.bye is not None:
                self._bye(s, s.client.bye)
            elif not s.reason:
                s.reason = "queda"
            self._finalize(s)
            self._drop(s)
        self._closed = True

    def _close_current(self, reason: str) -> None:
        s, self._session = self._session, None
        if s is not None:
            s.reason = reason
            s.client.close()
            log.info("reunião %s: tela parando (%s)", s.meeting_id, reason)

    # ---------------------------------------------------------------- widget
    def attach_widget(self, widget: Any, meeting_id: int, t0_perf: float) -> None:
        """O widget da gravação: o estado, os três pedidos do menu da tela e a exclusão da captura com a tela ativa. Ligação
        direta: um nome divergente quebra aqui, e não vira um menu que não faz nada."""
        self._widget = widget
        self._meeting = (meeting_id, t0_perf)
        widget.destroyed.connect(self._widget_gone)     # um método, nunca um lambda com `self` (F5.3: prendia o widget)
        self.state_changed.connect(widget.set_screen_state)
        widget.set_screen_state(self._state, self.monitor_label)
        widget.screen_start_requested.connect(self._start_requested)
        widget.screen_switch_requested.connect(self.switch)
        widget.screen_stop_requested.connect(self.stop_screen)
        if self._session is not None:
            self._exclude_widget()

    def _widget_gone(self, *_a: Any) -> None:
        self._widget = None

    def _start_requested(self, monitor_key: str) -> None:
        if self._meeting is not None:
            self.start(*self._meeting, monitor_key)

    def _exclude_widget(self) -> None:
        w = self._widget
        if w is None:
            return
        try:
            ok = self.platform.exclude_from_capture(int(w.winId()))
        except RuntimeError:                       # o widget já destruído
            return
        log.info("widget %s da captura com a tela gravando", "fora" if ok else "não saiu")

    # ---------------------------------------------------------------- mensagens (thread da interface)
    def _on_message(self, client: object, msg: object) -> None:
        if self._closed or not isinstance(msg, dict):
            return
        s = next((x for x in self._snapshot() if x.client is client), None)
        if s is None:
            log.debug("mensagem de um filho da tela já encerrado: %s", msg.get("type"))
            return
        kind, current = msg.get("type"), s is self._session
        size = {"monitor": msg.get("monitor"), "width": msg.get("width"), "height": msg.get("height"),
                "fps": self._params[0], "max_side": self._params[1]}     # o critério de disco do `--check` lê estes
        if kind in (sc.STARTED, sc.BACK, sc.SWITCHED):
            s.label = f"Monitor {msg.get('monitor')}"
            etype = EventType.SCREEN_SWITCH if kind == sc.SWITCHED else EventType.SCREEN_START
            self._event(s, etype, size | ({"back": True} if kind == sc.BACK else {}), msg.get("t_ms"))
            if current:
                self._label, self._alert = s.label, None
                self._set_state(PAUSED if self._paused else RECORDING)
        elif kind == sc.LOST:
            self._event(s, EventType.SCREEN_LOST, {"monitor": msg.get("monitor")}, msg.get("t_ms"))
            if current:
                self._alert = LOST_TEXT.format(label=s.label)
                self._set_state(LOST)
        elif kind == sc.STATUS and current:
            self._on_status(str(msg.get("state") or ""))
        elif kind == sc.STATS:
            s.stats = dict(msg.get("stats") or {})
        elif kind == sc.BYE:
            self._bye(s, msg)
        elif kind == sc.ERROR:
            s.error = msg
            log.warning("filho da tela: %s (nova tentativa %s)", msg.get("message"), "sim" if msg.get("retry") else "não")
        elif kind == sc.EXIT:
            self._on_exit(s, msg)

    def _bye(self, s: sc.ChildRun, msg: dict[str, Any]) -> None:
        """As estatísticas finais e os pedaços gravados e não gravados, que vão no `screen_stop`."""
        written, unwritten = sc.as_int(msg.get("written")) or 0, sc.as_int(msg.get("unwritten")) or 0
        s.stats = {**dict(msg.get("stats") or s.stats), "written": written, "unwritten": unwritten}
        s.t_ms = sc.as_int(msg.get("t_ms"))
        if unwritten:
            log.warning("reunião %s: %d pedaço(s) da tela não gravado(s) (o banco recusou até o fim)", s.meeting_id, unwritten)

    def _on_status(self, state: str) -> None:
        if state == recorder.FAILING and self._state == RECORDING:
            log.info("tela: 60 s sem conseguir ler o monitor")
            self._alert = FAILING_TEXT
            self._set_state(FAILING)
        elif state == recorder.OK and self._state == FAILING:
            self._alert = None
            self._set_state(PAUSED if self._paused else RECORDING)

    def _on_exit(self, s: sc.ChildRun, msg: dict[str, Any]) -> None:
        self._drop(s)
        if s.cancelled:
            return
        if not msg.get("expected"):
            s.reason = "queda"
        self._finalize(s)
        if s is self._session:                    # queda da tela atual (a parada pedida já a tirou daqui)
            self._session = None
            self._crashed(s, msg)
        self._launch_pending()

    def _crashed(self, s: sc.ChildRun, msg: dict[str, Any]) -> None:
        permanent = s.error is not None and s.error.get("retry") is False
        meeting = self._meeting is not None and self._meeting[0] == s.meeting_id
        if meeting and not permanent and self._retries < RETRIES:
            self._retries += 1
            log.warning("reunião %s: o filho da tela caiu (código %s); relançando", s.meeting_id, msg.get("code"))
            self._launch(s.meeting_id, s.t0_perf, s.key, s.label)    # se não lançar, o aviso já foi dado
            return
        log.warning("reunião %s: o filho da tela caiu de novo (código %s); a tela para e o áudio segue", s.meeting_id, msg.get("code"))
        self._alert = None
        self._set_state(STOPPED)
        self.on_error(CRASHED_TEXT)

    def _launch_pending(self) -> None:
        pending = self._pending
        if pending is None or any(s.meeting_id == pending[0] for s in self._snapshot()):
            return
        self._pending = None
        found = self._find(pending[2])
        if found is None or not self._launch(*pending, sc.short_label(found[1])):
            self._set_state(STOPPED)

    # ---------------------------------------------------------------- apoio
    def _set_state(self, state: str) -> None:
        self._state = state
        shown = (state, self.monitor_label)
        if shown != self._shown:                  # o rótulo também muda o que o widget mostra (a troca de monitor)
            self._shown = shown
            self.state_changed.emit(*shown)

    def _snapshot(self) -> list[sc.ChildRun]:
        with self._lock:
            return list(self._sessions)

    def _drop(self, s: sc.ChildRun) -> None:
        with self._lock:
            if s in self._sessions:
                self._sessions.remove(s)

    def _finalize(self, s: sc.ChildRun) -> None:
        if s.done or s.cancelled:
            return
        s.done = True
        self._event(s, EventType.SCREEN_STOP, {**s.stats, "reason": s.reason or "queda"}, s.t_ms)
        log.info("reunião %s: tela encerrada (%s): %d pedaço(s), %d quadro(s)", s.meeting_id, s.reason or "queda",
                 int(s.stats.get("segments") or 0), int(s.stats.get("frames") or 0))

    def _event(self, s: sc.ChildRun, etype: EventType, data: dict[str, Any], t_ms: object) -> None:
        t = sc.as_int(t_ms)
        if t is None:
            t = int((time.perf_counter() - s.t0_perf) * 1000)
        try:
            self.store.add_event(s.meeting_id, t, etype, data)
        except Exception as e:
            log.warning("reunião %s: evento %s não gravado: %s", s.meeting_id, etype, type(e).__name__)


__all__ = ["CRASHED_TEXT", "FAILING_TEXT", "LOST_TEXT", "OFF", "PAUSED", "RECORDING", "STOPPED", "ScreenRecHost", "session_t0"]
