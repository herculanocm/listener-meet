"""F5.7 (L2): as regras do aviso de reunião sem gravação, sem Qt e sem I/O. O relógio vem de quem chama (`now`, em segundos
monotônicos); quem lê o sensor (L3) passa cada leitura a `step` e devolve as respostas do aviso (`dismiss`, `expired`,
`record`). O app nunca grava sozinho (D-010): daqui só saem eventos.

Regras (por executável, em minúsculas), com o tempo contado seguido e uma leitura perdida tolerada (duas zeram):
- R1: app de reunião ligado, com microfone, por `seconds`;
- R2: navegador, com microfone e (som ou serviço reconhecido pelo título), por `seconds`;
- R3: outro app (fora da lista, com `other_apps`), com microfone e som, por 20 s;
- R4: só ouvindo (app de reunião, ou navegador com serviço): som sem microfone, por 45 s.
"Som" é um pico de pelo menos 0,01 em alguma leitura dos últimos 10 s. Só pelo registro (`degraded`), só a R1. Um aviso
por vez, no máximo 6 por hora (lembretes contam); com diálogo modal aberto, o aviso espera; gravando (`busy`), nenhum
aviso, o aviso à vista fecha e quem tem sinal de regra fica coberto até o fim da chamada. A chamada acaba depois de 30 s
e de pelo menos 3 leituras seguidas sem microfone e sem som do app; depois de "Agora não" ou sem resposta, 120 s de
carência. Nada aqui é de um sistema operacional (CP15): os executáveis vêm da lista da plataforma."""
from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from ..platform.base import KIND_BROWSER, KIND_MEETING, AudioActivity, WatchedApp

SOUND_MIN = 0.01               # o pico que conta como som
SOUND_WINDOW_S = 10.0          # "com som": alguma leitura com som nesta janela
OTHER_S = 20.0                 # R3
LISTEN_S = 45.0                # R4
END_S = 30.0                   # sem microfone e sem som por isto: a chamada acabou...
END_READS = 3                  # ...e em pelo menos tantas leituras seguidas (R-04: duas pausas da fala no modo "lento")
GRACE_S = 120.0                # carência depois de "Agora não" ou sem resposta
REMIND_S = 120.0               # o lembrete, depois de o aviso sumir sem resposta
PER_HOUR, HOUR_S = 6, 3600.0   # teto de avisos (com os lembretes)
SECONDS_RANGE = (3.0, 120.0)   # o "Avisar depois de" das Configurações
KIND_OTHER = "other"
# F5.7 L3 (R-12): o nome na tela de um "outro app" conhecido, pelo executável sem a extensão (em minúsculas); o resto fica
# como o sistema o mostra, sem a extensão
FRIENDLY_NAMES = {"whatsapp.root": "WhatsApp", "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal",
                  "discord": "Discord", "skype": "Skype"}
MODE_STOPPED, MODE_IDLE, MODE_ARMING, MODE_SLOW = "parado", "ocioso", "armando", "lento"


class State(StrEnum):
    QUIET = "quieto"
    ARMING = "armando"
    PROMPTED = "avisado"
    PENDING = "pendente"
    COVERED = "coberto"
    DISMISSED = "dispensado"


EPISODE = frozenset({State.PROMPTED, State.PENDING, State.COVERED, State.DISMISSED})


@dataclass(frozen=True, slots=True)
class Params:
    """As Configurações (o L3 liga). `apps`: os executáveis de reunião ligados (None = `default_apps`); `off`: "Não avisar
    para o X", vale para qualquer app."""
    enabled: bool = True
    seconds: float = 8.0
    remind: bool = True
    apps: frozenset[str] | None = None
    browsers: bool = True
    other_apps: bool = True
    off: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Prompt:
    exe: str                   # em minúsculas
    label: str                 # "Teams", "Google Meet no Chrome", "WhatsApp.Root"
    rule: str                  # "R1" a "R4"
    seconds: int               # há quanto tempo o sinal da regra segue (o texto do aviso)
    reminder: bool = False


@dataclass(frozen=True, slots=True)
class ClosePrompt:
    exe: str
    reason: str                # "gravação", "sumiu", "desligado"


@dataclass(frozen=True, slots=True)
class EpisodeEnded:
    exe: str
    seconds: float             # do começo da contagem ao último sinal
    outcome: str               # o estado em que acabou: "coberto", "dispensado", "pendente" ou "avisado"


@dataclass(frozen=True, slots=True)
class Pending:
    """Uma chamada sem gravação nem resposta (o aviso à vista ou sumido), para a bandeja."""
    exe: str
    label: str


