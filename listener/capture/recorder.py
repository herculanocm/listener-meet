"""Gravador de uma reunião: canal L (mic) e canal R (app ou sistema), pausa, mudo, marcadores, saúde dos canais.

Threads (F1.2 M-04, M-06, M-16): os callbacks de áudio só enfileiram um bloco imutável; uma única thread de trabalho
(não daemon) faz mono, reamostragem com estado, pedaços, FLAC e a gravação no destino, com nova tentativa e backoff
quando o banco não responde. Comandos (`abrir`, `pausar`, `parar`) entram na mesma fila e são processados em ordem.
A interface lê um snapshot imutável (`CaptureSnapshot`) substituído por referência. Nenhum pedaço é descartado: na
falha definitiva o FLAC fica retido e o resumo lista os `idx` não gravados (CP3). Eventos e marcadores são gravados por
quem chama (thread da interface), com a conexão dessa thread.
F4: `set_live_tap(fn)` liga o desvio ao vivo; cada bloco escrito pelo chunker (depois do preenchimento de buracos, na
thread de trabalho) vai para `fn` em O(1), com pausa e mudo marcados (`TapFlag.SILENCED`) em vez de zeros como fala; uma
exceção do desvio nunca sobe para a gravação (contada e logada uma vez).
F5 R8-01: `before_system_loopback` é chamado logo antes de abrir o loopback do sistema inteiro, por qualquer caminho (a
fonte pedida, a troca automática quando o loopback do app não abre, no começo, na troca de fonte e na reabertura): quem
cria o gravador cala ali o som que o próprio app toca (o player entraria no canal R). Uma falha do gancho nunca impede a
gravação.
"""
from __future__ import annotations

import queue
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import numpy as np

from ..config.defaults import SPECS, SettingSpec
from ..log import get_logger
from ..model import EventType, Health, MeetingStatus, SourceKind
from ..platform.base import AudioDevice, CaptureHandle, ErrorCallback, Platform
from . import audio
from .chunker import ChannelChunker, RawChunk, Tap
from .clock import Clock, SessionClock
from .sinks import ChunkSink, EncodedChunk, RecorderSink

log = get_logger("capture")

CH_INDEX = {"L": 0, "R": 1}
GAP_TOLERANCE_S = 0.25
REOPEN_EVERY_S = 5.0
DB_ALERT_AFTER_S = 30.0
QUEUE_WARN_BLOCKS = 50          # ~5 s de blocos de 100 ms à espera da thread de trabalho: "banco lento"


class SettingsReader(Protocol):
    def get[T](self, spec: SettingSpec[T]) -> T: ...


@dataclass(frozen=True, slots=True)
class SourceSpec:
    kind: str = SourceKind.SYSTEM        # SourceKind: "app" | "system" | "none"
    pid: int | None = None
    exe: str = ""
    label: str = ""                      # o que a tela mostra: o app e o título da janela

    def log_text(self) -> str:
        """A fonte para o log e os eventos: o programa e o pid, nunca o título da janela, que traz o nome de quem está do
        outro lado ("Chat | Fulano de Tal | Microsoft Teams"; LGPD, dado pessoal de terceiro)."""
        if self.kind == SourceKind.APP:
            return f"{self.exe or 'app'} (pid {self.pid})"
        return "sistema inteiro" if self.kind == SourceKind.SYSTEM else "sem saída"


