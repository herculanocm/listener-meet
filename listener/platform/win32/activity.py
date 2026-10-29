"""F5.7 (L1): o sensor do aviso de reunião sem gravação no Windows (`Win32Activity`, herdada pelo `Win32Platform`).

Sinais por app (a raiz, D-055), em todos os microfones e todas as saídas ativas (`sessions.FlowSessions` com `keep`):
- microfone em uso: alguma sessão de captura no estado ATIVA (ler as sessões não conta como uso [fato, 29/10]);
- som: o maior pico das sessões de saída agora (a janela de 10 s é das regras, no L2);
- serviço: só para os executáveis pedidos em `titles_for`, pelo título da janela com padrão estrito. O título nunca sai
  daqui.
Ficam de fora a árvore do próprio app (a raiz de `os.getpid()`, que sob o venv é o lançador, e os descendentes) e a
sessão dos sons do sistema (pid 0). O Toolhelp32 (~30 ms) só roda quando aparece um pid sem raiz no cache de 60 s; o
EnumWindows (~0,3 ms), só quando um app de `titles_for` tem sessão de áudio (com ou sem som: o serviço segue firme
nas pausas da fala, e a R4 precisa dele). Log só com contagens e tempos (D-053)."""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from ...log import get_logger
from ..base import KIND_BROWSER, KIND_MEETING, AppAudio, AudioActivity, MicWatch, WatchedApp
from . import consent, endpoints, processes, sessions
from .processes import ProcInfo

log = get_logger("platform.win32.activity")

ROOT_TTL_S = 60.0              # pid -> raiz no cache (um pid reaproveitado nesse tempo herda a raiz antiga)
SLOW_READ_MS = 100.0           # a leitura acima disto vai para o DEBUG
WATCHED: tuple[WatchedApp, ...] = (
    WatchedApp("ms-teams.exe", "Teams", KIND_MEETING), WatchedApp("teams.exe", "Teams", KIND_MEETING),
    WatchedApp("zoom.exe", "Zoom", KIND_MEETING),
    WatchedApp("webex.exe", "Webex", KIND_MEETING), WatchedApp("ciscocollabhost.exe", "Webex", KIND_MEETING),
    WatchedApp("atmgr.exe", "Webex", KIND_MEETING),
    WatchedApp("slack.exe", "Slack", KIND_MEETING),
    WatchedApp("msedge.exe", "Edge", KIND_BROWSER), WatchedApp("chrome.exe", "Chrome", KIND_BROWSER),
    WatchedApp("firefox.exe", "Firefox", KIND_BROWSER), WatchedApp("brave.exe", "Brave", KIND_BROWSER),
    WatchedApp("opera.exe", "Opera", KIND_BROWSER), WatchedApp("vivaldi.exe", "Vivaldi", KIND_BROWSER),
)
# Só a sala do Meet ("Meet - abc-defg-hij", com hífen ou travessão curto), no começo do título; o navegador acrescenta o
# resto (" - Google Chrome"). "Google Meet - ..." é a página inicial, não uma sala (R-06). As dicas soltas de
# `MEETING_TITLE_HINTS` ("call", "meet") não servem aqui: "Call of Duty - YouTube" não pode virar reunião.
MEET_TITLE = re.compile(r"^Meet [-\N{EN DASH}] [a-z]{3}-[a-z]{4}-[a-z]{3}(?=$|\s)")
INTERPRETERS = frozenset({"python.exe", "pythonw.exe"})   # o próprio app e os filhos dele (o ao vivo, os workers)


def match_service(title: str) -> str:
    """O serviço de reunião pelo título da janela de um navegador, com padrão estrito; vazio quando não reconhece."""
    if MEET_TITLE.match(title):
        return "Google Meet"
    # TODO(F5.7): Teams e Zoom no navegador, com o título de uma reunião real anotado pelo dono. O sufixo
    # "| Microsoft Teams" também aparece nas páginas de chat (frouxo demais); até lá, `service` vazio (a R2 vale com
    # microfone e som).
    return ""


