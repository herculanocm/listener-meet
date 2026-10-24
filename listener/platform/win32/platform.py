"""Windows: WASAPI (sounddevice), process loopback (proc-tap), loopback do sistema (PyAudioWPatch),
sessões de áudio e mudo do microfone (pycaw), atalhos (RegisterHotKey), processos (Toolhelp32),
identidade do processo, suspensão e foco (user32, kernel32, shell32)."""
from __future__ import annotations

import ctypes
import time
from pathlib import Path

import numpy as np

from ...log import get_logger
from ..base import AudioCallback, AudioDevice, PlayingApp, pick_device
from ..common import paths
from . import processes
from .hotkeys import Win32Hotkeys

log = get_logger("platform.win32")

APP_USER_MODEL_ID = "listener-meet"
BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
MEETING_EXES = {"ms-teams.exe", "teams.exe", "zoom.exe", "cpthost.exe", "webex.exe", "atmgr.exe",
                "slack.exe", "discord.exe", "skype.exe", "googlemeet.exe"}
MEETING_TITLE_HINTS = ("meet", "teams", "zoom", "webex", "reunião", "meeting", "chamada", "call")
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002


def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception as e:
        log.debug("CoInitialize: %s", e)


def _names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or (len(a) >= 20 and b.startswith(a)) or (len(b) >= 20 and a.startswith(b))


def _capture_endpoints() -> list[tuple[str, str, object]]:
    """[(nome amigável, id estável do endpoint, dispositivo do pycaw)] dos endpoints ativos. Vazio sem pycaw."""
    _com_init()
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception as e:
        log.debug("pycaw indisponível: %s", e)
        return []
    out: list[tuple[str, str, object]] = []
    try:
        for dev in AudioUtilities.GetAllDevices():
            try:
                name, dev_id = str(dev.FriendlyName or ""), str(dev.id or "")
                state = getattr(dev, "state", None)
                active = state is None or str(getattr(state, "name", state)).lower() in ("active", "1")
                if name and dev_id and active:
                    out.append((name, dev_id, dev))
            except Exception as e:
                log.debug("endpoint ilegível: %s", e)
    except Exception as e:
        log.warning("não consegui enumerar os endpoints de áudio: %s", e)
    return out


def _endpoint_volume(dev):
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


def _session_pid(session) -> int:
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
    def __init__(self, stop_fn, sample_rate: int, channels: int):
        self._stop_fn = stop_fn
        self.sample_rate = sample_rate
        self.channels = channels
        self._stopped = False

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            try:
                self._stop_fn()
            except Exception as e:
                log.debug("parar stream: %s", e)


class _Win32MicMute:
    """Muta o endpoint de captura escolhido no warm-up (F1.1 A2); sem ele, o microfone padrão do Windows."""

    def __init__(self) -> None:
        self._device: AudioDevice | None = None
        self._ep = None
        self.resolved: str | None = None

    def set_device(self, device: AudioDevice | None) -> bool:
        self._device, self._ep, self.resolved = device, None, None
        if device is None:
            return True
        ep, name = self._find(device)
        if ep is None:
            log.warning("endpoint de captura %r não encontrado; o mudo global vai no microfone padrão", device.name)
            return False
        self._ep, self.resolved = ep, name
        return True

    def _find(self, device: AudioDevice):
        for name, dev_id, dev in _capture_endpoints():
            if dev_id == device.id or _names_match(name, device.name):
                try:
                    return _endpoint_volume(dev), name
                except Exception as e:
                    log.debug("EndpointVolume de %s: %s", name, e)
        return None, None

    def _default(self):
        _com_init()
        from pycaw.pycaw import AudioUtilities

        mic = AudioUtilities.GetMicrophone()
        return _endpoint_volume(mic) if mic is not None else None

    def _endpoint(self):
        if self._ep is not None:
            return self._ep
        if self._device is not None:
            ep, name = self._find(self._device)
            if ep is not None:
                self._ep, self.resolved = ep, name
                return ep
        return self._default()

    def get(self) -> bool | None:
        try:
            ep = self._endpoint()
            return bool(ep.GetMute()) if ep is not None else None
        except Exception as e:
            log.debug("ler mudo: %s", e)
            self._ep = None
            return None

    def set(self, muted: bool) -> bool:
        try:
            ep = self._endpoint()
            if ep is None:
                return False
            ep.SetMute(1 if muted else 0, None)
            return True
        except Exception as e:
            log.warning("mutar microfone: %s", e)
            self._ep = None
            return False


