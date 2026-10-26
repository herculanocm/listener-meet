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
e avisos em texto puro na barra (R7-07)."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ..capture.audio import StreamResampler, decode_flac, resample
from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..model import EventType
from ..store.core import LEGACY_AUDIO_FILE

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
GAIN = np.float32(0.5)           # L e R misturados com ganho ½ cada
CHUNKS_KEPT = 3                  # pedaços decodificados guardados por canal (o da janela e os vizinhos)
REQUESTS_KEPT = 100              # instrumentação dos testes: os últimos pedidos

STOPPED, PLAYING, PAUSED, ERROR = "stopped", "playing", "paused", "error"
STATE_TEXT = {STOPPED: "parado", PLAYING: "tocando", PAUSED: "pausado", ERROR: "erro"}
NO_DEVICE = "sem dispositivo de saída de áudio"


class OutputStream(Protocol):
    """O que o player usa de um `sounddevice.OutputStream` mono float32 (a falsa dos testes imita isto)."""
    @property
    def samplerate(self) -> float: ...
    @property
    def latency(self) -> float: ...                   # segundos (num fluxo só de saída o `sounddevice` dá um número)
    def start(self) -> None: ...
    def write(self, data: np.ndarray, /) -> Any: ...  # bloqueante
    def stop(self) -> None: ...                       # espera o buffer tocar
    def abort(self) -> None: ...                      # descarta o buffer
    def close(self) -> None: ...


type OutputFactory = Callable[[], OutputStream]


def sounddevice_output() -> OutputStream:
    """O dispositivo de saída padrão do sistema, na taxa padrão dele, mono float32. O `sounddevice` só é importado aqui
    (sem PortAudio a importação levanta `OSError`, que vira "sem dispositivo de saída")."""
    import sounddevice as sd

    info = sd.query_devices(kind="output")
    rate = round(float(info["default_samplerate"]))
    stream: OutputStream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
    return stream


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

    @property
    def active(self) -> bool:
        return self.state in (PLAYING, PAUSED)


