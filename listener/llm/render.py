"""Markdown da ata (CP2: o formato é do projeto), determinístico a partir do JSON validado de `minutes`: `[mm:ss]` calculado
de `segments.t_start_ms` do primeiro `segment_id` válido; item sem evidência válida ganha a marca "sem evidência".
`text_llm` é este Markdown; `text_edited` a edição humana. Títulos, rótulos e a marca "sem evidência" no idioma da saída
(F5.3 A-07: "no evidence" na ata em inglês)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .evidence import NO_EVIDENCE, first_time_ms, valid_segment_ids
from .prompts.common import stamp

HEADINGS = {   # seções, os rótulos da ação e a marca do item sem evidência; o índice (`rag/chunker.py`) lê as seções e a marca
    "pt": ("Contexto", "Decisões", "Ações", "Destaques dos marcadores", "Pendências", "Próximos passos", "dono", "prazo",
           NO_EVIDENCE),
    "en": ("Context", "Decisions", "Actions", "Marker highlights", "Open points", "Next steps", "owner", "due", "no evidence"),
    "es": ("Contexto", "Decisiones", "Acciones", "Momentos marcados", "Pendientes", "Próximos pasos", "responsable", "plazo",
           "sin evidencia"),
}


def _headings(language: str) -> tuple[str, ...]:
    code = (language or "pt").lower().split("-")[0]
    return HEADINGS.get(code, HEADINGS["en"] if code != "pt" else HEADINGS["pt"])


def _mark(item: Mapping[str, Any], segments: Mapping[int, tuple[str, int]], no_evidence: str) -> str:
    ids = valid_segment_ids(item.get("segment_ids") or (), segments)
    t = first_time_ms(ids, segments)
    return f" [{stamp(t)}]" if t is not None else f" _({no_evidence})_"


def render_minutes(data: Mapping[str, Any], segments: Mapping[int, tuple[str, int]], language: str = "pt-BR",
                   people: Mapping[int, str] | None = None, markers: Mapping[int, str] | None = None) -> str:
    """`segments`: id -> (texto, t_start_ms); `people`: id -> nome (dono resolvido); `markers`: id -> nota."""
    h_ctx, h_dec, h_act, h_high, h_open, h_next, w_owner, w_due, w_none = _headings(language)
    people = people or {}
    markers = markers or {}
    out: list[str] = []
    context = str(data.get("context") or "").strip()
    if context:
        out += [f"## {h_ctx}", "", context, ""]
    out += [f"## {h_dec}", ""]
    out += [f"- {str(d.get('text') or '').strip()}{_mark(d, segments, w_none)}" for d in data.get("decisions") or ()] or ["- —"]
    out += ["", f"## {h_act}", ""]
    actions = data.get("actions") or ()
    for a in actions:
        pid = a.get("owner_person_id")
        owner = people.get(int(pid)) if isinstance(pid, int) and int(pid) in people else (a.get("owner_text") or None)
        due = a.get("due_date") or a.get("due_text") or None
        tail = "".join(f" · {label}: {value}" for label, value in ((w_owner, owner), (w_due, due)) if value)
        out.append(f"- {str(a.get('text') or '').strip()}{tail}{_mark(a, segments, w_none)}")
    if not actions:
        out.append("- —")
    highlights = data.get("highlights") or ()
    if highlights:
        out += ["", f"## {h_high}", ""]
        for hl in highlights:
            note = markers.get(int(hl.get("marker_id", -1)), "")
            prefix = f"**{note}** — " if note else ""
            out.append(f"- {prefix}{str(hl.get('text') or '').strip()}{_mark(hl, segments, w_none)}")
    for heading, key in ((h_open, "open_points"), (h_next, "next_steps")):
        items = data.get(key) or ()
        if items:
            out += ["", f"## {heading}", ""]
            out += [f"- {str(i.get('text') or '').strip()}{_mark(i, segments, w_none)}" for i in items]
    return "\n".join(out).strip() + "\n"