class Win32Platform:
    name = "win32"

    def __init__(self) -> None:
        self._hotkeys: Win32Hotkeys | None = None
        self._mute = _Win32MicMute()
        self.last_list_apps_ms = 0.0

    # ---------------------------------------------------------------- processo
    def app_init(self) -> None:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception as e:
            log.debug("AppUserModelID: %s", e)

    def keep_awake(self, on: bool) -> bool:
        try:
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        except Exception as e:
            log.debug("SetThreadExecutionState: %s", e)
            return False

    def bring_to_front(self, window_id: int) -> bool:
        try:
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = int(window_id)
            fg = user32.GetForegroundWindow()
            cur = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid and fg_tid != cur and user32.AttachThreadInput(fg_tid, cur, True))
            try:
                user32.keybd_event(VK_MENU, 0, 0, 0)
                user32.BringWindowToTop(hwnd)
                ok = bool(user32.SetForegroundWindow(hwnd))
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(fg_tid, cur, False)
            return ok
        except Exception as e:
            log.debug("bring_to_front: %s", e)
            return False

    # ---------------------------------------------------------------- dispositivos
    def list_input_devices(self) -> list[AudioDevice]:
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
                                   channels=int(d["max_input_channels"]), sample_rate=int(d["default_samplerate"]),
                                   index=i))
        return out

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
        found = pick_device(self.list_input_devices(), device.id, device.name)
        if found is None:
            log.warning("microfone %r não está mais nesta máquina; usando o padrão", device.name)
            dev = self.default_input_device()
            return dev.index if dev else None
        return found.index

    # ---------------------------------------------------------------- apps tocando
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
            try:
                procs = processes.snapshot_processes()
                titles = processes.window_titles_by_pid()
            except Exception as e:
                log.debug("processos e janelas: %s", e)
                procs, titles = {}, {}
            for pid, peak in peaks.items():
                info = procs.get(pid)
                exe = info.name if info else f"pid {pid}"
                root = processes.root_of(pid, procs) if procs else pid
                title = titles.get(pid) or titles.get(root, "")
                low = exe.lower()
                meeting = low in MEETING_EXES or (low in BROWSERS and any(h in title.lower() for h in MEETING_TITLE_HINTS))
                apps.append(PlayingApp(pid=pid, exe=exe, title=title, peak=peak, is_meeting_app=meeting))
            apps.sort(key=lambda a: (not a.is_meeting_app, -a.peak, a.exe.lower()))
        self.last_list_apps_ms = (time.perf_counter() - t0) * 1000
        log.debug("list_playing_apps: %d sessões, %d apps em %.0f ms", len(sessions), len(apps), self.last_list_apps_ms)
        return apps

    # ---------------------------------------------------------------- captura
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback):
        import sounddevice as sd

        idx = self._resolve_index(device)
        extra = sd.WasapiSettings(auto_convert=True)

        def cb(indata, frames, time_info, status):
            now = time.perf_counter()
            try:
                adc, cur = float(time_info.inputBufferAdcTime), float(time_info.currentTime)
                lag = (cur - adc) if (adc > 0 and cur >= adc) else frames / sample_rate
            except Exception:
                lag = frames / sample_rate
            on_audio(np.array(indata, dtype=np.float32, copy=True), now - lag)

        stream = sd.InputStream(device=idx, samplerate=sample_rate, channels=1, dtype="float32",
                                blocksize=max(160, int(sample_rate * 0.1)), callback=cb, extra_settings=extra)
        stream.start()
        log.info("microfone aberto: índice %s a %d Hz", idx, sample_rate)

        def stop():
            stream.stop()
            stream.close()

        return _StreamHandle(stop, sample_rate, 1)

    def open_app_loopback(self, pid: int, on_audio: AudioCallback):
        from proctap import ProcessAudioCapture

        fmt = {"sample_rate": 48000, "channels": 2, "sample_format": "float32"}
        state = {"sr": 48000, "ch": 2, "itemsize": 4, "dtype": np.float32}

        def cb(pcm: bytes, frames: int):
            # proc-tap 1.1.1 passa frames = -1: contamos pelos bytes
            n = len(pcm) // (state["ch"] * state["itemsize"])
            if n <= 0:
                return
            arr = np.frombuffer(pcm, dtype=state["dtype"])[: n * state["ch"]].reshape(n, state["ch"])
            if state["dtype"] == np.int16:
                arr = arr.astype(np.float32) / 32768.0
            on_audio(np.array(arr, dtype=np.float32, copy=True), time.perf_counter() - n / state["sr"])

        tap = ProcessAudioCapture(pid, on_data=cb)
        try:
            fmt = dict(tap.get_format())
        except Exception as e:
            log.debug("formato do proc-tap: %s", e)
        state["sr"] = int(fmt.get("sample_rate", 48000))
        state["ch"] = int(fmt.get("channels", 2))
        if str(fmt.get("sample_format", "float32")).lower() == "int16":
            state["dtype"], state["itemsize"] = np.int16, 2
        tap.start()
        log.info("loopback do processo %s aberto: %d Hz, %d canais", pid, state["sr"], state["ch"])

        def stop():
            try:
                tap.stop()
            finally:
                tap.close()

        return _StreamHandle(stop, state["sr"], state["ch"])

    def open_system_loopback(self, on_audio: AudioCallback):
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

        def cb(in_data, frame_count, time_info, status):
            now = time.perf_counter()
            adc, cur = time_info.get("input_buffer_adc_time", 0.0), time_info.get("current_time", 0.0)
            lag = (cur - adc) if (adc > 0 and cur >= adc) else frame_count / sr
            arr = np.frombuffer(in_data, dtype=np.int16).astype(np.float32).reshape(-1, ch) / 32768.0
            on_audio(arr, now - lag)
            return (None, pyaudio.paContinue)

        stream = pa.open(format=pyaudio.paInt16, channels=ch, rate=sr, input=True, input_device_index=dev["index"],
                         frames_per_buffer=sr // 10, stream_callback=cb)
        stream.start_stream()
        log.info("loopback do sistema aberto: %s, %d Hz, %d canais", dev["name"], sr, ch)

        def stop():
            try:
                stream.stop_stream()
                stream.close()
            finally:
                pa.terminate()

        return _StreamHandle(stop, sr, ch)

    # ---------------------------------------------------------------- controles
    def mic_mute(self):
        return self._mute

    def hotkeys(self):
        if self._hotkeys is None:
            self._hotkeys = Win32Hotkeys()
        return self._hotkeys

    # ---------------------------------------------------------------- processos e pastas
    def root_pid(self, exe: str) -> int | None:
        try:
            return processes.root_pid_for(exe)
        except Exception as e:
            log.debug("root_pid(%s): %s", exe, e)
            return None

    def config_dir(self) -> Path:
        return paths.config_dir()

    def data_dir_suggestion(self) -> Path:
        return paths.data_dir_suggestion()

    def synced_folder_name(self, path: Path) -> str | None:
        return paths.synced_folder_name(path)
