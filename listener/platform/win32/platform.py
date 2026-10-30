"""Windows: WASAPI (sounddevice), process loopback (proc-tap), loopback do sistema (PyAudioWPatch),
sessões de áudio e mudo do microfone (pycaw), atalhos (RegisterHotKey), processos (Toolhelp32),
identidade do processo, suspensão, foco e (F5.3) janela fora da captura de tela (user32, kernel32, shell32).

Streams voltam paradas (`CaptureHandle.start()`, M-06). COM: o mudo (`mute.py`) lembra a busca do endpoint e refaz o
ponteiro quando a thread muda (M-07); enumerações com cache curto (M-07, M-12). Nada daqui roda fora do Windows.
F5.3 L4: os endpoints pelo Core Audio sem reler o PortAudio (`endpoints.py`, A-19); o handle diz o endpoint aberto e o
microfone pedido pode ser exigido (`strict`, A-16); o loopback do sistema inteiro abre na saída escolhida, casada pelo
nome na lista do PyAudioWPatch (A-21); o mudo troca de endpoint de uma vez, sob trava (A-18); log com o id do endpoint,
nunca o nome do aparelho (A-22; o id `name:<nome>`, de quando o Core Audio não casa o nome, vai resumido: `log_id`).
F5.9 (L1a): o handle das streams em `stream.py`, o loopback do app em `app_loopback.py` (um mixin desta classe), o do
sistema em `system_loopback.py` e o mudo em `mute.py`; aqui ficam o microfone, os dispositivos e o processo."""
from __future__ import annotations

import ctypes
import functools
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, override

import numpy as np

from ...log import get_logger
from ..base import (NAME_ID_PREFIX, AudioCallback, AudioDevice, ChildProcess, Endpoints, ErrorCallback, Platform, PlayingApp,
                    log_id, pick_device, text_scale_from_percent)
from ..common import paths, portaudio, process
from . import activity, app_loopback, endpoints, mute, output, processes, sessions, system_loopback
from .hotkeys import Win32Hotkeys
from .stream import _StreamHandle

log = get_logger("platform.win32")

APP_USER_MODEL_ID = "listener-meet"
BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
MEETING_EXES = {"ms-teams.exe", "teams.exe", "zoom.exe", "cpthost.exe", "webex.exe", "atmgr.exe",
                "slack.exe", "discord.exe", "skype.exe", "googlemeet.exe"}
MEETING_TITLE_HINTS = ("meet", "teams", "zoom", "webex", "reunião", "meeting", "chamada", "call")
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
CREATE_NO_WINDOW, BELOW_NORMAL_PRIORITY_CLASS = 0x08000000, 0x00004000   # worker da F2 e ao vivo da F4
CHILD_FLAGS, STARTF_FORCEOFFFEEDBACK = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS, 0x00000080   # F5.8: sem o cursor "carregando"
PROCESS_SET_INFORMATION = 0x0200                       # F4: EcoQoS do filho ao vivo (SetProcessInformation)
PROCESS_POWER_THROTTLING = 4                           # ProcessPowerThrottling (PROCESS_INFORMATION_CLASS)
POWER_THROTTLING_VERSION, POWER_THROTTLING_EXECUTION_SPEED = 1, 0x1
STD_OUTPUT_HANDLE = -11 & 0xFFFFFFFF                   # (DWORD)-11
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
WDA_EXCLUDEFROMCAPTURE = 0x00000011                    # F5.3 (A-13): SetWindowDisplayAffinity, Windows 10 2004 ou mais novo
DEVICE_CACHE_S = 2.0           # lista de microfones (COM + PortAudio)
SLOW_LIST_APPS_MS = 500.0      # a lista de apps com som acima disto vai para o log mesmo sem mudar
# SetConsoleCtrlHandler: o que fechou o terminal do app (os demais encerram o processo em ~5 s sem passar pelo "Sair")
CTRL_EVENTS = {0: "Ctrl+C no terminal do app", 1: "Ctrl+Break no terminal do app", 2: "o terminal do app foi fechado",
               5: "o usuário saiu do Windows", 6: "o Windows está desligando"}
_CONSOLE_HANDLERS: list[Any] = []                      # o callback precisa viver o processo inteiro (o ctypes não o segura)
PROCESS_CACHE_S = 3.0          # snapshot Toolhelp32 e títulos de janela durante o polling de apps
ACCESSIBILITY_KEY = r"Software\Microsoft\Accessibility"   # F5.4 (A-08): o "Tamanho do texto", em HKCU
WIN11_BUILD = 22000            # F5.4: o Qt só escolhe o `windows11` a partir daqui; antes, o `windowsvista`


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


