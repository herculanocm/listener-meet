"""F5.3 L4 (A-19): os endpoints de áudio pelo Core Audio (pycaw, COM), sem reler o PortAudio, que segura o microfone
aberto durante a gravação. Cada leitura enumera só os ids ativos e os padrões de console e de comunicação de cada fluxo (a
assinatura da U-043, ~19 ms); nome e ContainerId saem do property store por chave (dois `GetValue`, nunca o
`CreateDevice` do pycaw, que lê todas as propriedades), só para id novo, quando a assinatura muda, ou para todos quando
quem pede quer os nomes de novo (o painel abrindo), em cache por id. COM na thread de quem chama: a da interface.
D-067: `default_id` é a escolha única do "aparelho padrão de agora" (papel console, ~2,5 ms, em qualquer thread, nenhum
objeto COM guardado), `device` e `name_of` acham um endpoint pelo id, e `names_match` e `by_name` casam nomes entre listas.
R-08: o COM é iniciado uma vez por thread (`com_init`) e nunca desfeito; os objetos nascem e são soltos em cada chamada."""
from __future__ import annotations

import ctypes
import functools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...log import get_logger
from ..base import Endpoint, Endpoints

log = get_logger("platform.win32.endpoints")

FLOW_RENDER, FLOW_CAPTURE = 0, 1                       # EDataFlow
ROLE_CONSOLE, ROLE_COMMUNICATIONS = 0, 2               # ERole
STATE_ACTIVE = 1                                       # DEVICE_STATE_ACTIVE
STGM_READ = 0
VT_LPWSTR, VT_CLSID = 31, 72
FRIENDLY_NAME = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)    # PKEY_Device_FriendlyName
DEVICE_DESC = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 2)       # PKEY_Device_DeviceDesc (quando falta o nome amigável)
CONTAINER_ID = ("{8c7ed206-3f8a-4827-b3ab-ae9e1faefc6c}", 2)      # PKEY_Device_ContainerId
# o ContainerId do próprio computador: microfone e alto-falantes de dentro dividem este; vira vazio (não é um aparelho)
LOCAL_CONTAINER = "{00000000-0000-0000-FFFF-FFFFFFFFFFFF}"


_com = threading.local()                               # R-08: as threads em que o COM já foi iniciado


def com_init() -> None:
    """O COM desta thread, uma vez (nunca desfeito: os objetos de cada leitura são soltos antes de ela acabar)."""
    if getattr(_com, "ready", False):
        return
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception as e:  # já iniciado em outro modo (o do PortAudio, por exemplo): segue valendo
        log.debug("CoInitialize: %s", e)
    _com.ready = True


@dataclass(slots=True)
class Scan:
    """Uma enumeração: (id, IMMDevice) ativos por fluxo e os padrões; os ponteiros COM valem só nesta thread e leitura."""
    capture: list[tuple[str, Any]]
    render: list[tuple[str, Any]]
    defaults: tuple[str, str, str, str]                # captura (console, comunicação), saída (console, comunicação)

    @property
    def signature(self) -> tuple[object, ...]:
        """A mesma forma de `Endpoints.signature` (e a da U-043, que o reinício do PortAudio compara)."""
        return (tuple(sorted(i for i, _ in self.capture)), self.defaults[0], self.defaults[1],
                tuple(sorted(i for i, _ in self.render)), self.defaults[2], self.defaults[3])


def scan() -> Scan | None:
    """Os endpoints ativos de captura e de saída e os padrões; None sem pycaw ou com o Core Audio falhando."""
    com_init()
    try:
        from pycaw.pycaw import AudioUtilities

        enumerator = AudioUtilities.GetDeviceEnumerator()
        flows: dict[int, list[tuple[str, Any]]] = {}
        defaults: list[str] = []
        for flow in (FLOW_CAPTURE, FLOW_RENDER):
            flows[flow] = active(enumerator, flow)
            defaults += [default_id(flow, role, enumerator) for role in (ROLE_CONSOLE, ROLE_COMMUNICATIONS)]
        return Scan(flows[FLOW_CAPTURE], flows[FLOW_RENDER], (defaults[0], defaults[1], defaults[2], defaults[3]))
    except Exception as e:
        log.debug("endpoints de áudio: %s", e)
        return None


def active(enumerator: Any, flow: int) -> list[tuple[str, Any]]:
    """(id, IMMDevice) dos endpoints ativos de um fluxo, na ordem do sistema, só com o id (o `CreateDevice` do pycaw lê
    todas as propriedades); F5.7: também a enumeração de `sessions.py`."""
    col = enumerator.EnumAudioEndpoints(flow, STATE_ACTIVE)
    return [(str(dev.GetId()), dev) for dev in (col.Item(i) for i in range(col.GetCount()))]


def signature() -> tuple[object, ...] | None:
    s = scan()
    return s.signature if s is not None else None


