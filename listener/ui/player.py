"""Player único do app (F5 item 10, F5A-25): toca uma reunião a partir de um ponto com L e R misturados (ganho ½ cada) até
parar, ou um canal só num intervalo (os trechos de 5 s da revisão); um som por vez: tocar outro ponto troca o som.

Uma thread alimentadora decodifica 60 s por vez dos canais (`store.playback.decode_range`, que posiciona cada pedaço pelo
tempo e preenche buracos com silêncio; a primeira janela tem 5 s para o som começar logo), pula os intervalos de pausa da
gravação (eventos `pause`/`resume`, D-011), reamostra com estado (`StreamResampler`) para a taxa padrão do dispositivo de
saída e escreve blocos de ~0,25 s com `OutputStream.write` bloqueante, o que dá o ritmo. Posição = quadros escritos menos
a latência de saída, convertida no tempo da reunião pelas âncoras (início de cada trecho contínuo, depois de cada pausa
pulada); a interface lê num `QTimer` de 100 ms. Pausar, trocar o ponto, voltar ou avançar e parar descartam o que está no
buffer do dispositivo (`abort`) e respondem em até um bloco. A thread nasce no primeiro "tocar", usa a própria conexão do
`Store`, fecha o fluxo e a conexão (`close_thread`) quando o som para e termina; a saída do app a para e espera
(`shutdown`).

Sem dispositivo de saída, sem o PortAudio do `sounddevice`, sem o arquivo de áudio do mês ou sem áudio gravado: estado
`erro` com o motivo, que a barra mostra; nada quebra. A fábrica do fluxo de saída é injetável (os testes usam uma falsa
que registra os quadros). Q9: o controlador põe um `guard` chamado antes de o som começar (a gravação em curso captura o
sistema inteiro: o som do player entraria no canal R)."""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ..capture.audio import StreamResampler, resample
from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..model import EventType
from ..store.core import LEGACY_AUDIO_FILE

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.player")

BLOCK_S = 0.25                   # cada `write` bloqueante: o ritmo e o tempo de resposta a um comando
DECODE_S = 60.0                  # janela decodificada de cada vez (L e R)
FIRST_DECODE_S = 5.0             # a primeira janela é curta: o som começa sem esperar 60 s de FLAC e reamostragem
POLL_MS = 100                    # a interface lê a posição a cada 100 ms
JOIN_S = 3.0                     # sair do app: espera a thread do player até isto
SEEK_MS = 10_000                 # -10 s e +10 s da barra
GAIN = np.float32(0.5)           # L e R misturados com ganho ½ cada

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
@dataclass(slots=True)
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
    next_t: int = 0
    pos: int = 0                 # posição guardada na pausa
    playing: bool = False
    first: bool = True
    exhausted: bool = False
    resampler: StreamResampler | None = None
    buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))

    @property
    def block(self) -> int:
        return max(1, int(self.rate * BLOCK_S))