@functools.cache
def _display_affinity() -> Any:
    """`SetWindowDisplayAffinity` com a assinatura declarada (F5.3, A-13); instância própria do user32, como o kernel32 acima:
    sem `argtypes`, o HWND de 64 bits chegaria truncado."""
    if sys.platform != "win32":
        raise OSError("user32 só existe no Windows")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    fn = user32.SetWindowDisplayAffinity
    fn.argtypes = (wintypes.HWND, wintypes.DWORD)
    fn.restype = wintypes.BOOL
    return fn


def _log_console_events() -> None:
    """Fechar o terminal que hospeda o app, sair do Windows ou desligá-lo encerra o processo em ~5 s sem passar pelo "Sair"
    (o app sumia do log sem o "app encerrado", pergunta 9 da D-055): aqui o motivo vai para o log antes. O handler devolve
    False: o tratamento padrão segue (encerrar; o Ctrl+C vira o KeyboardInterrupt do Python)."""
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    from ...log import drain

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(event: int) -> bool:
        what = CTRL_EVENTS.get(int(event), f"evento de console {event}")
        if int(event) in (0, 1):
            log.info("%s", what)
        else:
            log.warning("app encerrando: %s (sem passar pelo Sair; uma gravação em curso fica interrompida e é preservada)", what)
        with suppress(Exception):
            drain()                                        # R-08: só esvazia a fila; nada de parar o listener noutra thread
        return False

    callback = proto(handler)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetConsoleCtrlHandler.argtypes = (proto, wintypes.BOOL)
    k32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    if k32.SetConsoleCtrlHandler(callback, True):
        _CONSOLE_HANDLERS.append(callback)
    else:
        log.debug("SetConsoleCtrlHandler: erro %s (sem console?)", ctypes.get_last_error())


def _endpoint_signature() -> tuple[Any, ...] | None:
    """U-043: os endpoints ativos de captura e de saída e os padrões de cada um (console e comunicação), pelo Core Audio:
    muda quando o Windows ganha ou perde um dispositivo ou troca o padrão (~19 ms, 27/10). None sem pycaw ou se a
    enumeração falhar (aí nada é relido). F5.3: a enumeração mora em `endpoints.py`, com a leitura dos nomes (A-19)."""
    return endpoints.signature()


