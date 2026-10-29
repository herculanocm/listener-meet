"""F5.7 (L2): o condutor dos testes das regras do aviso: leituras do sensor de mentira num relógio manual, com os eventos
anotados pelo tempo em que saíram."""
from __future__ import annotations

from listener.platform.base import AppAudio, AudioActivity
from listener.ui.call_rules import CallRules, ClosePrompt, EpisodeEnded, Event, Params, Prompt
from tests.fake_activity import FAKE_WATCHED


def act(*apps: AppAudio, degraded: bool = False) -> AudioActivity:
    return AudioActivity(tuple(apps), degraded)


def teams(mic: bool = True, peak: float = 0.0) -> AppAudio:
    return AppAudio(100, "ms-teams.exe", mic, peak)


def zoom(mic: bool = True, peak: float = 0.0) -> AppAudio:
    return AppAudio(110, "Zoom.exe", mic, peak)


def chrome(mic: bool = False, peak: float = 0.0, service: str = "", pid: int = 200) -> AppAudio:
    return AppAudio(pid, "chrome.exe", mic, peak, service)


def whatsapp(mic: bool = True, peak: float = 0.2) -> AppAudio:
    return AppAudio(300, "WhatsApp.Root.exe", mic, peak)


def discord(mic: bool = True, peak: float = 0.2) -> AppAudio:
    return AppAudio(400, "Discord.exe", mic, peak)


class Calls:
    """`at(t, ...)` lê no tempo `t`; `run(t0, t1, ...)` lê de `t0` a `t1` (inclusive) a cada `every` segundos."""

    def __init__(self, params: Params | None = None) -> None:
        self.rules = CallRules(FAKE_WATCHED)
        self.params = params or Params()
        self.events: list[tuple[float, Event]] = []

    def at(self, t: float, activity: AudioActivity | None, busy: bool = False, modal: bool = False) -> list[Event]:
        got = self.rules.step(activity, t, busy, modal, self.params)
        self.events += [(t, e) for e in got]
        return got

    def run(self, t0: float, t1: float, activity: AudioActivity | None, busy: bool = False, modal: bool = False,
            every: float = 1.0) -> None:
        t = t0
        while t <= t1 + 1e-9:
            self.at(t, activity, busy, modal)
            t += every

    def prompts(self) -> list[tuple[float, Prompt]]:
        return [(t, e) for t, e in self.events if isinstance(e, Prompt)]

    def closes(self) -> list[tuple[float, ClosePrompt]]:
        return [(t, e) for t, e in self.events if isinstance(e, ClosePrompt)]

    def ended(self) -> list[tuple[float, EpisodeEnded]]:
        return [(t, e) for t, e in self.events if isinstance(e, EpisodeEnded)]