# ---------------------------------------------------------------- funções puras
def pause_intervals(events: Iterable[Mapping[str, Any]]) -> list[tuple[int, int | None]]:
    """Intervalos [pausa, retomada) da gravação a partir dos eventos `pause` e `resume` em ordem de tempo (D-011); uma pausa
    sem retomada vai até o fim (None); pausa repetida sem retomada no meio conta a primeira."""
    out: list[tuple[int, int | None]] = []
    start: int | None = None
    for e in sorted((e for e in events if e.get("t_ms") is not None), key=lambda e: int(e["t_ms"])):
        kind, t = str(e.get("type")), int(e["t_ms"])
        if kind == EventType.PAUSE and start is None:
            start = t
        elif kind == EventType.RESUME and start is not None:
            if t > start:
                out.append((start, t))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def skip_paused(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int | None:
    """Onde o som começa a partir de `t_ms`: ele mesmo fora das pausas, o fim da pausa que o contém, None numa pausa que vai
    até o fim da reunião."""
    t = int(t_ms)
    for a, b in intervals:
        if a <= t and (b is None or t < b):
            if b is None:
                return None
            t = b
    return t


def next_pause(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int | None:
    """Começo da próxima pausa depois de `t_ms` (a janela decodificada para ali)."""
    starts = [a for a, _b in intervals if a > t_ms]
    return min(starts) if starts else None


def audio_ms(t_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int:
    """Tempo de áudio no instante `t_ms` da reunião: ele menos o que as pausas da gravação tomaram antes dele (um instante
    dentro de uma pausa vale o começo dela)."""
    t = int(t_ms)
    paused = 0
    for a, b in sorted(intervals):
        if a >= t:
            break
        paused += (t if b is None else min(b, t)) - a
    return t - paused


def meeting_ms(u_ms: int, intervals: Iterable[tuple[int, int | None]]) -> int:
    """O instante da reunião com `u_ms` de áudio (o inverso de `audio_ms`): sempre fora das pausas, salvo a que vai até o fim
    (não há áudio depois dela: o começo dela)."""
    t = int(u_ms)
    for a, b in sorted(intervals):
        if t < a:
            break
        if b is None:
            return a
        t += b - a
    return t


def seek_target(base_ms: int, delta_ms: int, intervals: Iterable[tuple[int, int | None]], end_ms: int) -> int:
    """O ponto do -10 s ou +10 s em tempo de áudio (R7-04): as pausas da gravação não contam nos dois sentidos. Para trás, um
    alvo dentro de [a, b) vira a - (b - alvo), e assim por diante a cada pausa atravessada; para frente, b + (alvo - a);
    piso 0 e teto no fim do áudio. Antes, o alvo dentro da pausa ia para o fim dela e o -10 s ficava preso ali."""
    spans = list(intervals)
    u = max(0, audio_ms(base_ms, spans) + int(delta_ms))
    return max(0, min(meeting_ms(u, spans), max(0, int(end_ms) - 1)))


def mix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """L e R com ganho ½ cada, no comprimento do maior (o menor completa com silêncio)."""
    n = max(len(left), len(right))
    out = np.zeros(n, np.float32)
    out[:len(left)] += np.asarray(left, np.float32) * GAIN
    out[:len(right)] += np.asarray(right, np.float32) * GAIN
    return out


def position_ms(anchors: Iterable[tuple[int, int]], frames: int, rate: int) -> int:
    """Tempo da reunião no quadro de saída `frames`: a última âncora (quadro, ms) antes dele mais o que andou desde ela."""
    best: tuple[int, int] | None = None
    for f0, t0 in anchors:
        if f0 <= frames and (best is None or f0 >= best[0]):
            best = (f0, t0)
    if best is None or rate <= 0:
        return 0
    return int(best[1] + (frames - best[0]) * 1000 / rate)


def audio_path_missing(store: Store, meeting: Mapping[str, Any]) -> str | None:
    """O arquivo de áudio do mês da reunião quando ele falta na pasta do banco (o `attach` do app criaria um vazio)."""
    name = str(meeting.get("audio_file") or LEGACY_AUDIO_FILE)
    return None if (store.base_dir / name).exists() else name


# ---------------------------------------------------------------- motor (sem Qt)
_EMPTY = np.zeros(0, np.float32)
_EMPTY.setflags(write=False)


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
    depois de tratar a pausa: o bloco em voo saiu e o buffer do dispositivo foi descartado (R8-01)."""
    message: str = ""
    done: threading.Event | None = None


class _Chunks:
    """Os pedaços FLAC de um som para a alimentadora (R7-02): cada um decodificado uma vez (`AudioRepo.get_chunk`) e posto na
    taxa gravada; as janelas saem deles pela posição no tempo, em amostras absolutas (sem deriva entre janelas), com
    silêncio no buraco entre pedaços (como o `decode_range`, F1.1 A17). Guarda os `CHUNKS_KEPT` mais recentes de cada canal;
    a lista dos pedaços é lida uma vez por som (uma gravação em curso ganha pedaços entre um som e outro)."""

    def __init__(self, store: Store, meeting_id: int, in_rate: int):
        self.store = store
        self.meeting_id = meeting_id
        self.in_rate = in_rate
        self._meta: dict[str, list[tuple[int, int, int]]] = {}       # canal -> [(idx, primeira amostra, amostras)]
        self._cache: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()

    def window(self, channel: str, s0: int, s1: int) -> np.ndarray:
        """As amostras [s0, s1) do canal, na taxa gravada."""
        out = np.zeros(max(0, s1 - s0), np.float32)
        for idx, c0, n in self._list(channel):
            if c0 >= s1 or c0 + n <= s0:
                continue
            samples = self._samples(channel, idx)
            a_out, a_in = max(0, c0 - s0), max(0, s0 - c0)
            length = min(len(samples) - a_in, len(out) - a_out)
            if length > 0:
                out[a_out:a_out + length] = samples[a_in:a_in + length]
        return out

    def _list(self, channel: str) -> list[tuple[int, int, int]]:
        meta = self._meta.get(channel)
        if meta is None:
            meta = []
            for c in self.store.list_chunks(self.meeting_id, channel):
                sr = int(c.get("sample_rate") or self.in_rate)
                meta.append((int(c["idx"]), round(int(c["t_start_ms"]) * self.in_rate / 1000), round(int(c["frames"]) * self.in_rate / sr)))
            self._meta[channel] = meta
        return meta

    def _samples(self, channel: str, idx: int) -> np.ndarray:
        key = (channel, idx)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        row = self.store.audio.get_chunk(self.meeting_id, channel, idx)
        samples = _EMPTY                                         # o pedaço sumiu (reunião apagada): silêncio
        if row is not None:
            samples, sr = decode_flac(row["flac"])
            if sr != self.in_rate:
                samples = resample(samples, sr, self.in_rate)
        self._cache[key] = samples
        while len(self._cache) > CHUNKS_KEPT * max(1, len(self._meta)):
            self._cache.popitem(last=False)
        return samples


@dataclass(slots=True, eq=False)
class _Cursor:
    """Onde a alimentadora está no trecho atual (só a thread dela mexe)."""
    feed: _Feed
    chunks: _Chunks
    next_t: int
    resampler: StreamResampler | None


class PlaybackEngine:
    """O lado sem Qt do player: comandos da interface numa fila, a escritora, a alimentadora e o estado lido sob um lock."""

    def __init__(self, store: Store, factory: OutputFactory, *, decode_s: float = DECODE_S, queue_s: float = QUEUE_S):
        self.store = store
        self.factory = factory
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
        self.underflows = 0                     # instrumentação: buracos que o dispositivo contou no último som (R7-02)
        self.starved = 0                        # instrumentação: esperas pela alimentadora no último som

    # ---------------------------------------------------------------- interface (thread do Qt)
    def play(self, req: PlayRequest) -> None:
        with self._cv:
            self._state, self._req, self._message = PLAYING, req, ""
            self._paused_at = int(req.t_ms)
            self._written, self._anchors, self._rate = 0, [(0, int(req.t_ms))], 0
        self._send("play", req)

    def pause(self, message: str = "") -> None:
        """Pausa o que toca; `message` é o aviso da barra até retomar (R7-01). Já pausado, só troca o aviso. O aviso vai
        também no comando: a thread, tratando antes um "retomar" mais antigo, reescreve o estado e o aviso, e a pausa os
        devolve."""
        with self._cv:
            if self._state == PLAYING:
                self._paused_at, self._state = self._position_locked(), PAUSED
            if self._state == PAUSED:
                self._message = message
        self._send("pause", _Pause(message))

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
            return PlayerState(self._state, req.meeting_id if req else None, t, self._duration, req.title if req else "", self._message)

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
                        self._cv.wait()               # pausado: espera um comando
                    cmd = self._cmds.popleft() if self._cmds else None
                if cmd is not None:
                    sess = self._handle(sess, cmd)
                    if cmd[0] == "quit":
                        return
                elif sess is not None:
                    sess = self._step(sess)
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
        match cmd:
            case ("play", PlayRequest() as req):
                return self._open(sess, req)
            case ("pause", _Pause() as pause):
                try:
                    if sess is not None and sess.playing:
                        sess.pos = self.snapshot().t_ms
                        self._abort(sess)
                        sess.playing = False
                        with self._cv:
                            if self._feed is not None and self._feed.sess is sess:
                                self._feed = None         # o que estava pronto sai (R7-02)
                            self._paused_at, self._state, self._message = sess.pos, PAUSED, pause.message
                finally:
                    if pause.done is not None:
                        pause.done.set()                  # quem espera (o gancho do loopback, R8-01) segue
                return sess
            case ("resume", _):
                if sess is not None and not sess.playing:
                    self._start_run(sess, sess.pos)
                return sess
            case ("seek", int() as delta):
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
            case _:                                   # "stop" e "quit"
                self._close(sess)
                return None

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
        if sess is not None:                          # o som anterior acaba aqui; o fluxo fica para este
            self._abort(sess)
            self._report(sess)
            stream = sess.stream
        else:
            try:
                stream = self.factory()
                self.opened += 1
            except Exception as e:  # sem PortAudio, sem dispositivo, taxa recusada
                log.warning("player: %s: %s", NO_DEVICE, e)
                self._error(f"{NO_DEVICE} ({e})")
                return None
        rate = round(float(stream.samplerate))
        try:
            latency = round(float(stream.latency) * rate)
        except (TypeError, ValueError):
            latency = 0
        new = _Session(req, stream, rate, latency, *info)
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
            self._written, self._anchors, self._state, self._message = 0, [(0, start_t)], PLAYING, ""
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
        if not sess.started:
            try:
                sess.stream.start()
            except Exception as e:  # o dispositivo recusou começar (sumiu entre abrir e tocar): o fluxo fecha e a barra avisa
                log.warning("player: %s: %s", NO_DEVICE, e)
                self._close(sess)
                self._error(f"{NO_DEVICE} ({e})")
                return None
            sess.started = True
        try:
            underflowed = sess.stream.write(block)
        except Exception as e:  # o dispositivo sumiu no meio (fone desconectado): aviso na barra, o som para
            log.warning("player: a saída de áudio falhou: %s", e)
            self._close(sess)
            self._error(f"a saída de áudio parou ({e})")
            return None
        if underflowed:
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
            try:
                sess.stream.stop()                    # o que já está no dispositivo termina de tocar
            except Exception as e:  # o fluxo pode ter caído: parar assim mesmo
                log.debug("player: parar o fluxo: %s", e)
        with self._cv:
            self._paused_at, self._state, self._message = sess.end_t, STOPPED, ""
        self._close(sess, abort=False)

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
        chunks = cur.chunks if cur is not None and cur.feed.sess is sess else _Chunks(self.store, sess.req.meeting_id, sess.in_rate)
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

    def _decode(self, chunks: _Chunks, sess: _Session, t0: int, t1: int) -> np.ndarray:
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

    def _close(self, sess: _Session | None, abort: bool = True) -> None:
        if sess is None or sess.closed:
            return
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

    def __init__(self, store: Store, output_factory: OutputFactory | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.engine = PlaybackEngine(store, output_factory or sounddevice_output)
        self.guard: Callable[[PlayRequest], bool] | None = None
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
        self.requests.append(req)
        self.engine.play(req)
        self._kick()
        return True

    def toggle(self) -> bool:
        """Pausa ou retoma o que estiver carregado; False quando não há nada (ou o `guard` recusou retomar)."""
        st = self.state()
        if st.state == PLAYING:
            self.engine.pause()
        elif st.state == PAUSED and st.meeting_id is not None:
            req = PlayRequest(st.meeting_id, st.t_ms, title=st.title)
            if self.guard is not None and not self.guard(req):
                return False
            self.engine.resume()
        else:
            return False
        self._kick()
        return True

    def pause(self, notice: str = "") -> bool:
        """Pausa o som que toca com o aviso `notice` na barra até retomar (R7-01: o warm-up abriu ou a gravação vai captar o
        sistema inteiro); já pausado, só troca o aviso. Retomar passa pelo `guard` (Q9). True quando havia som tocando."""
        st = self.state().state
        if st not in (PLAYING, PAUSED):
            return False
        self.engine.pause(notice)
        self._kick()
        return st == PLAYING

    def pause_and_wait(self, notice: str, timeout_s: float) -> tuple[bool, bool]:
        """`pause` que espera o motor confirmar que o som parou de sair (R8-01, antes do loopback do sistema): (tocava,
        confirmado no prazo). Seguro em qualquer thread: só o motor é tocado aqui, e a barra é refeita na thread da interface
        pelo sinal `_kicked`."""
        result = self.engine.pause_and_wait(notice, timeout_s)
        self._kicked.emit()
        return result

    def clear_notice(self) -> None:
        """Tira o aviso da pausa da barra; o som segue pausado (a gravação que pediu a pausa não começou ou acabou, R8-01)."""
        self.engine.clear_notice()
        self._kick()

    def seek(self, delta_ms: int) -> None:
        if self.state().active:
            self.engine.seek(delta_ms)
            self._kick()

    def stop(self, meeting_id: int | None = None) -> None:
        """Para o som (só se for desta reunião, quando `meeting_id` é dado: fechar a reunião para o som dela)."""
        st = self.state()
        if meeting_id is not None and st.meeting_id != meeting_id:
            return
        if st.active or self.engine.alive:
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
        if last is None or (st.state, st.meeting_id, st.title, st.message, st.duration_ms) != (
                last.state, last.meeting_id, last.title, last.message, last.duration_ms):
            self.state_changed.emit(st)
        if last is None or st.t_ms != last.t_ms or st.meeting_id != last.meeting_id:
            self.position_changed.emit(st)
        if st.state not in (PLAYING, PAUSED) and not self.engine.alive:
            self._timer.stop()


class PlayerBar(QWidget):
    """Barra do player na reunião aberta: Tocar/Pausar, -10 s, +10 s, "mm:ss / mm:ss" e o título do que toca; o aviso do
    player (sem dispositivo, sem áudio, pausado para a gravação) numa linha discreta. "Tocar" com outra reunião (ou nada)
    carregada começa esta reunião em `start()` (a fala selecionada). Título e aviso em texto puro: um título com "<b>" não
    vira HTML (R7-07)."""

    def __init__(self, player: Player, meeting_id: int | None = None, *, title: Callable[[], str] | None = None,
                 start: Callable[[], int] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.player = player
        self.meeting_id = meeting_id
        self.title_fn = title or (lambda: "")
        self.start_fn = start or (lambda: 0)
        self.setObjectName("playerBar")
        self.setStyleSheet("QWidget#playerBar { border-top: 1px solid palette(mid); } "
                           "QLabel#playerTime { font-family: monospace; }")
        self.btn_play = QPushButton("Tocar")
        self.btn_play.setToolTip("Toca a reunião a partir da fala selecionada (ou pausa e retoma)")
        self.btn_back = QPushButton("-10 s")
        self.btn_back.setToolTip("Volta 10 segundos")
        self.btn_fwd = QPushButton("+10 s")
        self.btn_fwd.setToolTip("Avança 10 segundos")
        self.time = QLabel("00:00 / 00:00")
        self.time.setObjectName("playerTime")
        self.what = QLabel("")
        self.what.setTextFormat(Qt.TextFormat.PlainText)
        self.what.setStyleSheet("color: palette(placeholder-text);")
        self.what.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setStyleSheet("color: #e67e22;")
        self.note.setWordWrap(True)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_back.clicked.connect(lambda: self.seek(-SEEK_MS))
        self.btn_fwd.clicked.connect(lambda: self.seek(SEEK_MS))
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        for w in (self.btn_play, self.btn_back, self.btn_fwd, self.time):
            row.addWidget(w)
        row.addWidget(self.what, 1)
        row.addWidget(self.note, 1)
        player.state_changed.connect(self._on_state)
        player.position_changed.connect(self._on_position)
        self.show_state(player.state())

    def mine(self, st: PlayerState) -> bool:
        return self.meeting_id is None or st.meeting_id == self.meeting_id

    def toggle(self) -> None:
        st = self.player.state()
        if st.active and self.mine(st):
            self.player.toggle()
        elif self.meeting_id is not None:
            self.player.play_from(self.meeting_id, self.start_fn(), title=self.title_fn())
        elif st.state == PAUSED:
            self.player.toggle()

    def seek(self, delta_ms: int) -> None:
        if self.mine(self.player.state()):
            self.player.seek(delta_ms)

    def _on_state(self, st: object) -> None:
        if isinstance(st, PlayerState):
            self.show_state(st)

    def _on_position(self, st: object) -> None:
        if isinstance(st, PlayerState) and self.mine(st) and st.state != ERROR and st.meeting_id is not None:
            self.time.setText(f"{fmt_mmss(st.t_ms / 1000)} / {fmt_mmss((st.duration_ms or 0) / 1000)}")

    def show_state(self, st: PlayerState) -> None:
        mine = self.mine(st)
        playing = st.state == PLAYING and mine
        self.btn_play.setText("Pausar" if playing else "Tocar")
        self.btn_back.setEnabled(st.active and mine)
        self.btn_fwd.setEnabled(st.active and mine)
        if mine and st.meeting_id is not None and st.state != ERROR:
            self.time.setText(f"{fmt_mmss(st.t_ms / 1000)} / {fmt_mmss((st.duration_ms or 0) / 1000)}")
        else:
            self.time.setText("--:-- / --:--")
        if st.active:
            prefix = "" if mine else "outra reunião: "
            self.what.setText(f"{prefix}{STATE_TEXT[st.state]} · {st.title}" if st.title else f"{prefix}{STATE_TEXT[st.state]}")
        else:
            self.what.setText("")
        # o motivo do erro e o aviso da pausa pela gravação (R7-01) na barra da reunião que tocava
        self.note.setText(st.message if st.state in (ERROR, PAUSED) and (mine or st.meeting_id is None) else "")


__all__ = ["ERROR", "PAUSED", "PLAYING", "STOPPED", "OutputStream", "PlayRequest", "PlaybackEngine", "Player", "PlayerBar",
           "PlayerState", "audio_ms", "meeting_ms", "mix", "pause_intervals", "position_ms", "seek_target", "skip_paused",
           "sounddevice_output"]