@dataclass(slots=True, kw_only=True)
class RecordConfig:
    mic_device: AudioDevice | None = None
    source: SourceSpec = field(default_factory=SourceSpec)
    mic_rate: int = 16000
    target_rate: int = 16000
    chunk_seconds: float = 60.0
    silence_rms: float = 0.004
    l_alert_seconds: float = 15.0
    r_alert_seconds: float = 20.0
    stop_timeout_s: float = 120.0       # tempo máximo para gravar os pedaços pendentes ao parar (M-04)
    retry_base_s: float = 1.0           # backoff 1, 2, 4... 30 s (testes e medições usam um valor menor)

    @classmethod
    def from_settings(cls, settings: SettingsReader, mic_device: AudioDevice | None, source: SourceSpec) -> RecordConfig:
        """Lê as chaves `capture.*` já validadas (M-24); texto inválido no banco cai no padrão com aviso."""
        return cls(mic_device=mic_device, source=source,
                   mic_rate=settings.get(SPECS["capture.mic_rate"]), target_rate=settings.get(SPECS["capture.target_rate"]),
                   chunk_seconds=settings.get(SPECS["capture.chunk_seconds"]), silence_rms=settings.get(SPECS["capture.silence_rms"]),
                   l_alert_seconds=settings.get(SPECS["capture.l_alert_seconds"]),
                   r_alert_seconds=settings.get(SPECS["capture.r_alert_seconds"]))

    def apply_live(self, settings: SettingsReader) -> None:
        """Escopo `now`: vale na gravação em curso."""
        self.silence_rms = settings.get(SPECS["capture.silence_rms"])
        self.l_alert_seconds = settings.get(SPECS["capture.l_alert_seconds"])
        self.r_alert_seconds = settings.get(SPECS["capture.r_alert_seconds"])

    def validate(self) -> list[str]:
        """Erros que fariam a gravação morrer pedaço a pedaço (M-08); vazio = ok."""
        errors = []
        if self.target_rate <= 0:
            errors.append(f"taxa gravada inválida: {self.target_rate}")
        if self.mic_rate <= 0:
            errors.append(f"taxa do microfone inválida: {self.mic_rate}")
        if not 0.1 <= self.chunk_seconds <= 3600:
            errors.append(f"duração do pedaço fora de 0,1 a 3600 s: {self.chunk_seconds}")
        if not 0.0 <= self.silence_rms <= 1.0:
            errors.append(f"limiar de silêncio fora de 0 a 1: {self.silence_rms}")
        if self.l_alert_seconds <= 0 or self.r_alert_seconds <= 0:
            errors.append("tempo de alerta precisa ser positivo")
        return errors


@dataclass(slots=True)
class ChannelState:
    """Estado de um canal visto pela thread da interface (quem abre e fecha as streams)."""
    name: str
    active: bool = False
    detail: str = ""
    lost_since: float | None = None    # instante (sessão) em que o dispositivo sumiu (M-09)
    last_reopen: float = -1e9


@dataclass(frozen=True, slots=True)
class CaptureSnapshot:
    """O que a interface lê: imutável, trocado por referência pela thread de trabalho."""
    levels: tuple[float, float] = (0.0, 0.0)            # RMS do último bloco (L, R)
    last_signal: tuple[float, float] = (0.0, 0.0)       # último instante com sinal acima do limiar
    blocks: tuple[int, int] = (0, 0)
    chunks: tuple[int, int] = (0, 0)                    # pedaços gravados no destino
    bytes_written: int = 0
    drift: dict[str, dict[str, Any]] = field(default_factory=dict)   # por canal; dicionário novo a cada publicação
    pending: int = 0                                    # pedaços codificados à espera do banco
    pending_since: float | None = None                  # instante (sessão) do primeiro pedaço pendente
    db_error: str = ""
    unsaved: tuple[str, ...] = ()                       # "L3", "R7": retidos após falha definitiva
    lost: frozenset[str] = frozenset()                  # canais cujo dispositivo sumiu


# ---------------------------------------------------------------- mensagens da fila (imutáveis)
@dataclass(frozen=True, slots=True)
class _Block:
    channel: str
    source: int
    data: np.ndarray
    t_perf: float


@dataclass(frozen=True, slots=True)
class _Open:
    channel: str
    source: int
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class _Flags:
    paused: bool
    muted: bool


@dataclass(frozen=True, slots=True)
class _Lost:
    channel: str
    source: int
    reason: str


@dataclass(frozen=True, slots=True)
class _Stop:
    end_rel: float
    cancel: bool


type _Msg = _Block | _Open | _Flags | _Lost | _Stop


