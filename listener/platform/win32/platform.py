"""Windows: WASAPI (sounddevice), process loopback (proc-tap), loopback do sistema (PyAudioWPatch),
sessões de áudio e mudo do microfone (pycaw), atalhos (RegisterHotKey), processos (Toolhelp32),
identidade do processo, suspensão e foco (user32, kernel32, shell32).

Streams voltam paradas (`CaptureHandle.start()`, M-06). COM: `_Win32MicMute` lembra a busca do endpoint e refaz o
ponteiro quando a thread muda (M-07); enumerações com cache curto (M-07, M-12). Nada daqui roda fora do Windows."""
from __future__ import annotations

import ctypes
import functools
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, override

import numpy as np

from ...capture.mixer import FrameMixer
from ...log import get_logger
from ..base import AudioCallback, AudioDevice, ChildProcess, ErrorCallback, MicMute, Platform, PlayingApp, pick_device
from ..common import paths, process
from . import processes
from .hotkeys import Win32Hotkeys

log = get_logger("platform.win32")

APP_USER_MODEL_ID = "listener-meet"
BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
MEETING_EXES = {"ms-teams.exe", "teams.exe", "zoom.exe", "cpthost.exe", "webex.exe", "atmgr.exe",
                "slack.exe", "discord.exe", "skype.exe", "googlemeet.exe"}
MEETING_TITLE_HINTS = ("meet", "teams", "zoom", "webex", "reunião", "meeting", "chamada", "call")
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
CREATE_NO_WINDOW, DETACHED_PROCESS, BELOW_NORMAL_PRIORITY_CLASS = 0x08000000, 0x00000008, 0x00004000   # worker da F2
PROCESS_SET_INFORMATION = 0x0200                       # F4: EcoQoS do filho ao vivo (SetProcessInformation)
PROCESS_POWER_THROTTLING = 4                           # ProcessPowerThrottling (PROCESS_INFORMATION_CLASS)
POWER_THROTTLING_VERSION, POWER_THROTTLING_EXECUTION_SPEED = 1, 0x1
STD_OUTPUT_HANDLE = -11 & 0xFFFFFFFF                   # (DWORD)-11
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
DEVICE_CACHE_S = 2.0           # lista de microfones (COM + PortAudio)
PROCESS_CACHE_S = 3.0          # snapshot Toolhelp32 e títulos de janela durante o polling de apps
MUTE_RETRY_S = 30.0            # após falhar a busca do endpoint do mudo, só tenta de novo depois disto


