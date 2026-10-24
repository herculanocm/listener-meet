"""Gravador de uma reunião: canal L (mic) e canal R (app ou sistema), pausa, mudo, marcadores, saúde dos canais.

Threads: callbacks de áudio só copiam para os chunkers; uma thread de trabalho faz mono, reamostragem,
FLAC e grava no banco. Eventos e marcadores são gravados por quem chama (thread da interface).
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

import numpy as np

from ..log import get_logger
from ..platform.base import AudioDevice, Platform
from . import audio as A
from .chunker import ChannelChunker, RawChunk
from .clock import SessionClock

log = get_logger("capture")


@dataclass
class SourceSpec:
    kind: str = "system"        # "app" | "system" | "none"
    pid: int | None = None
    exe: str = ""
    label: str = ""


@dataclass
class RecordConfig:
    mic_device: AudioDevice | None = None
    source: SourceSpec = field(default_factory=SourceSpec)
    mic_rate: int = 16000
    target_rate: int = 16000
    chunk_seconds: float = 60.0
    silence_rms: float = 0.004
    l_alert_seconds: float = 15.0
    r_alert_seconds: float = 20.0
    block_ms: int = 100


@dataclass
class ChannelState:
    name: str
    level: float = 0.0            # RMS do último bloco (0 a 1)
    last_signal_rel: float = 0.0  # último instante com sinal acima do limiar
    blocks: int = 0
    active: bool = False
    detail: str = ""


class Recorder:
    def __init__(self, platform: Platform, store, meeting_id: int, cfg: RecordConfig,
                 clock: SessionClock | None = None):
        self.platform = platform
        self.store = store
        self.meeting_id = meeting_id
        self.cfg = cfg
        self.clock = clock or SessionClock.start()
        self.L = ChannelState("L")
        self.R = ChannelState("R")
        self.paused = False
        self.muted = False
        self.markers = 0
        self.warnings: list[str] = []
        self._l_handle = None
        self._r_handle = None
        self._l_chunker: ChannelChunker | None = None
        self._r_chunker: ChannelChunker | None = None
        self._queue: queue.Queue[RawChunk | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False
        self.chunks_written = {"L": 0, "R": 0}
        self.bytes_written = 0
        self.source = cfg.source

    # ---------------------------------------------------------------- ciclo
    def start(self) -> None:
        self._worker = threading.Thread(target=self._work, name="chunks-worker", daemon=True)
        self._worker.start()
        self.store.add_event(self.meeting_id, self._ms(), "start", {"source": self.source.kind, "exe": self.source.exe})
        try:
            self._l_handle = self.platform.open_mic(self.cfg.mic_device, self.cfg.mic_rate, self._on_l)
            self._l_chunker = ChannelChunker("L", self._l_handle.sample_rate, 1, self._enqueue,
                                             chunk_seconds=self.cfg.chunk_seconds)
            self.L.active = True
            self.L.detail = self.cfg.mic_device.name if self.cfg.mic_device else "microfone padrão"
        except Exception as e:
            self._warn(f"microfone não iniciou: {e}")
        self._open_source(self.source)

    def _open_source(self, spec: SourceSpec) -> None:
        self.R.active = False
        try:
            if spec.kind == "app" and spec.pid:
                self._r_handle = self.platform.open_app_loopback(spec.pid, self._on_r)
                self.R.detail = spec.label or spec.exe or f"pid {spec.pid}"
            elif spec.kind == "system":
                self._r_handle = self.platform.open_system_loopback(self._on_r)
                self.R.detail = "sistema inteiro"
            else:
                self._r_handle = None
                self.R.detail = "sem saída"
                return
        except Exception as e:
            self._warn(f"saída ({spec.kind}) não iniciou: {e}")
            if spec.kind == "app":
                try:
                    self._r_handle = self.platform.open_system_loopback(self._on_r)
                    self.R.detail = "sistema inteiro (fallback)"
                    spec = SourceSpec("system")
                except Exception as e2:
                    self._warn(f"loopback do sistema não iniciou: {e2}")
                    self._r_handle = None
                    return
            else:
                self._r_handle = None
                return
        self.source = spec
        if self._r_chunker is None:
            self._r_chunker = ChannelChunker("R", self._r_handle.sample_rate, self._r_handle.channels,
                                             self._enqueue, chunk_seconds=self.cfg.chunk_seconds)
        elif self._r_chunker.sample_rate != self._r_handle.sample_rate or self._r_chunker.channels != self._r_handle.channels:
            # fonte nova com formato diferente: fecha o chunker antigo no instante atual e abre outro na sequência
            end = self.clock.rel()
            old = self._r_chunker
            old.close(end)
            self._r_chunker = ChannelChunker("R", self._r_handle.sample_rate, self._r_handle.channels, self._enqueue,
                                             chunk_seconds=self.cfg.chunk_seconds, start_rel=end)
            self._r_chunker.idx = old.idx
        self.R.active = True

    def switch_source(self, spec: SourceSpec) -> None:
        """Troca a fonte do canal R sem parar; o buraco entre uma e outra é preenchido com silêncio."""
        with self._lock:
            if self._r_handle is not None:
                self._r_handle.stop()
                self._r_handle = None
            self._open_source(spec)
        self.store.add_event(self.meeting_id, self._ms(), "source_change",
                             {"kind": self.source.kind, "exe": self.source.exe, "pid": self.source.pid})

    def pause(self) -> None:
        if not self.paused:
            self.paused = True
            self.store.add_event(self.meeting_id, self._ms(), "pause", {})

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self.store.add_event(self.meeting_id, self._ms(), "resume", {})

    def set_muted(self, muted: bool, source: str = "app") -> None:
        if muted != self.muted:
            self.muted = muted
            self.store.add_event(self.meeting_id, self._ms(), "mute" if muted else "unmute", {"source": source})

    def mark(self, note: str = "") -> int:
        t_ms = self._ms()
        marker_id = self.store.add_marker(self.meeting_id, t_ms, note)
        self.store.add_event(self.meeting_id, t_ms, "marker", {"note": note, "marker_id": marker_id})
        self.markers += 1
        return t_ms

    def stop(self) -> dict:
        if self._stopped:
            return self.stats()
        self._stopped = True
        end_rel = self.clock.rel()
        with self._lock:
            for h in (self._l_handle, self._r_handle):
                if h is not None:
                    h.stop()
            self._l_handle = self._r_handle = None
            for ch in (self._l_chunker, self._r_chunker):
                if ch is not None:
                    ch.close(end_rel)
        self._queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=120)
        self.store.add_event(self.meeting_id, int(end_rel * 1000), "stop", self.stats_light())
        self.store.finish_meeting(self.meeting_id, int(end_rel * 1000))
        log.info("reunião %s parada em %.1f s: %s", self.meeting_id, end_rel, self.stats_light())
        return self.stats()

    def cancel(self) -> None:
        if not self._stopped:
            self._stopped = True
            with self._lock:
                for h in (self._l_handle, self._r_handle):
                    if h is not None:
                        h.stop()
                self._l_handle = self._r_handle = None
            self._queue.put(None)
            if self._worker is not None:
                self._worker.join(timeout=30)
        self.store.delete_meeting(self.meeting_id)

    # ---------------------------------------------------------------- estado
    def elapsed(self) -> float:
        return self.clock.rel()

    def levels(self) -> dict[str, float]:
        return {"L": self.L.level, "R": self.R.level}

    def health(self) -> dict[str, str]:
        """ok | warn | alert | muted | paused | off por canal."""
        now = self.clock.rel()
        out = {}
        for st, alert_s in ((self.L, self.cfg.l_alert_seconds), (self.R, self.cfg.r_alert_seconds)):
            if not st.active:
                out[st.name] = "off"
            elif self.paused:
                out[st.name] = "paused"
            elif st.name == "L" and self.muted:
                out[st.name] = "muted"
            else:
                silent = now - st.last_signal_rel
                warn_at = min(10.0, alert_s / 2.0)      # verde até 10 s (ou metade do limiar, se menor)
                out[st.name] = "ok" if silent < warn_at else ("warn" if silent < alert_s else "alert")
        return out

    def stats_light(self) -> dict:
        return {"chunks": dict(self.chunks_written), "bytes": self.bytes_written, "markers": self.markers,
                "gaps_L": self._l_chunker.gap_frames_total if self._l_chunker else None,
                "gaps_R": self._r_chunker.gap_frames_total if self._r_chunker else None,
                "drift": {ch.channel: ch.drift_stats() for ch in (self._l_chunker, self._r_chunker) if ch is not None}}

    def stats(self) -> dict:
        s = self.stats_light()
        s.update({"meeting_id": self.meeting_id, "duration_s": round(self.clock.rel(), 1),
                  "warnings": list(self.warnings), "source": self.source.kind, "source_detail": self.R.detail})
        return s

    # ---------------------------------------------------------------- áudio (threads de áudio)
    def _on_l(self, block: np.ndarray, t_first_perf: float) -> None:
        st = self.L
        st.blocks += 1
        level = A.rms(block)
        st.level = level
        t_rel = self.clock.rel_from_perf(t_first_perf)
        if level > self.cfg.silence_rms and not (self.paused or self.muted):
            st.last_signal_rel = t_rel
        if self.paused or self.muted:
            block = np.zeros_like(block)
        ch = self._l_chunker
        if ch is not None:
            ch.write(block, t_rel)

    def _on_r(self, block: np.ndarray, t_first_perf: float) -> None:
        st = self.R
        st.blocks += 1
        level = A.rms(block)
        st.level = level
        t_rel = self.clock.rel_from_perf(t_first_perf)
        if level > self.cfg.silence_rms and not self.paused:
            st.last_signal_rel = t_rel
        if self.paused:
            block = np.zeros_like(block)
        ch = self._r_chunker
        if ch is not None:
            ch.write(block, t_rel)

    def _enqueue(self, chunk: RawChunk) -> None:
        self._queue.put(chunk)

    # ---------------------------------------------------------------- thread de trabalho
    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._persist(item)
            except Exception as e:
                self._warn(f"falha ao gravar pedaço {item.channel}{item.idx}: {e}")

    def _persist(self, c: RawChunk) -> None:
        mono = A.to_mono(c.data)
        target = int(self.cfg.target_rate)
        if c.sample_rate != target:
            mono = A.resample(mono, c.sample_rate, target)
            frames = len(mono)
            gap = int(round(c.gap_frames * target / c.sample_rate))
        else:
            frames, gap = c.frames, c.gap_frames
        flac = A.encode_flac(mono, target)
        self.store.insert_chunk(self.meeting_id, c.channel, c.idx, int(round(c.start_rel * 1000)), frames, target, gap, flac)
        self.chunks_written[c.channel] = self.chunks_written.get(c.channel, 0) + 1
        self.bytes_written += len(flac)

    # ---------------------------------------------------------------- util
    def _ms(self) -> int:
        return int(self.clock.rel() * 1000)

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning("reunião %s: %s", self.meeting_id, msg)
        try:
            self.store.add_event(self.meeting_id, self._ms(), "warning", {"message": msg})
        except Exception:
            pass