def own_tree(pid: int, procs: dict[int, ProcInfo]) -> frozenset[int]:
    """O próprio app: a raiz de `pid` (sob o venv, o lançador `python.exe` pai do interpretador) e os descendentes com o
    executável do interpretador (o filho ao vivo, os workers). Um navegador aberto por um link do app, ou um processo cujo
    ppid velho caiu num pid reaproveitado pelo lançador (o explorer), ficam fora (R-02)."""
    root = processes.app_root(pid, procs) if pid in procs else pid
    names = INTERPRETERS | ({procs[pid].name.lower()} if pid in procs else set())
    children: dict[int, list[int]] = {}
    for p in procs.values():
        if p.name.lower() in names:
            children.setdefault(p.ppid, []).append(p.pid)
    out, todo = {root}, [root]
    while todo:
        for child in children.get(todo.pop(), ()):
            if child not in out:
                out.add(child)
                todo.append(child)
    return frozenset(out | {pid})


class RootCache:
    """pid -> (raiz, executável da raiz) por `ttl_s`, só para os pids da leitura atual (o que some sai do cache). O
    Toolhelp32 só roda quando aparece um pid sem raiz guardada (ou vencida); na mesma leitura, o pid da árvore do próprio
    app, o que saiu e o sem nome ficam marcados como fora."""

    def __init__(self, snapshot: Callable[[], dict[int, ProcInfo]] = processes.snapshot_processes,
                 clock: Callable[[], float] = time.monotonic, own_pid: int | None = None, ttl_s: float = ROOT_TTL_S) -> None:
        self._snapshot, self._clock, self._ttl = snapshot, clock, ttl_s
        self._own = os.getpid() if own_pid is None else own_pid
        self._cache: dict[int, tuple[int, str, float]] = {}       # pid -> (raiz, exe, vence); raiz 0 = fora
        self.snapshots = 0                                          # instrumentação: retratos de processos tirados

    def resolve(self, pids: Iterable[int]) -> dict[int, tuple[int, str]]:
        """A raiz e o executável de cada pid de dentro; os de fora (o próprio app, o pid 0, o que saiu) não aparecem."""
        now = self._clock()
        wanted = {p for p in pids if p > 0}
        self._cache = {p: e for p, e in self._cache.items() if p in wanted}   # R-07: o pid que sumiu pode voltar noutro app
        stale = [p for p in wanted if p not in self._cache or self._cache[p][2] <= now]
        if stale:
            self._refresh(stale, now)
        out: dict[int, tuple[int, str]] = {}
        for p in wanted:
            root, exe, _until = self._cache[p]
            if root > 0:
                out[p] = (root, exe)
        return out

    def _refresh(self, pids: list[int], now: float) -> None:
        procs = self._snapshot()
        self.snapshots += 1
        own = own_tree(self._own, procs)
        until = now + self._ttl
        for p in pids:                                  # a raiz de um pid de fora da árvore do app também é de fora
            root = processes.app_root(p, procs) if p in procs else p
            info = procs.get(root)
            if info is not None and p not in own:
                self._cache[p] = (root, info.name, until)
            else:
                self._cache[p] = (0, "", until)


@dataclass(slots=True)
class AppSignals:
    """Os sinais de um app somados entre os pids dele: `mic` é um OU e `peak` o maior."""
    exe: str
    mic: bool = False
    peak: float = 0.0
    members: list[int] = field(default_factory=list)


def group_apps(mic_pids: Iterable[int], peaks: Mapping[int, float],
               roots: Mapping[int, tuple[int, str]]) -> dict[int, AppSignals]:
    """Os sinais por app (a raiz, D-055): o WebView2 do Teams soma no ms-teams.exe raiz. O pid fora de `roots` (o próprio
    app, o pid 0, o processo que saiu) fica de fora."""
    mic = set(mic_pids)
    apps: dict[int, AppSignals] = {}
    for pid in sorted(mic | set(peaks)):
        found = roots.get(pid)
        if found is None:
            continue
        root, exe = found
        app = apps.setdefault(root, AppSignals(exe))
        app.mic = app.mic or pid in mic
        app.peak = max(app.peak, peaks.get(pid, 0.0))
        app.members.append(pid)
    return apps


def services_for(apps: Mapping[int, AppSignals], titles_for: frozenset[str],
                 titles: Callable[[set[int]], dict[int, str]]) -> dict[int, str]:
    """O serviço de cada app de `titles_for` pelo título da janela da raiz ou, sem ela, do primeiro pid com janela; o
    EnumWindows só roda quando um desses apps tem sessão, e o texto só é lido para os pids deles (a raiz e os membros)."""
    wanted = {root: app for root, app in apps.items() if app.exe.lower() in titles_for}
    if not wanted:
        return {}
    pids = {p for root, app in wanted.items() for p in (root, *app.members)}
    try:
        by_pid = titles(pids)
    except Exception as e:
        log.debug("títulos de janela: %s", type(e).__name__)
        return {}
    out: dict[int, str] = {}
    for root, app in wanted.items():
        title = by_pid.get(root) or next((by_pid[m] for m in app.members if by_pid.get(m)), "")
        out[root] = match_service(title)
    return out