class ProcessMemoryCounters(ctypes.Structure):          # PROCESS_MEMORY_COUNTERS do psapi
    _fields_ = (("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t))


class ProcessPowerThrottlingState(ctypes.Structure):    # PROCESS_POWER_THROTTLING_STATE
    _fields_ = (("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong))


def disable_power_throttling(pid: int) -> bool:
    """EcoQoS desligado no processo `pid` (F4): `ControlMask` = velocidade de execução e `StateMask` = 0 pedem "nunca
    estrangular", senão o Windows 11 tende a mandar um processo abaixo do normal para os núcleos de eficiência [exp].
    Instância própria do kernel32 (`WinDLL`) para não mexer nas assinaturas que outros módulos fixaram."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.SetProcessInformation.restype = wintypes.BOOL
        k32.SetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = k32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            log.info("EcoQoS não desligado no processo %s: OpenProcess falhou (erro %s)", pid, ctypes.get_last_error())
            return False
        try:
            state = ProcessPowerThrottlingState(POWER_THROTTLING_VERSION, POWER_THROTTLING_EXECUTION_SPEED, 0)
            ok = bool(k32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.addressof(state), ctypes.sizeof(state)))
        finally:
            k32.CloseHandle(handle)
    except Exception as e:
        log.info("EcoQoS não desligado no processo %s: %s", pid, e)
        return False
    if not ok:
        log.info("EcoQoS não desligado no processo %s: SetProcessInformation falhou (erro %s)", pid, ctypes.get_last_error())
    return ok


def _own_process_kernel32() -> Any:
    """kernel32 com as assinaturas das chamadas sobre o próprio processo. Sem `restype`, o pseudo-handle de
    `GetCurrentProcess` (-1) volta como `c_long` e chega truncado (0xFFFFFFFF) no Windows de 64 bits: `SetPriorityClass` e
    `GetProcessMemoryInfo` falhavam com ERROR_INVALID_HANDLE (visto no Windows em 25/10). Instância própria, como acima."""
    if sys.platform != "win32":
        raise OSError("kernel32 só existe no Windows")
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = ()
    k32.SetPriorityClass.restype = wintypes.BOOL
    k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD)
    return k32


def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception as e:
        log.debug("CoInitialize: %s", e)


def _names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or (len(a) >= 20 and b.startswith(a)) or (len(b) >= 20 and a.startswith(b))


def _capture_endpoints() -> list[tuple[str, str, Any]]:
    """[(nome amigável, id estável do endpoint, dispositivo do pycaw)] dos endpoints de captura ativos. Vazio sem pycaw.
    Enumera só `eCapture` ativos (M-07); se a API filtrada falhar, cai em `GetAllDevices()` filtrando o estado."""
    _com_init()
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception as e:
        log.debug("pycaw indisponível: %s", e)
        return []
    out: list[tuple[str, str, Any]] = []
    devices: list[Any] = []
    try:
        from pycaw.constants import DEVICE_STATE, EDataFlow

        enumerator = AudioUtilities.GetDeviceEnumerator()
        collection = enumerator.EnumAudioEndpoints(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
        devices = [AudioUtilities.CreateDevice(collection.Item(i)) for i in range(collection.GetCount())]
    except Exception as e:
        log.debug("enumeração filtrada de endpoints falhou (%s); usando GetAllDevices", e)
        try:
            devices = list(AudioUtilities.GetAllDevices())
        except Exception as e2:
            log.warning("não consegui enumerar os endpoints de áudio: %s", e2)
            return []
    for dev in devices:
        try:
            name, dev_id = str(dev.FriendlyName or ""), str(dev.id or "")
            state = getattr(dev, "state", None)
            active = state is None or str(getattr(state, "name", state)).lower() in ("active", "1")
            if name and dev_id and active:
                out.append((name, dev_id, dev))
        except Exception as e:
            log.debug("endpoint ilegível: %s", e)
    return out


def _endpoint_volume(dev: Any) -> Any:
    ep = getattr(dev, "EndpointVolume", None)
    if ep is not None:
        return ep
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    imm = getattr(dev, "_dev", dev)
    iface = imm.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    try:
        return iface.QueryInterface(IAudioEndpointVolume)
    except Exception:
        from ctypes import POINTER, cast

        return cast(iface, POINTER(IAudioEndpointVolume))


def _session_pid(session: Any) -> int:
    try:
        pid = int(getattr(session, "ProcessId", 0) or 0)
        if pid:
            return pid
    except Exception as e:
        log.debug("ProcessId da sessão: %s", e)
    try:
        return int(session._ctl.GetProcessId())
    except Exception:
        return 0


class _StreamHandle:
    """`CaptureHandle`: começa parada; `stop()` é idempotente e silencia o `on_error` de um encerramento nosso."""

    def __init__(self, start_fn: Callable[[], None], stop_fn: Callable[[], None], sample_rate: int, channels: int,
                 on_error: ErrorCallback | None = None):
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_error = on_error
        self._started = False
        self._stopped = False

    def start(self) -> None:
        if not self._started and not self._stopped:
            self._started = True
            self._start_fn()

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            try:
                self._stop_fn()
            except Exception as e:
                log.debug("parar stream: %s", e)

    def finished(self) -> None:
        """Chamado pela biblioteca quando a stream termina: só é erro se não fomos nós que paramos."""
        if not self._stopped and self.on_error is not None:
            self._stopped = True
            try:
                self.on_error("stream encerrada pelo sistema (dispositivo removido?)")
            except Exception as e:
                log.debug("on_error: %s", e)


class _Win32MicMute(MicMute):
    """Muta o endpoint de captura escolhido no warm-up (F1.1 A2); sem ele, o microfone padrão do Windows.
    Lembra o resultado da busca (não re-enumera a cada `get()`), espera 30 s após uma falha e refaz o ponteiro COM
    quando é usado por outra thread (M-07)."""

    def __init__(self) -> None:
        self._device: AudioDevice | None = None
        self._ep: Any = None
        self._ep_thread: int | None = None
        self._lookup_done = False
        self._retry_after = 0.0
        self.resolved: str | None = None

    @override
    def set_device(self, device: AudioDevice | None) -> bool:
        self._device, self._ep, self._ep_thread, self.resolved = device, None, None, None
        self._lookup_done, self._retry_after = False, 0.0
        if device is None:
            return True
        return self._lookup() is not None

    def _lookup(self) -> Any:
        """Procura o endpoint do dispositivo escolhido uma vez (ou após o backoff); guarda o resultado."""
        if self._device is None:
            return None
        if self._lookup_done and (self._ep is not None or time.monotonic() < self._retry_after):
            return self._ep
        self._lookup_done = True
        for name, dev_id, dev in _capture_endpoints():
            if dev_id == self._device.id or _names_match(name, self._device.name):
                try:
                    ep = _endpoint_volume(dev)
                except Exception as e:
                    log.debug("EndpointVolume de %s: %s", name, e)
                else:
                    self._ep, self.resolved, self._ep_thread = ep, name, threading.get_ident()
                    return ep
        self._retry_after = time.monotonic() + MUTE_RETRY_S
        log.warning("endpoint de captura %r não encontrado; o mudo global vai no microfone padrão", self._device.name)
        return None

    def _default(self) -> Any:
        _com_init()
        from pycaw.pycaw import AudioUtilities

        mic = AudioUtilities.GetMicrophone()
        return _endpoint_volume(mic) if mic is not None else None

    def _endpoint(self) -> Any:
        if self._ep is not None and self._ep_thread != threading.get_ident():
            _com_init()
            self._ep, self._lookup_done = None, False      # ponteiro COM de outra thread: refaz nesta
        ep = self._lookup() if self._device is not None else None
        return ep if ep is not None else self._default()

    @override
    def get(self) -> bool | None:
        try:
            ep = self._endpoint()
            return bool(ep.GetMute()) if ep is not None else None
        except Exception as e:
            log.debug("ler mudo: %s", e)
            self._ep, self._retry_after = None, time.monotonic() + MUTE_RETRY_S
            return None

    @override
    def set(self, muted: bool) -> bool:
        try:
            ep = self._endpoint()
            if ep is None:
                return False
            ep.SetMute(1 if muted else 0, None)
        except Exception as e:
            log.warning("mutar microfone: %s", e)
            self._ep, self._retry_after = None, time.monotonic() + MUTE_RETRY_S
            return False
        else:
            return True


class Win32Platform(Platform):
    name = "win32"

    def __init__(self) -> None:
        self._hotkeys: Win32Hotkeys | None = None
        self._mute = _Win32MicMute()
        self.last_list_apps_ms = 0.0
        self._devices_cache: tuple[float, list[AudioDevice]] | None = None
        self._procs_cache: tuple[float, dict[int, processes.ProcInfo], dict[int, str]] | None = None

    # ---------------------------------------------------------------- processo
    @override
    def app_init(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception as e:
            log.debug("AppUserModelID: %s", e)

    @override
    def keep_awake(self, on: bool) -> bool:
        if sys.platform != "win32":
            return False
        import ctypes

        try:
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        except Exception as e:
            log.debug("SetThreadExecutionState: %s", e)
            return False

    @override
    def bring_to_front(self, window_id: int) -> bool:
        if sys.platform != "win32":
            return False
        import ctypes

        try:
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = int(window_id)
            fg = user32.GetForegroundWindow()
            cur = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid and fg_tid != cur and user32.AttachThreadInput(fg_tid, cur, True))
            ok = False
            user32.keybd_event(VK_MENU, 0, 0, 0)
            try:
                user32.BringWindowToTop(hwnd)
                ok = bool(user32.SetForegroundWindow(hwnd))
                user32.SetFocus(hwnd)
            finally:
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)   # nunca deixar o Alt preso (M-12)
                if attached:
                    user32.AttachThreadInput(fg_tid, cur, False)
        except Exception as e:
            log.debug("bring_to_front: %s", e)
            return False
        else:
            return ok

    # ---------------------------------------------------------------- dispositivos
    @override
    def list_input_devices(self) -> list[AudioDevice]:
        now = time.monotonic()
        if self._devices_cache is not None and now - self._devices_cache[0] < DEVICE_CACHE_S:
            return list(self._devices_cache[1])
        import sounddevice as sd

        apis = sd.query_hostapis()
        endpoints = _capture_endpoints()
        out: list[AudioDevice] = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            if "WASAPI" not in apis[d["hostapi"]]["name"]:   # MME/DirectSound/WDM-KS duplicam os mesmos microfones
                continue
            stable = next((dev_id for name, dev_id, _ in endpoints if _names_match(name, d["name"])), None)
            out.append(AudioDevice(id=stable or f"name:{d['name']}", name=d["name"], api="WASAPI",
                                   channels=int(d["max_input_channels"]), sample_rate=int(d["default_samplerate"]), index=i))
        self._devices_cache = (now, out)
        return list(out)

    def _invalidate_devices(self) -> None:
        self._devices_cache = None

    @override
    def default_input_device(self) -> AudioDevice | None:
        import sounddevice as sd

        devs = self.list_input_devices()
        if not devs:
            return None
        try:
            for api in sd.query_hostapis():
                if "WASAPI" in api["name"]:
                    idx = int(api["default_input_device"])
                    for d in devs:
                        if d.index == idx:
                            return d
        except Exception as e:
            log.debug("dispositivo padrão WASAPI: %s", e)
        return devs[0]

    def _resolve_index(self, device: AudioDevice | None) -> int | None:
        import sounddevice as sd

        if device is None:
            dev = self.default_input_device()
            return dev.index if dev else None
        if device.index is not None:
            try:
                if sd.query_devices(device.index)["name"] == device.name:
                    return device.index
            except Exception as e:
                log.debug("índice %s inválido: %s", device.index, e)
        self._invalidate_devices()                         # pode ter mudado (dock, USB): enumera de novo
        found = pick_device(self.list_input_devices(), device.id, device.name)
        if found is None:
            log.warning("microfone %r não está mais nesta máquina; usando o padrão", device.name)
            dev = self.default_input_device()
            return dev.index if dev else None
        return found.index

    # ---------------------------------------------------------------- apps tocando
    def _processes(self) -> tuple[dict[int, processes.ProcInfo], dict[int, str]]:
        """Snapshot de processos e títulos com cache de 3 s: os picos mudam a cada poll, os processos não (M-12)."""
        now = time.monotonic()
        if self._procs_cache is not None and now - self._procs_cache[0] < PROCESS_CACHE_S:
            return self._procs_cache[1], self._procs_cache[2]
        try:
            procs = processes.snapshot_processes()
            titles = processes.window_titles_by_pid()
        except Exception as e:
            log.debug("processos e janelas: %s", e)
            procs, titles = {}, {}
        self._procs_cache = (now, procs, titles)
        return procs, titles

    @override
    def list_playing_apps(self) -> list[PlayingApp]:
        t0 = time.perf_counter()
        _com_init()
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        except Exception as e:
            log.debug("pycaw indisponível: %s", e)
            return []
        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception as e:
            log.warning("sessões de áudio: %s", e)
            return []
        peaks: dict[int, float] = {}
        for s in sessions:
            pid = _session_pid(s)
            if not pid:
                continue
            peak = 0.0
            try:
                peak = float(s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
            except Exception as e:
                log.debug("pico da sessão %s: %s", pid, e)
            peaks[pid] = max(peaks.get(pid, 0.0), peak)
        apps: list[PlayingApp] = []
        if peaks:
            procs, titles = self._processes()
            # D-055: uma linha por app, com o pid da raiz (o Teams novo toca a chamada num WebView2 fora da árvore do
            # ms-teams.exe que tem a outra sessão); o loopback captura a árvore inteira da raiz
            for root, (peak, members) in processes.group_by_app(peaks, procs).items():
                info = procs.get(root)
                exe = info.name if info else f"pid {root}"
                title = titles.get(root) or next((titles[m] for m in members if titles.get(m)), "")
                low = exe.lower()
                meeting = low in MEETING_EXES or (low in BROWSERS and any(h in title.lower() for h in MEETING_TITLE_HINTS))
                apps.append(PlayingApp(pid=root, exe=exe, title=title, peak=peak, is_meeting_app=meeting))
            apps.sort(key=lambda a: (not a.is_meeting_app, -a.peak, a.exe.lower()))
        self.last_list_apps_ms = (time.perf_counter() - t0) * 1000
        log.debug("list_playing_apps: %d sessões, %d apps em %.0f ms", len(sessions), len(apps), self.last_list_apps_ms)
        return apps

    # ---------------------------------------------------------------- captura
    @override
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None) -> _StreamHandle:
        import sounddevice as sd

        try:
            idx = self._resolve_index(device)
        except Exception:
            self._invalidate_devices()
            raise
        extra = sd.WasapiSettings(auto_convert=True)
        handle: _StreamHandle | None = None

        def cb(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            now = time.perf_counter()
            try:
                adc, cur = float(time_info.inputBufferAdcTime), float(time_info.currentTime)
                lag = (cur - adc) if (adc > 0 and cur >= adc) else frames / sample_rate
            except Exception:
                lag = frames / sample_rate
            if status and status.input_overflow:
                log.debug("microfone: input_overflow")
            on_audio(np.array(indata, dtype=np.float32, copy=True), now - lag)

        def finished() -> None:
            if handle is not None:
                handle.finished()

        stream = sd.InputStream(device=idx, samplerate=sample_rate, channels=1, dtype="float32",
                                blocksize=max(160, int(sample_rate * 0.1)), callback=cb, extra_settings=extra,
                                finished_callback=finished)

        def start() -> None:
            stream.start()
            log.info("microfone aberto: índice %s a %d Hz", idx, sample_rate)

        def stop() -> None:
            stream.stop()
            stream.close()

        handle = _StreamHandle(start, stop, sample_rate, 1, on_error)
        return handle

    def _session_pids(self) -> set[int]:
        """Os processos com sessão de áudio agora (qualquer estado), para os alvos do loopback do app."""
        _com_init()
        try:
            from pycaw.pycaw import AudioUtilities

            return {pid for s in AudioUtilities.GetAllSessions() if (pid := _session_pid(s))}
        except Exception as e:
            log.debug("sessões de áudio para o loopback: %s", e)
            return set()

    @override
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

    @override
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> _StreamHandle:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        dev = speakers if speakers.get("isLoopbackDevice") else next(
            (lb for lb in pa.get_loopback_device_info_generator() if speakers["name"] in lb["name"]), None)
        if dev is None:
            pa.terminate()
            raise RuntimeError("nenhum dispositivo de loopback WASAPI encontrado")
        sr, ch = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])

        def cb(in_data: bytes, frame_count: int, time_info: dict[str, float], _status: int) -> tuple[None, int]:
            now = time.perf_counter()
            adc, cur = time_info.get("input_buffer_adc_time", 0.0), time_info.get("current_time", 0.0)
            lag = (cur - adc) if (adc > 0 and cur >= adc) else frame_count / sr
            arr = np.frombuffer(in_data, dtype=np.float32).reshape(-1, ch).copy()    # float32 nativo do WASAPI (M-11)
            on_audio(arr, now - lag)
            return (None, pyaudio.paContinue)

        stream = pa.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True, input_device_index=dev["index"],
                         frames_per_buffer=sr // 10, stream_callback=cb, start=False)

        def start() -> None:
            stream.start_stream()
            log.info("loopback do sistema aberto: %s, %d Hz, %d canais", dev["name"], sr, ch)

        def stop() -> None:
            try:
                stream.stop_stream()
                stream.close()
            finally:
                pa.terminate()

        return _StreamHandle(start, stop, sr, ch, on_error)

    # ---------------------------------------------------------------- controles
    @override
    def mic_mute(self) -> _Win32MicMute:
        return self._mute

    @override
    def hotkeys(self) -> Win32Hotkeys:
        if self._hotkeys is None:
            self._hotkeys = Win32Hotkeys()
        return self._hotkeys

    # ---------------------------------------------------------------- processos e pastas
    @override
    def root_pid(self, exe: str) -> int | None:
        try:
            return processes.root_pid_for(exe)
        except Exception as e:
            log.debug("root_pid(%s): %s", exe, e)
            return None

    @override
    def spawn_detached(self, argv: list[str], env: Mapping[str, str] | None = None) -> int:
        """Sem console, solto do app e já em prioridade abaixo do normal (F2 item 2)."""
        if sys.platform != "win32":
            return process.spawn_detached(argv, env)
        flags = CREATE_NO_WINDOW | DETACHED_PROCESS | BELOW_NORMAL_PRIORITY_CLASS
        p = subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.DEVNULL,  # noqa: S603
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
        return int(p.pid)

    @override
    def spawn_child(self, argv: list[str], env: Mapping[str, str] | None = None) -> ChildProcess:
        """F4: sem console (`CREATE_NO_WINDOW`), abaixo do normal e preso ao app (sem `DETACHED_PROCESS`), com stdin e stdout
        binários e stderr descartado; EcoQoS desligado logo depois de nascer (falha só vai para o log)."""
        if sys.platform != "win32":
            return process.spawn_child(argv, env)
        flags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        return subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.PIPE,  # noqa: S603
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)

    @override
    def disable_own_power_throttling(self) -> bool:
        """O filho ao vivo se tira do EcoQoS ao nascer: num venv, `.venv\\Scripts\\python.exe` é um lançador que cria o
        interpretador como neto, e o EcoQoS (ao contrário da prioridade abaixo do normal) não passa para ele."""
        return disable_power_throttling(os.getpid())

    @override
    def sync_stdout_handle(self) -> None:
        """Num processo sem console (`pythonw`) a UCRT não chama `SetStdHandle` no `dup2`: o handle padrão continuaria
        sendo o do fd 1 antigo, já fechado (e reaproveitável), e código nativo que escreva nele erraria o destino."""
        if sys.platform != "win32":
            return
        import msvcrt
        from ctypes import wintypes

        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
            k32.SetStdHandle.restype = wintypes.BOOL
            if not k32.SetStdHandle(STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1)):
                log.info("handle padrão da saída não sincronizado (erro %s)", ctypes.get_last_error())
        except Exception as e:
            log.info("handle padrão da saída não sincronizado: %s", e)

    @override
    def lower_own_priority(self) -> bool:
        """`SetPriorityClass(BELOW_NORMAL)`; nunca `PROCESS_MODE_BACKGROUND_BEGIN` (derruba I/O e o SQLite sofre, D-F2-14)."""
        if sys.platform != "win32":
            return process.lower_own_priority()
        try:
            k32 = _own_process_kernel32()
            if k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
                return True
            log.info("SetPriorityClass falhou (erro %s)", ctypes.get_last_error())
        except Exception as e:
            log.info("SetPriorityClass: %s", e)
        return False

    @override
    def memory_rss(self) -> int | None:
        if sys.platform != "win32":
            return process.memory_rss()
        try:
            k32 = _own_process_kernel32()
            pmc = ProcessMemoryCounters()
            pmc.cb = ctypes.sizeof(pmc)
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
            log.debug("GetProcessMemoryInfo falhou (erro %s)", ctypes.get_last_error())
        except Exception as e:
            log.debug("GetProcessMemoryInfo: %s", e)
        return None

    @override
    def config_dir(self) -> Path:
        return paths.config_dir()

    @override
    def data_dir_suggestion(self) -> Path:
        return paths.data_dir_suggestion()

    @override
    def synced_folder_name(self, path: Path) -> str | None:
        return paths.synced_folder_name(path)
