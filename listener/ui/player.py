"""Player único do app (F5 item 10, F5A-25): toca uma reunião a partir de um ponto com L e R misturados (ganho ½ cada) até
parar, ou um canal só num intervalo (os trechos de 5 s da revisão); um som por vez: tocar outro ponto troca o som.

Duas threads por som (R7-02: o `write` nunca espera decodificação). A alimentadora (`player-feed`) decodifica cada pedaço
FLAC uma vez (`AudioRepo.get_chunk`), corta dele janelas curtas (`decode_s`, 1 s) posicionadas pelo tempo (buraco entre
pedaços vira silêncio), pula os intervalos de pausa da gravação (eventos `pause`/`resume`, D-011), mistura, reamostra com
estado (`StreamResampler`) para a taxa padrão do dispositivo de saída e deixa o áudio numa fila limitada (`QUEUE_S`, 3 s à
frente). A escritora (`player`) atende os comandos e só tira da fila e escreve blocos de ~0,25 s com `OutputStream.write`
bloqueante, o que dá o ritmo; o fluxo começa no primeiro bloco pronto. Cada trecho contínuo (tocar, retomar, ±10 s) é uma
geração da fila: pausar, trocar o ponto e ±10 s descartam o que estava pronto e o buffer do dispositivo (`abort`) e
respondem em até um bloco. O `underflowed` de cada `write` e as esperas pela alimentadora são contados e vão para o log
quando o som para. Posição = quadros escritos menos a latência de saída, convertida no tempo da reunião pelas âncoras
(início de cada trecho contínuo, depois de cada pausa pulada); a interface lê num `QTimer` de 100 ms. ±10 s andam em tempo
de áudio: as pausas da gravação não contam nos dois sentidos (R7-04). A escritora nasce no primeiro "tocar" com a sua
alimentadora, cada uma com a própria conexão do `Store` (`close_thread` ao terminar), e as duas acabam quando o som para;
comando que sobrar numa queda relança a escritora; a saída do app as para e espera (`shutdown`).

Sem dispositivo de saída, sem o PortAudio do `sounddevice`, sem o arquivo de áudio do mês ou sem áudio gravado: estado
`erro` com o motivo, que a barra mostra; nada quebra. A fábrica do fluxo de saída é injetável (os testes usam uma falsa
que registra os quadros). Q9: o controlador põe um `guard` chamado antes de o som começar (a gravação em curso captura o
sistema inteiro: o som do player entraria no canal R) e pausa o som com um aviso na barra (`pause(aviso)`, R7-01). Títulos
e avisos em texto puro na barra (R7-07). F5.3 (A-17): `release_output` solta o fluxo de saída e espera a escritora
confirmar, para a troca de microfone que relê o PortAudio (que só reinicia sem stream aberto); fecha também o fluxo que a
pausa do R8-01 deixa aberto a gravação inteira com o sistema inteiro. As funções puras e os pedaços decodificados moram em
`player_source.py` (este módulo reexporta os nomes).

D-067: com a plataforma (`devices`), o fluxo abre no padrão de saída de agora, e a escritora o acompanha tocando
(`player_route.Follow`: ~2,5 ms por leitura, fora da thread da interface; nada lido parado nem pausado); o aparelho que
caiu no meio reabre uma vez no padrão de agora (R-03); o fim espera o buffer esvaziar antes do `stop` (R-01); o buraco é o
buffer vazio antes da escrita (R-07); o padrão fora da lista do PortAudio é a `player_route.Reroute`."""
from __future__ import annotations

import importlib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..capture.audio import StreamResampler
from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..platform.base import OutputStream
from .player_route import ROUTE_CHECK_S, ROUTE_STUCK, Follow, OutputDevices, OutputName, Reroute, drain, ran_dry
from .player_source import EMPTY as _EMPTY
from .player_source import (Chunks, audio_ms, audio_path_missing, meeting_ms, mix, next_pause, pause_intervals, position_ms, seek_target,
                            skip_paused)

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.player")

BLOCK_S = 0.25                   # cada `write` bloqueante: o ritmo e o tempo de resposta a um comando
DECODE_S = 1.0                   # janela que a alimentadora corta dos pedaços já decodificados de cada vez (R7-02)
QUEUE_S = 3.0                    # áudio pronto à frente do dispositivo, no máximo (fila limitada da alimentadora, R7-02)
POLL_MS = 100                    # a interface lê a posição a cada 100 ms
JOIN_S = 3.0                     # sair do app: espera a thread do player até isto
FEED_JOIN_S = 2.0                # a escritora espera a alimentadora terminar a janela em curso até isto
SEEK_MS = 10_000                 # -10 s e +10 s da barra
REQUESTS_KEPT = 100              # instrumentação dos testes: os últimos pedidos

STOPPED, PLAYING, PAUSED, ERROR = "stopped", "playing", "paused", "error"
STATE_TEXT = {STOPPED: "parado", PLAYING: "tocando", PAUSED: "pausado", ERROR: "erro"}
NO_DEVICE = "sem dispositivo de saída de áudio"


type OutputFactory = Callable[[str | None], OutputStream]   # D-067: None = o padrão de agora; um id = exatamente ele


def sounddevice_output(endpoint_id: str | None = None) -> OutputStream:
    """O dispositivo de saída padrão da biblioteca (portável), mono float32 na taxa padrão dele; o app abre pela plataforma
    (D-067). Sem PortAudio a importação levanta `OSError`, que vira "sem dispositivo de saída"."""
    from ..platform.common import output

    return output.open_default(endpoint_id)


def _stream_format(stream: OutputStream) -> tuple[int, int, str]:
    """(taxa, latência em quadros, endpoint aberto) de um fluxo."""
    rate = round(float(stream.samplerate))
    try:
        latency = round(float(stream.latency) * rate)
    except (TypeError, ValueError):
        latency = 0
    return rate, latency, str(getattr(stream, "endpoint_id", "") or "")


