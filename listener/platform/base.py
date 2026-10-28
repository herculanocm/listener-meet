"""Interfaces da camada de plataforma. Nada específico de sistema operacional aqui."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


NAME_ID_PREFIX = "name:"        # o id de um aparelho sem id do sistema: o prefixo e o nome (que pode ter nome de pessoa)


def log_id(device_id: str | None) -> str:
    """F5.3 (A-22, R-05): o id de um aparelho para o log e os eventos. O do endpoint vai como está; o `name:<nome>`, montado
    quando o sistema não deu id, traz o nome do aparelho ("AirPods de Fulano") e vira um resumo curto e estável."""
    if not device_id:
        return "(sem id)"
    if device_id.startswith(NAME_ID_PREFIX):
        return "name#" + hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:8]
    return device_id


@dataclass(frozen=True, slots=True)
class AudioDevice:
    id: str            # identificador estável (ID do endpoint no Windows; "name:<nome>" quando não há): é o que se persiste
    name: str
    api: str           # "WASAPI", "CoreAudio", "PulseAudio"...
    channels: int
    sample_rate: int
    index: int | None = None   # índice na biblioteca de áudio nesta sessão: muda ao plugar e desplugar, nunca persistir


def pick_device(devices: list[AudioDevice], device_id: str | None, name: str | None) -> AudioDevice | None:
    """Acha o dispositivo salvo: pelo id estável, depois pelo nome (exato, depois por prefixo). Índices antigos
    (só dígitos, F1) são ignorados. None = não está mais nesta máquina: quem chama avisa e cai no padrão."""
    if device_id and not device_id.isdigit():
        for d in devices:
            if d.id == device_id:
                return d
    if name:
        for d in devices:
            if d.name == name:
                return d
        low = name.lower()
        for d in devices:
            dn = d.name.lower()
            if len(low) >= 20 and (dn.startswith(low[:24]) or low.startswith(dn[:24])):
                return d
    return None


@dataclass(frozen=True, slots=True)
class Endpoint:
    """F5.3 (A-19): um endpoint de áudio como o sistema o vê agora, sem a biblioteca de captura (que só relê os
    dispositivos reiniciando). `container` é o aparelho físico: o microfone e as saídas de um fone têm o mesmo; vazio
    quando não se sabe ou quando o aparelho é parte do computador."""
    id: str
    name: str
    container: str = ""


@dataclass(frozen=True, slots=True)
class Endpoints:
    """F5.3 (A-19), a leitura única dos endpoints ativos: microfones, saídas e os padrões de console e de comunicação de
    cada fluxo. `signature` muda quando o sistema ganha ou perde um endpoint ou troca um padrão (a mesma da U-043)."""
    capture: tuple[Endpoint, ...] = ()
    render: tuple[Endpoint, ...] = ()
    capture_default: str = ""          # padrão de console ("" = nenhum microfone)
    capture_comm: str = ""             # padrão de comunicação
    render_default: str = ""
    render_comm: str = ""

    @property
    def signature(self) -> tuple[object, ...]:
        return (tuple(sorted(e.id for e in self.capture)), self.capture_default, self.capture_comm,
                tuple(sorted(e.id for e in self.render)), self.render_default, self.render_comm)

    def ids(self) -> frozenset[str]:
        return frozenset(e.id for e in (*self.capture, *self.render))

    def mic(self, endpoint_id: str) -> Endpoint | None:
        return next((e for e in self.capture if e.id == endpoint_id), None)

    def output(self, endpoint_id: str) -> Endpoint | None:
        return next((e for e in self.render if e.id == endpoint_id), None)


@dataclass(frozen=True, slots=True)
class PlayingApp:
    pid: int
    exe: str                   # nome do executável
    title: str = ""            # título da janela principal, se conhecido
    peak: float = 0.0          # pico de áudio recente, 0 a 1
    is_meeting_app: bool = False

    @property
    def label(self) -> str:
        base = self.exe.removesuffix(".exe")
        return f"{base} — {self.title}" if self.title else base


# Callback de áudio: (bloco float32 com shape (frames, canais), perf_counter do primeiro frame). Roda na thread do
# driver: só copiar e enfileirar. Callback de erro: motivo em texto quando a stream morre sozinha (dispositivo sumiu).
type AudioCallback = Callable[[np.ndarray, float], None]
type ErrorCallback = Callable[[str], None]


class CaptureHandle(Protocol):
    """Stream devolvida parada: quem chama registra o formato e só então chama `start()` (F1.2 M-06). `device_id` é o
    endpoint realmente aberto (F5.3, A-16: o pedido pode ter caído no padrão); vazio quando não se sabe ou no loopback de
    um app."""
    sample_rate: int
    channels: int
    device_id: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class MicMute(Protocol):
    """Afinizado à thread em que foi usado pela primeira vez quando a plataforma usa COM (win32): `get()` e `set()`
    podem ser chamados de outra thread, mas o adaptador refaz o endpoint ao notar a troca (M-07). F5.3 (A-18): a troca
    de endpoint é atômica sob uma trava; com `muted`, tira o mudo do antigo e põe no novo."""
    def set_device(self, device: AudioDevice | None, muted: bool = False) -> bool: ...   # True se achou o endpoint
    def get(self) -> bool | None: ...          # None = não sei
    def set(self, muted: bool) -> bool: ...    # True se conseguiu


class Hotkeys(Protocol):
    def validate(self, combo: str) -> str | None: ...   # motivo da recusa (sintaxe) ou None quando o atalho é aceitável
    def register(self, combo: str, callback: Callable[[], None]) -> bool: ...
    def errors(self) -> dict[str, str]: ...    # combo -> motivo da falha (sintaxe, já usado por outro app...)
    def unregister_all(self) -> None: ...
    def stop(self) -> None: ...


class ByteSink(Protocol):
    def write(self, data: bytes, /) -> int: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class ByteSource(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...
    def close(self) -> None: ...


class ChildProcess(Protocol):
    """Filho com pipes (F4, `spawn_child`): o que o cliente do ao vivo usa de um `subprocess.Popen` em modo binário."""
    @property
    def pid(self) -> int: ...
    @property
    def stdin(self) -> ByteSink | None: ...
    @property
    def stdout(self) -> ByteSource | None: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...
    def terminate(self) -> None: ...


class Platform(Protocol):
    name: str

    # processo
    def app_init(self) -> None: ...                      # antes de criar a interface: identidade do processo etc.
    def keep_awake(self, on: bool) -> bool: ...          # impede a suspensão do sistema enquanto grava
    def bring_to_front(self, window_id: int) -> bool: ...  # traz uma janela nossa para a frente mesmo em segundo plano
    # F5.3 (A-13): tira uma janela nossa da captura de tela (compartilhamento, gravação, print) e ela segue visível aqui;
    # True quando o sistema aceitou (o aviso que não sai da captura mostra só o tipo durante a gravação)
    def exclude_from_capture(self, window_id: int) -> bool: ...

    # dispositivos e apps
    def list_input_devices(self) -> list[AudioDevice]: ...
    def default_input_device(self) -> AudioDevice | None: ...
    def list_playing_apps(self) -> list[PlayingApp]: ...
    # U-043: relê os dispositivos de áudio se o sistema ganhou ou perdeu um (ou trocou o padrão), só sem stream aberto
    # além dos `own_streams` de quem pede (`release` os fecha antes); True quando a lista foi relida
    def refresh_audio_devices(self, own_streams: int = 0, release: Callable[[], None] | None = None) -> bool: ...
    # F5.3 (A-17): o sistema mudou desde a última leitura da biblioteca de áudio (reler pede todo stream fechado)
    def audio_devices_changed(self) -> bool: ...
    # F5.3 (A-19): os endpoints ativos e os padrões direto do sistema, sem reler a biblioteca de áudio (que segura o
    # microfone aberto); nomes em cache por id, relidos com `refresh_names`. None quando o sistema não responde (aí a
    # lista é a da biblioteca, sem "novo"). Na thread da interface
    def audio_endpoints(self, refresh_names: bool = False) -> Endpoints | None: ...

    # captura (o callback roda em thread de áudio: só copiar e enfileirar); a stream volta parada. F5.3 (A-16): com
    # `strict`, o microfone pedido que não está na lista é erro, não o padrão; `output_id` (A-21) é a saída cujo som o
    # loopback do sistema inteiro grava (None = a padrão de console; a escolhida que sumiu cai na padrão)
    def open_mic(self, device: AudioDevice | None, sample_rate: int, on_audio: AudioCallback,
                 on_error: ErrorCallback | None = None, strict: bool = False) -> CaptureHandle: ...
    def open_app_loopback(self, pid: int, on_audio: AudioCallback, on_error: ErrorCallback | None = None) -> CaptureHandle: ...
    def open_system_loopback(self, on_audio: AudioCallback, on_error: ErrorCallback | None = None,
                             output_id: str | None = None) -> CaptureHandle: ...

    # controles
    def mic_mute(self) -> MicMute: ...
    def hotkeys(self) -> Hotkeys: ...

    # processos
    def root_pid(self, exe: str) -> int | None: ...
    def spawn_detached(self, argv: list[str], env: Mapping[str, str] | None = None) -> int: ...   # worker da F2 solto do app; pid
    # F4: processo ao vivo preso ao app, com stdin e stdout binários (stderr descartado), prioridade abaixo do normal e,
    # no Windows, sem janela e com o EcoQoS desligado; morre com a gravação
    def spawn_child(self, argv: list[str], env: Mapping[str, str] | None = None) -> ChildProcess: ...
    # F4, chamados pelo próprio filho ao vivo ao nascer: num venv do Windows o pid do `Popen` é o do lançador, então o
    # EcoQoS se desliga de dentro; e, depois do `dup2` do fd 1, o handle padrão do SO passa a seguir o fd
    def disable_own_power_throttling(self) -> bool: ...
    def sync_stdout_handle(self) -> None: ...
    def lower_own_priority(self) -> bool: ...        # o worker se rebaixa ao nascer (nunca `preexec_fn`)
    def memory_rss(self) -> int | None: ...          # RSS do próprio processo em bytes, só para o log

    # pastas do sistema
    def config_dir(self) -> Path: ...
    def data_dir_suggestion(self) -> Path: ...
    def synced_folder_name(self, path: Path) -> str | None: ...
