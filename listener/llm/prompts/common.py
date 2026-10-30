"""O que os prompts compartilham (contrato "Prompts"): contexto da reunião, linhas `#<segment_id> [mm:ss] <rótulo>: <texto>`,
instrução de idioma (`output_language` injetado; glossário, nomes e siglas nunca traduzidos). Instruções e contrato em
pt-BR, o idioma padrão do app (D-071; antes, em inglês por experimento); `{idioma}` vira o nome do idioma em português.
Nenhum SDK aqui. F5.6 L2 (A-24 a A-26): cada tarefa separa as `INSTRUCTIONS` (o papel e o estilo, que o usuário pode
trocar nas Configurações) do `CONTRACT` (formato, esquema, citações, rótulos de voz: fixo, CP2); `system_text` junta os
dois, e o padrão sai igual, byte a byte, à cópia congelada de `tests/data/prompts_ptbr.json`."""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...config.prompts import VARIABLE
from ..types import Message, Role

LANGUAGE_NAMES = {"pt-br": "português do Brasil", "pt": "português", "en": "inglês", "es": "espanhol", "fr": "francês",
                  "de": "alemão", "it": "italiano"}
VOICE_LABELS = "Rótulos de voz como 'Voz 2' são nomes: copie-os exatamente, nunca os traduza."   # F5.5 (A-20, P-03)
ORIGINAL_LANGUAGE = "o idioma original"   # `{idioma}` sem idioma conhecido (a transcrição pelo áudio sem dica)
LINE_RE = re.compile(r"^#(?P<id>\d+) \[(?P<mm>\d+):(?P<ss>\d{2})\] (?P<speaker>[^:]+): (?P<text>.*)$")


@dataclass(frozen=True, slots=True)
class Line:
    segment_id: int
    t_start_ms: int
    speaker: str                  # "Ana (CFO)" para resolvidos, "Voz 2" sem pessoa (F5.5; na identidade, "SPK_3" na pendente)
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
    return (f"Escreva todo texto livre em {language_name(output_language)}. Nunca traduza nomes próprios, siglas, nomes de "
            "produto nem termos do glossário; mantenha-os exatamente como aparecem na transcrição.")


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
    parts = [f"Data da reunião: {ctx.date}"]
    if ctx.title:
        parts.append(f"Título: {ctx.title}")
    if ctx.participants_text:
        parts.append(f"Participantes esperados (como o usuário digitou): {ctx.participants_text}")
    if ctx.expected:
        parts.append("Participantes esperados com cadastro: " + "; ".join(p.render() for p in ctx.expected))
    if ctx.speakers:
        parts.append("Falantes identificados nesta reunião (person_id, nome — cargo, empresa): "
                     + "; ".join(p.render() for p in ctx.speakers))
    if ctx.glossary:
        parts.append(f"Glossário (mantenha a grafia): {ctx.glossary}")
    if ctx.markers:
        parts.append("Marcadores que o usuário pôs durante a reunião: "
                     + "; ".join(f"marcador {m.id} em [{stamp(m.t_ms)}]" + (f': "{m.note}"' if m.note else "")
                                 for m in ctx.markers))
    return "\n".join(parts)


def system(text: str, ctx: MeetingCtx) -> Message:
    return Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}")


def own(default: str, custom: str | None, language: str | None) -> str:
    """As instruções da tarefa (F5.6 L2, A-26): as do usuário, sem os espaços das pontas e com `{idioma}` trocado pelo nome
    do idioma, ou as do app (`custom` vazio ou None). Só essa troca: nada de `str.format` sobre o texto do usuário; chaves
    soltas vão como estão (não quebram nem injetam campos)."""
    text = (custom or "").strip()
    if not text:
        return default
    return text.replace(VARIABLE, language_name(language) if language else ORIGINAL_LANGUAGE)


def compose(instructions: str, contract: str) -> str:
    """Instruções e contrato numa mensagem: separados por um espaço ou por uma quebra de linha quando as instruções têm
    mais de uma linha."""
    sep = "\n" if "\n" in instructions else " "
    return f"{instructions}{sep}{contract}"


def system_text(default: str, contract: str, custom: str | None, language: str | None) -> str:
    """O texto do sistema de uma tarefa: as instruções (as do usuário ou `default`) e depois o contrato, que tem a última
    palavra (A-24)."""
    return compose(own(default, custom, language), contract)


def user(*blocks: str) -> Message:
    return Message(Role.USER, "\n\n".join(b for b in blocks if b))


def ids_schema() -> dict:
    return {"type": "array", "items": {"type": "integer"}}


def text_block(name: str, lines: Sequence[Line]) -> str:
    return f"{name} (formato: #segment_id [mm:ss] falante: texto):\n{render_lines(lines)}"