def to_activity(apps: Mapping[int, AppSignals], services: Mapping[int, str]) -> AudioActivity:
    out = [AppAudio(root, app.exe, app.mic, app.peak, services.get(root, "")) for root, app in apps.items()]
    out.sort(key=lambda a: (a.exe.lower(), a.pid))
    return AudioActivity(tuple(out))


class ActivityReader:
    """As leituras do sensor numa thread só (a do L3): os gerenciadores de captura e de saída ficam guardados e `close()`
    os solta nela. A leitura inteira que falha solta tudo e levanta; o endpoint ou a sessão que falha é pulado."""

    def __init__(self, capture: sessions.FlowSessions | None = None, render: sessions.FlowSessions | None = None,
                 roots: RootCache | None = None,
                 titles: Callable[[set[int]], dict[int, str]] = processes.window_titles_by_pid) -> None:
        self._capture = capture if capture is not None else sessions.FlowSessions(endpoints.FLOW_CAPTURE, keep=True)
        self._render = render if render is not None else sessions.FlowSessions(endpoints.FLOW_RENDER, keep=True)
        self._roots = roots if roots is not None else RootCache()
        self._titles = titles
        self.last_ms = 0.0                                          # instrumentação: a última leitura

    def read(self, titles_for: frozenset[str] = frozenset()) -> AudioActivity:
        t0 = time.perf_counter()
        try:
            mic, peaks = self._signals()
            roots = self._roots.resolve(mic | set(peaks))
        except Exception:
            self.close()
            raise
        apps = group_apps(mic, peaks, roots)
        activity = to_activity(apps, services_for(apps, titles_for, self._titles))
        self.last_ms = (time.perf_counter() - t0) * 1000
        if self.last_ms > SLOW_READ_MS:
            log.debug("leitura do sensor lenta: %.0f ms (%d apps)", self.last_ms, len(activity.apps))
        return activity

    def _signals(self) -> tuple[set[int], dict[int, float]]:
        captured, rendered = self._capture.read(), self._render.read()
        for flow, reading in ((self._capture, captured), (self._render, rendered)):
            if flow.failed and not reading:                         # todos os endpoints do fluxo falharam
                raise OSError(f"nenhum endpoint do fluxo {flow.flow} respondeu ({flow.failed} falharam)")
        mic = {s.pid for group in captured.values() for s in group if s.state == sessions.STATE_ACTIVE}
        return mic, sessions.peaks_by_pid(rendered.values())

    def close(self) -> None:
        self._capture.close()
        self._render.close()


class Win32Activity:
    """F5.7 (L1): a parte do sensor no `Win32Platform`, herdada por ele (o `platform.py` não cresce). Quem chama o quê:
    - `audio_activity` e `close_audio_activity`: só na thread do sensor (L3). Os gerenciadores COM nascem, vivem e
      morrem nela; `close_audio_activity` antes de ela sair. Com `degraded`, só o registro (sem COM).
    - `mic_users`, `watched_apps` e `mic_watch`: em qualquer thread (registro e ctypes, sem COM).
    - o `MicWatch` devolvido: `wait` e `close` na thread do sensor; `cancel` de qualquer thread."""

    _activity: ActivityReader | None = None

    def audio_activity(self, titles_for: frozenset[str] = frozenset(), degraded: bool = False) -> AudioActivity | None:
        if degraded:
            entries = consent.mic_entries()
            return consent.degraded_activity(entries) if entries is not None else None
        if self._activity is None:
            self._activity = ActivityReader()
        return self._activity.read(titles_for)

    def close_audio_activity(self) -> None:
        if self._activity is not None:
            self._activity.close()

    def mic_users(self) -> frozenset[str] | None:
        entries = consent.mic_entries()
        return consent.user_ids(entries) if entries is not None else None

    def watched_apps(self) -> tuple[WatchedApp, ...]:
        return WATCHED

    def mic_watch(self) -> MicWatch | None:
        return consent.Win32MicWatch.open()
