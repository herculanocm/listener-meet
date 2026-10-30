"""F5.9 (L1a e L2): o loopback de um app pelo proc-tap, fora de `platform.py`. D-055: o loopback de processo do Windows pega
o processo e os filhos diretos, não os netos, e o Teams novo toca a chamada num WebView2 neto da raiz: cada sessão de
áudio do app mais funda que isso ganha uma captura própria, somada à da raiz (`capture/mixer.py`). F5.9 (item 3): o mixer
existe sempre, mesmo com um alvo, e uma vigia numa thread própria (`AppWatch`) relê a cada 2 s os processos e as sessões
de saída para somar o processo novo (a chamada que entra depois do "Gravar") e parar o tap do que saiu. Um mixin do
`Win32Platform`: o snapshot com cache (`_processes`) e as sessões de saída (`_render_sessions`) são dele."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from ...capture.mixer import FrameMixer
from ...log import get_logger
from ..base import AudioCallback, ErrorCallback
from . import endpoints, processes, sessions
from .stream import _StreamHandle

log = get_logger("platform.win32.app_loopback")

RESCAN_S = 2.0                 # F5.9 (item 3): a vigia do app relê processos e sessões a cada tanto
WATCH_JOIN_S = 1.0             # o `stop()` espera a vigia sair até isto (uma releitura leva 15 a 30 ms, pico 278 ms)

type Snapshot = Callable[[], dict[int, processes.ProcInfo]]
type FlowFactory = Callable[[], sessions.FlowSessions]
type OpenTap = Callable[[int, AudioCallback], _StreamHandle]


def new_targets(current: Iterable[int], root: int, session_pids: Iterable[int], procs: dict[int, processes.ProcInfo]) -> list[int]:
    """F5.9 (item 3), pura: os pids do app com sessão de áudio que ainda não têm tap e estão mais fundos que um alvo (o
    filho direto de um alvo já vem no loopback dele, D-055). Os taps de hoje entram como alvos, para o filho novo de um
    deles não ganhar um tap em dobro. Do mais raso ao mais fundo."""
    known = set(current)
    return [p for p in processes.loopback_targets(root, set(session_pids) | known, procs) if p not in known]


class _ProcessLife:
    """F5.9 (item 1, D-070): a prova de vida de um tap pelo handle do seu processo (`OpenProcess(SYNCHRONIZE)`), aberto na
    abertura e solto no `close`; sem handle (o Windows recusou), só o fim da thread do tap conta como morte, porque ela
    nunca cai sozinha (o laço engole erros)."""

    def __init__(self, pid: int, tap: Any) -> None:
        self.tap = tap
        self.handle = processes.open_process_handle(pid)
        if self.handle is None:
            log.debug("processo %s: sem handle para a prova de vida; vale só a thread do tap", pid)

    def alive(self) -> bool | None:
        if self.handle is None:
            return False if not self.tap.is_running else None
        exited = processes.has_exited(self.handle)
        return None if exited is None else not exited

    def close(self) -> None:
        if self.handle is not None:
            processes.close_handle(self.handle)
            self.handle = None


class _Feed:
    """O callback de um tap somado: o índice no mixer só existe depois da checagem de formato (`add_source` não gasta um
    slot com o tap que fica de fora), e o tap só entrega depois do `start()`; sem índice, o bloco cai (nunca vai à raiz)."""

    def __init__(self, mixer: FrameMixer) -> None:
        self.mixer = mixer
        self.index = 0

    def __call__(self, block: np.ndarray, t: float) -> None:
        if self.index > 0:
            self.mixer.feed(self.index, block, t)


class AppTaps:
    """F5.9 (item 3): os taps de um app somados num mixer: a raiz (índice 0) e um por processo mais fundo, no índice que
    `add_source` deu. A vigia (thread própria) soma os novos e para o que saiu; a interface inicia e fecha; a trava cobre
    a lista, nunca a abertura de um tap (o proc-tap espera a ativação por até 10 s, e a interface fecha sob esta trava).
    Uma tentativa por pid, como a raiz: o que recusou, veio em outro formato ou não começou fica em `skipped`; o tap
    parado fica em `taps` só para o `close`. Fechado, nada mais entra."""

    def __init__(self, root: int, main: _StreamHandle, mixer: FrameMixer, open_tap: OpenTap) -> None:
        self.root = root
        self.mixer = mixer
        self._open_tap = open_tap
        self.taps: dict[int, _StreamHandle] = {root: main}
        self.dead: set[int] = set()                        # os sub-alvos cujo processo saiu (tap parado)
        self.skipped: set[int] = set()                     # os pids que falharam uma vez
        self.lock = threading.Lock()
        self.started = False
        self.closed = False

    def pids(self) -> list[int]:
        with self.lock:
            return list(self.taps)

    def known(self) -> set[int]:
        """Os pids que não precisam de tap: os vivos e os que já falharam. O parado fica de fora: o filho dele que ganha
        sessão depois entra, e um pid reaproveitado ganha um tap novo no lugar do parado."""
        with self.lock:
            return (set(self.taps) - self.dead) | self.skipped

    def add(self, pid: int) -> bool:
        """Um tap a mais, somado ao mixer: aberto fora da trava e registrado sob ela (fechado no meio, o recém-aberto para);
        iniciado se os outros já rodam."""
        feed = _Feed(self.mixer)
        handle = self._open(pid, feed)
        if handle is None:
            self._skip(pid)
            return False
        with self.lock:
            if self.closed or (pid in self.taps and pid not in self.dead):
                handle.stop()
                return False
            feed.index = self.mixer.add_source()
            self.taps[pid] = handle
            self.dead.discard(pid)
            start_it = self.started
        if start_it and not self._start(pid, handle):
            self._skip(pid)
            return False
        return True

    def _open(self, pid: int, feed: _Feed) -> _StreamHandle | None:
        try:
            handle = self._open_tap(pid, feed)
        except Exception as e:                            # o processo saiu ou recusou: segue com o que há
            log.warning("loopback do processo %s (do app %s) não abriu: %s", pid, self.root, e)
            return None
        if (handle.sample_rate, handle.channels) != (self.mixer.sample_rate, self.mixer.channels):
            log.warning("loopback do processo %s em outro formato (%d Hz, %d canais): fica de fora", pid, handle.sample_rate,
                        handle.channels)
            handle.stop()
            return None
        return handle

    def _start(self, pid: int, handle: _StreamHandle) -> bool:
        try:
            handle.start()
        except Exception as e:
            log.warning("loopback do processo %s (do app %s) não começou: %s", pid, self.root, e)
            handle.stop()
            return False
        return True

    def _skip(self, pid: int) -> None:
        with self.lock:
            self.skipped.add(pid)

    def stop_dead(self) -> list[int]:
        """Os sub-alvos cujo processo saiu têm o tap parado (a thread do proc-tap gira a 1 ms à toa), sem sair do mixer."""
        with self.lock:
            dead = [pid for pid, h in self.taps.items() if pid != self.root and pid not in self.dead and h.alive() is False]
            for pid in dead:
                self.taps[pid].stop()
            self.dead.update(dead)
        return dead

    def start(self) -> None:
        with self.lock:
            self.started = True
            for h in self.taps.values():
                h.start()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for h in self.taps.values():
                h.stop()


class AppWatch(threading.Thread):
    """F5.9 (item 3): a vigia do loopback do app. A cada `every_s` tira o próprio snapshot de processos (Toolhelp32, nunca
    o cache de 3 s do `_Poller`) e lê as sessões de saída com um `FlowSessions(keep=True)` criado, lido e fechado nesta
    thread (o COM exige); o pid novo do app mais fundo que um alvo ganha um tap; o sub-alvo que saiu tem o tap parado.
    Nunca na thread da interface (reler custa 15 a 30 ms, pico 278 ms)."""

    def __init__(self, taps: AppTaps, snapshot: Snapshot, flow_factory: FlowFactory, every_s: float = RESCAN_S) -> None:
        super().__init__(name=f"app-loopback-{taps.root}", daemon=True)
        self._taps = taps
        self._snapshot = snapshot
        self._flow_factory = flow_factory
        self._every = every_s
        self._stopping = threading.Event()                 # não `_stop`: o `Thread` usa esse nome por dentro
        self.rescans = 0                                   # instrumentação
        self.errors = 0                                    # releituras que falharam fora da leitura (no log, a primeira)
        self.last_ms = 0.0

    def run(self) -> None:
        flow: sessions.FlowSessions | None = None
        try:
            flow = self._flow_factory()
            while not self._stopping.wait(self._every):
                self._rescan_safely(flow)
        except Exception as e:
            log.warning("vigia do loopback do app %s parou: %s", self._taps.root, e)
        finally:
            if flow is not None:
                flow.close()                               # na thread que leu

    def _rescan_safely(self, flow: sessions.FlowSessions) -> None:
        """Uma falha fora da leitura não derruba a vigia pelo resto da gravação: contada, e no log uma vez."""
        try:
            self.rescan(flow)
        except Exception as e:
            self.errors += 1
            if self.errors == 1:
                log.warning("vigia do loopback do app %s: releitura falhou (%s); as próximas falhas só são contadas",
                            self._taps.root, e)

    def rescan(self, flow: sessions.FlowSessions) -> list[int]:
        """Uma releitura: para o tap de quem saiu e soma o processo novo; devolve os pids somados. A releitura que falha
        (o Core Audio fora) fica para a próxima."""
        t0 = time.perf_counter()
        self.rescans += 1
        root = self._taps.root
        try:
            procs = self._snapshot()
            session_pids = {s.pid for group in flow.read().values() for s in group}
        except Exception as e:
            log.debug("vigia do loopback do app %s: releitura falhou: %s", root, e)
            return []
        for pid in self._taps.stop_dead():
            log.info("loopback do app %s: o processo %s saiu; tap parado", root, pid)
        added = [pid for pid in new_targets(self._taps.known(), root, session_pids, procs) if self._taps.add(pid)]
        if added:
            log.info("loopback do app %s: processo(s) %s somado(s), %d tap(s)", root, ", ".join(map(str, added)),
                     len(self._taps.pids()))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return added

    def close(self) -> None:
        """Da thread da interface: acorda a vigia e espera até `WATCH_JOIN_S`; presa no COM, fica para trás como daemon."""
        self._stopping.set()
        if self.is_alive():
            self.join(WATCH_JOIN_S)


class Win32AppLoopback:
    """`Platform.open_app_loopback` no Windows (D-055), herdado pelo `Win32Platform` (o `platform.py` não cresce)."""

    _processes: Callable[[], tuple[dict[int, processes.ProcInfo], dict[int, str]]]   # do `Win32Platform`
    _render_sessions: sessions.FlowSessions                                          # do `Win32Platform`
    watch_every_s: float = RESCAN_S                                                  # os testes encurtam

    def _watch_snapshot(self) -> dict[int, processes.ProcInfo]:
        """O snapshot da vigia: o próprio (11 a 20 ms), na thread dela; nunca o cache do `_Poller`."""
        return processes.snapshot_processes()

    def _watch_flow(self) -> sessions.FlowSessions:
        """As sessões de saída da vigia, guardadas (`keep`); criadas, lidas e fechadas na thread dela."""
        return sessions.FlowSessions(endpoints.FLOW_RENDER, keep=True)

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
        filhos diretos, então cada sessão do app mais funda que isso ganha uma captura própria, somada à da raiz. F5.9
        (item 3): o mixer existe sempre e a vigia nasce no `start()` e morre no `stop()`."""
        procs, _titles = self._processes()
        targets = processes.loopback_targets(pid, self._session_pids(), procs) if procs else [pid]
        holder: dict[str, FrameMixer] = {}                 # o formato vem da captura da raiz; os dados, só depois do start
        main = self._open_process_loopback(pid, lambda block, t: holder["mixer"].feed(0, block, t), on_error)
        mixer = holder["mixer"] = FrameMixer(1, main.sample_rate, main.channels, on_audio)
        taps = AppTaps(pid, main, mixer, self._open_process_loopback)
        for extra in targets[1:]:
            taps.add(extra)
        log.info("loopback do app %s: %d processo(s) somados (%s)", pid, len(taps.pids()), ", ".join(map(str, taps.pids())))
        watch = AppWatch(taps, self._watch_snapshot, self._watch_flow, self.watch_every_s)

        def start() -> None:
            taps.start()
            watch.start()

        def stop() -> None:
            watch.close()
            taps.close()

        # F5.9 (item 1): o composto responde pela raiz; um sub-alvo morto é da vigia (o tap parado), não do canal perdido
        return _StreamHandle(start, stop, main.sample_rate, main.channels, on_error, alive_fn=main.alive)

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
        life = _ProcessLife(pid, tap)                     # F5.9 (item 1): a prova de vida é o handle do processo

        def start() -> None:
            tap.start()
            log.info("loopback do processo %s aberto: %d Hz, %d canais", pid, sr, ch)

        def stop() -> None:
            try:
                tap.stop()
            finally:
                try:
                    tap.close()
                finally:
                    life.close()                          # o handle vive o que a captura vive

        return _StreamHandle(start, stop, sr, ch, on_error, alive_fn=life.alive)
