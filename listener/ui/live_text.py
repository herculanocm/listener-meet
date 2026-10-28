"""Os textos da janela "Ao vivo" (F4 U-021 a U-023), fora do `ui/live.py`, que passava de 400 linhas (F5.4 L4): a linha e a
nota de uma legenda, a chave dela (para não marcar duas vezes), o resumo parcial como HTML por classes (com "marcar") e a
nota do marcador de um item do parcial. Sem Qt: texto puro e as contas."""
from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ..llm.prompts.common import stamp

NOTE_MAX = 300                    # nota do marcador criado de uma legenda ou de um item do parcial
KIND_PREFIX = {"decisions": "Decisão", "actions": "Ação"}
KIND_TITLE = {"decisions": "Possíveis decisões", "actions": "Possíveis ações"}
STATE_TEXT = {"off": "legenda ao vivo desligada nesta gravação", "unavailable": "legenda ao vivo indisponível nesta gravação",
              "stopping": "gravação parada: terminando as últimas legendas", "stopped": "ao vivo encerrado"}

type MarkCaption = Callable[[Mapping[str, Any]], tuple[bool, str]]
type MarkItem = Callable[[str, Mapping[str, Any], int | None], tuple[bool, str]]
type Refresh = Callable[[], str]


def clip(text: str, limit: int = NOTE_MAX) -> str:
    """Espaços simples e no máximo `limit` caracteres (com reticências)."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def caption_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Identidade de uma legenda para não marcar duas vezes: o id do banco ou, sem ele, canal e tempo."""
    if row.get("id") is not None:
        return ("id", int(row["id"]))
    return ("t", str(row.get("channel") or ""), int(row.get("t_start_ms") or 0))


def caption_note(row: Mapping[str, Any]) -> str:
    """Nota do marcador de uma legenda (U-022): "Ana — CFO: texto" cortado em ~300 caracteres."""
    label, text = row.get("label"), str(row.get("text") or "")
    return clip(f"{label}: {text}" if label else text)


def caption_line(row: Mapping[str, Any]) -> str:
    label = row.get("label")
    text = " ".join(str(row.get("text") or "").split())
    return f"[{stamp(int(row.get('t_start_ms') or 0))}] " + (f"{label}: {text}" if label else text)


def item_key(kind: str, item: Mapping[str, Any]) -> tuple[str, str]:
    return kind, " ".join(str(item.get("text") or "").casefold().split())


def item_note(kind: str, item: Mapping[str, Any]) -> str:
    """"Decisão: ..." ou "Ação: ... (dono)" cortado em ~300 caracteres (U-023)."""
    owner = str(item.get("owner") or "").strip() if kind == "actions" else ""
    return clip(f"{KIND_PREFIX.get(kind, 'Item')}: {str(item.get('text') or '').strip()}" + (f" ({owner})" if owner else ""))


def partial_data(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """`data_json` do parcial (resumo, decisões e ações); vazio quando ilegível."""
    if not row:
        return {}
    try:
        data = json.loads(str(row.get("data_json") or "{}"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def partial_html(data: Mapping[str, Any], marked: set[tuple[str, str]], can_mark: bool) -> str:
    """O parcial como HTML: resumo e as listas com "marcar" (link `mark:<espécie>:<índice>`) ou "marcado"."""
    parts = [f"<p>{html.escape(str(data.get('summary') or '')).replace(chr(10), '<br>') or '<i>(sem texto)</i>'}</p>"]
    for kind, title in KIND_TITLE.items():
        items = [it for it in data.get(kind) or () if isinstance(it, Mapping)]
        if not items:
            continue
        parts.append(f"<p><b>{title}</b></p><ul>")
        for i, it in enumerate(items):
            t = it.get("t_ms")
            when = f"[{stamp(int(t))}] " if isinstance(t, int | float) else ""
            owner = f" <i>({html.escape(str(it['owner']))})</i>" if kind == "actions" and it.get("owner") else ""
            if item_key(kind, it) in marked:
                tail = ' · <span class="success">marcado ✓</span>'
            elif can_mark:
                tail = f' · <a href="mark:{kind}:{i}">marcar</a>'
            else:
                tail = ""
            parts.append(f"<li>{when}{html.escape(str(it.get('text') or ''))}{owner}{tail}</li>")
        parts.append("</ul>")
    return "".join(parts)


def hhmm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "?"


def speaker_parts(label: str | None) -> tuple[str, str]:
    """F5.4 L4 (U-081): o rótulo "Nome — Cargo" da legenda em nome e cargo (o cargo vazio quando não há)."""
    name, _sep, role = str(label or "").partition(" — ")
    return name.strip(), role.strip()


__all__ = ["KIND_PREFIX", "KIND_TITLE", "NOTE_MAX", "STATE_TEXT", "MarkCaption", "MarkItem", "Refresh", "caption_key", "caption_line",
           "caption_note", "clip", "hhmm", "item_key", "item_note", "partial_data", "partial_html", "speaker_parts"]
