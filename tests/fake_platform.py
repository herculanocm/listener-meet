"""Plataforma simulada para os testes (roda em qualquer sistema): streams dirigidas por um relógio manual.

`FakePlatform.pump(seconds)` avança o `ManualClock` e entrega os blocos de todas as streams iniciadas, em ordem de
tempo, de forma síncrona: com `Recorder(inline_worker=True)` tudo acontece na thread do teste (F1.2 M-27).
F5.3 L4: `endpoints` é o que o Core Audio vê agora (`next_endpoints` entra na leitura seguinte) e `devices` o que o
PortAudio leu da última vez (só muda no reinício, com o padrão dessa leitura); `open_mic(strict=True)` recusa o pedido
fora da lista; o loopback do sistema abre na saída pedida, resolvida contra a lista do primeiro aberto enquanto algum
segue aberto (um `PyAudio()` vivo mantém a lista antiga, A-15), e `loopbacks_at_open` conta os abertos em cada abertura.
D-067: o padrão de agora é o do Core Audio (`endpoints`): `default_output_id` e `default_input_id` o leem (contados em
`default_reads`), `default_input_device` prefere o microfone padrão de agora quando ele está na lista, `open_output` abre um
fluxo falso no padrão de saída e `play_sound` anota onde o teste do warm-up tocou (`played`).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

import numpy as np

from listener.capture.clock import ManualClock
from listener.platform.base import (AudioActivity, AudioCallback, AudioDevice, ChildProcess, Endpoint, Endpoints,
                                    ErrorCallback, Platform, PlayingApp, WatchedApp)
from tests.fake_activity import FAKE_WATCHED, FakeMicWatch
from tests.fake_player import FakeOutput
from tests.fake_streams import FakeChild, FakeHotkeys, FakeMute, FakeStream   # F5.9 (L1a): reexportados daqui

MIC = AudioDevice("fake-mic-0", "Mic Fake", "fake", 1, 16000, index=0)
SPEAKERS = Endpoint("fake-out-0", "Alto-falantes Fake")
BASE_ENDPOINTS = Endpoints(capture=(Endpoint(MIC.id, MIC.name),), render=(SPEAKERS,), capture_default=MIC.id,
                           capture_comm=MIC.id, render_default=SPEAKERS.id, render_comm=SPEAKERS.id)


class FakePlatform(Platform):
    name = "fake"

    def __init__(self, tmp: Path, r_silent_windows: list[tuple[float, float]] | None = None, mic_fail: bool = False,
                 app_fail: bool = False, clock: ManualClock | None = None, r_format: tuple[int, int] = (48000, 2),
                 system_format: tuple[int, int] = (48000, 2)):
        self.tmp = Path(tmp)
        self.clock = clock or ManualClock()
        self.r_silent_windows = r_silent_windows or []
        self.mic_fail = mic_fail
        self.app_fail = app_fail
        self.r_format = r_format
        self.system_format = system_format
        self._mute = FakeMute()
        self._hk = FakeHotkeys()
        self.streams: list[FakeStream] = []
        self.awake: bool | None = None
        self.spawned: list[tuple[list[str], dict[str, str]]] = []   # F2: o worker nunca é lançado de verdade
        self.children: list[FakeChild] = []                          # F4: filhos ao vivo silenciosos
        self.lowered = 0
        self.next_pid = 5000
        self.devices = [MIC]
        self.next_devices: list[AudioDevice] | None = None    # U-043: o teste "conecta" um dispositivo aqui
        self.refreshes = 0
        self.mic_streams: list[FakeStream] = []
        # F5.3 L4: o Core Audio (agora e na próxima leitura), o que o PortAudio leu da última vez e quem mais o segura
        self.endpoints: Endpoints | None = BASE_ENDPOINTS
        self.next_endpoints: Endpoints | None = None
        self.endpoint_reads = 0
        self._pa_signature = BASE_ENDPOINTS.signature
        self._pa_default: str | None = None                   # o padrão da última leitura do PortAudio (None = o primeiro)
        self.other_streams: Callable[[], int] = lambda: 0     # o player nos testes do controlador
        self.fail_ids: set[str] = set()                       # microfones que recusam abrir
        self.start_fail_ids: set[str] = set()                 # microfones que abrem e recusam o `start()`
        self.system_fail = False                              # o loopback do sistema recusa abrir
        self.system_start_fail = False                        # ...ou abre e recusa o `start()`
        self.mics_at_open: list[int] = []                     # quantos microfones seguiam abertos em cada abertura (A-15)
        self.loopbacks: list[FakeStream] = []
        self.loopbacks_at_open: list[int] = []                # quantos loopbacks do sistema seguiam abertos em cada abertura
        self._lb_view: Endpoints | None = None                # a lista do PyAudio vivo (A-15)
        self.capture_exclusion = True                         # F5.3 (A-13): o que `exclude_from_capture` devolve
        self.excluded: list[int] = []                         # as janelas que pediram para sair da captura
        self.text_scale_factor = 1.0                          # F5.4 (A-08): o "Tamanho do texto" do sistema simulado
        # F5.7 (L1): o sensor de mentira; o teste troca a leitura, faz a leitura inteira falhar e muda o indicador
        self.activity: AudioActivity | None = AudioActivity(())
        self.degraded_reading: AudioActivity | None = AudioActivity((), degraded=True)
        self.activity_error: Exception | None = None
        self.activity_reads: list[tuple[frozenset[str], bool]] = []   # (titles_for, degraded) de cada leitura
        self.activity_closed = 0
        self.mic_user_ids: frozenset[str] | None = frozenset()
        self.watch: FakeMicWatch | None = FakeMicWatch()
        self.default_reads = 0                                # D-067: leituras do padrão de agora
        self.played: list[tuple[str, int, int]] = []          # D-067: (endpoint, amostras, taxa) de cada `play_sound`

    # ---- condução dos testes ----
    def pump(self, seconds: float, step: float = 0.1) -> None:
        """Avança o relógio em passos de `step` entregando os blocos de todas as streams em ordem de tempo."""
        target = self.clock.rel() + seconds
        while self.clock.rel() + 1e-9 < target:
            self.clock.advance(min(step, target - self.clock.rel()))
            now = self.clock.rel()
            for s in list(self.streams):
                s.pump(now)

    def live_streams(self) -> list[FakeStream]:
        return [s for s in self.streams if s.started and not s.stopped]

    def settle(self) -> None:
        """F5.3: o PortAudio leu agora (o app abriu assim): a lista e o padrão dele são os do Core Audio de agora."""
        if self.endpoints is not None:
            self._pa_signature = self.endpoints.signature
            self._pa_default = self.endpoints.capture_default or None

    # ---- Platform ----
    @override
    def app_init(self) -> None:
        pass

    @override
    def keep_awake(self, on: bool) -> bool:
        self.awake = on
        return True

    @override
    def bring_to_front(self, window_id: int) -> bool:
        return True

    @override
    def exclude_from_capture(self, window_id: int) -> bool:
        self.excluded.append(int(window_id))
        return self.capture_exclusion

    @override
    def text_scale(self) -> float:
        return self.text_scale_factor

    @override
    def ui_style(self) -> str:
        return "Fusion"                                       # F5.4 (A-03): o offscreen não tem o `windows11`

    @override
    def list_input_devices(self) -> list[AudioDevice]:
        return list(self.devices)

    @override
    def refresh_audio_devices(self, own_streams: int = 0, release: Callable[[], None] | None = None) -> bool:
        """U-043: `next_devices` é o que o "sistema" passou a ter; relê só sem stream de microfone aberto além dos de quem
        pede. Aberto é o que ninguém fechou (A-03): a stream que o driver encerrou (`fail`) segura, como no PortAudio.
        F5.3: também relê quando o Core Audio mudou desde a última leitura (o padrão novo entra aqui)."""
        if not self.audio_devices_changed() or self._held() > own_streams:
            return False
        if release is not None:
            release()
        if self.next_devices is not None:
            self.devices, self.next_devices = list(self.next_devices), None
        if self.endpoints is not None:
            self._pa_signature = self.endpoints.signature
            self._pa_default = self.endpoints.capture_default or None
        self.refreshes += 1
        return True

    def _held(self) -> int:
        return sum(1 for s in self.mic_streams if s.stop_calls == 0) + self.other_streams()

    @override
    def audio_devices_changed(self) -> bool:
        return self.next_devices is not None or (self.endpoints is not None and self.endpoints.signature != self._pa_signature)

    @override
    def audio_endpoints(self, refresh_names: bool = False) -> Endpoints | None:
        if self.next_endpoints is not None:
            self.endpoints, self.next_endpoints = self.next_endpoints, None
        self.endpoint_reads += 1
        return self.endpoints

    @override
    def default_input_device(self) -> AudioDevice | None:
        devices = self.list_input_devices()
        now = self.endpoints.capture_default if self.endpoints is not None else ""      # D-067: o padrão de agora, pelo id
        found = next((d for d in devices if now and d.id == now), None)
        return found or next((d for d in devices if d.id == self._pa_default), devices[0] if devices else None)

    @override
    def default_output_id(self) -> str | None:
        self.default_reads += 1
        return (self.endpoints.render_default or None) if self.endpoints is not None else None

    @override
    def default_input_id(self) -> str | None:
        self.default_reads += 1
        return (self.endpoints.capture_default or None) if self.endpoints is not None else None

    @override
    def open_output(self, endpoint_id: str | None = None) -> FakeOutput:
        out = FakeOutput()
        out.endpoint_id = out.device = endpoint_id or (self.endpoints.render_default if self.endpoints is not None else "")
        return out

    @override
    def play_sound(self, samples: np.ndarray, sample_rate: int, endpoint_id: str | None = None) -> None:
        view, wanted = self.endpoints, endpoint_id or ""     # a saída pedida que está na lista, senão a padrão (R-04)
        self.played.append((wanted if view and view.output(wanted) else (view.render_default if view else ""), len(samples), sample_rate))

    @override
    def list_playing_apps(self) -> list[PlayingApp]:
        return [PlayingApp(pid=4242, exe="meeting.exe", title="Reunião", peak=0.5, is_meeting_app=True)]

    @override
    def audio_activity(self, titles_for: frozenset[str] = frozenset(), degraded: bool = False) -> AudioActivity | None:
        self.activity_reads.append((titles_for, degraded))
        if degraded:
            return self.degraded_reading
        if self.activity_error is not None:
            raise self.activity_error
        return self.activity

    @override
    def close_audio_activity(self) -> None:
        self.activity_closed += 1

    @override
    def mic_users(self) -> frozenset[str] | None:
        return self.mic_user_ids

    @override
    def watched_apps(self) -> tuple[WatchedApp, ...]:
        return FAKE_WATCHED

    @override
    def mic_watch(self) -> FakeMicWatch | None:
        return self.watch

    def _stream(self, sample_rate: int, channels: int, on_audio: AudioCallback, on_error: ErrorCallback | None,
                freq: float, silent: list[tuple[float, float]] | None = None) -> FakeStream:
        s = FakeStream(self.clock, sample_rate, channels, on_audio, on_error, freq=freq, silent_windows=silent)
        self.streams.append(s)
        return s

    @override
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None, strict: bool = False) -> FakeStream:
        if self.mic_fail:
            raise RuntimeError("sem microfone")
        found = self.default_input_device() if device is None else next((d for d in self.devices if d.id == device.id), None)
        if found is None:
            if strict and device is not None:
                raise LookupError(f"o microfone {device.id} não está na lista")
            found = self.default_input_device()
        if found is not None and found.id in self.fail_ids:
            raise RuntimeError("o microfone recusou abrir")
        self.mics_at_open.append(sum(1 for s in self.mic_streams if s.stop_calls == 0))
        stream = self._stream(sample_rate, 1, on_audio, on_error, freq=440.0)
        stream.device_id = found.id if found else ""
        stream.fail_start = stream.device_id in self.start_fail_ids
        self.mic_streams.append(stream)                  # U-043: o que o PortAudio do sounddevice abriria
        return stream

    @override
    def open_app_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> FakeStream:
        if self.app_fail:
            raise RuntimeError("app sem áudio")
        return self._stream(*self.r_format, on_audio, on_error, freq=220.0, silent=self.r_silent_windows)

    @override
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None,
                             output_id: str | None = None) -> FakeStream:
        if self.system_fail:
            raise RuntimeError("loopback do sistema indisponível")
        still_open = sum(1 for s in self.loopbacks if s.stop_calls == 0)
        self.loopbacks_at_open.append(still_open)
        if not still_open or self._lb_view is None:
            self._lb_view = self.endpoints                     # PyAudio novo: a lista de agora (com outro vivo, a antiga)
        view = self._lb_view
        stream = self._stream(*self.system_format, on_audio, on_error, freq=330.0, silent=self.r_silent_windows)
        if view is not None:
            stream.device_id = output_id if output_id and view.output(output_id) is not None else view.render_default
        stream.fail_start = self.system_start_fail
        self.loopbacks.append(stream)
        return stream

    @override
    def mic_mute(self) -> FakeMute:
        return self._mute

    @override
    def hotkeys(self) -> FakeHotkeys:
        return self._hk

    @override
    def root_pid(self, exe: str) -> int | None:
        return 4242 if exe == "meeting.exe" else None

    @override
    def spawn_detached(self, argv: list[str], env: Mapping[str, str] | None = None) -> int:
        self.spawned.append((list(argv), dict(env or {})))
        self.next_pid += 1
        return self.next_pid

    @override
    def spawn_child(self, argv: list[str], env: Mapping[str, str] | None = None) -> ChildProcess:
        self.next_pid += 1
        child = FakeChild(argv, env, self.next_pid)
        self.children.append(child)
        return child

    @override
    def disable_own_power_throttling(self) -> bool:
        return False

    @override
    def sync_stdout_handle(self) -> None:
        pass

    @override
    def lower_own_priority(self) -> bool:
        self.lowered += 1
        return True

    @override
    def memory_rss(self) -> int | None:
        return 123_000_000

    @override
    def config_dir(self) -> Path:
        return self.tmp / "config"

    @override
    def data_dir_suggestion(self) -> Path:
        return self.tmp / "data"

    @override
    def synced_folder_name(self, path: Path) -> str | None:
        from listener.platform.common.paths import synced_folder_name
        return synced_folder_name(path)
