"""F5.7 (L3, U-094, D-065, D-066): a cola do aviso de reunião sem gravação, na thread da interface: a thread do sensor
(`ui/call_sensor.py`), as regras (`ui/call_rules.py`), o cartão (`ui/call_prompt.py`) e a bandeja; as respostas fora do
cartão ("Antes de gravar", atalho, "Não avisar") moram em `ui/call_answers.py`. O app nunca grava sozinho (D-010, CP6).
- Cada leitura é um passo das regras com `busy` (o do controlador: gravando, salvando ou saindo) e `modal`. O contrato do
  `busy` (o do `step`, R-03): na borda, um passo com uma leitura real pedida na hora (o cartão fecha já; sem ela em 3 s, um
  passo sem leitura cobre pela última); durante o `busy`, nenhum passo. Com o "Antes de gravar" aberto (R-02), o sensor
  para de ler e as regras só veem `modal`: o aviso espera, sem cobrir a chamada.
- Um diálogo modal à vista esconde o cartão e pausa o prazo; fechado, o cartão volta com o tempo que restava (R-08). As
  Configurações são relidas a cada 10 s e quando um diálogo modal fecha (CP8).
- O cartão sai da captura de tela antes de aparecer (A-13); sem a exclusão, o aviso fica só na bandeja, com os mesmos 20 s.
- Vigia: sem leitura por mais de 3 intervalos do modo, um WARNING; o tique atrasado (suspensão) recomeça a conta (R-09).
  Saída (`shutdown`, antes da parada de emergência, que fecha o banco): a thread para e é esperada por 1 s; presa, fica para
  trás (`call_sensor.abandon`) e o `app.py` sai já, sem a derrubada do PySide (R-01).
- Log (D-053, D-059): executável, pid, regra, segundos, desfecho, contagens e tempos; nunca título, caminho nem aparelho."""
from __future__ import annotations

import functools
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

from ..log import get_logger
from ..platform.base import KIND_BROWSER, AudioActivity
from . import call_config as cfg
from .call_answers import CLOSED_OFF, OFF_NOTICE, CallAnswersMixin, Host, open_warmup_dialog
from .call_prompt import CallPrompt, record_tip
from .call_rules import MODE_IDLE, MODE_STOPPED, CallRules, ClosePrompt, EpisodeEnded, Event, Params, Prompt
from .call_sensor import INTERVALS, CallSensor, Intervals, abandon, delay_for
from .tasks import live_threads, release

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.call_watch")

TICK_MS = 1000                   # o `busy`, o modal, a releitura, a borda e o vigia
SETTINGS_S = 10.0                # as Configurações relidas (CP8)
EDGE_WAIT_S = 3.0                # a leitura da borda do `busy` que não chega: cobre pela última
STUCK_TIMES = 3                  # o vigia: leituras que faltam até o WARNING; e tiques perdidos até recomeçar (R-09)
STOP_WAIT_MS = 1000              # a saída espera a thread por isto e depois a deixa
CLOSED_BY = {"gravação": "fechado pela gravação", "sumiu": "fechado: a chamada acabou", "desligado": CLOSED_OFF}