type Event = Prompt | ClosePrompt | EpisodeEnded


def default_apps(watched: Sequence[WatchedApp]) -> frozenset[str]:
    """Os apps de reunião da lista de fábrica, todos ligados (R-08: a lista é da plataforma)."""
    return frozenset(w.exe.lower() for w in watched if w.kind == KIND_MEETING)


@dataclass(frozen=True, slots=True)
class _Seen:
    """Os sinais de um executável numa leitura (as raízes de mesmo nome somadas)."""
    name: str                  # o executável como o sistema o mostra (o rótulo de "outro app")
    mic: bool
    peak: float
    service: str


@dataclass(slots=True)
class _Track:
    state: State = State.QUIET
    label: str = ""
    rule: str = ""
    since: float = 0.0         # começo da contagem da regra atual
    misses: int = 0            # leituras seguidas sem a regra (ARMANDO)
    hit: bool = False          # a regra valeu nesta leitura
    started: float = 0.0       # começo do episódio
    last_signal: float = 0.0   # a última leitura com microfone ou som
    silent: int = 0            # leituras reais seguidas sem microfone e sem som
    expired_at: float | None = None
    reminded: bool = False
    recording: bool = False    # "Gravar…" abriu o "Antes de gravar": espera o `busy`
    grace_until: float = 0.0
    sound: deque[tuple[float, float]] = field(default_factory=deque)


def _merge(activity: AudioActivity) -> dict[str, _Seen]:
    out: dict[str, _Seen] = {}
    for app in activity.apps:
        key = app.exe.lower()
        old = out.get(key)
        if old is None:
            out[key] = _Seen(app.exe, app.mic, app.peak, app.service)
        else:
            out[key] = _Seen(old.name, old.mic or app.mic, max(old.peak, app.peak), old.service or app.service)
    return out


def app_name(name: str) -> str:
    """O nome na tela de um executável fora da lista de fábrica: o amigável dos conhecidos (`FRIENDLY_NAMES`) ou o executável
    sem a extensão, se tiver (o do Windows perde a dele; o "discord" do Linux fica como está)."""
    stem = PurePath(name).stem or name
    return FRIENDLY_NAMES.get(stem.lower(), stem)


def _clamp(seconds: float) -> float:
    low, high = SECONDS_RANGE
    return min(max(float(seconds), low), high)


def rule_for(kind: str | None, mic: bool, sound: bool, service: str, degraded: bool,
             seconds: float) -> tuple[str, float] | None:
    """A regra que vale agora e o tempo dela; None sem regra."""
    if kind is None or (degraded and kind != KIND_MEETING):
        return None
    if kind == KIND_MEETING:
        if mic:
            return "R1", seconds
        return ("R4", LISTEN_S) if sound and not degraded else None
    if kind == KIND_BROWSER:
        if mic and (sound or service):
            return "R2", seconds
        return ("R4", LISTEN_S) if service and sound and not mic else None
    return ("R3", OTHER_S) if mic and sound else None


