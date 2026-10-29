"""F5.7: objetos falsos no lugar do COM do Core Audio (endpoint, gerenciador, sessão e medidor), novos a cada enumeração e
a cada Activate, com `weakref` para os testes provarem que nada sobra preso. Usados por `test_win32_sessions.py` e
`test_win32_activity.py`; o `QueryInterface` do medidor pede o pycaw (só no Windows)."""
from __future__ import annotations

import weakref
from typing import Any


class FakeMeter:
    def __init__(self, peak: float | Exception) -> None:
        self.peak = peak

    def GetPeakValue(self) -> float:  # noqa: N802 (nome do COM)
        if isinstance(self.peak, Exception):
            raise self.peak
        return self.peak


class FakeSession:
    """IAudioSessionControl e IAudioSessionControl2 num só: o QueryInterface do medidor dá o pico."""

    def __init__(self, pid: int | Exception, peak: float | Exception = 0.0,
                 state: int | Exception | None = None) -> None:
        self.pid, self.peak, self.state = pid, peak, state

    def QueryInterface(self, iface: Any) -> Any:  # noqa: N802
        from pycaw.pycaw import IAudioMeterInformation

        return FakeMeter(self.peak) if iface is IAudioMeterInformation else self

    def GetProcessId(self) -> int:  # noqa: N802
        if isinstance(self.pid, Exception):
            raise self.pid
        return self.pid

    def GetState(self) -> int:  # noqa: N802
        """Sem `state`, falha (o falso da Etapa 0 não tinha estado): a sessão fica com o estado desconhecido."""
        if self.state is None or isinstance(self.state, Exception):
            raise self.state or OSError("sem estado")
        return self.state


class FakeEnumerator:
    def __init__(self, sessions: list[FakeSession | None]) -> None:
        self.sessions = sessions

    def GetCount(self) -> int:  # noqa: N802
        return len(self.sessions)

    def GetSession(self, i: int) -> FakeSession | None:  # noqa: N802
        return self.sessions[i]


class FakeManager:
    def __init__(self, sessions: list[FakeSession | None], fail: bool = False) -> None:
        self.sessions, self.fail = sessions, fail

    def GetSessionEnumerator(self) -> FakeEnumerator:  # noqa: N802
        if self.fail:
            raise OSError("AUDCLNT_E_DEVICE_INVALIDATED")
        return FakeEnumerator(self.sessions)


class Device:
    """O IMMDevice de uma enumeração: novo a cada uma, como o ponteiro COM."""

    def __init__(self, eid: str) -> None:
        self.eid = eid


class Handle:
    """O IAudioSessionManager2 entregue por um Activate: novo a cada um, como o ponteiro COM."""

    def __init__(self, manager: FakeManager) -> None:
        self.manager = manager

    def GetSessionEnumerator(self) -> FakeEnumerator:  # noqa: N802
        return self.manager.GetSessionEnumerator()


class Outputs:
    """As saídas ativas de mentira, com cada ponteiro entregue anotado por `weakref`."""

    def __init__(self, managers: dict[str, FakeManager]) -> None:
        self.managers = managers
        self.broken: set[str] = set()                     # o Activate falha nestes
        self.down = False                                 # a enumeração inteira falha
        self.handed: list[weakref.ref[Any]] = []

    def list(self, flow: int) -> list[tuple[str, Any]]:
        if self.down:
            raise OSError("Core Audio fora")
        out = [(eid, Device(eid)) for eid in self.managers]
        self.handed += [weakref.ref(dev) for _, dev in out]
        return out

    def activate(self, device: Device) -> Handle:
        if device.eid in self.broken:
            raise OSError("Activate falhou")
        handle = Handle(self.managers[device.eid])
        self.handed.append(weakref.ref(handle))
        return handle

    def alive(self) -> int:
        """Quantos ponteiros entregues ainda estão presos em algum lugar."""
        return sum(ref() is not None for ref in self.handed)
