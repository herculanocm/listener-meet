"""D-067: a saída do player que acompanha o padrão do Windows, fora do `player.py` (R-14).

- `Follow`, a parte da escritora: com o som tocando, lê o padrão a cada `ROUTE_CHECK_S` e logo depois de tocar, retomar e
  ±10 s (pausado, não lê: o retomar confere, R-09); outro aparelho, o fluxo novo abre antes de o antigo fechar e o som
  segue do ponto que se ouvia (repete no máximo o buffer, nunca pula). O aparelho que caiu no meio do som (o `write` ou o
  `start` falhou) reabre uma vez no padrão de agora, do mesmo ponto (R-03). O aparelho fora da lista do PortAudio deixa
  o som onde está com o aviso (`note`); o que não há como identificar (R-11), calado.
- `Reroute`, na thread da interface (onde o PortAudio reinicia, U-043): o padrão fora da lista relê a lista quando nada
  além do player a segura (uma gravação segura o microfone: espera ela acabar), no máximo a cada `REROUTE_RETRY_S`. A
  escritora solta o fluxo antes e fora do `portaudio.LOCK` (R-05; dentro dele a contagem é conferida de novo), e a mesma
  tentativa só fica marcada quando a releitura aconteceu ou quando nada mudou desde a última (R-06).
- `drain` e `ran_dry`: o `stop` do WASAPI volta na hora, com o fim ainda no buffer (R-01), e o `underflowed` do PortAudio
  nunca vem (R-07); os dois saem do espaço livre do buffer (`write_available`), que volta ao tamanho dele vazio."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..platform.base import OutputStream, OutputUnknownError, log_id
from ..platform.common import portaudio

log = get_logger("ui.player")

ROUTE_CHECK_S = 1.0              # tocando, o padrão de saída é lido a cada 1 s (e logo depois de tocar, retomar e ±10 s)
ROUTE_STUCK = "o som segue no aparelho anterior até o novo padrão do Windows ficar disponível para o app"
REROUTE_RETRY_S = 2.0            # a releitura que não aconteceu é tentada de novo depois disto
DETACH_WAIT_S = 1.0              # a releitura espera a escritora soltar o fluxo até isto
DRAIN_MARGIN_S = 0.1             # R-01: a espera do fim vai até a latência do fluxo mais isto
DRAIN_POLL_S = 0.01

type Refresh = Callable[[int, Callable[[], None] | None], bool]   # `Platform.refresh_audio_devices`


class OutputDevices(Protocol):
    """O que o player usa da plataforma para acompanhar o padrão de saída do Windows (a `Platform` serve)."""
    def default_output_id(self) -> str | None: ...
    def open_output(self, endpoint_id: str | None = None) -> OutputStream: ...
    def refresh_audio_devices(self, own_streams: int = 0, release: Callable[[], None] | None = None) -> bool: ...
    def audio_devices_changed(self) -> bool: ...


def write_free(stream: OutputStream) -> int | None:
    """O espaço livre no buffer do fluxo, em quadros; None sem a medida (o fluxo falso dos testes) ou com o fluxo caído."""
    try:
        free = getattr(stream, "write_available", None)   # a medida é do sounddevice (o falso pode não ter)
    except Exception:  # o fluxo caiu (PortAudioError): sem a medida
        return None
    return int(free) if free is not None else None


def ran_dry(stream: OutputStream, frames: int) -> bool:
    """R-07: o buffer (`frames`, a latência em quadros) está vazio antes da escrita: o dispositivo ficou sem som."""
    free = write_free(stream)
    return frames > 0 and free is not None and free >= frames


def drain(stream: OutputStream, frames: int, rate: int, interrupted: Callable[[], bool]) -> float:
    """R-01: espera o buffer esvaziar (o espaço livre volta a `frames`) antes do `stop`, até a latência mais a margem; um
    comando novo (pausar, parar) corta a espera. Devolve quanto esperou, em segundos."""
    t0 = time.monotonic()
    if frames <= 0 or rate <= 0:
        return 0.0
    deadline = t0 + frames / rate + DRAIN_MARGIN_S
    while time.monotonic() < deadline and not interrupted():
        free = write_free(stream)
        if free is None or free >= frames:
            break
        time.sleep(DRAIN_POLL_S)
    return time.monotonic() - t0


class RouteSession(Protocol):
    """O que o `Follow` usa de um som do motor (`_Session`)."""
    req: Any
    stream: OutputStream
    rate: int
    latency: int
    pos: int
    playing: bool
    started: bool
    closed: bool
    written: int
    underflows: int
    starved: int
    endpoint: str
    resume: bool
    retried: bool


class RouteEngine(Protocol):
    """O que o `Follow` usa do `PlaybackEngine` (na thread da escritora)."""
    factory: Callable[[str | None], OutputStream]
    opened: int
    _cv: threading.Condition
    _feed: Any
    def snapshot(self) -> Any: ...
    def _abort(self, sess: Any) -> None: ...
    def _release(self, sess: Any) -> None: ...
    def _report(self, sess: Any) -> None: ...
    def _start_run(self, sess: Any, t_ms: int) -> None: ...


class Follow:
    """A escritora acompanhando o padrão. `note`, `stuck` e `has_stream` sob o `_cv` do motor; o resto, só a escritora."""

    def __init__(self, engine: RouteEngine, probe: Callable[[], str | None] | None, clock: Callable[[], float]):
        self.engine, self.probe, self.clock = engine, probe, clock
        self.next_check = 0.0
        self.note = ""
        self.stuck: tuple[str, int] | None = None     # (padrão que não abriu, leitura do PortAudio): não tenta de novo
        self.reread: tuple[str, int] | None = None    # o mesmo, quando reler a lista pode resolver (a `Reroute` lê)
        self.has_stream = False
        self.moves = 0                                # instrumentação: trocas de aparelho com o som tocando

    def due(self, sess: RouteSession | None) -> bool:
        """Tocando com o fluxo aberto e na hora de ler o padrão (pausado, nunca: R-09)."""
        return (self.probe is not None and sess is not None and sess.playing and not sess.closed
                and self.clock() >= self.next_check)

    def follow[S: RouteSession](self, sess: S) -> S:
        """O padrão de agora: outro aparelho (que não falhou nesta leitura do PortAudio), o som vai para ele."""
        self.next_check = self.clock() + ROUTE_CHECK_S
        try:
            target = self.probe() if self.probe is not None else None
        except Exception as e:  # o Core Audio não respondeu: fica onde está e lê de novo depois
            log.debug("player: o padrão de saída não foi lido: %s", e)
            return sess
        if not target or target == sess.endpoint:
            if target:
                self.set_note("")
            return sess
        with self.engine._cv:
            stuck = self.stuck
        return sess if stuck == (target, portaudio.generation()) else self.move(sess, target)

    def move[S: RouteSession](self, sess: S, target: str) -> S:
        """O fluxo novo abre antes de o antigo fechar; não abriu, segue no antigo (com o aviso, menos o sem como saber)."""
        try:
            stream = self.engine.factory(target)
        except Exception as e:  # fora da lista do PortAudio, ambíguo, sem como saber (R-11) ou recusou abrir
            key = (target, portaudio.generation())
            quiet = isinstance(e, OutputUnknownError)
            with self.engine._cv:
                self.stuck, self.reread = key, (None if quiet else key)
                self.note = self.note if quiet else ROUTE_STUCK
            log.warning("player: o padrão de saída novo (endpoint %s) não abriu: %s; o som segue no endpoint %s", log_id(target), e,
                        log_id(sess.endpoint))
            return sess
        self.swap(sess, stream, target)
        self.moves += 1
        self.set_note("")
        log.info("player: reunião %s: a saída foi para o endpoint %s em %s (o padrão do Windows mudou)", sess.req.meeting_id,
                 log_id(sess.endpoint), fmt_mmss(sess.pos / 1000))
        return sess

    def recover(self, sess: RouteSession, error: Exception) -> bool:
        """R-03: o aparelho caiu no meio do som: reabre uma vez, no padrão de agora, do ponto que se ouvia. False quando já
        tentou desde a última escrita boa ou quando nada abriu (aí o erro aparece)."""
        if sess.retried:
            return False
        sess.retried = True
        try:
            stream = self.engine.factory(None)
        except Exception as e:  # nenhuma saída abre
            log.warning("player: a saída caiu (%s) e nenhuma outra abriu: %s", error, e)
            return False
        self.swap(sess, stream, "")
        log.info("player: reunião %s: a saída caiu (%s); o som voltou no endpoint %s em %s", sess.req.meeting_id, error,
                 log_id(sess.endpoint), fmt_mmss(sess.pos / 1000))
        return True

    def swap(self, sess: RouteSession, stream: OutputStream, target: str) -> None:
        """O som passa para `stream` do ponto que se ouvia (lido depois de abrir: o mínimo repetido)."""
        engine = self.engine
        engine.opened += 1
        sess.pos = engine.snapshot().t_ms
        engine._abort(sess)
        old, sess.stream = sess.stream, stream
        try:
            old.close()
        except Exception as e:  # R-10: o antigo caído; o novo segue
            log.debug("player: fechar o fluxo antigo: %s", e)
        engine._report(sess)
        rate = round(float(stream.samplerate))
        try:
            sess.latency = round(float(stream.latency) * rate)
        except (TypeError, ValueError):
            sess.latency = 0
        sess.rate, sess.endpoint = rate, str(getattr(stream, "endpoint_id", "") or "") or target
        sess.written = sess.underflows = sess.starved = 0
        engine._start_run(sess, sess.pos)

    def detach[S: RouteSession](self, sess: S | None, done: threading.Event) -> S | None:
        """O fluxo fecha para a releitura do PortAudio; tocando, guarda o ponto que se ouvia para o `reattach`."""
        try:
            if sess is not None and not sess.closed:
                if sess.playing:
                    sess.pos, sess.resume = self.engine.snapshot().t_ms, True
                    self.engine._abort(sess)
                    sess.playing = False
                    with self.engine._cv:
                        if self.engine._feed is not None and self.engine._feed.sess is sess:
                            self.engine._feed = None
                self.engine._release(sess)
        finally:
            done.set()
        return sess

    def set_note(self, note: str) -> None:
        with self.engine._cv:
            self.note = note
            if not note:
                self.stuck = self.reread = None

    def reset(self) -> None:
        """(sob o `_cv`) O som acabou: sem fluxo, sem aviso."""
        self.has_stream, self.note, self.stuck, self.reread = False, "", None, None


class RerouteEngine(Protocol):
    """O que a releitura usa do `PlaybackEngine`."""
    @property
    def has_stream(self) -> bool: ...
    def stuck_id(self) -> tuple[str, int] | None: ...
    def detach_and_wait(self, timeout_s: float) -> bool: ...
    def reattach(self) -> None: ...


class Reroute:
    """`tick` no `poll` do player (100 ms, só com som carregado). True quando tentou reler."""

    def __init__(self, engine: RerouteEngine, refresh: Refresh, changed: Callable[[], bool],
                 clock: Callable[[], float] = time.monotonic):
        self.engine, self.refresh, self.changed, self.clock = engine, refresh, changed, clock
        self._tried: tuple[str, int] | None = None   # (aparelho, leitura) já relidos ou sem nada novo: não se repete
        self._next = 0.0

    def tick(self) -> bool:
        stuck = self.engine.stuck_id()
        if stuck is None or stuck == self._tried or self.clock() < self._next:
            return False
        self._next = self.clock() + REROUTE_RETRY_S
        own = 1 if self.engine.has_stream else 0
        if portaudio.open_streams() > own:
            return False                                   # outro stream do app (a gravação): o aviso fica na barra
        if not self._changed():
            self._tried = stuck                            # R-06: nada mudou desde a leitura: reler não acharia
            return True
        reread = False
        try:
            if self.engine.detach_and_wait(DETACH_WAIT_S):     # R-05: fora do LOCK; o refresh confere de novo dentro
                reread = self.refresh(0, None)
            else:
                log.warning("player: a saída não foi solta em %.1f s para a releitura", DETACH_WAIT_S)
        except Exception as e:  # a lista fica a antiga; o som segue onde está
            log.warning("player: a releitura dos dispositivos falhou: %s", e)
        finally:
            self.engine.reattach()
        if reread:
            self._tried = stuck
        log.info("player: releitura dos dispositivos para o padrão novo (endpoint %s): %s", log_id(stuck[0]),
                 "feita" if reread else "não feita; tenta de novo")
        return True

    def _changed(self) -> bool:
        try:
            return self.changed()
        except Exception as e:  # sem a assinatura do sistema: tenta reler
            log.debug("player: a assinatura dos dispositivos: %s", e)
            return True


__all__ = ["DETACH_WAIT_S", "REROUTE_RETRY_S", "ROUTE_CHECK_S", "ROUTE_STUCK", "Follow", "OutputDevices", "Reroute",
           "RerouteEngine", "drain", "ran_dry", "write_free"]
