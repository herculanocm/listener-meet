"""F5.7 (L3): a thread do sensor do aviso de reunião sem gravação. Uma `QThread` sem pai, no padrão do `_MutePoller` e do
`sources._Poller`: os objetos do sistema de áudio nascem, vivem e morrem nela (`audio_activity` e, antes de sair,
`close_audio_activity`), e cada leitura vai à interface por sinal enfileirado (`reading`: um `AudioActivity`, ou None quando
a leitura falhou). Nenhuma callback do sistema.

Quando lê, pelo modo que a interface põe (`set_mode`, o `mode()` das regras):
- "parado" (gravando, salvando, "Antes de gravar" aberto ou aviso desligado): não lê; acorda a cada 10 s só para ver o modo;
- "ocioso": espera o aviso do indicador de microfone (`MicWatch`) por até 20 s e lê quando ele avisa ou quando o tempo vence;
  a espera vai em fatias de 1 s, e o pedido de leitura da interface (`request`, a borda do `busy`) não espera os 20 s;
- "armando": a cada 2 s; "lento": a cada 5 s; sem o `MicWatch`, a cada 5 s fora do "parado".

Falhas: as seguidas espaçam as tentativas (2, 4, 8 s, até 60 s); da terceira em diante, lê só pelo indicador de microfone do
sistema (`degraded`, só a regra R1), com WARNING ao entrar nesse modo e INFO ao voltar, e a leitura inteira é tentada de novo
no espaço da vez. Nenhuma exceção sai da thread: cada volta tem `try` e o erro vai ao DEBUG com o tipo. Log só com tempos,
contagens e tipos (D-053): nunca título de janela, caminho nem nome de aparelho."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import shiboken6
from PySide6.QtCore import QThread, Signal

from ..log import get_logger
from .call_rules import MODE_ARMING, MODE_IDLE, MODE_STOPPED

if TYPE_CHECKING:
    from ..platform.base import AudioActivity, MicWatch, Platform

log = get_logger("ui.call_sensor")


@dataclass(frozen=True, slots=True)
class Intervals:
    """Os tempos do sensor, em segundos (os testes passam outros, curtos)."""
    stopped: float = 10.0          # "parado": só acorda para ver o modo
    idle: float = 20.0             # "ocioso": o tempo-limite da espera do `MicWatch`
    arming: float = 2.0            # "armando"
    poll: float = 5.0              # "lento", ou "ocioso" sem o `MicWatch`
    slice: float = 1.0             # a espera do `MicWatch` em fatias (o pedido de leitura não espera os 20 s)
    backoff: float = 2.0           # depois da primeira falha seguida; dobra a cada uma até `backoff_max`
    backoff_max: float = 60.0
    degrade_after: int = 3         # falhas seguidas até ler só pelo indicador de microfone do sistema
    slow_ms: float = 100.0         # a leitura acima disto vai ao DEBUG


INTERVALS = Intervals()
ABANDONED: list[QThread] = []                # as threads presas na saída do app (`abandon`)


def backoff_s(failures: int, iv: Intervals = INTERVALS) -> float:
    """O espaço até a próxima tentativa depois de `failures` falhas seguidas: 2, 4, 8... até 60 s."""
    return min(iv.backoff_max, iv.backoff * 2 ** max(0, failures - 1))


def delay_for(mode: str, has_watch: bool, failures: int, iv: Intervals = INTERVALS) -> tuple[float, bool]:
    """A espera até a próxima volta e se ela é a do `MicWatch`. Antes do modo só pelo indicador, a falha espaça a próxima
    tentativa; nele, as leituras seguem no ritmo do modo."""
    if mode == MODE_STOPPED:
        return iv.stopped, False
    if 0 < failures < iv.degrade_after:
        return backoff_s(failures, iv), False
    if mode == MODE_ARMING:
        return iv.arming, False
    if mode == MODE_IDLE and has_watch:
        return iv.idle, True
    return iv.poll, False


def abandon(thread: QThread) -> None:
    """A thread presa que fica para trás na saída. Nenhuma referência a salva da derrubada do PySide (o `atexit` dele apaga todo
    QObject do Python e o Qt aborta com a QThread rodando, 0xC0000409): o `app.py` sai já (`os._exit`) se ela segue presa."""
    ABANDONED.append(thread)


def stuck_threads() -> int:
    """Quantas das abandonadas seguem rodando (as que terminaram depois saem do jeito normal)."""
    return sum(1 for t in ABANDONED if shiboken6.isValid(t) and t.isRunning())


class CallSensor(QThread):
    """A thread do sensor. `set_mode`, `set_titles`, `request` e `halt` são da thread da interface; o resto roda nela."""
    reading = Signal(object)                 # AudioActivity | None (None: a leitura falhou)

    def __init__(self, platform: Platform, intervals: Intervals = INTERVALS) -> None:
        super().__init__(None)
        self.platform = platform
        self.iv = intervals
        self._mode = MODE_STOPPED
        self._titles: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._requested = False
        self._wake = threading.Event()
        self._halted = threading.Event()
        self._watch: MicWatch | None = None
        self.has_watch = False
        self.failures = 0                    # leituras inteiras seguidas que falharam
        self._retry_at = 0.0
        self.reads = 0                       # instrumentação: leituras entregues

    # ---------------------------------------------------------------- da thread da interface
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """O modo novo vale na hora: a espera em curso acaba e, fora do "parado", vem uma leitura."""
        if mode != self._mode:
            self._mode = mode
            self._wake.set()

    def set_titles(self, exes: frozenset[str]) -> None:
        """Os executáveis (minúsculos) cujo serviço de reunião sai do título da janela (os navegadores ligados)."""
        self._titles = exes

    def request(self) -> None:
        """Uma leitura já, mesmo no "parado" (a borda do `busy`, R-03)."""
        with self._lock:
            self._requested = True
        self._wake.set()

    def halt(self) -> None:
        """A saída: acorda a espera (também a do `MicWatch`) e a thread sai na volta seguinte."""
        self._halted.set()
        self._wake.set()
        watch = self._watch
        if watch is not None:
            try:
                watch.cancel()
            except Exception as e:
                log.debug("sensor de chamada: cancelar a espera do indicador falhou (%s)", type(e).__name__)

    # ---------------------------------------------------------------- na thread do sensor
    @override
    def run(self) -> None:
        self._open_watch()
        try:
            while not self._halted.is_set():
                self._turn()
        finally:
            self._close()

    def _turn(self) -> None:
        try:
            delay, on_watch = delay_for(self._mode, self._watch is not None, self.failures, self.iv)
            self._sleep(delay, on_watch)
            if self._halted.is_set():
                return
            if self._take_request() or self._mode != MODE_STOPPED:
                self._read()
        except Exception as e:                               # nada sai da thread
            log.debug("sensor de chamada: a volta falhou (%s)", type(e).__name__)
            self._halted.wait(self.iv.arming)

    def _take_request(self) -> bool:
        with self._lock:
            asked, self._requested = self._requested, False
        return asked

    def _sleep(self, seconds: float, on_watch: bool) -> None:
        """Espera o tempo do modo, acordando no pedido da interface; no "ocioso", também no aviso do indicador."""
        watch = self._watch
        if on_watch and watch is not None:
            end = time.monotonic() + seconds
            while not self._wake.is_set() and (left := end - time.monotonic()) > 0:
                try:
                    if watch.wait(min(self.iv.slice, left)):
                        break                                # o indicador mudou: lê
                except Exception as e:
                    log.debug("sensor de chamada: o aviso do indicador falhou (%s); passa a consultar a cada %.0f s",
                              type(e).__name__, self.iv.poll)
                    self._drop_watch()
                    break
        else:
            self._wake.wait(seconds)
        self._wake.clear()                                   # depois de acordar: o pedido que chega agora fica para a volta

    def _read(self) -> None:
        t0 = time.perf_counter()
        activity = self._activity()
        ms = (time.perf_counter() - t0) * 1000
        if ms > self.iv.slow_ms:
            log.debug("sensor de chamada: leitura de %.0f ms", ms)
        self.reads += 1
        self.reading.emit(activity)

    def _activity(self) -> AudioActivity | None:
        """A leitura inteira (no espaço da vez, depois de falhas); da terceira falha seguida em diante, a do indicador."""
        now = time.monotonic()
        if self.failures < self.iv.degrade_after or now >= self._retry_at:
            try:
                activity = self.platform.audio_activity(self._titles)
            except Exception as e:
                self._failed(e, now)
            else:
                self._recovered()
                return activity
        if self.failures >= self.iv.degrade_after:
            return self._degraded()
        return None

    def _failed(self, error: Exception, now: float) -> None:
        self.failures += 1
        self._retry_at = now + backoff_s(self.failures, self.iv)
        log.debug("sensor de chamada: a leitura falhou (%s), %d seguida(s)", type(error).__name__, self.failures)
        if self.failures == self.iv.degrade_after:
            log.warning("sensor de chamada: %d leituras seguidas falharam; até voltar, só o indicador de microfone do sistema "
                        "(só a regra R1)", self.failures)

    def _recovered(self) -> None:
        if self.failures >= self.iv.degrade_after:
            log.info("sensor de chamada: as leituras voltaram depois de %d falha(s)", self.failures)
        self.failures = 0

    def _degraded(self) -> AudioActivity | None:
        try:
            return self.platform.audio_activity(frozenset(), degraded=True)
        except Exception as e:
            log.debug("sensor de chamada: o indicador de microfone falhou (%s)", type(e).__name__)
            return None

    def _open_watch(self) -> None:
        try:
            watch = self.platform.mic_watch()
        except Exception as e:
            log.debug("sensor de chamada: o aviso do indicador não abriu (%s)", type(e).__name__)
            watch = None
        self._watch, self.has_watch = watch, watch is not None
        log.debug("sensor de chamada: thread iniciada, %s", "com o aviso do indicador de microfone" if watch is not None
                  else f"sem o aviso do indicador (consulta a cada {self.iv.poll:.0f} s)")

    def _drop_watch(self) -> None:
        watch, self._watch, self.has_watch = self._watch, None, False
        if watch is not None:
            try:
                watch.close()
            except Exception as e:
                log.debug("sensor de chamada: fechar o aviso do indicador falhou (%s)", type(e).__name__)

    def _close(self) -> None:
        """Antes de sair, nesta thread: o aviso do indicador e os objetos do sistema de áudio."""
        self._drop_watch()
        try:
            self.platform.close_audio_activity()
        except Exception as e:
            log.debug("sensor de chamada: soltar o sistema de áudio falhou (%s)", type(e).__name__)
        log.debug("sensor de chamada: thread encerrada (%d leitura(s))", self.reads)


__all__ = ["ABANDONED", "INTERVALS", "CallSensor", "Intervals", "abandon", "backoff_s", "delay_for", "stuck_threads"]