class Win32Platform(activity.Win32Activity, output.Win32Output, app_loopback.Win32AppLoopback, Platform):
    name = "win32"

    def __init__(self) -> None:
        self._hotkeys: Win32Hotkeys | None = None
        self._mute = mute._Win32MicMute()
        self.last_list_apps_ms = 0.0
        self._devices_cache: tuple[float, list[AudioDevice]] | None = None
        self._audio_signature: tuple[Any, ...] | None = None    # U-043: os endpoints na última leitura do PortAudio
        self._indices: dict[str, int] = {}                       # id -> índice nesta leitura do PortAudio (A-02)
        self._endpoints = endpoints.EndpointReader()             # F5.3 (A-19): o Core Audio, com os nomes em cache
        self._render_sessions = sessions.FlowSessions(endpoints.FLOW_RENDER)   # F5.7: todas as saídas, sem guardar COM
        self._apps_sig: tuple[tuple[int, str], ...] | None = None   # o último conjunto de apps com som que foi para o log
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
        _log_console_events()

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

    @override
    def exclude_from_capture(self, window_id: int) -> bool:
        """F5.3 (A-13): `WDA_EXCLUDEFROMCAPTURE` tira a janela de toda captura (o Teams compartilhando a tela, gravadores,
        print) e ela segue na tela. Antes do Windows 10 2004, ou se o Windows recusar, False (o erro vai ao DEBUG)."""
        if sys.platform != "win32":
            return False
        try:
            ok = bool(_display_affinity()(int(window_id), WDA_EXCLUDEFROMCAPTURE))
        except Exception as e:
            log.debug("SetWindowDisplayAffinity: %s", e)
            return False
        if not ok:
            log.debug("SetWindowDisplayAffinity recusou a janela %#x (erro %s)", int(window_id), ctypes.get_last_error())
        return ok

    # ---------------------------------------------------------------- aparência (F5.4)
    @override
    def text_scale(self) -> float:
        """A-08 (U-052): o `TextScaleFactor` (DWORD de 100 a 225) da chave de acessibilidade do usuário; ausente ou ilegível
        (o "Tamanho do texto" nunca mexido não cria o valor), 1,0."""
        if sys.platform != "win32":
            return 1.0
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ACCESSIBILITY_KEY) as key:
                value, kind = winreg.QueryValueEx(key, "TextScaleFactor")
        except OSError:
            return 1.0
        return text_scale_from_percent(value) if kind == winreg.REG_DWORD else 1.0

    @override
    def ui_style(self) -> str:
        """A-01: o `windows11` do Qt, o que ele próprio escolhe no Windows 11; no 10, o `windowsvista`, como o Qt (o
        `windows11` desenha as setas e marcas com a Segoe Fluent Icons, que o 10 não tem)."""
        if sys.platform != "win32":
            return "Fusion"
        return "windows11" if sys.getwindowsversion().build >= WIN11_BUILD else "windowsvista"

    # ---------------------------------------------------------------- dispositivos
    @override
    def list_input_devices(self) -> list[AudioDevice]:
        now = time.monotonic()
        if self._devices_cache is not None and now - self._devices_cache[0] < DEVICE_CACHE_S:
            return list(self._devices_cache[1])
        self.refresh_audio_devices()                       # U-043: o fone conectado com o app aberto entra na lista
        import sounddevice as sd

        with portaudio.LOCK:
            apis = sd.query_hostapis()
            devices = sd.query_devices()
        caps = endpoints.capture_endpoints()               # F5.9 (L1a): em `endpoints.py`
        ids = self.wasapi_ids_now() or {}                  # R-02: o id exato desta leitura; sem ele, pelo nome
        out: list[AudioDevice] = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] <= 0:
                continue
            if "WASAPI" not in apis[d["hostapi"]]["name"]:   # MME/DirectSound/WDM-KS duplicam os mesmos microfones
                continue
            found = endpoints.by_name(caps, d["name"], lambda c: str(c[0]))   # F5.9 (item 5): o nome igual, e um só
            stable = ids.get(i) or (found[1] if found is not None else None)
            out.append(AudioDevice(id=stable or f"{NAME_ID_PREFIX}{d['name']}", name=d["name"], api="WASAPI",
                                   channels=int(d["max_input_channels"]), sample_rate=int(d["default_samplerate"]), index=i))
        self._devices_cache = (now, out)
        self._indices = {d.id: d.index for d in out if d.index is not None}
        return list(out)

    def _invalidate_devices(self) -> None:
        self._devices_cache = None

    @override
    def refresh_audio_devices(self, own_streams: int = 0, release: Callable[[], None] | None = None) -> bool:
        """U-043: relê os dispositivos de áudio quando o Windows ganhou ou perdeu um, ou trocou o padrão, desde a última
        leitura do PortAudio (que só lê ao iniciar). Só na thread da interface e sem stream aberto além dos `own_streams`
        de quem pede, que `release` fecha logo antes do reinício (o medidor do warm-up). True quando a lista foi relida
        (a primeira chamada relê sempre: o PortAudio pode ter iniciado antes de o fone chegar)."""
        if threading.current_thread() is not threading.main_thread():
            return False
        sig = _endpoint_signature()
        if sig is None or sig == self._audio_signature:
            return False
        with portaudio.LOCK:
            if portaudio.open_streams() > own_streams:
                return False                               # alguém toca ou grava: fica para a próxima
            if release is not None:
                release()
            if not portaudio.restart():
                return False
            self.remember_wasapi_ids()                     # R-02: na thread em que o PortAudio reiniciou
        first = self._audio_signature is None
        self._audio_signature = sig
        self._invalidate_devices()
        self._indices = {}                                 # o reinício renumera: índice antigo não vale (A-02)
        if not first:
            log.info("dispositivos de áudio mudaram: lista relida")
        return True

    @override
    def audio_devices_changed(self) -> bool:
        """F5.3 (A-17): a assinatura do Core Audio difere da última leitura do PortAudio (só então reler vale a pena)."""
        sig = _endpoint_signature()
        return sig is not None and sig != self._audio_signature

    @override
    def audio_endpoints(self, refresh_names: bool = False) -> Endpoints | None:
        """F5.3 (A-19): microfones, saídas e padrões pelo Core Audio, sem reiniciar o PortAudio; na thread da interface."""
        if threading.current_thread() is not threading.main_thread():
            log.debug("leitura dos endpoints pedida fora da thread da interface: ignorada")
            return None
        return self._endpoints.read(refresh_names)

    def _resolve_index(self, device: AudioDevice | None) -> int | None:
        dev = self._resolve_input(device)
        return dev.index if dev else None

    def _resolve_input(self, device: AudioDevice | None, strict: bool = False) -> AudioDevice | None:
        """O dispositivo do PortAudio a abrir: o pedido (o índice só vale se é desta leitura, A-02; senão pelo id) ou o
        padrão. F5.3 (A-16): com `strict`, o pedido fora da lista é erro em vez do padrão."""
        if device is None:
            return self.default_input_device()
        if device.index is not None and self._indices.get(device.id) == device.index:
            return device                                  # o índice é desta leitura do PortAudio (A-02: o MME repete nomes)
        self._invalidate_devices()                         # pode ter mudado (dock, USB, reinício): enumera de novo
        found = pick_device(self.list_input_devices(), device.id, device.name)
        if found is not None:
            return found
        if strict:                                        # a mensagem vai à tela e ao log: o id resumido, nunca o nome (A-22)
            raise LookupError(f"o microfone {log_id(device.id)} não está na lista do sistema de áudio")
        log.warning("microfone %s não está mais nesta máquina; usando o padrão", log_id(device.id))
        return self.default_input_device()

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
        """F5.7 (item 10): as sessões de todas as saídas ativas, não só a padrão (`sessions.py`, com os gerenciadores criados
        e soltos na leitura, na thread do `_Poller`); o mesmo pid em duas saídas é uma linha só, com o maior pico; a saída
        que falha fica de fora e a falha geral dá a lista vazia."""
        t0 = time.perf_counter()
        try:
            reading = self._render_sessions.read()
        except Exception as e:
            log.warning("sessões de áudio: %s", e)
            return []
        peaks = sessions.peaks_by_pid(reading.values())
        n_sessions = sum(len(group) for group in reading.values())
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
        sig = tuple(sorted((a.pid, a.exe) for a in apps))
        if sig != self._apps_sig:                          # uma linha quando a lista muda, não a cada 0,5 s do warm-up
            self._apps_sig = sig
            log.debug("apps com som mudaram: %s (%d sessões, %.0f ms)",
                      ", ".join(f"{a.exe} (pid {a.pid}, pico {a.peak:.2f})" for a in apps) or "nenhum", n_sessions,
                      self.last_list_apps_ms)
        elif self.last_list_apps_ms > SLOW_LIST_APPS_MS:
            log.debug("lista de apps com som lenta: %.0f ms (%d sessões)", self.last_list_apps_ms, n_sessions)
        return apps

    # ---------------------------------------------------------------- captura
    @override
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None, strict: bool = False) -> _StreamHandle:
        import sounddevice as sd

        try:
            dev = self._resolve_input(device, strict)
        except Exception:
            self._invalidate_devices()
            raise
        idx, dev_id = (dev.index, dev.id) if dev is not None else (None, "")
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

        with portaudio.LOCK:                               # U-043: o reinício do PortAudio não cruza esta abertura
            stream = portaudio.track(sd.InputStream(device=idx, samplerate=sample_rate, channels=1, dtype="float32",
                                                    blocksize=max(160, int(sample_rate * 0.1)), callback=cb,
                                                    extra_settings=extra, finished_callback=finished))

        def start() -> None:
            stream.start()
            log.info("microfone aberto: endpoint %s (índice %s) a %d Hz", log_id(dev_id), idx, sample_rate)

        def stop() -> None:
            stream.stop()
            stream.close()

        def active() -> bool:
            return bool(stream.active)                    # F5.9 (item 1): o guarda "iniciada e não parada por nós" é do handle

        handle = _StreamHandle(start, stop, sample_rate, 1, on_error, close_fn=stream.close, device_id=dev_id, alive_fn=active)
        return handle

    @override
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None,
                             output_id: str | None = None) -> _StreamHandle:
        """F5.9 (L1a): em `system_loopback.py`; a leitura do Core Audio sai daqui (na thread da interface)."""
        return system_loopback.open_system_loopback(self.audio_endpoints(), on_audio, on_error, output_id)

    # ---------------------------------------------------------------- controles
    @override
    def mic_mute(self) -> mute._Win32MicMute:
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
        """Sem janela, solto do app e já em prioridade abaixo do normal (F2 item 2). Sem `DETACHED_PROCESS` (D-056): com
        ele, o lançador do venv (`.venv\\Scripts\\python.exe`) nascia sem console e o interpretador que ele cria ganhava um
        console novo e visível, uma janela preta por worker que, fechada, matava o worker sem rastro (0xC000013A). Só com
        `CREATE_NO_WINDOW` o lançador ganha um console sem janela, o interpretador o herda e o worker sobrevive ao app."""
        if sys.platform != "win32":
            return process.spawn_detached(argv, env)
        p = subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.DEVNULL,  # noqa: S603
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=CHILD_FLAGS,
                             startupinfo=subprocess.STARTUPINFO(dwFlags=STARTF_FORCEOFFFEEDBACK))
        return int(p.pid)

    @override
    def spawn_child(self, argv: list[str], env: Mapping[str, str] | None = None) -> ChildProcess:
        """F4: sem janela e abaixo do normal (as flags do worker), com stdin e stdout binários e stderr descartado; EcoQoS
        desligado logo depois de nascer (falha só vai para o log)."""
        if sys.platform != "win32":
            return process.spawn_child(argv, env)
        return subprocess.Popen(argv, env=dict(env) if env is not None else None, stdin=subprocess.PIPE,  # noqa: S603
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, creationflags=CHILD_FLAGS,
                                startupinfo=subprocess.STARTUPINFO(dwFlags=STARTF_FORCEOFFFEEDBACK))

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
