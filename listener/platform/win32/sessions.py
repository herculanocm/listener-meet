"""F5.7 (item 10 e L1): as sessões de áudio de todos os endpoints ativos de um fluxo, pelo Core Audio (pycaw, COM).

O `GetAllSessions` do pycaw só olha a saída padrão multimídia e lê todas as propriedades dela (`CreateDevice`): uma chamada
no padrão de comunicação (o fone) ou em outra saída ficava fora do "Apps tocando agora" [fato, 29/10]. Aqui, a enumeração é
a de `endpoints.active` (só os ids), e o gerenciador de sessões (IAudioSessionManager2) de cada endpoint é criado e solto
na própria leitura, na thread de quem lê: nada de COM fica no objeto para ser solto em outra thread ou na saída do
interpretador (o histórico das quedas 0xC0000409), contra ~1 ms de ganho. Guardar é opção (`keep`) para quem lê sempre
na mesma thread e solta nela com `close()` (o sensor do L1). Ativar o gerenciador não abre stream nem conta como uso do
microfone [fato, 29/10]. Log só com o id do endpoint (`log_id`), nunca o nome do aparelho (A-22)."""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ...log import get_logger
from ..base import log_id
from . import endpoints

log = get_logger("platform.win32.sessions")

type ListEndpoints = Callable[[int], list[tuple[str, Any]]]     # fluxo -> [(id, IMMDevice)] ativos
type Activate = Callable[[Any], Any]                           # IMMDevice -> IAudioSessionManager2


@dataclass(frozen=True, slots=True)
class Session:
    """Uma sessão de áudio de um endpoint: o processo dono e o pico agora (0 a 1; 0 quando o medidor não responde)."""
    pid: int
    peak: float


def active_endpoints(flow: int) -> list[tuple[str, Any]]:
    """Os endpoints ativos do fluxo nesta thread (COM iniciado aqui); levanta quando o Core Audio não responde."""
    endpoints.com_init()
    from pycaw.pycaw import AudioUtilities

    return endpoints.active(AudioUtilities.GetDeviceEnumerator(), flow)


def activate_manager(device: Any) -> Any:
    """O gerenciador de sessões de um endpoint (só a política de áudio: nenhuma stream é aberta)."""
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioSessionManager2

    return device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)


def endpoint_sessions(manager: Any) -> list[Session]:
    """As sessões de um endpoint, menos a dos sons do sistema (pid 0) e as de pid ilegível; o pico que falha vale 0.
    Levanta quando o próprio endpoint falha (removido ou invalidado): quem percorre o fluxo pula só ele."""
    from pycaw.pycaw import IAudioMeterInformation, IAudioSessionControl2

    out: list[Session] = []
    enum = manager.GetSessionEnumerator()
    for i in range(enum.GetCount()):
        ctl = enum.GetSession(i)
        if ctl is None:
            continue
        try:
            ctl2 = ctl.QueryInterface(IAudioSessionControl2)
            pid = int(ctl2.GetProcessId())
        except Exception as e:
            log.debug("pid da sessão: %s", e)
            continue
        if pid <= 0:
            continue
        peak = 0.0
        try:
            peak = float(ctl2.QueryInterface(IAudioMeterInformation).GetPeakValue())
        except Exception as e:
            log.debug("pico da sessão %s: %s", pid, e)
        out.append(Session(pid, peak))
    return out


def peaks_by_pid(groups: Iterable[Iterable[Session]]) -> dict[int, float]:
    """O maior pico de cada pid entre os endpoints: um app que toca em duas saídas vira uma linha só (na ordem de chegada)."""
    peaks: dict[int, float] = {}
    for group in groups:
        for s in group:
            peaks[s.pid] = max(peaks.get(s.pid, 0.0), s.peak)
    return peaks


class FlowSessions:
    """As sessões de cada endpoint ativo de um fluxo (`endpoints.FLOW_RENDER` ou `FLOW_CAPTURE`). Sem `keep`, os
    gerenciadores são criados e soltos dentro de cada leitura. Com `keep`, ficam guardados por id e são refeitos todos
    quando os ids do fluxo ou a thread mudam, e um a um quando o endpoint falha (na leitura seguinte); `close()` os solta e
    deve ser chamado na thread que lê. Uma trava serializa as leituras: o "Antes de gravar" lê num `_Poller`, e um novo pode
    nascer antes de o anterior sair."""

    def __init__(self, flow: int, list_endpoints: ListEndpoints = active_endpoints,
                 activate: Activate = activate_manager, keep: bool = False) -> None:
        self.flow = flow
        self.keep = keep
        self._list = list_endpoints
        self._activate = activate
        self._lock = threading.Lock()
        self._key: tuple[int, tuple[str, ...]] | None = None    # com `keep`: (thread, ids do fluxo) dos guardados
        self._managers: dict[str, Any] = {}                     # com `keep`: id -> IAudioSessionManager2
        self.activations = 0                                    # instrumentação: gerenciadores ativados até aqui

    def read(self) -> dict[str, list[Session]]:
        """As sessões por id de endpoint, na ordem do sistema; o endpoint que falha fica de fora desta leitura. Levanta
        quando a enumeração do fluxo falha (erro geral): os guardados são soltos e quem chama decide o log."""
        with self._lock:
            try:
                devices = self._list(self.flow)
            except Exception:
                self._key, self._managers = None, {}
                raise
            managers = self._kept(devices) if self.keep else {}   # sem `keep`: solto ao sair, nesta thread
            out: dict[str, list[Session]] = {}
            for eid, device in devices:
                sessions = self._read_endpoint(managers, eid, device)
                if sessions is not None:
                    out[eid] = sessions
            return out

    def close(self) -> None:
        """Solta os gerenciadores guardados (com `keep`), na thread de quem chama: a que lê."""
        with self._lock:
            self._key, self._managers = None, {}

    def _kept(self, devices: list[tuple[str, Any]]) -> dict[str, Any]:
        key = (threading.get_ident(), tuple(eid for eid, _ in devices))
        if key != self._key:                                    # M-07: ponteiro COM de outra thread é refeito nesta
            self._key, self._managers = key, {}
        return self._managers

    def _read_endpoint(self, managers: dict[str, Any], eid: str, device: Any) -> list[Session] | None:
        manager = managers.get(eid)
        try:
            if manager is None:
                self.activations += 1
                manager = managers[eid] = self._activate(device)
            return endpoint_sessions(manager)
        except Exception as e:
            managers.pop(eid, None)                             # refeito na próxima leitura
            log.debug("sessões do endpoint %s: %s", log_id(eid), e)
            return None