def prewarm_audio() -> threading.Thread:
    """U-031: o primeiro "Tocar" do processo levava 2 a 5 s no Windows (25/10): o import do `sounddevice`, que inicia o
    PortAudio, o do `scipy.signal` da reamostragem e o buffer do MME. Chamado na thread da interface logo depois da bandeja:
    o `sounddevice` é importado aqui (~0,3 s; o PortAudio inicia na mesma thread que abre o microfone, como sem o
    pré-aquecimento quando se grava primeiro) e o `scipy.signal` numa thread de fundo (~1 s, só import, sem COM). Sem
    PortAudio não faz nada: o player avisa quando tocar."""
    try:
        importlib.import_module("sounddevice")
    except Exception as e:  # sem PortAudio: o aviso é do player, na hora de tocar
        log.debug("pré-aquecimento do áudio: %s", e)

    def scipy_signal() -> None:
        try:
            importlib.import_module("scipy.signal")
        except Exception as e:  # o player importa de novo ao tocar e avisa se faltar
            log.debug("pré-aquecimento do scipy.signal: %s", e)

    thread = threading.Thread(target=scipy_signal, name="prewarm-audio", daemon=True)
    thread.start()
    return thread


class PlaybackError(Exception):
    """Motivo para o usuário de não poder tocar (reunião sem áudio, arquivo do mês ausente)."""


@dataclass(frozen=True, slots=True)
class PlayRequest:
    meeting_id: int
    t_ms: int
    end_ms: int | None = None    # None = até o fim da reunião
    channel: str | None = None   # None = L e R misturados; "L" ou "R" = um canal só (a revisão)
    title: str = ""


class PlayerState(NamedTuple):
    state: str                   # STOPPED, PLAYING, PAUSED ou ERROR
    meeting_id: int | None
    t_ms: int
    duration_ms: int | None
    title: str = ""
    message: str = ""
    note: str = ""               # D-067: a saída não acompanhou o padrão do Windows (tocando ou pausado)
    endpoint: str = ""           # F5.9 (item 10): o endpoint do fluxo que toca ("" = parado ou não se sabe); a barra põe o nome

    @property
    def active(self) -> bool:
        return self.state in (PLAYING, PAUSED)


# ---------------------------------------------------------------- motor (sem Qt)
@dataclass(slots=True, eq=False)
class _Session:
    req: PlayRequest
    stream: OutputStream
    rate: int                    # taxa do dispositivo
    latency: int                 # quadros
    in_rate: int                 # taxa gravada
    channels: tuple[str, ...]
    end_t: int                   # fim do áudio a tocar (ms)
    duration_ms: int | None
    pauses: list[tuple[int, int | None]]
    pos: int = 0                 # posição guardada na pausa
    playing: bool = False
    started: bool = False        # o fluxo começou neste trecho (começa no primeiro bloco pronto, R7-02)
    closed: bool = False
    written: int = 0             # quadros escritos no som inteiro (o log ao parar)
    underflows: int = 0          # `write` que voltou com `underflowed`: o dispositivo ficou sem som (R7-02)
    starved: int = 0             # vezes em que a fila esvaziou com o som correndo (a alimentadora atrasou)
    endpoint: str = ""           # D-067: o endpoint do fluxo ("" = não se sabe)
    resume: bool = False         # D-067: solto tocando para a releitura do PortAudio: o `reattach` volta do mesmo ponto
    retried: bool = False        # R-03: o aparelho caiu e já foi reaberto uma vez desde a última escrita boa

    @property
    def block(self) -> int:
        return max(1, int(self.rate * BLOCK_S))


@dataclass(slots=True, eq=False)
class _Feed:
    """Um trecho contínuo (uma geração da fila, R7-02): a alimentadora produz a partir de `start_t` e a escritora tira o áudio
    na ordem. Trocar de trecho (pausar, outro ponto, ±10 s) troca o objeto, e o que estava pronto sai junto."""
    sess: _Session
    start_t: int
    limit: int                   # quadros prontos à frente, no máximo (`QUEUE_S`)
    parts: deque[np.ndarray] = field(default_factory=deque)
    queued: int = 0              # quadros na fila
    produced: int = 0            # quadros produzidos (as âncoras das pausas puladas contam por aqui)
    done: bool = False           # fim do áudio: o que está na fila é o resto
    error: str = ""


@dataclass(frozen=True, slots=True, eq=False)
class _Pause:
    """O comando de pausa: o aviso da barra (R7-01) e, quando alguém espera a confirmação, o evento que a escritora acende
    depois de tratar a pausa: o bloco em voo saiu e o buffer do dispositivo foi descartado (R8-01). `release` (a pausa do
    warm-up, U-043) também fecha o fluxo: o PortAudio fica livre para reler os dispositivos, e a retomada abre outro."""
    message: str = ""
    done: threading.Event | None = None
    release: bool = False


@dataclass(slots=True, eq=False)
class _Cursor:
    """Onde a alimentadora está no trecho atual (só a thread dela mexe)."""
    feed: _Feed
    chunks: Chunks
    next_t: int
    resampler: StreamResampler | None