class _Pipeline:
    """Um canal na thread de trabalho: mono -> reamostragem com estado -> pedaços na taxa gravada."""

    def __init__(self, channel: str, sr_in: int, ch_in: int, target_rate: int, chunk_seconds: float,
                 on_chunk: Callable[[RawChunk], None], tap: Tap | None = None):
        self.channel = channel
        self.target = int(target_rate)
        self.chunker = ChannelChunker(channel, self.target, 1, on_chunk, chunk_seconds=chunk_seconds, gap_tolerance=GAP_TOLERANCE_S,
                                      tap=tap)
        self.sr_in = int(sr_in)
        self.ch_in = int(ch_in)
        self.resampler: audio.StreamResampler | None = None
        self.min_in = 1
        self._acc: list[np.ndarray] = []
        self._acc_n = 0
        self._acc_t = 0.0
        self._acc_silenced = False          # F4: um lote nunca mistura pausa ou mudo com fala (a marca vale para o lote todo)
        self.expected_t: float | None = None
        self._set_format(sr_in, ch_in)

    def _set_format(self, sr_in: int, ch_in: int) -> None:
        self.sr_in, self.ch_in = int(sr_in), int(ch_in)
        self.resampler = audio.StreamResampler(self.sr_in, self.target) if self.sr_in != self.target else None
        self.min_in = max(1, self.sr_in // 10)          # reamostra em lotes de ~100 ms (10 ms do proc-tap custa 7x)
        self.expected_t = None

    def set_input(self, sr_in: int, ch_in: int) -> None:
        """Fonte nova (troca de app): fecha a cauda da antiga; o chunker continua a mesma linha do tempo."""
        if (sr_in, ch_in) != (self.sr_in, self.ch_in):
            self.flush()
            self._set_format(sr_in, ch_in)
        else:
            self.flush()

    def feed(self, block: np.ndarray, t_rel: float, silenced: bool = False) -> None:
        mono = audio.to_mono(block)
        n = len(mono)
        if n == 0:
            return
        if self.expected_t is not None and t_rel - self.expected_t > GAP_TOLERANCE_S:
            self.flush()                                  # buraco: termina a cauda antes do silêncio
        if self._acc and silenced != self._acc_silenced:
            self._drain()                                 # pausa ou mudo mudou: o lote anterior sai com a marca dele
        if not self._acc:
            self._acc_t = t_rel
            self._acc_silenced = silenced
        self._acc.append(mono)
        self._acc_n += n
        self.expected_t = t_rel + n / self.sr_in
        if self._acc_n >= self.min_in:
            self._drain()

    def _drain(self) -> None:
        if not self._acc:
            return
        x = np.concatenate(self._acc) if len(self._acc) > 1 else self._acc[0]
        t = self._acc_t
        self._acc, self._acc_n = [], 0
        if self.resampler is None:
            self.chunker.write(x, t, silenced=self._acc_silenced)
            return
        lag = self.resampler.pending_in / self.sr_in       # entrada retida pelo filtro: a saída começa antes de `t`
        y = self.resampler.process(x)
        if len(y):
            self.chunker.write(y, t - lag, silenced=self._acc_silenced)

    def flush(self) -> None:
        self._drain()
        if self.resampler is not None:
            y = self.resampler.flush()
            if len(y):
                self.chunker.write(y, None, silenced=self._acc_silenced)   # contíguo ao que já saiu
        self.expected_t = None

    def close(self, end_rel: float) -> None:
        self.flush()
        self.chunker.close(end_rel)


class Recorder:
    def __init__(self, platform: Platform, sink: RecorderSink, meeting_id: int, cfg: RecordConfig,
                 clock: Clock | None = None, *, inline_worker: bool = False, extra_sinks: Sequence[ChunkSink] = (),
                 before_system_loopback: Callable[[], object] | None = None):
        self.platform = platform
        self.sink = sink
        self.extra_sinks = tuple(extra_sinks)
        self.before_system_loopback = before_system_loopback     # R8-01: chamado antes de abrir o loopback do sistema
        self.meeting_id = meeting_id
        self.cfg = cfg
        self.clock: Clock = clock or SessionClock.start()
        self.L = ChannelState("L")
        self.R = ChannelState("R")
        self.paused = False
        self.muted = False
        self.markers = 0
        self.warnings: list[str] = []
        self.source = cfg.source
        self._l_handle: CaptureHandle | None = None
        self._r_handle: CaptureHandle | None = None
        self._inline = inline_worker
        self._queue: queue.SimpleQueue[_Msg] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False
        self._next_source = 0
        # estado da thread de trabalho
        self._snap = CaptureSnapshot()
        self._pipes: dict[str, _Pipeline] = {}
        self._sources: dict[str, int] = {}
        self._w_paused = False
        self._w_muted = False
        self._w_cancel = False
        self._pending: deque[EncodedChunk] = deque()
        self._failed: list[EncodedChunk] = []
        self._failures = 0
        self._retry_at: float | None = None
        self._zeros = np.zeros(48000, np.float32)
        # F4: desvio ao vivo (trocado por referência pela interface, lido pela thread de trabalho)
        self._live_tap: Tap | None = None
        self.tap_errors = 0

    # ---------------------------------------------------------------- ciclo (thread da interface)
    def start(self) -> None:
        errors = self.cfg.validate()
        if errors:
            raise ValueError("configuração de captura inválida: " + "; ".join(errors))
        if not self._inline:
            self._worker = threading.Thread(target=self._run, name="capture-worker", daemon=False)
            self._worker.start()
        self.sink.add_event(self.meeting_id, self._ms(), EventType.START, {"source": str(self.source.kind), "exe": self.source.exe})
        self._open_mic()
        self._open_source(self.source)

    def _new_source_id(self) -> int:
        self._next_source += 1
        return self._next_source

    def _audio_callback(self, channel: str, source: int) -> Callable[[np.ndarray, float], None]:
        def cb(block: np.ndarray, t_first_perf: float) -> None:
            self._post(_Block(channel, source, block, t_first_perf))
        return cb

    def _error_callback(self, channel: str, source: int) -> ErrorCallback:
        def cb(reason: str) -> None:
            self._post(_Lost(channel, source, reason))
        return cb

    def _open_mic(self) -> bool:
        sid = self._new_source_id()
        try:
            handle = self.platform.open_mic(self.cfg.mic_device, self.cfg.mic_rate, self._audio_callback("L", sid),
                                            self._error_callback("L", sid))
        except Exception as e:
            self._warn(f"microfone não iniciou: {e}")
            return False
        self._l_handle = handle
        self._post(_Open("L", sid, handle.sample_rate, handle.channels))
        handle.start()                                    # só depois de a thread de trabalho conhecer o formato (M-06)
        self.L.active = True
        self.L.detail = self.cfg.mic_device.name if self.cfg.mic_device else "microfone padrão"
        return True

    def _open_source(self, spec: SourceSpec) -> bool:
        self.R.active = False
        sid = self._new_source_id()
        cb, err = self._audio_callback("R", sid), self._error_callback("R", sid)
        handle: CaptureHandle | None
        try:
            if spec.kind == SourceKind.APP and spec.pid:
                handle = self.platform.open_app_loopback(spec.pid, cb, err)
                self.R.detail = spec.log_text()           # vai para o log e os eventos: sem o título da janela
            elif spec.kind == SourceKind.SYSTEM:
                self._before_system()
                handle = self.platform.open_system_loopback(cb, err)
                self.R.detail = "sistema inteiro"
            else:
                self._r_handle = None
                self.R.detail = "sem saída"
                return False
        except Exception as e:
            self._warn(f"saída ({spec.kind}) não iniciou: {e}")
            if spec.kind != SourceKind.APP:
                self._r_handle = None
                return False
            try:
                self._before_system()                     # a troca automática também capta o sistema inteiro (R8-01)
                handle = self.platform.open_system_loopback(cb, err)
                self.R.detail = "sistema inteiro (fallback)"
                spec = SourceSpec(SourceKind.SYSTEM)
            except Exception as e2:
                self._warn(f"loopback do sistema não iniciou: {e2}")
                self._r_handle = None
                return False
        self.source = spec
        self._r_handle = handle
        self._post(_Open("R", sid, handle.sample_rate, handle.channels))
        handle.start()
        self.R.active = True
        return True

    def _before_system(self) -> None:
        """R8-01: o loopback do sistema inteiro vai abrir; quem criou o gravador cala antes o que o app toca."""
        hook = self.before_system_loopback
        if hook is None:
            return
        try:
            hook()
        except Exception as e:  # o gancho nunca impede a gravação
            log.warning("reunião %s: o aviso antes do loopback do sistema falhou: %s", self.meeting_id, e)

    def switch_source(self, spec: SourceSpec) -> None:
        """Troca a fonte do canal R sem parar; o buraco entre uma e outra é preenchido com silêncio."""
        with self._lock:
            if self._stopped:
                return
            old, self._r_handle = self._r_handle, None
            if old is not None:
                old.stop()                                # um callback tardio da antiga é ignorado pelo id da fonte
            self._open_source(spec)
        self.sink.add_event(self.meeting_id, self._ms(), EventType.SOURCE_CHANGE,
                            {"kind": str(self.source.kind), "exe": self.source.exe, "pid": self.source.pid})

    def check_devices(self) -> None:
        """Chamada periódica da interface (M-09): registra `device_lost` e tenta reabrir a cada 5 s pelo id estável."""
        if self._stopped:
            return
        now = self.clock.rel()
        lost = self._snap.lost
        for st in (self.L, self.R):
            if st.name not in lost:
                continue
            if st.lost_since is None:
                st.lost_since = now
                self._warn(f"dispositivo do canal {st.name} parou de responder ({st.detail})", EventType.DEVICE_LOST)
            if now - st.last_reopen < REOPEN_EVERY_S:
                continue
            st.last_reopen = now
            with self._lock:
                ok = self._open_mic() if st.name == "L" else self._open_source(self.source)
            if ok:
                st.lost_since = None
                log.info("canal %s voltou (%s)", st.name, st.detail)
                self.sink.add_event(self.meeting_id, self._ms(), EventType.DEVICE_BACK, {"channel": st.name, "detail": st.detail})

    def pause(self) -> None:
        if not self.paused:
            self.paused = True
            self._post(_Flags(True, self.muted))
            self.sink.add_event(self.meeting_id, self._ms(), EventType.PAUSE, {})

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self._post(_Flags(False, self.muted))
            self.sink.add_event(self.meeting_id, self._ms(), EventType.RESUME, {})

    def set_muted(self, muted: bool, source: str = "app") -> None:
        if muted != self.muted:
            self.muted = muted
            self._post(_Flags(self.paused, muted))
            self.sink.add_event(self.meeting_id, self._ms(), EventType.MUTE if muted else EventType.UNMUTE, {"source": source})

    def mark(self, note: str = "", t_ms: int | None = None, origin: str | None = None) -> int:
        """Marcador agora ou num instante já gravado (F4 U-022 e U-023: `t_ms` da legenda ou do item do resumo parcial, com a
        `origin` no evento); o mesmo sink e o mesmo evento `marker` que a ata da F3 lê."""
        t_ms = self._ms() if t_ms is None else max(0, int(t_ms))
        marker_id = self.sink.add_marker(self.meeting_id, t_ms, note)
        data: dict[str, Any] = {"note": note, "marker_id": marker_id}
        if origin:
            data["origin"] = origin
        self.sink.add_event(self.meeting_id, t_ms, EventType.MARKER, data)
        self.markers += 1
        return t_ms

    def stop(self) -> dict[str, Any]:
        """Para as streams, enfileira o fechamento e espera a fila esvaziar (até `stop_timeout_s` se o banco falhar)."""
        with self._lock:
            if self._stopped:
                return self.stats()
            self._stopped = True
            end_rel = self.clock.rel()
            self._stop_handles()
            self._post(_Stop(end_rel, cancel=False))
        self._join_worker()
        snap = self._snap
        status = MeetingStatus.RECORDED
        if snap.unsaved:
            status = MeetingStatus.INTERRUPTED
            msg = f"{len(snap.unsaved)} pedaço(s) não gravado(s) no banco: {', '.join(snap.unsaved)}"
            self.warnings.append(msg)
            log.error("reunião %s: %s (%s)", self.meeting_id, msg, snap.db_error)
            self.sink.add_event(self.meeting_id, round(end_rel * 1000), EventType.ERROR,
                                {"message": msg, "unsaved": list(snap.unsaved), "db_error": snap.db_error})
        self.sink.add_event(self.meeting_id, round(end_rel * 1000), EventType.STOP, self.stats_light())
        self.sink.finish_meeting(self.meeting_id, round(end_rel * 1000), status)
        log.info("reunião %s parada em %.1f s (%s): %s", self.meeting_id, end_rel, status, self.stats_light())
        return self.stats()

    def cancel(self) -> None:
        with self._lock:
            if not self._stopped:
                self._stopped = True
                self._stop_handles()
                self._post(_Stop(self.clock.rel(), cancel=True))
        self._join_worker()
        self.sink.delete_meeting(self.meeting_id)

    @property
    def stopped(self) -> bool:
        return self._stopped

    def set_live_tap(self, fn: Tap | None) -> None:
        """F4: liga (ou desliga, com None) o desvio ao vivo; vale a partir do próximo bloco da thread de trabalho."""
        self._live_tap = fn

    def _tap(self, channel: str, start_frame: int, rate: int, block: np.ndarray, flags: int) -> None:
        """Chamado pelo chunker na thread de trabalho: O(1) e nunca derruba a gravação."""
        fn = self._live_tap
        if fn is None:
            return
        try:
            fn(channel, start_frame, rate, block, flags)
        except Exception:
            self.tap_errors += 1
            if self.tap_errors == 1:
                log.exception("reunião %s: desvio ao vivo falhou (a gravação segue; as próximas falhas só são contadas)",
                              self.meeting_id)

    def _stop_handles(self) -> None:
        for h in (self._l_handle, self._r_handle):
            if h is not None:
                h.stop()
        self._l_handle = self._r_handle = None

    def _join_worker(self) -> None:
        if self._worker is not None:
            self._worker.join()
            self._worker = None

    # ---------------------------------------------------------------- estado (thread da interface)
    def elapsed(self) -> float:
        return self.clock.rel()

    @property
    def snapshot(self) -> CaptureSnapshot:
        return self._snap

    def levels(self) -> dict[str, float]:
        lv = self._snap.levels
        return {"L": lv[0], "R": lv[1]}

    def health(self) -> dict[str, Health]:
        """Por canal: ok | warn | alert | muted | paused | off | lost; e `db`: ok | warn | alert (banco lento ou parado)."""
        now = self.clock.rel()
        snap = self._snap
        out: dict[str, Health] = {}
        for st, alert_s in ((self.L, self.cfg.l_alert_seconds), (self.R, self.cfg.r_alert_seconds)):
            if not st.active:
                out[st.name] = Health.OFF
            elif st.name in snap.lost:
                out[st.name] = Health.LOST
            elif self.paused:
                out[st.name] = Health.PAUSED
            elif st.name == "L" and self.muted:
                out[st.name] = Health.MUTED
            else:
                silent = now - snap.last_signal[CH_INDEX[st.name]]
                warn_at = min(10.0, alert_s / 2.0)      # verde até 10 s (ou metade do limiar, se menor)
                out[st.name] = Health.OK if silent < warn_at else (Health.WARN if silent < alert_s else Health.ALERT)
        out["db"] = self._db_health(now, snap)
        return out

    def _db_health(self, now: float, snap: CaptureSnapshot) -> Health:
        if snap.pending and snap.pending_since is not None:
            return Health.WARN if now - snap.pending_since < DB_ALERT_AFTER_S else Health.ALERT
        if snap.unsaved:
            return Health.ALERT
        if self._queue.qsize() > QUEUE_WARN_BLOCKS:
            return Health.WARN
        return Health.OK

    def stats_light(self) -> dict[str, Any]:
        snap = self._snap
        return {"chunks": {"L": snap.chunks[0], "R": snap.chunks[1]}, "bytes": snap.bytes_written, "markers": self.markers,
                "gaps_L": snap.drift["L"]["gap_frames"] if "L" in snap.drift else None,
                "gaps_R": snap.drift["R"]["gap_frames"] if "R" in snap.drift else None,
                "drift": dict(snap.drift), "pending": snap.pending, "unsaved": list(snap.unsaved)}

    def stats(self) -> dict[str, Any]:
        s = self.stats_light()
        s.update({"meeting_id": self.meeting_id, "duration_s": round(self.clock.rel(), 1), "warnings": list(self.warnings),
                  "source": str(self.source.kind), "source_detail": self.R.detail})
        return s

    # ---------------------------------------------------------------- fila
    def _post(self, msg: _Msg) -> None:
        if self._inline:
            self._dispatch(msg)
        else:
            self._queue.put_nowait(msg)

    def _run(self) -> None:
        try:
            while True:
                timeout = None
                if self._pending and self._retry_at is not None:
                    timeout = max(0.0, self._retry_at - time.monotonic())
                try:
                    msg = self._queue.get(timeout=timeout)
                except queue.Empty:
                    self._flush_pending()
                    continue
                if self._dispatch(msg):
                    return
        except BaseException:
            log.exception("thread de trabalho da reunião %s morreu", self.meeting_id)
            raise
        finally:
            try:
                self.sink.close_thread()
            except Exception as e:
                log.debug("fechar conexão da thread de trabalho: %s", e)

    def _dispatch(self, msg: _Msg) -> bool:
        match msg:
            case _Block():
                self._on_block(msg)
            case _Open():
                self._on_open(msg)
            case _Flags(paused=paused, muted=muted):
                self._w_paused, self._w_muted = paused, muted
            case _Lost():
                self._on_lost(msg)
            case _Stop():
                self._on_stop(msg)
                return True
        return False

    # ---------------------------------------------------------------- thread de trabalho
    def _publish(self, **changes: Any) -> None:
        self._snap = replace(self._snap, **changes)

    def _on_open(self, msg: _Open) -> None:
        self._sources[msg.channel] = msg.source
        pipe = self._pipes.get(msg.channel)
        if pipe is None:
            self._pipes[msg.channel] = _Pipeline(msg.channel, msg.sample_rate, msg.channels, self.cfg.target_rate,
                                                 self.cfg.chunk_seconds, self._on_raw_chunk, tap=self._tap)
        else:
            pipe.set_input(msg.sample_rate, msg.channels)
        if msg.channel in self._snap.lost:
            self._publish(lost=self._snap.lost - {msg.channel})

    def _on_lost(self, msg: _Lost) -> None:
        if self._sources.get(msg.channel) != msg.source:
            return
        log.warning("canal %s: stream encerrada pelo sistema (%s)", msg.channel, msg.reason)
        self._publish(lost=self._snap.lost | {msg.channel})

    def _on_block(self, msg: _Block) -> None:
        if self._sources.get(msg.channel) != msg.source:
            return                                        # stream antiga depois de uma troca de fonte
        pipe = self._pipes.get(msg.channel)
        if pipe is None:
            return
        i = CH_INDEX[msg.channel]
        t_rel = self.clock.rel_from_perf(msg.t_perf)
        level = audio.rms(msg.data)
        silenced = self._w_paused or (self._w_muted and msg.channel == "L")
        snap = self._snap
        levels, last, blocks = list(snap.levels), list(snap.last_signal), list(snap.blocks)
        levels[i] = level
        blocks[i] += 1
        if level > self.cfg.silence_rms and not silenced:
            last[i] = t_rel
        self._snap = replace(snap, levels=(levels[0], levels[1]), last_signal=(last[0], last[1]), blocks=(blocks[0], blocks[1]))
        data = self._silence(len(msg.data)) if silenced else msg.data
        pipe.feed(data, t_rel, silenced)

    def _silence(self, n: int) -> np.ndarray:
        if n > len(self._zeros):
            self._zeros = np.zeros(n, np.float32)
        return self._zeros[:n]

    def _on_raw_chunk(self, raw: RawChunk) -> None:
        if self._w_cancel:
            return
        if raw.silent or raw.data is None:
            flac = audio.silent_flac(raw.sample_rate, raw.frames)
        else:
            flac = audio.encode_flac(raw.data, raw.sample_rate)
        enc = EncodedChunk(self.meeting_id, raw.channel, raw.idx, round(raw.start_rel * 1000), raw.frames, raw.sample_rate,
                           raw.gap_frames, flac)
        for sink in self.extra_sinks:
            try:
                sink.write_chunk(enc)
            except Exception:
                log.exception("destino secundário falhou no pedaço %s", enc.key)
        self._pending.append(enc)
        self._publish(pending=len(self._pending), pending_since=self._snap.pending_since if self._snap.pending else self.clock.rel(),
                      drift={ch: p.chunker.drift_stats() for ch, p in self._pipes.items()})
        self._flush_pending()

    def _flush_pending(self, deadline: float | None = None) -> None:
        """Grava os pedaços pendentes em ordem; `OperationalError` espera com backoff (1, 2, 4... 30 s) e tenta de novo.
        Com `deadline` (ao parar), dorme e insiste até o prazo; sem ele, volta à fila e tenta no próximo despertar."""
        while self._pending:
            now = time.monotonic()
            if self._retry_at is not None and now < self._retry_at:
                if deadline is None:
                    return
                wait = min(self._retry_at - now, deadline - now)
                if wait <= 0:
                    return
                time.sleep(wait)
                continue
            enc = self._pending[0]
            try:
                self.sink.write_chunk(enc)
            except sqlite3.IntegrityError:
                log.exception("pedaço %s já existia no banco; mantendo o existente", enc.key)
                self._pending.popleft()
            except sqlite3.OperationalError as e:
                self._failures += 1
                backoff = min(30.0 * self.cfg.retry_base_s, self.cfg.retry_base_s * 2 ** (self._failures - 1))
                self._retry_at = time.monotonic() + backoff
                log.warning("banco não aceitou o pedaço %s (%s); %d pendente(s), nova tentativa em %.0f s",
                            enc.key, e, len(self._pending), backoff)
                self._publish(db_error=str(e), pending=len(self._pending))
                if deadline is None:
                    return
                continue
            except Exception as e:
                log.exception("pedaço %s não pôde ser gravado; retido em memória", enc.key)
                self._failed.append(self._pending.popleft())
                self._publish(db_error=str(e), pending=len(self._pending), unsaved=tuple(c.key for c in self._failed))
            else:
                self._pending.popleft()
                self._failures, self._retry_at = 0, None
                snap = self._snap
                chunks = list(snap.chunks)
                chunks[CH_INDEX[enc.channel]] += 1
                self._publish(chunks=(chunks[0], chunks[1]), bytes_written=snap.bytes_written + len(enc.flac),
                              pending=len(self._pending), pending_since=None if not self._pending else snap.pending_since,
                              db_error="" if not self._pending else snap.db_error)

    def _on_stop(self, msg: _Stop) -> None:
        self._w_cancel = msg.cancel
        for pipe in self._pipes.values():
            pipe.close(msg.end_rel)
        if not msg.cancel:
            self._flush_pending(deadline=time.monotonic() + self.cfg.stop_timeout_s)
        unsaved = tuple(c.key for c in self._failed) + tuple(c.key for c in self._pending)
        self._publish(drift={ch: p.chunker.drift_stats() for ch, p in self._pipes.items()}, unsaved=unsaved,
                      pending=len(self._pending))

    # ---------------------------------------------------------------- util
    def _ms(self) -> int:
        return round(self.clock.rel() * 1000)

    def _warn(self, msg: str, type_: EventType = EventType.WARNING) -> None:
        self.warnings.append(msg)
        log.warning("reunião %s: %s", self.meeting_id, msg)
        try:
            self.sink.add_event(self.meeting_id, self._ms(), type_, {"message": msg})
        except Exception as e:
            log.debug("evento de aviso não gravado: %s", e)