class PlaybackEngine:
    """O lado sem Qt do player: comandos da interface numa fila, a thread alimentadora e o estado lido sob um lock."""

    def __init__(self, store: Store, factory: OutputFactory, *, decode_s: float = DECODE_S, first_decode_s: float = FIRST_DECODE_S):
        self.store = store
        self.factory = factory
        self.decode_ms = int(decode_s * 1000)
        self.first_decode_ms = int(first_decode_s * 1000)
        self._cv = threading.Condition()
        self._cmds: deque[tuple[str, Any]] = deque()
        self._thread: threading.Thread | None = None
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

    # ---------------------------------------------------------------- interface (thread do Qt)
    def play(self, req: PlayRequest) -> None:
        with self._cv:
            self._state, self._req, self._message = PLAYING, req, ""
            self._paused_at = int(req.t_ms)
            self._written, self._anchors, self._rate = 0, [(0, int(req.t_ms))], 0
        self._send("play", req)

    def pause(self) -> None:
        with self._cv:
            if self._state == PLAYING:
                self._paused_at, self._state = self._position_locked(), PAUSED
        self._send("pause")

    def resume(self) -> None:
        with self._cv:
            if self._state == PAUSED:
                self._state = PLAYING
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
        """Para o som e espera a thread (a saída do app, antes de fechar o banco). False quando ela não terminou a tempo."""
        self._send("quit", start=False)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)
            return not thread.is_alive()
        return True

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
            if not alive and not start:
                return                              # nada tocando: parar não precisa de thread
            self._cmds.append((cmd, arg))
            if not alive:
                self._thread = threading.Thread(target=self._run, name="player", daemon=True)
                self._thread.start()
            self._cv.notify_all()

    def _position_locked(self) -> int:
        if self._rate <= 0:
            return self._anchors[0][1] if self._anchors else 0
        t = position_ms(self._anchors, max(0, self._written - self._latency), self._rate)
        return min(t, self._end_t) if self._end_t else t

    # ---------------------------------------------------------------- thread alimentadora
    def _run(self) -> None:
        sess: _Session | None = None
        me = threading.current_thread()
        try:
            while True:
                with self._cv:
                    while not self._cmds and (sess is None or not sess.playing):
                        if sess is None:              # parado: a thread termina (nasce de novo no próximo "tocar")
                            if self._thread is me:
                                self._thread = None
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
                if self._thread is me:
                    self._thread = None
            self.store.close_thread()

    def _handle(self, sess: _Session | None, cmd: tuple[str, Any]) -> _Session | None:
        match cmd:
            case ("play", PlayRequest() as req):
                return self._open(sess, req)
            case ("pause", _):
                if sess is not None and sess.playing:
                    sess.pos = self.snapshot().t_ms
                    self._abort(sess)
                    sess.playing = False
                    with self._cv:
                        self._paused_at, self._state = sess.pos, PAUSED
                return sess
            case ("resume", _):
                if sess is not None and not sess.playing:
                    self._start_run(sess, sess.pos)
                return sess
            case ("seek", int() as delta):
                if sess is None:
                    return None
                base = self.snapshot().t_ms if sess.playing else sess.pos
                target = max(0, min(base + delta, max(0, sess.end_t - 1)))
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
        stream = sess.stream if sess is not None else None
        if stream is not None:
            self._abort(sess)
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
        try:
            self._start_run(new, start)
        except Exception as e:  # o dispositivo recusou começar (sumiu entre abrir e tocar): o fluxo fecha e a barra avisa
            log.warning("player: %s: %s", NO_DEVICE, e)
            self._close(new)
            self._error(f"{NO_DEVICE} ({e})")
            return None
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
        """Um trecho contínuo novo a partir de `t_ms` (o começo, a retomada ou o ponto do ±10 s): buffer e reamostragem
        zerados, âncora no quadro 0 e o fluxo ligado."""
        start = skip_paused(t_ms, sess.pauses)
        sess.next_t = sess.end_t if start is None else min(start, sess.end_t)
        sess.buffer = np.zeros(0, np.float32)
        sess.resampler = StreamResampler(sess.in_rate, sess.rate) if sess.in_rate != sess.rate else None
        sess.first, sess.exhausted, sess.playing = True, False, True
        with self._cv:
            self._written, self._anchors, self._state, self._message = 0, [(0, sess.next_t)], PLAYING, ""
            self._rate, self._latency, self._duration, self._end_t = sess.rate, sess.latency, sess.duration_ms, sess.end_t
        sess.stream.start()

    def _step(self, sess: _Session) -> _Session | None:
        """Completa o buffer até um bloco e escreve (bloqueante); no fim do áudio, espera o resto tocar e para."""
        while len(sess.buffer) < sess.block and not sess.exhausted:
            self._produce(sess)
        if not len(sess.buffer):
            try:
                sess.stream.stop()                    # o que já está no dispositivo termina de tocar
            except Exception as e:  # o fluxo pode ter caído: parar assim mesmo
                log.debug("player: parar o fluxo: %s", e)
            with self._cv:
                self._paused_at, self._state, self._message = sess.end_t, STOPPED, ""
            self._close(sess, abort=False)
            return None
        block, sess.buffer = sess.buffer[:sess.block], sess.buffer[sess.block:]
        try:
            sess.stream.write(block)
        except Exception as e:  # o dispositivo sumiu no meio (fone desconectado): aviso na barra, o som para
            log.warning("player: a saída de áudio falhou: %s", e)
            self._close(sess)
            self._error(f"a saída de áudio parou ({e})")
            return None
        with self._cv:
            self._written += len(block)
        return sess

    def _produce(self, sess: _Session) -> None:
        t = sess.next_t
        if t >= sess.end_t:
            self._flush(sess)
            sess.exhausted = True
            return
        after = skip_paused(t, sess.pauses)
        if after != t:                                # pausa da gravação: o resto do trecho sai e a âncora pula
            self._flush(sess)
            if after is None or after >= sess.end_t:
                sess.next_t, sess.exhausted = sess.end_t, True
                return
            sess.next_t = after
            sess.resampler = StreamResampler(sess.in_rate, sess.rate) if sess.resampler is not None else None
            with self._cv:
                self._anchors.append((self._written + len(sess.buffer), after))
            return
        span = self.first_decode_ms if sess.first else self.decode_ms
        stop = min(t + span, sess.end_t)
        pause = next_pause(t, sess.pauses)
        if pause is not None:
            stop = min(stop, pause)
        x = self._decode(sess, t, stop)
        y = sess.resampler.process(x) if sess.resampler is not None else x
        sess.buffer = np.concatenate([sess.buffer, y]) if len(sess.buffer) else y
        sess.next_t, sess.first = stop, False

    def _decode(self, sess: _Session, t0: int, t1: int) -> np.ndarray:
        from ..store.playback import decode_range

        n = max(0, round((t1 - t0) * sess.in_rate / 1000))
        parts: list[np.ndarray] = []
        for ch in sess.channels:
            samples, sr = decode_range(self.store, sess.req.meeting_id, ch, t0, t1)
            if not len(samples):
                samples, sr = np.zeros(n, np.float32), sess.in_rate       # sem pedaço aqui: silêncio no lugar
            if sr != sess.in_rate:
                samples = resample(samples, sr, sess.in_rate)
            parts.append(samples[:n] if len(samples) > n else np.pad(samples, (0, n - len(samples))))
        return mix(parts[0], parts[1]) if len(parts) == 2 else np.asarray(parts[0], np.float32)

    def _flush(self, sess: _Session) -> None:
        if sess.resampler is not None:
            tail = sess.resampler.flush()
            if len(tail):
                sess.buffer = np.concatenate([sess.buffer, tail]) if len(sess.buffer) else tail

    def _abort(self, sess: _Session | None) -> None:
        if sess is None:
            return
        try:
            sess.stream.abort()
        except Exception as e:  # fluxo já parado ou caído
            log.debug("player: abortar o fluxo: %s", e)

    def _close(self, sess: _Session | None, abort: bool = True) -> None:
        if sess is None:
            return
        if abort:
            self._abort(sess)
        try:
            sess.stream.close()
        except Exception as e:
            log.debug("player: fechar o fluxo: %s", e)
        sess.playing = False
        with self._cv:
            if self._state in (PLAYING, PAUSED):
                self._paused_at, self._state = self._position_locked(), STOPPED

    def _error(self, message: str) -> None:
        with self._cv:
            self._state, self._message = ERROR, message