class PlaybackEngine:
    """O lado sem Qt do player: comandos da interface numa fila, a escritora, a alimentadora e o estado lido sob um lock."""

    def __init__(self, store: Store, factory: OutputFactory, *, decode_s: float = DECODE_S, queue_s: float = QUEUE_S,
                 probe: Callable[[], str | None] | None = None, clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.factory = factory
        self.route = Follow(self, probe, clock)     # D-067: o padrão de saída de agora (probe None = não acompanha)
        self.decode_ms = max(1, int(decode_s * 1000))
        self.queue_s = max(BLOCK_S, float(queue_s))
        self._cv = threading.Condition()
        self._cmds: deque[tuple[str, Any]] = deque()
        self._thread: threading.Thread | None = None
        self._feeders: set[threading.Thread] = set()   # alimentadoras vivas (cada uma sai do conjunto ao terminar, R8-03)
        self._feed: _Feed | None = None             # o trecho que a alimentadora prepara (sob `_cv`)
        self._shut = False                          # a saída do app: nenhuma thread nasce depois dela
        # estado visto pela interface (sob `_cv`)
        self._state = STOPPED
        self._req: PlayRequest | None = None
        self._message = ""
        self._duration: int | None = None
        self._rate = 0
        self._latency = 0
        self._written = 0                       # quadros entregues ao dispositivo desde o começo do trecho atual
        self._anchors: list[tuple[int, int]] = [(0, 0)]
        self._end_t = 0
        self._paused_at = 0
        self.opened = 0                         # instrumentação: fluxos de saída abertos
        self.underflows = self.starved = 0      # instrumentação: buracos na saída (underflow) e esperas pela alimentadora (R7-02)

    # ---------------------------------------------------------------- interface (thread do Qt)
    def play(self, req: PlayRequest) -> None:
        with self._cv:
            self._state, self._req, self._message = PLAYING, req, ""
            self._paused_at = int(req.t_ms)
            self._written, self._anchors, self._rate = 0, [(0, int(req.t_ms))], 0
        self._send("play", req)

    def pause(self, message: str = "", release: bool = False) -> None:
        """Pausa o que toca; `message` é o aviso da barra até retomar (R7-01). Já pausado, só troca o aviso. O aviso vai
        também no comando: a thread, tratando antes um "retomar" mais antigo, reescreve o estado e o aviso, e a pausa os
        devolve. `release` fecha também o fluxo (a pausa do warm-up, U-043)."""
        with self._cv:
            if self._state == PLAYING:
                self._paused_at, self._state = self._position_locked(), PAUSED
            if self._state == PAUSED:
                self._message = message
        self._send("pause", _Pause(message, release=release))

    def pause_and_wait(self, message: str, timeout_s: float) -> tuple[bool, bool]:
        """`pause` mais a espera da confirmação da escritora: o bloco em voo saiu e o buffer do dispositivo foi descartado,
        nenhum bloco do player sai depois (R8-01: logo antes de o gravador abrir o loopback do sistema). Seguro em qualquer
        thread. Devolve (tocava, confirmado no prazo); sem escritora viva não há som a parar."""
        done = threading.Event()
        with self._cv:
            playing = self._state == PLAYING
            if playing:
                self._paused_at, self._state = self._position_locked(), PAUSED
            if self._state == PAUSED:
                self._message = message
            alive = self._thread is not None and self._thread.is_alive()
        if not alive:
            return playing, True
        self._send("pause", _Pause(message, done))
        return playing, done.wait(timeout_s)

    def release_and_wait(self, message: str, timeout_s: float) -> bool:
        """F5.3 (A-17): o fluxo de saída fecha e a escritora confirma: o som que tocava pausa com `message`; o já pausado
        guarda o aviso dele (o fluxo que a pausa do R8-01 deixou aberto também fecha). True quando nada ficou aberto no
        prazo; sem escritora viva não há fluxo."""
        done = threading.Event()
        with self._cv:
            if self._state == PLAYING:
                self._paused_at, self._state, self._message = self._position_locked(), PAUSED, message
            alive = self._thread is not None and self._thread.is_alive()
        if not alive:
            return True
        self._send("pause", _Pause(message, done, release=True))
        return done.wait(timeout_s)

    def detach_and_wait(self, timeout_s: float) -> bool:
        """D-067: a releitura do PortAudio (só sem stream aberto) vai acontecer: a escritora fecha o fluxo e guarda o ponto;
        tocando, o som volta dali no `reattach` sem passar por pausado. True sem fluxo aberto no prazo."""
        if not self.alive:
            return True
        done = threading.Event()
        self._send("detach", done)
        return done.wait(timeout_s)

    def reattach(self) -> None:
        """D-067: depois da releitura, o som solto pelo `detach` volta no padrão de agora; o que não abriu tenta de novo."""
        with self._cv:
            self.route.stuck = self.route.reread = None
        self._send("reattach", start=False)

    def stuck_id(self) -> tuple[str, int] | None:   # D-067: o padrão fora da lista que reler pode resolver, ou None
        with self._cv:
            return self.route.reread

    @property
    def has_stream(self) -> bool:               # D-067: a escritora tem um fluxo aberto (um bool: lido sem o lock)
        return self.route.has_stream

    def kick(self) -> None:                     # acorda a escritora pausada (os testes, depois de andar o relógio manual)
        with self._cv:
            self._cv.notify_all()

    def clear_notice(self) -> None:
        """Tira o aviso da pausa (a gravação que o pediu não começou ou acabou); o som segue pausado (R8-01)."""
        with self._cv:
            if self._state == PAUSED:
                self._message = ""

    def resume(self) -> None:
        with self._cv:
            if self._state == PAUSED:
                self._state, self._message = PLAYING, ""
                self._written, self._anchors, self._rate = 0, [(0, self._paused_at)], 0
        self._send("resume")

    def seek(self, delta_ms: int) -> None:
        self._send("seek", int(delta_ms))

    def stop(self) -> None:
        with self._cv:
            if self._state != ERROR:
                self._state, self._message = STOPPED, ""
        self._send("stop", start=False)

    def shutdown(self, timeout_s: float = JOIN_S) -> bool:
        """Para o som e espera a escritora e as alimentadoras num prazo só (a saída do app, antes de fechar o banco); depois
        disto nenhuma thread nasce. True só sem thread do player viva: uma janela lenta da alimentadora não passa mais do
        `store.close()` como se tivesse acabado (R8-03)."""
        with self._cv:
            self._shut = True
        self._send("quit", start=False)
        deadline = time.monotonic() + timeout_s
        me = threading.current_thread()
        while True:
            with self._cv:
                alive = [t for t in (self._thread, *self._feeders) if t is not None and t is not me and t.is_alive()]
            left = deadline - time.monotonic()
            if not alive or left <= 0:
                return not alive
            alive[0].join(left)

    def snapshot(self) -> PlayerState:
        with self._cv:
            req = self._req
            t = self._position_locked() if self._state == PLAYING else self._paused_at
            return PlayerState(self._state, req.meeting_id if req else None, t, self._duration, req.title if req else "", self._message,
                               self.route.note if self._state in (PLAYING, PAUSED) else "", self.route.endpoint)

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _send(self, cmd: str, arg: Any = None, *, start: bool = True) -> None:
        with self._cv:
            alive = self._thread is not None and self._thread.is_alive()
            if not alive and (not start or self._shut):
                return                              # nada tocando: parar não precisa de thread; depois da saída, nada nasce
            self._cmds.append((cmd, arg))
            if not alive:
                self._spawn_locked()
            self._cv.notify_all()

    def _spawn_locked(self) -> None:
        """(sob `_cv`) Uma escritora nova, já iniciada quando entra no lugar: o `alive`, lido sem o lock, nunca a vê parada."""
        thread = threading.Thread(target=self._run, name="player", daemon=True)
        thread.start()
        self._thread = thread

    def _position_locked(self) -> int:
        if self._rate <= 0:
            return self._anchors[0][1] if self._anchors else 0
        t = position_ms(self._anchors, max(0, self._written - self._latency), self._rate)
        return min(t, self._end_t) if self._end_t else t

    # ---------------------------------------------------------------- escritora
    def _run(self) -> None:
        """A escritora. Fica em `_thread` até o fim do `finally` (a alimentadora juntada, a conexão fechada): o `shutdown` só
        a vê parada quando nada dela segue rodando (R8-03); comando que chegar no meio relança uma escritora nova."""
        sess: _Session | None = None
        me = threading.current_thread()
        quit_feed = threading.Event()
        feeder = threading.Thread(target=self._feed_loop, args=(quit_feed,), name="player-feed", daemon=True)
        with self._cv:
            self._feeders.add(feeder)
        feeder.start()
        try:
            while True:
                with self._cv:
                    while not self._cmds and (sess is None or not sess.playing):
                        if sess is None:              # parado: a thread termina (nasce de novo no próximo "tocar")
                            quit_feed.set()           # ainda sob o lock: esta alimentadora não pega o trecho de outra
                            self._cv.notify_all()
                            return
                        self._cv.wait()               # pausado: espera um comando (R-09: sem ler o padrão)
                    cmd = self._cmds.popleft() if self._cmds else None
                if cmd is not None:
                    sess = self._handle(sess, cmd)
                    if cmd[0] == "quit":
                        return
                elif sess is not None:
                    sess = self.route.follow(sess) if self.route.due(sess) else self._step(sess)
        except Exception as e:  # o player nunca derruba o app: o motivo vai para a barra
            log.exception("player falhou")
            self._error(f"o player falhou: {e}")
            self._close(sess)
            sess = None
        finally:
            self._close(sess)
            with self._cv:
                quit_feed.set()
                self._cv.notify_all()
            feeder.join(FEED_JOIN_S)
            self.store.close_thread()
            with self._cv:
                if self._cmds and self._thread in (me, None) and not self._shut:
                    self._spawn_locked()            # sobrou comando (chegou durante a queda): uma escritora nova o trata (R7-02)
                elif self._thread is me:
                    self._thread = None

    def _handle(self, sess: _Session | None, cmd: tuple[str, Any]) -> _Session | None:
        if cmd[0] in ("play", "resume", "seek", "reattach"):
            self.route.next_check = 0.0               # D-067: o padrão é lido logo depois, antes do primeiro bloco
        match cmd:
            case ("play", PlayRequest() as req):
                return self._open(sess, req)
            case ("pause", _Pause() as pause):
                return self._on_pause(sess, pause)
            case ("resume", _):
                return self._on_resume(sess)
            case ("seek", int() as delta):
                return self._on_seek(sess, delta)
            case ("detach", threading.Event() as done):
                return self.route.detach(sess, done)
            case ("reattach", _):
                if sess is not None and sess.resume:     # D-067: solto tocando pela releitura: volta do mesmo ponto
                    sess.resume = False
                    return self._open(None, replace(sess.req, t_ms=sess.pos))
                return sess
            case _:                                   # "stop" e "quit"
                self._close(sess)
                return None

    def _on_pause(self, sess: _Session | None, pause: _Pause) -> _Session | None:
        try:
            if sess is not None and sess.playing:
                sess.pos = self.snapshot().t_ms
                self._abort(sess)
                sess.playing = False
                with self._cv:
                    if self._feed is not None and self._feed.sess is sess:
                        self._feed = None         # o que estava pronto sai (R7-02)
                    self._paused_at, self._state, self._message = sess.pos, PAUSED, pause.message
            if sess is not None:
                sess.resume = False                   # D-067: pausado durante a releitura não volta a tocar sozinho
            if pause.release and sess is not None and not sess.playing:
                self._release(sess)               # U-043: o fluxo sai; a sessão guarda o ponto para a retomada
        finally:
            if pause.done is not None:
                pause.done.set()                  # quem espera (o gancho do loopback, R8-01) segue
        return sess

    def _on_resume(self, sess: _Session | None) -> _Session | None:
        if sess is not None and sess.closed:         # pausa que soltou o fluxo: outro, pela fábrica, do mesmo ponto
            return self._open(None, replace(sess.req, t_ms=sess.pos))
        if sess is not None and not sess.playing:
            self._start_run(sess, sess.pos)
        return sess

    def _on_seek(self, sess: _Session | None, delta: int) -> _Session | None:
        if sess is None:
            return None
        base = self.snapshot().t_ms if sess.playing else sess.pos
        target = seek_target(base, delta, sess.pauses, sess.end_t)     # em tempo de áudio (R7-04)
        if sess.playing:
            self._abort(sess)
            self._start_run(sess, target)
        else:
            sess.pos = target
            with self._cv:
                self._paused_at = target
        return sess

    def _open(self, sess: _Session | None, req: PlayRequest) -> _Session | None:
        """Um "tocar" novo: lê a reunião, reaproveita o fluxo aberto (descartando o som que estava nele) ou abre um."""
        try:
            info = self._meeting(req)
        except PlaybackError as e:
            self._close(sess)
            self._error(str(e))
            return None
        except Exception as e:  # banco: o motivo vai para a barra
            log.warning("player: reunião %s ilegível: %s", req.meeting_id, e)
            self._close(sess)
            self._error(f"não consegui ler o áudio: {e}")
            return None
        if sess is not None and not sess.closed:      # o som anterior acaba aqui; o fluxo fica para este
            self._abort(sess)
            self._report(sess)
            stream = sess.stream
        else:
            try:
                stream = self.factory(None)               # D-067: o padrão de agora
                self.opened += 1
            except Exception as e:  # sem PortAudio, sem dispositivo, taxa recusada
                log.warning("player: %s: %s", NO_DEVICE, e)
                self._error(f"{NO_DEVICE} ({e})")
                return None
        rate, latency, endpoint = _stream_format(stream)
        new = _Session(req, stream, rate, latency, *info, endpoint=endpoint)
        with self._cv:
            self.route.has_stream = True
        start = int(req.t_ms)
        if start >= new.end_t:
            start = max(0, new.end_t - 1)
        self._start_run(new, start)
        return new

    def _meeting(self, req: PlayRequest) -> tuple[int, tuple[str, ...], int, int | None, list[tuple[int, int | None]]]:
        """(taxa gravada, canais, fim do áudio a tocar, duração, pausas) ou `PlaybackError` com o motivo."""
        m = self.store.get_meeting(req.meeting_id)
        if m is None:
            raise PlaybackError("esta reunião não existe mais")
        missing = audio_path_missing(self.store, m)
        if missing is not None:
            raise PlaybackError(f"sem o arquivo de áudio desta reunião na pasta do banco ({missing})")
        stats = self.store.chunk_stats(req.meeting_id)
        channels = (req.channel,) if req.channel else tuple(ch for ch in ("L", "R") if ch in stats)
        if not channels or any(ch not in stats for ch in channels):
            raise PlaybackError("esta reunião não tem áudio gravado" + (f" no canal {req.channel}" if req.channel else ""))
        in_rate = max(int(stats[ch]["sr"] or 16000) for ch in channels)
        covered = max(int(stats[ch]["t1"] or 0) for ch in channels)
        end_t = min(covered, int(req.end_ms)) if req.end_ms is not None else covered
        duration = m.get("duration_ms")
        return (in_rate, channels, end_t, int(duration) if duration else covered,
                pause_intervals(self.store.list_events(req.meeting_id)))

    def _start_run(self, sess: _Session, t_ms: int) -> None:
        """Um trecho contínuo novo a partir de `t_ms` (o começo, a retomada ou o ponto do ±10 s): uma geração nova da fila (o
        que estava pronto sai) e a âncora no quadro 0; o fluxo começa quando o primeiro bloco fica pronto (R7-02)."""
        start = skip_paused(t_ms, sess.pauses)
        start_t = sess.end_t if start is None else min(start, sess.end_t)
        sess.playing = True
        with self._cv:
            self._feed = _Feed(sess, start_t, max(sess.block, int(sess.rate * self.queue_s)))
            self._written, self._anchors, self._state, self._message, self.route.endpoint = 0, [(0, start_t)], PLAYING, "", sess.endpoint
            self._rate, self._latency, self._duration, self._end_t = sess.rate, sess.latency, sess.duration_ms, sess.end_t
            self._cv.notify_all()

    def _step(self, sess: _Session) -> _Session | None:
        """Escreve o próximo bloco pronto (bloqueante: dá o ritmo); com a fila vazia, espera a alimentadora sem deixar de
        atender os comandos; no fim do áudio, espera o resto tocar e para. Nunca decodifica (R7-02)."""
        with self._cv:
            feed, waited = self._feed, False
            while not self._cmds and feed is not None and feed.sess is sess and not feed.queued and not feed.done:
                waited = True
                self._cv.wait()
                feed = self._feed
            if self._cmds:
                return sess                          # comando primeiro: o laço do `_run` trata
            if feed is None or feed.sess is not sess:
                block, error = _EMPTY, ""            # sem trecho (não acontece): o som acaba
            else:
                block, error = self._take(feed, sess.block), feed.error
                if waited and sess.started:
                    sess.starved += 1                # a fila esvaziou com o som correndo: a alimentadora atrasou
                self._cv.notify_all()                # coube mais na fila: a alimentadora segue
        if not len(block):
            self._finish(sess, error)
            return None
        return self._write(sess, block)

    def _write(self, sess: _Session, block: np.ndarray) -> _Session | None:
        """Começa o fluxo no primeiro bloco do trecho e escreve (bloqueante). R-07: o buraco é o buffer vazio antes da
        escrita (o `underflowed` do PortAudio não vem). R-03: o aparelho que cai reabre uma vez no padrão de agora."""
        starting = not sess.started
        try:
            if starting:
                sess.stream.start()
                sess.started = True
            dry = not starting and ran_dry(sess.stream, sess.latency)
            starting = False
            underflowed = sess.stream.write(block)
        except Exception as e:  # o fone saiu no meio, ou o aparelho sumiu entre abrir e começar
            if self.route.recover(sess, e):
                return sess
            log.warning("player: %s: %s", NO_DEVICE if starting else "a saída de áudio falhou", e)
            self._close(sess)
            self._error(f"{NO_DEVICE} ({e})" if starting else f"a saída de áudio parou ({e})")
            return None
        sess.retried = False
        if dry or underflowed:
            sess.underflows += 1                     # o dispositivo ficou sem som antes deste bloco (R7-02)
        sess.written += len(block)
        with self._cv:
            self._written += len(block)
        return sess

    @staticmethod
    def _take(feed: _Feed, n: int) -> np.ndarray:
        """(sob `_cv`) Até `n` quadros do começo da fila, juntando as partes."""
        parts: list[np.ndarray] = []
        need = n
        while need > 0 and feed.parts:
            head = feed.parts[0]
            if len(head) <= need:
                parts.append(feed.parts.popleft())
                need -= len(head)
            else:
                parts.append(head[:need])
                feed.parts[0] = head[need:]
                need = 0
        out = parts[0] if len(parts) == 1 else (np.concatenate(parts) if parts else _EMPTY)
        feed.queued -= len(out)
        return out

    def _finish(self, sess: _Session, error: str) -> None:
        """Fim do áudio: o que já está no dispositivo termina de tocar e o som para (com o motivo, se a alimentadora falhou)."""
        if error:
            self._close(sess)
            self._error(error)
            return
        if sess.started:
            drain(sess.stream, sess.latency, sess.rate, self._commanded)    # R-01: o `stop` do WASAPI não espera
            try:
                sess.stream.stop()                    # o que já está no dispositivo termina de tocar
            except Exception as e:  # o fluxo pode ter caído: parar assim mesmo
                log.debug("player: parar o fluxo: %s", e)
        with self._cv:
            self._paused_at, self._state, self._message = sess.end_t, STOPPED, ""
        self._close(sess, abort=False)

    def _commanded(self) -> bool:
        with self._cv:
            return bool(self._cmds)

    # ---------------------------------------------------------------- alimentadora (R7-02)
    def _feed_loop(self, quit_: threading.Event) -> None:
        """Prepara o trecho atual à frente da escritora, até `QUEUE_S`, uma janela curta por vez fora do lock; trecho trocado
        no meio de uma janela, o que ela fez sai. Usa a própria conexão do `Store` e a fecha ao terminar."""
        cur: _Cursor | None = None
        try:
            while True:
                with self._cv:
                    feed = self._next_feed(quit_)
                if feed is None:
                    return
                y, anchor, done, error = _EMPTY, None, False, ""
                try:
                    if cur is None or cur.feed is not feed:              # outro trecho: começa do ponto dele
                        cur = self._cursor(cur, feed)
                    y, anchor, done = self._produce(cur)
                except Exception as e:  # banco ou FLAC ilegível: o som para com o motivo na barra
                    log.exception("player: a decodificação falhou")
                    done, error = True, f"o player falhou: {e}"
                with self._cv:
                    if self._feed is not feed:
                        continue                                          # o trecho mudou no meio: o que fiz sai
                    if len(y):
                        feed.parts.append(y)
                        feed.queued += len(y)
                        feed.produced += len(y)
                    if anchor is not None:
                        self._anchors.append((feed.produced, anchor))      # a pausa pulada: o tempo salta neste quadro
                    feed.done, feed.error = done, error
                    self._cv.notify_all()
        finally:
            self.store.close_thread()
            with self._cv:
                self._feeders.discard(threading.current_thread())      # o `shutdown` deixa de esperá-la (R8-03)

    def _next_feed(self, quit_: threading.Event) -> _Feed | None:
        """(sob `_cv`) O trecho que precisa de mais áudio; espera enquanto a fila está cheia ou não há trecho. None = sair."""
        while not quit_.is_set():
            feed = self._feed
            if feed is not None and not feed.done and feed.queued < feed.limit:
                return feed
            self._cv.wait()
        return None

    def _cursor(self, cur: _Cursor | None, feed: _Feed) -> _Cursor:
        """O começo de um trecho: no mesmo som, os pedaços já decodificados ficam (servem ao ±10 s); outro som, outra lista."""
        sess = feed.sess
        chunks = cur.chunks if cur is not None and cur.feed.sess is sess else Chunks(self.store, sess.req.meeting_id, sess.in_rate)
        resampler = StreamResampler(sess.in_rate, sess.rate) if sess.in_rate != sess.rate else None
        return _Cursor(feed, chunks, feed.start_t, resampler)

    def _produce(self, cur: _Cursor) -> tuple[np.ndarray, int | None, bool]:
        """Um passo da alimentadora: (áudio, âncora, fim). Uma janela curta de áudio; ou a pausa da gravação pulada (o resto da
        reamostragem e o tempo de depois dela, a âncora); ou o fim do áudio (o resto da reamostragem)."""
        sess, t = cur.feed.sess, cur.next_t
        if t >= sess.end_t:
            return self._flush(cur), None, True
        after = skip_paused(t, sess.pauses)
        if after != t:
            tail = self._flush(cur)
            if after is None or after >= sess.end_t:
                cur.next_t = sess.end_t
                return tail, None, True
            cur.next_t = after
            cur.resampler = StreamResampler(sess.in_rate, sess.rate) if cur.resampler is not None else None
            return tail, after, False
        stop = min(t + self.decode_ms, sess.end_t)
        pause = next_pause(t, sess.pauses)
        if pause is not None:
            stop = min(stop, pause)
        x = self._decode(cur.chunks, sess, t, stop)
        cur.next_t = stop
        return (cur.resampler.process(x) if cur.resampler is not None else x), None, False

    def _decode(self, chunks: Chunks, sess: _Session, t0: int, t1: int) -> np.ndarray:
        """[t0, t1) de L e R misturados (ou do canal pedido) na taxa gravada, cortado dos pedaços já decodificados."""
        s0, s1 = round(t0 * sess.in_rate / 1000), round(t1 * sess.in_rate / 1000)
        parts = [chunks.window(ch, s0, s1) for ch in sess.channels]
        return mix(parts[0], parts[1]) if len(parts) == 2 else parts[0]

    @staticmethod
    def _flush(cur: _Cursor) -> np.ndarray:
        return cur.resampler.flush() if cur.resampler is not None else _EMPTY

    # ---------------------------------------------------------------- fim
    def _abort(self, sess: _Session | None) -> None:
        """Descarta o buffer do dispositivo; o fluxo para e começa de novo no próximo bloco escrito."""
        if sess is None or not sess.started:
            return
        sess.started = False
        try:
            sess.stream.abort()
        except Exception as e:  # fluxo já parado ou caído
            log.debug("player: abortar o fluxo: %s", e)

    def _release(self, sess: _Session) -> None:
        """A pausa que solta o dispositivo (U-043, A-04): o fluxo fecha e a sessão segue pausada no mesmo ponto; a retomada
        abre outro pela fábrica (`_handle`, "resume")."""
        if sess.closed:
            return
        sess.closed = True
        with self._cv:
            self.route.has_stream = False
        try:
            sess.stream.close()
        except Exception as e:
            log.debug("player: soltar o fluxo: %s", e)
        self._report(sess)

    def _close(self, sess: _Session | None, abort: bool = True) -> None:
        if sess is None:
            return
        if not sess.closed:                            # a solta pela pausa do warm-up já fechou o fluxo: só o estado muda
            sess.closed = True
            if abort:
                self._abort(sess)
            try:
                sess.stream.close()
            except Exception as e:
                log.debug("player: fechar o fluxo: %s", e)
            sess.playing = False
            self._report(sess)
        with self._cv:
            self.route.reset()
            if self._feed is not None and self._feed.sess is sess:
                self._feed = None                     # a alimentadora para; o que estava pronto sai
            if self._state in (PLAYING, PAUSED):
                self._paused_at, self._state = self._position_locked(), STOPPED

    def _report(self, sess: _Session) -> None:
        """O som acabou (parou, trocou de ponto, caiu): os buracos que o dispositivo contou (`underflowed`) e as esperas pela
        alimentadora vão para o log (R7-02; no Windows, a medida do MME)."""
        self.underflows, self.starved = sess.underflows, sess.starved
        seconds = sess.written / sess.rate if sess.rate else 0.0
        log.info("player: reunião %s: %.1f s tocados, %d buraco(s) na saída (underflow), %d espera(s) pela decodificação",
                 sess.req.meeting_id, seconds, sess.underflows, sess.starved)

    def _error(self, message: str) -> None:
        with self._cv:
            self._state, self._message = ERROR, message


# ---------------------------------------------------------------- Qt
class Player(QObject):
    """O player do app (um só, criado pelo controlador e passado às janelas). `state_changed` quando muda o estado, a reunião
    ou o aviso; `position_changed` a cada 100 ms enquanto toca. `guard(pedido)` é chamado antes de o som começar (Q9)."""
    state_changed = Signal(object)              # PlayerState
    position_changed = Signal(object)           # PlayerState
    _kicked = Signal()                          # refaz a leitura na thread da interface (pedido vindo de outra thread)

    def __init__(self, store: Store, output_factory: OutputFactory | None = None, parent: QObject | None = None, *,
                 devices: OutputDevices | None = None, clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        factory = output_factory or (devices.open_output if devices is not None else sounddevice_output)
        self.engine = PlaybackEngine(store, factory, probe=devices.default_output_id if devices is not None else None, clock=clock)
        self.reroute = (Reroute(self.engine, devices.refresh_audio_devices, devices.audio_devices_changed, clock)   # D-067
                        if devices is not None else None)
        self.names = OutputName(devices)                # F5.9 (item 10): o nome da saída para a barra, só quando o id muda
        self.guard: Callable[[PlayRequest], bool] | None = None
        self.before_play: Callable[[], object] | None = None   # U-043: o controlador relê os dispositivos (saída nova)
        self.requests: deque[PlayRequest] = deque(maxlen=REQUESTS_KEPT)   # instrumentação dos testes
        self._last: PlayerState | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._kicked.connect(self._kick)        # direto na thread da interface; de outra thread, na fila dela

    def state(self) -> PlayerState:
        return self.engine.snapshot()

    def play_from(self, meeting_id: int, t_ms: int, *, end_ms: int | None = None, channel: str | None = None, title: str = "") -> bool:
        """Toca a reunião a partir de `t_ms` (até `end_ms` e só `channel`, quando dados). False quando o `guard` recusou."""
        req = PlayRequest(int(meeting_id), max(0, int(t_ms)), end_ms, channel, title)
        if self.state().state != PLAYING and self.guard is not None and not self.guard(req):
            return False
        if self.before_play is not None and not self.engine.alive:   # com som aberto o fluxo é reaproveitado: não relê
            try:
                self.before_play()
            except Exception as e:  # o player toca mesmo assim, na saída que já conhecia
                log.debug("player: reler os dispositivos: %s", e)
        self.requests.append(req)
        log.info("player: tocar a reunião %s de %s%s%s", req.meeting_id, fmt_mmss(req.t_ms / 1000),
                 f" até {fmt_mmss(req.end_ms / 1000)}" if req.end_ms is not None else "", f" (canal {req.channel})" if req.channel else "")
        self.engine.play(req)
        self._kick()
        return True

    def toggle(self) -> bool:
        """Pausa ou retoma o que estiver carregado; False quando não há nada (ou o `guard` recusou retomar)."""
        st = self.state()
        if st.state == PLAYING:
            log.info("player: pausar a reunião %s em %s", st.meeting_id, fmt_mmss(st.t_ms / 1000))
            self.engine.pause()
        elif st.state == PAUSED and st.meeting_id is not None:
            req = PlayRequest(st.meeting_id, st.t_ms, title=st.title)
            if self.guard is not None and not self.guard(req):
                log.info("player: retomar a reunião %s recusado (a gravação capta o sistema inteiro)", st.meeting_id)
                return False
            log.info("player: retomar a reunião %s de %s", st.meeting_id, fmt_mmss(st.t_ms / 1000))
            if self.before_play is not None:               # R-11: a pausa do warm-up soltou o fluxo; a saída pode ter mudado
                try:
                    self.before_play()
                except Exception as e:  # retoma mesmo assim, na saída que já conhecia
                    log.debug("player: reler os dispositivos na retomada: %s", e)
            self.engine.resume()
        else:
            return False
        self._kick()
        return True

    def pause(self, notice: str = "", release: bool = False) -> bool:
        """Pausa o som que toca com o aviso `notice` na barra até retomar (R7-01: o warm-up abriu ou a gravação vai captar o
        sistema inteiro); já pausado, só troca o aviso. Retomar passa pelo `guard` (Q9). True quando havia som tocando.
        `release` (o warm-up, U-043) fecha também o fluxo: o PortAudio fica livre para reler os dispositivos."""
        st = self.state().state
        if st not in (PLAYING, PAUSED):
            return False
        log.info("player: pausar a reunião %s (%s%s)", self.state().meeting_id, notice or "sem aviso",
                 "; o fluxo de saída é solto" if release else "")
        self.engine.pause(notice, release)
        self._kick()
        return st == PLAYING

    def pause_and_wait(self, notice: str, timeout_s: float) -> tuple[bool, bool]:
        """`pause` que espera o motor confirmar que o som parou de sair (R8-01, antes do loopback do sistema): (tocava,
        confirmado no prazo). Seguro em qualquer thread: só o motor é tocado aqui, e a barra é refeita na thread da interface
        pelo sinal `_kicked`."""
        result = self.engine.pause_and_wait(notice, timeout_s)
        self._kicked.emit()
        return result

    def release_output(self, notice: str, timeout_s: float) -> bool:
        """F5.3 (A-17): solta o fluxo de saída e espera (a troca de microfone vai reler o PortAudio): o som que toca pausa
        com `notice` na barra, e retomar passa pelo `guard` como sempre. True quando nada ficou aberto no prazo."""
        st = self.state()
        if st.active or self.engine.alive:
            log.info("player: soltar a saída da reunião %s (%s)", st.meeting_id, notice)
        released = self.engine.release_and_wait(notice, timeout_s)
        self._kicked.emit()
        return released

    def clear_notice(self) -> None:
        """Tira o aviso da pausa da barra; o som segue pausado (a gravação que pediu a pausa não começou ou acabou, R8-01)."""
        self.engine.clear_notice()
        self._kick()

    def seek(self, delta_ms: int) -> None:
        if self.state().active:
            log.debug("player: %+d s na reunião %s", delta_ms // 1000, self.state().meeting_id)
            self.engine.seek(delta_ms)
            self._kick()

    def stop(self, meeting_id: int | None = None) -> None:
        """Para o som (só se for desta reunião, quando `meeting_id` é dado: fechar a reunião para o som dela)."""
        st = self.state()
        if meeting_id is not None and st.meeting_id != meeting_id:
            return
        if st.active or self.engine.alive:
            log.info("player: parar a reunião %s em %s", st.meeting_id, fmt_mmss(st.t_ms / 1000))
            self.engine.stop()
            self._kick()

    def shutdown(self, timeout_s: float = JOIN_S) -> bool:
        self._timer.stop()
        done = self.engine.shutdown(timeout_s)
        if not done:
            log.warning("a thread do player não terminou em %.1f s", timeout_s)
        return done

    def _kick(self) -> None:
        self.poll()
        if not self._timer.isActive():
            self._timer.start()

    def poll(self) -> None:
        st = self.state()
        last = self._last
        self._last = st
        if last is None or (st.state, st.meeting_id, st.title, st.message, st.duration_ms, st.note, st.endpoint) != (
                last.state, last.meeting_id, last.title, last.message, last.duration_ms, last.note, last.endpoint):
            self.state_changed.emit(st)
        if last is None or st.t_ms != last.t_ms or st.meeting_id != last.meeting_id:
            self.position_changed.emit(st)
        if self.reroute is not None and st.active:
            self.reroute.tick()                         # D-067: o padrão novo fora da lista do PortAudio
        if st.state not in (PLAYING, PAUSED) and not self.engine.alive:
            self._timer.stop()


__all__ = ["ERROR", "PAUSED", "PLAYING", "ROUTE_CHECK_S", "ROUTE_STUCK", "STOPPED", "OutputDevices", "OutputStream", "PlayRequest",
           "PlaybackEngine", "Player", "PlayerState", "audio_ms", "audio_path_missing", "meeting_ms", "mix", "next_pause",
           "pause_intervals", "position_ms", "prewarm_audio", "seek_target", "skip_paused", "sounddevice_output"]
