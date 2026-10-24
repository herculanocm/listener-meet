"""Windows: WASAPI (sounddevice), process loopback (proc-tap), loopback do sistema (PyAudioWPatch),
sessões de áudio e mudo do microfone (pycaw), atalhos (pynput), processos (Toolhelp32)."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ..base import AudioCallback, AudioDevice, PlayingApp
from ..common import paths
from ..common.hotkeys import PynputHotkeys
from . import processes

BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
MEETING_EXES = {"ms-teams.exe", "teams.exe", "zoom.exe", "cpthost.exe", "webex.exe", "atmgr.exe",
                "slack.exe", "discord.exe", "skype.exe", "googlemeet.exe"}
MEETING_TITLE_HINTS = ("meet", "teams", "zoom", "webex", "reunião", "meeting", "chamada", "call")


def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass


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
            except Exception:
                pass


class _Win32MicMute:
    def _endpoint(self):
        _com_init()
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return None
        ep = getattr(mic, "EndpointVolume", None)
        if ep is not None:
            return ep
        from comtypes import CLSCTX_ALL

        iface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        try:
            return iface.QueryInterface(IAudioEndpointVolume)
        except Exception:
            from ctypes import POINTER, cast

            return cast(iface, POINTER(IAudioEndpointVolume))

    def get(self) -> bool | None:
        try:
            ep = self._endpoint()
            return bool(ep.GetMute()) if ep is not None else None
        except Exception:
            return None

    def set(self, muted: bool) -> bool:
        try:
            ep = self._endpoint()
            if ep is None:
                return False
            ep.SetMute(1 if muted else 0, None)
            return True
        except Exception:
            return False


class Win32Platform:
    name = "win32"

    def __init__(self) -> None:
        self._hotkeys: PynputHotkeys | None = None
        self._mute = _Win32MicMute()

    # ---------------------------------------------------------------- dispositivos
    def list_input_devices(self) -> list[AudioDevice]:
        import sounddevice as sd

        apis = sd.query_hostapis()
        out: list[AudioDevice] = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            api = apis[d["hostapi"]]["name"]
            if "WASAPI" not in api:         # MME/DirectSound/WDM-KS duplicam os mesmos microfones
                continue
            out.append(AudioDevice(id=str(i), name=d["name"], api="WASAPI",
                                   channels=int(d["max_input_channels"]), sample_rate=int(d["default_samplerate"])))
        return out

    def default_input_device(self) -> AudioDevice | None:
        import sounddevice as sd

        devs = self.list_input_devices()
        if not devs:
            return None
        try:
            default = sd.query_devices(kind="input")
            want = default["name"][:24].lower()
            for d in devs:
                if d.name[:24].lower() == want:
                    return d
        except Exception:
            pass
        return devs[0]

    # ---------------------------------------------------------------- apps tocando
    def list_playing_apps(self) -> list[PlayingApp]:
        _com_init()
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        except Exception:
            return []
        apps: dict[int, PlayingApp] = {}
        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception:
            return []
        for s in sessions:
            proc = getattr(s, "Process", None)
            if proc is None:
                continue
            try:
                pid = int(proc.pid)
                exe = proc.name()
            except Exception:
                continue
            peak = 0.0
            try:
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                peak = float(meter.GetPeakValue())
            except Exception:
                pass
            app = apps.get(pid) or PlayingApp(pid=pid, exe=exe)
            app.peak = max(app.peak, peak)
            apps[pid] = app
        if not apps:
            return []
        try:
            procs = processes.snapshot_processes()
            titles = processes.window_titles_by_pid()
        except Exception:
            procs, titles = {}, {}
        for app in apps.values():
            root = processes.root_of(app.pid, procs) if procs else app.pid
            app.title = titles.get(app.pid) or titles.get(root, "")
            low_exe = app.exe.lower()
            app.is_meeting_app = low_exe in MEETING_EXES or (
                low_exe in BROWSERS and any(h in app.title.lower() for h in MEETING_TITLE_HINTS))
        return sorted(apps.values(), key=lambda a: (not a.is_meeting_app, -a.peak, a.exe.lower()))

    # ---------------------------------------------------------------- captura
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback):
        import sounddevice as sd

        dev = device or self.default_input_device()
        idx = int(dev.id) if dev else None
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
        except Exception:
            pass
        state["sr"] = int(fmt.get("sample_rate", 48000))
        state["ch"] = int(fmt.get("channels", 2))
        if str(fmt.get("sample_format", "float32")).lower() == "int16":
            state["dtype"], state["itemsize"] = np.int16, 2
        tap.start()

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
            self._hotkeys = PynputHotkeys()
        return self._hotkeys

    # ---------------------------------------------------------------- processos e pastas
    def root_pid(self, exe: str) -> int | None:
        try:
            return processes.root_pid_for(exe)
        except Exception:
            return None

    def config_dir(self) -> Path:
        return paths.config_dir()

    def data_dir_suggestion(self) -> Path:
        return paths.data_dir_suggestion()

    def synced_folder_name(self, path: Path) -> str | None:
        return paths.synced_folder_name(path)