# ---------------------------------------------------------------- Qt
class Player(QObject):
    """O player do app (um só, criado pelo controlador e passado às janelas). `state_changed` quando muda o estado, a reunião
    ou o aviso; `position_changed` a cada 100 ms enquanto toca. `guard(pedido)` é chamado antes de o som começar (Q9)."""
    state_changed = Signal(object)              # PlayerState
    position_changed = Signal(object)           # PlayerState

    def __init__(self, store: Store, output_factory: OutputFactory | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.engine = PlaybackEngine(store, output_factory or sounddevice_output)
        self.guard: Callable[[PlayRequest], bool] | None = None
        self.requests: list[PlayRequest] = []   # instrumentação dos testes
        self._last: PlayerState | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)

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
    player (sem dispositivo, sem áudio) numa linha discreta. "Tocar" com outra reunião (ou nada) carregada começa esta reunião
    em `start()` (a fala selecionada)."""

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
        self.what.setStyleSheet("color: palette(placeholder-text);")
        self.what.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.note = QLabel("")
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
        self.note.setText(st.message if st.state == ERROR and (mine or st.meeting_id is None) else "")


__all__ = ["ERROR", "PAUSED", "PLAYING", "STOPPED", "OutputStream", "PlayRequest", "PlaybackEngine", "Player", "PlayerBar",
           "PlayerState", "mix", "pause_intervals", "position_ms", "skip_paused", "sounddevice_output"]
