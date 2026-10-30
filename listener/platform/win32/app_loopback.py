"""F5.9 (L1a): o loopback de um app pelo proc-tap, fora de `platform.py`. D-055: o loopback de processo do Windows pega o
processo e os filhos diretos, não os netos, e o Teams novo toca a chamada num WebView2 neto da raiz: cada sessão de áudio
do app mais funda que isso ganha uma captura própria, somada à da raiz (`capture/mixer.py`). Um mixin do `Win32Platform`:
o snapshot de processos (`_processes`, com cache) e as sessões de saída (`_render_sessions`) são dele."""
from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ...capture.mixer import FrameMixer
from ...log import get_logger
from ..base import AudioCallback, ErrorCallback
from . import processes, sessions
from .stream import _StreamHandle

log = get_logger("platform.win32.app_loopback")


class Win32AppLoopback:
    """`Platform.open_app_loopback` no Windows (D-055), herdado pelo `Win32Platform` (o `platform.py` não cresce)."""

    _processes: Callable[[], tuple[dict[int, processes.ProcInfo], dict[int, str]]]   # do `Win32Platform`
    _render_sessions: sessions.FlowSessions                                          # do `Win32Platform`

    def _session_pids(self) -> set[int]:
        """Os processos com sessão de áudio agora (qualquer estado), para os alvos do loopback do app. F5.7: em todas as
        saídas, pelo `FlowSessions` sem guardar (o WebView2 neto do Teams que só toca no fone de comunicação também)."""
        try:
            reading = self._render_sessions.read()
        except Exception as e:
            log.debug("sessões de áudio para o loopback: %s", e)
            return set()
        return {s.pid for group in reading.values() for s in group}

    def open_app_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> _StreamHandle:
        """O som do app de `pid` (a raiz que a lista de apps oferece, D-055): o loopback do Windows pega o processo e os
        filhos diretos, então cada sessão do app mais funda que isso ganha uma captura própria, somada à da raiz."""
        procs, _titles = self._processes()
        targets = processes.loopback_targets(pid, self._session_pids(), procs) if procs else [pid]
        if len(targets) == 1:
            return self._open_process_loopback(pid, on_audio, on_error)
        holder: dict[str, FrameMixer] = {}                 # o formato vem da captura da raiz; os dados, só depois do start
        main = self._open_process_loopback(pid, lambda block, t: holder["mixer"].feed(0, block, t), on_error)
        mixer = holder["mixer"] = FrameMixer(len(targets), main.sample_rate, main.channels, on_audio)
        parts = [main]
        for i, extra in enumerate(targets[1:], start=1):
            try:
                h = self._open_process_loopback(extra, functools.partial(mixer.feed, i), None)
            except Exception as e:                        # o processo saiu ou recusou: segue com a raiz
                log.warning("loopback do processo %s (do app %s) não abriu: %s", extra, pid, e)
                continue
            if (h.sample_rate, h.channels) != (main.sample_rate, main.channels):
                log.warning("loopback do processo %s em outro formato (%d Hz, %d canais): fica de fora", extra, h.sample_rate,
                            h.channels)
                h.stop()
                continue
            parts.append(h)
        log.info("loopback do app %s: %d processo(s) somados (%s)", pid, len(parts), ", ".join(str(t) for t in targets))

        def start() -> None:
            for h in parts:
                h.start()

        def stop() -> None:
            for h in parts:
                h.stop()

        return _StreamHandle(start, stop, main.sample_rate, main.channels, on_error)

    def _open_process_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> _StreamHandle:
        from proctap import ProcessAudioCapture

        handler: Callable[[bytes, int], None] | None = None

        def cb(pcm: bytes, frames: int) -> None:
            if handler is not None:
                handler(pcm, frames)

        tap = ProcessAudioCapture(pid, on_data=cb)
        fmt: dict[str, Any] = {"sample_rate": 48000, "channels": 2, "sample_format": "float32"}
        try:
            fmt = dict(tap.get_format())
        except Exception as e:
            log.debug("formato do proc-tap: %s", e)
        sr, ch = int(fmt.get("sample_rate", 48000)), int(fmt.get("channels", 2))
        is_int16 = str(fmt.get("sample_format", "float32")).lower() == "int16"
        itemsize = 2 if is_int16 else 4
        frame_bytes = ch * itemsize
        dtype = np.int16 if is_int16 else np.float32
        scale = np.float32(1.0 / 32768.0)

        def on_data(pcm: bytes, _frames: int) -> None:
            # proc-tap 1.1.1 passa frames = -1: contamos pelos bytes; constantes capturadas, sem dict (M-11)
            n = len(pcm) // frame_bytes
            if n <= 0:
                return
            arr = np.frombuffer(pcm, dtype=dtype)[: n * ch].reshape(n, ch)
            block = arr.astype(np.float32) * scale if is_int16 else arr.copy()     # uma cópia só
            on_audio(block, time.perf_counter() - n / sr)

        handler = on_data

        def start() -> None:
            tap.start()
            log.info("loopback do processo %s aberto: %d Hz, %d canais", pid, sr, ch)

        def stop() -> None:
            try:
                tap.stop()
            finally:
                tap.close()

        return _StreamHandle(start, stop, sr, ch, on_error)