def default_id(flow: int, role: int = ROLE_CONSOLE, enumerator: Any = None) -> str:
    """D-067: o endpoint padrão de agora de um fluxo e papel ("" quando não há). A escolha única do "aparelho padrão" do
    app: o player, o teste do warm-up, o microfone e o loopback que acompanham. Sem `enumerator`, um novo, na thread de
    quem chama, solto na volta (~2,5 ms, quase tudo no `GetDefaultAudioEndpoint`, 29/10); levanta quando o Core Audio
    não responde."""
    if enumerator is None:
        com_init()
        from pycaw.pycaw import AudioUtilities

        enumerator = AudioUtilities.GetDeviceEnumerator()
    try:
        return str(enumerator.GetDefaultAudioEndpoint(flow, role).GetId())
    except Exception:  # nenhum dispositivo desse fluxo conectado: sem padrão
        return ""


def device(endpoint_id: str) -> Any:
    """D-067: o IMMDevice de um id, na thread de quem chama (solto quando quem chama o larga); None sem ele."""
    if not endpoint_id:
        return None
    com_init()
    try:
        from pycaw.pycaw import AudioUtilities

        return AudioUtilities.GetDeviceEnumerator().GetDevice(endpoint_id)
    except Exception as e:
        log.debug("endpoint ausente: %s", e)
        return None


def name_of(endpoint_id: str) -> str:
    """D-067: o nome amigável de um endpoint pelo id (o property store, por chave); vazio quando ele não responde. Só para
    casar com a lista da biblioteca de áudio: nunca vai ao log (A-22)."""
    dev = device(endpoint_id)
    return read_props(dev)[0] if dev is not None else ""


def names_match(a: str, b: str) -> bool:
    """O mesmo aparelho em duas listas: o nome igual, ou um começa pelo outro com 20 caracteres ou mais (o MME corta em 31)."""
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or (len(a) >= 20 and b.startswith(a)) or (len(b) >= 20 and a.startswith(b))


def by_name[T](items: list[T], name: str, key: Callable[[T], str]) -> T | None:
    """O item com este nome: o igual primeiro, depois pelo prefixo longo (o fone Bluetooth tem saídas de nomes parecidos)."""
    low = name.strip().lower()
    exact = next((it for it in items if key(it).strip().lower() == low), None)
    return exact if exact is not None else next((it for it in items if names_match(key(it), name)), None)


@functools.cache
def _key(fmtid: str, pid: int) -> Any:
    from comtypes import GUID
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY

    key = PROPERTYKEY()
    key.fmtid = GUID(fmtid)
    key.pid = pid
    return key


def _value(store: Any, key: tuple[str, int]) -> str:
    """Um valor de texto ou de GUID do property store; vazio quando é de outro tipo. O PROPVARIANT é liberado sempre."""
    from comtypes import GUID

    pv = store.GetValue(ctypes.byref(_key(*key)))
    try:
        if pv.vt == VT_LPWSTR:
            return str(pv.union.pwszVal or "")
        if pv.vt == VT_CLSID:                          # o union guarda um ponteiro para o GUID (o pycaw não o lê)
            ptr = ctypes.c_void_p.from_buffer(pv.union).value
            return str(GUID.from_address(ptr)).upper() if ptr else ""
        return ""
    finally:
        pv.clear()


def read_props(dev: Any) -> tuple[str, str]:
    """(nome amigável, ContainerId) por chave; vazios quando o endpoint não responde (o nome fica de fora do log)."""
    try:
        store = dev.OpenPropertyStore(STGM_READ)
        name = _value(store, FRIENDLY_NAME) or _value(store, DEVICE_DESC)
        container = _value(store, CONTAINER_ID)
    except Exception as e:
        log.debug("propriedades do endpoint: %s", e)
        return "", ""
    return name, "" if container == LOCAL_CONTAINER else container


class EndpointReader:
    """`Platform.audio_endpoints` no Windows (A-19): a assinatura a cada chamada; a mesma leitura volta enquanto ela não
    muda; nome e ContainerId por chave, em cache por id (todos de novo com `refresh_names`)."""

    def __init__(self) -> None:
        self._props: dict[str, tuple[str, str]] = {}
        self._last: Endpoints | None = None
        self.last_ms = 0.0                             # instrumentação (medições do L4): a última leitura
        self.props_read = 0                            # instrumentação: endpoints lidos do property store

    def read(self, refresh_names: bool = False) -> Endpoints | None:
        t0 = time.perf_counter()
        s = scan()
        if s is None:
            return None
        last = self._last
        if last is not None and not refresh_names and last.signature == s.signature:
            self.last_ms = (time.perf_counter() - t0) * 1000
            return last
        for eid, dev in (*s.capture, *s.render):
            if refresh_names or eid not in self._props:
                self._props[eid] = read_props(dev)
                self.props_read += 1
        out = Endpoints(capture=tuple(self._endpoint(eid) for eid, _ in s.capture),
                        render=tuple(self._endpoint(eid) for eid, _ in s.render),
                        capture_default=s.defaults[0], capture_comm=s.defaults[1],
                        render_default=s.defaults[2], render_comm=s.defaults[3])
        self._last = out
        self.last_ms = (time.perf_counter() - t0) * 1000
        return out

    def _endpoint(self, eid: str) -> Endpoint:
        name, container = self._props.get(eid, ("", ""))
        return Endpoint(eid, name, container)
