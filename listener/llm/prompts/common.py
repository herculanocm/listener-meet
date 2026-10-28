"""O que os prompts compartilham (contrato "Prompts"): contexto da reunião, linhas `#<segment_id> [mm:ss] <rótulo>: <texto>`,
instrução de idioma (`output_language` injetado; glossário, nomes e siglas nunca traduzidos). Instruções em inglês com
exemplos em pt-BR [exp: melhor obediência entre modelos]. Nenhum SDK aqui."""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..types import Message, Role

LANGUAGE_NAMES = {"pt-br": "Brazilian Portuguese", "pt": "Portuguese", "en": "English", "es": "Spanish", "fr": "French", "de": "German",
                  "it": "Italian"}
LINE_RE = re.compile(r"^#(?P<id>\d+) \[(?P<mm>\d+):(?P<ss>\d{2})\] (?P<speaker>[^:]+): (?P<text>.*)$")


@dataclass(frozen=True, slots=True)
class Line:
    segment_id: int
    t_start_ms: int
    speaker: str                  # "Ana (CFO)" para resolvidos, "SPK_3" para pendentes
    text: str
    label: str | None = None      # rótulo do cluster (SPK_n) mesmo quando o nome está resolvido

    @property
    def stamp(self) -> str:
        return stamp(self.t_start_ms)

    def render(self) -> str:
        return f"#{self.segment_id} [{self.stamp}] {self.speaker}: {self.text}"


@dataclass(frozen=True, slots=True)
class Person:
    id: int
    name: str
    role: str = ""
    company: str = ""

    def render(self) -> str:
        extra = ", ".join(x for x in (self.role, self.company) if x)
        return f"({self.id}) {self.name}" + (f" — {extra}" if extra else "")


@dataclass(frozen=True, slots=True)
class Marker:
    id: int
    t_ms: int
    note: str


@dataclass(frozen=True, slots=True)
class MeetingCtx:
    title: str | None
    date: str                     # AAAA-MM-DD da reunião (a LLM devolve prazos absolutos a partir dela)
    participants_text: str | None
    expected: tuple[Person, ...]  # participantes esperados casados com `people`
    markers: tuple[Marker, ...]
    output_language: str          # "pt-BR" ou "en": o idioma da reunião (F5.3 A-02); o chat global usa o padrão
    glossary: str = ""
    speakers: tuple[Person, ...] = ()   # U-041 (A-10): quem fala, identificado, com cargo e empresa (nunca o e-mail)


def stamp(t_ms: int) -> str:
    s = max(0, int(t_ms) // 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


def language_instruction(output_language: str) -> str:
    return (f"Write all free text in {language_name(output_language)}. Never translate proper names, acronyms, product names "
            "or glossary terms; keep them exactly as they appear in the transcript.")


def render_lines(lines: Iterable[Line]) -> str:
    return "\n".join(ln.render() for ln in lines)


def parse_lines(text: str) -> list[Line]:
    """Inverso de `render_lines` (o fake usa para responder de acordo com o pedido)."""
    out: list[Line] = []
    for raw in text.splitlines():
        m = LINE_RE.match(raw.strip())
        if m:
            out.append(Line(int(m["id"]), (int(m["mm"]) * 60 + int(m["ss"])) * 1000, m["speaker"], m["text"]))
    return out


def header(ctx: MeetingCtx) -> str:
    parts = [f"Meeting date: {ctx.date}"]
    if ctx.title:
        parts.append(f"Title: {ctx.title}")
    if ctx.participants_text:
        parts.append(f"Expected participants (as typed by the user): {ctx.participants_text}")
    if ctx.expected:
        parts.append("Expected participants with a profile: " + "; ".join(p.render() for p in ctx.expected))
    if ctx.speakers:
        parts.append("Identified speakers in this meeting (person_id, name — role, company): "
                     + "; ".join(p.render() for p in ctx.speakers))
    if ctx.glossary:
        parts.append(f"Glossary (keep spelling): {ctx.glossary}")
    if ctx.markers:
        parts.append("Markers set by the user during the meeting: " + "; ".join(f"marker {m.id} at [{stamp(m.t_ms)}]"
                                                                               + (f': "{m.note}"' if m.note else "") for m in ctx.markers))
    return "\n".join(parts)


def system(text: str, ctx: MeetingCtx) -> Message:
    return Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}")


def user(*blocks: str) -> Message:
    return Message(Role.USER, "\n\n".join(b for b in blocks if b))


def ids_schema() -> dict:
    return {"type": "array", "items": {"type": "integer"}}


def text_block(name: str, lines: Sequence[Line]) -> str:
    return f"{name} (format: #segment_id [mm:ss] speaker: text):\n{render_lines(lines)}"