class CallWatch(CallAnswersMixin, QObject):
    """A cola. `start` depois do `controller.start` (a bandeja existe); `shutdown` na saída."""

    def __init__(self, host: Host, platform: Platform, store: Store, *, clock: Callable[[], float] = time.monotonic,
                 intervals: Intervals = INTERVALS, card_factory: Callable[..., CallPrompt] = CallPrompt,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host, self.platform, self.store = host, platform, store
        self._clock, self._iv, self.card_factory = clock, intervals, card_factory
        self.watched = tuple(platform.watched_apps())
        self.rules = CallRules(self.watched)
        self.params = Params(enabled=False)
        self.sensor: CallSensor | None = None
        self.card: CallPrompt | None = None
        self.card_exe = ""
        self._raw: tuple[str, ...] = ()
        self._pids: dict[str, int] = {}
        self._names: dict[str, str] = {}             # exe em minúsculas -> como o sistema o mostra
        self._recent: dict[str, float] = {}          # exe -> quando o último aviso dele apareceu (R-06)
        self._busy = self._modal = self._warmup_up = self._stuck = self._exclusion_warned = self._closed = False
        self._edge_at: float | None = None
        self._mode = ""
        self._beat = self._settings_at = self._last_tick = clock()
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self.tick)

    # ---------------------------------------------------------------- ciclo
    def start(self) -> None:
        """Liga a bandeja e a pilha dos avisos, lê as Configurações e solta a thread (sem sensor nesta plataforma, só diz)."""
        tray = self.host.tray
        if tray is not None:
            tray.set_call_handlers(self.open_for, self.silence)
        self.host.toasts.avoid_also(self.card_rect)
        self.reload(force=True)
        if not self.watched:
            log.info("aviso de reunião sem gravação: sem sensor nesta plataforma")
            return
        sensor = CallSensor(self.platform, self._iv)
        sensor.reading.connect(self._on_reading, Qt.ConnectionType.QueuedConnection)
        sensor.finished.connect(functools.partial(release, sensor))
        live_threads.add(sensor)                     # referência viva até `finished` (sem pai, M-12)
        self.sensor = sensor
        sensor.set_titles(self._titles())
        self._mode = ""                              # a thread nasce "parado": recebe o modo da releitura acima
        self._push_mode()
        sensor.start()
        self._last_tick = self._clock()
        self._tick.start()

    def shutdown(self) -> None:
        """A saída do app, antes da parada de emergência (que fecha o banco): o aviso some e a thread para; presa no sistema
        de áudio por mais de 1 s, fica para trás. De novo não faz nada."""
        if self._closed:
            return
        self._closed = True
        self._tick.stop()
        self._close_card("")
        if self.host.tray is not None:
            self.host.tray.set_call("", "")
        sensor, self.sensor = self.sensor, None
        if sensor is None:
            return
        with suppress(RuntimeError, TypeError):
            sensor.reading.disconnect(self._on_reading)
        sensor.halt()
        if not sensor.wait(STOP_WAIT_MS):
            log.warning("sensor de chamada não parou em %d ms (preso no sistema de áudio?): fica para trás", STOP_WAIT_MS)
            abandon(sensor)
        log.info("aviso de reunião sem gravação: parado (%d leitura(s))", sensor.reads)

    # ---------------------------------------------------------------- Configurações (CP8)
    def reload(self, force: bool = False) -> None:
        """Relê as seis chaves; só o texto que mudou é convertido. Desligado, o aviso à vista fecha e as chamadas somem."""
        self._settings_at = self._clock()
        try:
            raw = cfg.read_raw(self.store.get_setting)
        except Exception as e:
            log.debug("aviso de reunião: Configurações não lidas (%s)", type(e).__name__)
            return
        if raw == self._raw and not force:
            return
        self._raw = raw
        old, self.params = self.params, cfg.params_from(cfg.values_from(raw), self.watched)
        if force or old.enabled != self.params.enabled:
            p = self.params
            log.info("aviso de reunião sem gravação %s: depois de %.0f s, lembrete %s, outros apps %s, %d app(s) desligado(s)",
                     "ligado" if p.enabled else "desligado", p.seconds, "sim" if p.remind else "não",
                     "sim" if p.other_apps else "não", len(p.off))
        else:
            log.debug("aviso de reunião: Configurações mudaram (%d app(s) desligado(s))", len(self.params.off))
        if not self.params.enabled:
            self._run(None)                          # as regras desligadas fecham o aviso à vista e esquecem as chamadas
        if self.sensor is not None:
            self.sensor.set_titles(self._titles())
        self._refresh()

    def _titles(self) -> frozenset[str]:
        """Os navegadores ligados: só deles o sensor lê o serviço pelo título (que nunca sai da plataforma)."""
        if not self.params.enabled:
            return frozenset()
        return frozenset(w.exe.lower() for w in self.watched if w.kind == KIND_BROWSER and w.exe.lower() not in self.params.off)

    # ---------------------------------------------------------------- leituras e passos
    def tick(self) -> None:
        """A cada 1 s: a borda do `busy`, o "Antes de gravar" e o diálogo modal, a releitura, a borda sem leitura e o vigia."""
        if self._closed:
            return
        now = self._clock()
        if now - self._last_tick > STUCK_TIMES * TICK_MS / 1000:
            self._beat = now                         # R-09: suspensão (ou o laço parado): o vigia recomeça a contar
        self._last_tick = now
        self._check_busy(now)
        self._check_modal()
        if now - self._settings_at >= SETTINGS_S:
            self.reload()
        if self._edge_at is not None and now - self._edge_at >= EDGE_WAIT_S:
            log.debug("sensor de chamada: a leitura da borda não chegou em %.0f s; cobre pela última", EDGE_WAIT_S)
            self._edge_at = None
            self._run(None)
        self._check_stuck(now)

    def _check_busy(self, now: float) -> None:
        busy = bool(self.host.busy)
        if busy == self._busy:
            return
        self._busy = busy
        self._edge_at = None
        if busy:
            self._close_card(CLOSED_BY["gravação"])  # na hora; as regras confirmam com a leitura da borda
            self._edge_at = now
            if self.sensor is not None:
                self.sensor.request()
        self._refresh()

    def _check_modal(self) -> None:
        """R-02 e R-08: o "Antes de gravar" para o sensor; um diálogo modal esconde o cartão e pausa o prazo até fechar."""
        warmup, modal = open_warmup_dialog() is not None, self._modal_now()
        if warmup != self._warmup_up:
            self._warmup_up = warmup
            self._refresh()
        if modal == self._modal:
            return
        self._modal = modal
        card = self.card
        if modal and card is not None and card.pause():
            log.debug("aviso de reunião (%s): escondido enquanto um diálogo modal está aberto", self.card_exe)
        elif not modal:
            self.reload()                            # um diálogo fechou (as Configurações salvas valem já)
            if self.card is not None and self.card.resume():
                log.debug("aviso de reunião (%s): de volta, com o prazo que restava", self.card_exe)
        self.host.toasts.relayout()

    def _on_reading(self, activity: object) -> None:
        if self._closed:
            return
        now = self._clock()
        self._beat = now
        if self._stuck:
            self._stuck = False
            log.info("sensor de chamada voltou a ler")
        reading = activity if isinstance(activity, AudioActivity) else None
        if reading is not None:
            self._remember(reading)
        self._check_busy(now)
        if self._busy:
            if self._edge_at is None:
                return                               # durante o `busy`, nenhum passo
            self._edge_at = None
        self._run(reading)

    def _remember(self, reading: AudioActivity) -> None:
        for app in reading.apps:
            low = app.exe.lower()
            self._names[low] = app.exe
            if app.pid:
                self._pids[low] = app.pid

    def _run(self, reading: AudioActivity | None) -> None:
        events = self.rules.step(reading, self._clock(), self._busy, self._modal_now(), self.params)
        for event in events:
            self._handle(event)
        self._refresh()

    def _handle(self, event: Event) -> None:
        match event:
            case Prompt():
                self._show(event)
            case ClosePrompt() if event.exe == self.card_exe:
                self._close_card(CLOSED_BY.get(event.reason, event.reason))
            case EpisodeEnded():
                log.info("chamada encerrada: %s (pid %d), %.1f min, %s", event.exe, self._pids.pop(event.exe, 0),
                         event.seconds / 60, event.outcome)

    @staticmethod
    def _modal_now() -> bool:
        return QApplication.activeModalWidget() is not None

    def _push_mode(self) -> None:
        if self._busy or self._warmup_up or not self.params.enabled:
            mode = MODE_STOPPED
        else:
            mode = self.rules.mode()
            mode = MODE_IDLE if mode == MODE_STOPPED else mode     # o "parado" do último passo, já vencido
        if mode == self._mode:
            return
        if self._mode:
            log.debug("sensor de chamada: modo %s para %s", self._mode, mode)
        self._mode = mode
        self._beat = self._clock()                   # o vigia conta do zero no modo novo
        if self.sensor is not None:
            self.sensor.set_mode(mode)

    def _check_stuck(self, now: float) -> None:
        if self.sensor is None or self._stuck or self._mode in ("", MODE_STOPPED):
            return
        gap = delay_for(self._mode, self.sensor.has_watch, 0, self._iv)[0]
        if now - self._beat > STUCK_TIMES * gap:
            self._stuck = True
            log.warning("sensor de chamada sem leitura há %.0f s (modo %s): a thread pode estar presa no sistema de áudio",
                        now - self._beat, self._mode)

    def _refresh(self) -> None:
        """Depois de cada passo ou resposta: o modo do sensor e a chamada que a bandeja mostra."""
        self._push_mode()
        if self.host.tray is not None:
            self.host.tray.set_call(*self.tray_call(self._busy or self._closed))

    # ---------------------------------------------------------------- o cartão
    def _show(self, prompt: Prompt) -> None:
        self._close_card("")
        card = self.card_factory(prompt.label, prompt.seconds, prompt.reminder, self._record_tip(prompt.label))
        shown = self._exclude(card)                  # antes do primeiro `show` (A-13): nada vaza para a captura
        self.card, self.card_exe = card, prompt.exe
        self._recent[prompt.exe] = self._clock()
        card.record_clicked.connect(self._on_record)
        card.dismissed.connect(self._on_dismiss)
        card.expired.connect(self._on_expired)
        if shown:
            card.place()
            card.show()
            card.announce()
            self.host.toasts.relayout()
        card.start()                                 # os 20 s, à vista ou só na bandeja
        log.info("chamada sem gravação: %s (pid %d), regra %s, %d s; %s %s", prompt.exe, self._pids.get(prompt.exe, 0),
                 prompt.rule, prompt.seconds, "lembrete" if prompt.reminder else "aviso",
                 "mostrado" if shown else "só na bandeja (não saiu da captura de tela)")

    def _exclude(self, card: CallPrompt) -> bool:
        """A-13: o cartão fora da captura de tela; o primeiro "não" da sessão vai ao log."""
        try:
            ok = bool(self.platform.exclude_from_capture(int(card.winId())))
        except Exception as e:
            log.debug("aviso de reunião: exclusão da captura (%s)", type(e).__name__)
            ok = False
        if not ok and not self._exclusion_warned:
            self._exclusion_warned = True
            log.warning("aviso de reunião: a janela não saiu da captura de tela; o aviso fica só na bandeja")
        return ok

    def _record_tip(self, label: str) -> str:
        combo = ""
        with suppress(Exception):
            combo = self.store.get_setting("hotkeys.start")
        return record_tip(label, combo)

    def card_rect(self) -> tuple[int, int, int, int] | None:
        """O retângulo do cartão à vista, que a pilha dos avisos evita (`ToastManager.avoid_also`)."""
        card = self.card
        if card is None or not card.isVisible():
            return None
        g = card.frameGeometry()
        return g.x(), g.y(), g.width(), g.height()

    def _close_card(self, why: str) -> None:
        card, exe = self.card, self.card_exe
        self.card, self.card_exe = None, ""
        if card is None:
            return
        card.finish()
        self.host.toasts.relayout()
        if why:
            log.info("aviso de reunião (%s): %s", exe, why)

    def _on_record(self) -> None:
        exe = self.card_exe
        self._close_card("Gravar")
        if exe:                                      # depois do clique: o "Antes de gravar" é modal
            QTimer.singleShot(0, self, functools.partial(self.open_for, exe, "aviso"))

    def _on_dismiss(self) -> None:
        exe = self.card_exe
        self._close_card("Agora não")
        self.rules.dismiss(exe)
        self._refresh()

    def _on_expired(self) -> None:
        exe = self.card_exe
        self._close_card("sumiu sem clique")
        self.rules.expired(exe)
        self._refresh()


__all__ = ["CLOSED_BY", "EDGE_WAIT_S", "OFF_NOTICE", "SETTINGS_S", "STOP_WAIT_MS", "CallWatch", "Host"]
