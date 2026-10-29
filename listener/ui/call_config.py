"""F5.7 (L3): as Configurações do aviso de reunião sem gravação, sem Qt. Liga as chaves `detect.*` aos `Params` das regras
(`ui/call_rules.py`) e monta as linhas da lista "Apps observados" (U-094), que o cartão das Configurações
(`ui/settings_detect.py`) mostra e a bandeja edita ("Não avisar para o Teams").

As listas guardam executáveis separados por vírgula, com a caixa como o sistema os mostrou (o rótulo de um app acrescentado
sai daí) e comparados em minúsculas:
- `detect.apps`: os acrescentados pelo dono, que viram app de reunião (R1); vazio é só a lista de fábrica da plataforma;
- `detect.off`: os desmarcados, de fábrica, acrescentados ou "outros" desligados pela bandeja (R3); nunca avisam.

As linhas: cada app de reunião de fábrica numa linha só por rótulo (o Teams novo e o clássico são o "Teams"), desmarcada
quando algum executável dele está desligado; a "Reunião no navegador" com todos os navegadores, desmarcada só com todos
desligados (o navegador desligado sozinho pela bandeja ganha a linha dele, desmarcada); os acrescentados, com o "Remover";
e os desligados que não cabem em nenhuma, desmarcados. Nada aqui é de um sistema operacional (CP15): os executáveis vêm da
plataforma (`watched_apps`) e do que está tocando."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..config.defaults import SPECS
from ..log import get_logger
from ..platform.base import KIND_BROWSER, KIND_MEETING, WatchedApp
from .call_rules import Params, app_name, default_apps

log = get_logger("ui.call_config")

ENABLED, SECONDS, REMIND = "detect.enabled", "detect.seconds", "detect.remind"
APPS, OFF, OTHER_APPS = "detect.apps", "detect.off", "detect.other_apps"
KEYS = (ENABLED, SECONDS, REMIND, APPS, OFF, OTHER_APPS)
BROWSER_ROW = "Reunião no navegador"
BROWSER_AFTER = 2                    # a linha dos navegadores entra depois dos dois primeiros apps (Teams e Zoom, U-094)


def names(raw: object) -> tuple[str, ...]:
    """Os executáveis de uma lista salva, na ordem e sem repetir (a caixa fica; a comparação é em minúsculas)."""
    out: dict[str, str] = {}
    for part in str(raw or "").split(","):
        item = part.strip()
        if item and item.lower() not in out:
            out[item.lower()] = item
    return tuple(out.values())


def exe_set(raw: object) -> frozenset[str]:
    return frozenset(item.lower() for item in names(raw))


def join(items: Iterable[str]) -> str:
    """A lista para o banco: sem repetir (pela minúscula) e em ordem alfabética, para o texto não mudar à toa."""
    seen: dict[str, str] = {}
    for item in items:
        if item.strip():
            seen.setdefault(item.strip().lower(), item.strip())
    return ",".join(seen[key] for key in sorted(seen))


def read_raw(get_setting: Callable[[str], str]) -> tuple[str, ...]:
    """O texto salvo das seis chaves (quem relê a cada 10 s só converte quando ele muda: o valor inválido avisa uma vez)."""
    return tuple(str(get_setting(key)) for key in KEYS)


def values_from(raw: Sequence[str]) -> dict[str, Any]:
    """Os valores pela `SPECS`; o texto que ela recusa (só de um banco editado à mão) fica no padrão, com o aviso no log."""
    out: dict[str, Any] = {}
    for key, text in zip(KEYS, raw, strict=True):
        try:
            out[key] = SPECS[key].value(text)
        except (ValueError, TypeError):
            log.warning("configuração %s inválida: o aviso de reunião usa o padrão", key)
            out[key] = SPECS[key].default
    return out


def params_from(values: dict[str, Any], watched: Sequence[WatchedApp]) -> Params:
    """Os `Params` das regras: os de reunião são os de fábrica mais os acrescentados; os desligados valem para qualquer app."""
    return Params(enabled=bool(values[ENABLED]), seconds=float(values[SECONDS]), remind=bool(values[REMIND]),
                  apps=default_apps(watched) | exe_set(values[APPS]), browsers=True, other_apps=bool(values[OTHER_APPS]),
                  off=exe_set(values[OFF]))


def silence_names(watched: Sequence[WatchedApp], exe: str) -> tuple[str, ...]:
    """O que "Não avisar para o X" desliga: o app de reunião inteiro (todos os executáveis com o mesmo rótulo) ou só o
    executável (um navegador, um acrescentado, um outro app)."""
    low = exe.lower()
    match = next((w for w in watched if w.exe.lower() == low), None)
    if match is None or match.kind != KIND_MEETING:
        return (exe,)
    return tuple(w.exe for w in watched if w.kind == KIND_MEETING and w.label == match.label)


def label_for(watched: Sequence[WatchedApp], exe: str) -> str:
    """O nome de um executável na tela: o da lista de fábrica ou o do arquivo sem a extensão."""
    low = exe.lower()
    return next((w.label for w in watched if w.exe.lower() == low), app_name(exe))


@dataclass(frozen=True, slots=True)
class AppRow:
    """Uma linha de "Apps observados": o rótulo, os executáveis (com a caixa de origem), se é acrescentado (o "Remover") e
    se é a dos navegadores (desmarcada só com todos desligados)."""
    label: str
    exes: tuple[str, ...]
    added: bool = False
    browsers: bool = False

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(e.lower() for e in self.exes)

    def checked(self, off: frozenset[str]) -> bool:
        return not self.keys <= off if self.browsers else not self.keys & off


def _factory_rows(watched: Sequence[WatchedApp]) -> list[AppRow]:
    groups: dict[str, list[str]] = {}
    for w in watched:
        if w.kind == KIND_MEETING:
            groups.setdefault(w.label, []).append(w.exe)
    rows = [AppRow(label, tuple(exes)) for label, exes in groups.items()]
    browsers = tuple(w.exe for w in watched if w.kind == KIND_BROWSER)
    if browsers:
        rows.insert(min(BROWSER_AFTER, len(rows)), AppRow(BROWSER_ROW, browsers, browsers=True))
    return rows


def app_rows(watched: Sequence[WatchedApp], apps_raw: object, off_raw: object) -> list[AppRow]:
    """As linhas da lista, na ordem da tela: as de fábrica, os acrescentados e os desligados que não cabem em nenhuma."""
    rows = _factory_rows(watched)
    known = {key for row in rows for key in row.keys}
    for exe in names(apps_raw):
        if exe.lower() not in known:
            rows.append(AppRow(app_name(exe), (exe,), added=True))
            known.add(exe.lower())
    off = exe_set(off_raw)
    browsers = next((row for row in rows if row.browsers), None)
    all_browsers_off = browsers is not None and not browsers.checked(off)
    for exe in names(off_raw):
        low = exe.lower()
        in_browsers = browsers is not None and low in browsers.keys
        if (low in known and not in_browsers) or (in_browsers and all_browsers_off):
            continue
        rows.append(AppRow(label_for(watched, exe), (exe,)))
        known.add(low)
    return rows


def lists_from(rows: Iterable[tuple[AppRow, bool]]) -> tuple[str, str]:
    """(`detect.apps`, `detect.off`) das linhas como estão na tela (a linha e se está marcada)."""
    added: list[str] = []
    off: list[str] = []
    for row, checked in rows:
        if row.added:
            added.extend(row.exes)
        if not checked:
            off.extend(row.exes)
    return join(added), join(off)


__all__ = ["APPS", "BROWSER_ROW", "ENABLED", "KEYS", "OFF", "OTHER_APPS", "REMIND", "SECONDS", "AppRow", "app_name", "app_rows",
           "exe_set", "join", "label_for", "lists_from", "names", "params_from", "read_raw", "silence_names", "values_from"]