class CallRules:
    """O estado por executável (QUIETO, ARMANDO, AVISADO, PENDENTE, COBERTO, DISPENSADO) e o teto por hora."""

    def __init__(self, watched: Sequence[WatchedApp]) -> None:
        self._watched = {w.exe.lower(): w for w in watched}
        self._default_apps = default_apps(watched)
        self._tracks: dict[str, _Track] = {}
        self._prompts: deque[float] = deque()       # quando cada aviso apareceu (o teto por hora)
        self._now = 0.0
        self._stopped = False

    # ---------------------------------------------------------------- a leitura
    def step(self, activity: AudioActivity | None, now: float, busy: bool, modal: bool, params: Params) -> list[Event]:
        """Um passo por leitura (`activity` None: a leitura falhou). O contrato com quem lê (L3, R-03): na borda do `busy`
        (começou a gravar, a salvar ou abriu o "Antes de gravar"), um passo com a leitura real; durante o `busy`, nenhum
        passo, ou passos sem leitura, que só cobrem pela última leitura real (nada conta e nada acaba sem leitura). A
        primeira leitura depois do `busy` segue o normal."""
        self._now, self._stopped = now, (busy or not params.enabled)
        if not params.enabled:
            return self._reset("desligado")
        events: list[Event] = []
        if not (busy and activity is None):
            self._read(activity, params, events)
        if busy:
            self._cover(events)
        else:
            self._offer(modal, params, events)
        self._forget()
        return events

    def _read(self, activity: AudioActivity | None, params: Params, events: list[Event]) -> None:
        seen = _merge(activity) if activity is not None else None
        degraded = activity is not None and activity.degraded
        for exe in sorted(set(self._tracks) | set(seen or {})):
            track = self._tracks.setdefault(exe, _Track())
            cur = seen.get(exe) if seen is not None else None
            self._observe(exe, track, cur, seen is not None, params, degraded, events)
            self._finish(exe, track, events)

    def _observe(self, exe: str, track: _Track, cur: _Seen | None, real: bool, params: Params, degraded: bool,
                 events: list[Event]) -> None:
        sound = self._signal(track, cur, real)
        kind = self._kind(exe, params)
        if kind is None and track.state in (State.PROMPTED, State.PENDING):
            self._silence(exe, track, events)
        found = None
        if cur is not None:
            found = rule_for(kind, cur.mic, sound, cur.service, degraded, _clamp(params.seconds))
            track.label = self._label(exe, kind, cur) if found else track.label
        self._count(track, found[0] if found else "")

    def _signal(self, track: _Track, cur: _Seen | None, real: bool) -> bool:
        """O pico na janela de som e o último sinal (microfone ou som) do app; a leitura real sem sinal conta para o fim.
        Devolve se há som na janela."""
        now = self._now
        if cur is not None:
            track.sound.append((now, cur.peak))
        if cur is not None and (cur.mic or cur.peak >= SOUND_MIN):
            track.last_signal, track.silent = now, 0
        elif real:
            track.silent += 1
        while track.sound and track.sound[0][0] <= now - SOUND_WINDOW_S:
            track.sound.popleft()
        return any(peak >= SOUND_MIN for _t, peak in track.sound)

    def _count(self, track: _Track, rule: str) -> None:
        """A contagem seguida da regra: uma leitura perdida passa, duas zeram (ARMANDO)."""
        now, track.hit = self._now, bool(rule)
        if not rule:
            track.misses += 1
            if track.state == State.ARMING and track.misses >= 2:
                track.state = State.QUIET
            return
        track.misses = 0
        if track.state == State.QUIET and now >= track.grace_until:
            track.state, track.started, track.last_signal = State.ARMING, now, now
            track.rule, track.since = rule, now
        elif track.state == State.ARMING and rule != track.rule:
            track.rule, track.since = rule, now

    def _silence(self, exe: str, track: _Track, events: list[Event]) -> None:
        """O app foi desligado ("Não avisar para…") com a chamada sem resposta: fecha o aviso e dispensa."""
        if track.state == State.PROMPTED and not track.recording:
            events.append(ClosePrompt(exe, "desligado"))
        track.state = State.DISMISSED

    def _finish(self, exe: str, track: _Track, events: list[Event]) -> None:
        """30 s e 3 leituras seguidas sem microfone e sem som: a chamada acabou (com carência depois de "Agora não" ou sem
        resposta)."""
        now = self._now
        if track.state not in EPISODE or now - track.last_signal < END_S or track.silent < END_READS:
            return
        if track.state == State.PROMPTED and not track.recording:
            events.append(ClosePrompt(exe, "sumiu"))
        events.append(EpisodeEnded(exe, round(track.last_signal - track.started, 1), track.state.value))
        grace = now + GRACE_S if track.state in (State.DISMISSED, State.PENDING) else 0.0
        self._tracks[exe] = _Track(label=track.label, grace_until=grace, sound=track.sound)

    def _cover(self, events: list[Event]) -> None:
        """Gravando, salvando ou com o "Antes de gravar" aberto: o aviso à vista fecha e quem tem sinal de regra (ou o
        aviso) fica coberto até o fim da chamada."""
        for exe, track in self._tracks.items():
            if track.state == State.PROMPTED and not track.recording:
                events.append(ClosePrompt(exe, "gravação"))
            if track.state == State.PROMPTED or (track.hit and track.state != State.COVERED):
                if track.state == State.QUIET:
                    track.started = track.last_signal = self._now
                track.state, track.recording = State.COVERED, False

    # ---------------------------------------------------------------- o aviso
    def _offer(self, modal: bool, params: Params, events: list[Event]) -> None:
        """Um aviso por vez: o primeiro pronto (o mais antigo na contagem), se não houver diálogo modal nem aviso à vista e
        o teto por hora deixar."""
        if modal or any(self._visible(t) for t in self._tracks.values()) or not self._room():
            return
        ready = [(t.since, exe) for exe, t in self._tracks.items() if self._ready(t, params)]
        if not ready:
            return
        exe = min(ready)[1]
        track = self._tracks[exe]
        reminder = track.state == State.PENDING
        start = track.started if reminder else track.since
        events.append(Prompt(exe, track.label, track.rule, int(self._now - start), reminder))
        track.state, track.reminded = State.PROMPTED, track.reminded or reminder
        self._prompts.append(self._now)

    def _ready(self, track: _Track, params: Params) -> bool:
        if not track.hit:
            return False
        if track.state == State.ARMING:
            limit = {"R3": OTHER_S, "R4": LISTEN_S}.get(track.rule, _clamp(params.seconds))
            return self._now - track.since >= limit
        return (track.state == State.PENDING and params.remind and not track.reminded and track.expired_at is not None
                and self._now - track.expired_at >= REMIND_S)

    def _room(self) -> bool:
        while self._prompts and self._prompts[0] <= self._now - HOUR_S:
            self._prompts.popleft()
        return len(self._prompts) < PER_HOUR

    @staticmethod
    def _visible(track: _Track) -> bool:
        return track.state == State.PROMPTED and not track.recording

    # ---------------------------------------------------------------- as respostas (o L3)
    def dismiss(self, exe: str) -> None:
        """"Agora não", o ✕, ou o Esc / fechar sem gravar no "Antes de gravar" aberto pelo aviso: até o fim da chamada."""
        track = self._tracks.get(exe.lower())
        if track is not None and track.state != State.QUIET:
            track.state, track.recording = State.DISMISSED, False

    def expired(self, exe: str, now: float | None = None) -> None:
        """O aviso sumiu sem clique (20 s): pendente; com `remind`, um lembrete só, 2 min depois de `now` (o relógio de quem
        chama; sem ele, o do último passo). F5.7 L3 (R-05): vale também para o aviso à espera da gravação, o "Gravar" do
        "Antes de gravar" que não começou: a chamada volta a pendente, não vira "Agora não"."""
        track = self._tracks.get(exe.lower())
        if track is not None and track.state == State.PROMPTED:
            track.state, track.recording = State.PENDING, False
            track.expired_at = self._now if now is None else now

    def record(self, exe: str) -> None:
        """"Gravar…" abriu o "Antes de gravar": segue avisado até o `busy` chegar (aí coberto)."""
        track = self._tracks.get(exe.lower())
        if track is not None and track.state in (State.PROMPTED, State.PENDING):
            track.state, track.recording = State.PROMPTED, True

    # ---------------------------------------------------------------- o estado para fora
    def state(self, exe: str) -> State:
        track = self._tracks.get(exe.lower())
        return track.state if track is not None else State.QUIET

    def pending(self) -> tuple[Pending, ...]:
        return tuple(Pending(exe, t.label) for exe, t in sorted(self._tracks.items())
                     if t.state == State.PENDING or self._visible(t))

    def mode(self) -> str:
        """"parado" (desligado ou ocupado), "armando" (contando ou sem resposta), "lento" (só chamadas respondidas
        esperando o fim) ou "ocioso"."""
        if self._stopped:
            return MODE_STOPPED
        states = {t.state for t in self._tracks.values()}
        if states & {State.ARMING, State.PROMPTED, State.PENDING}:
            return MODE_ARMING
        return MODE_SLOW if states & {State.COVERED, State.DISMISSED} else MODE_IDLE

    # ---------------------------------------------------------------- auxiliares
    def _kind(self, exe: str, params: Params) -> str | None:
        """A classe do app: de reunião ligado, navegador (com "Reunião no navegador"), outro (fora da lista) ou None (um
        app da lista desligado, ou "Não avisar para…")."""
        if exe in params.off:
            return None
        watched = self._watched.get(exe)
        if watched is not None and watched.kind == KIND_BROWSER:
            return KIND_BROWSER if params.browsers else None
        if exe in (params.apps if params.apps is not None else self._default_apps):
            return KIND_MEETING
        if watched is not None:
            return None
        return KIND_OTHER if params.other_apps else None

    def _label(self, exe: str, kind: str | None, cur: _Seen) -> str:
        watched = self._watched.get(exe)
        if kind == KIND_BROWSER:
            name = watched.label if watched is not None else app_name(cur.name)
            return f"{cur.service} no {name}" if cur.service else name
        if watched is not None:
            return watched.label
        return app_name(cur.name)

    def _reset(self, reason: str) -> list[Event]:
        events: list[Event] = [ClosePrompt(exe, reason) for exe, t in sorted(self._tracks.items()) if self._visible(t)]
        self._tracks.clear()
        return events

    def _forget(self) -> None:
        """Solta os quietos sem nada guardado (sem carência e sem som na janela)."""
        for exe in [e for e, t in self._tracks.items()
                    if t.state == State.QUIET and not t.sound and self._now >= t.grace_until]:
            del self._tracks[exe]
