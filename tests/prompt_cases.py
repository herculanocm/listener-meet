"""Contextos representativos de cada prompt da LLM (F5.6 L2): a cópia congelada de `tests/data/prompts_ptbr.json` saiu
destes casos rodados no código em pt-BR (D-071); `test_prompts_custom.py` monta os mesmos casos no código de hoje e compara
byte a byte. As falas trazem chaves soltas e `{idioma}` de propósito: o dado da reunião nunca passa pelo substituidor."""
from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

from listener.llm import prompts
from listener.llm.prompts.common import Line, Marker, MeetingCtx, Person
from listener.llm.prompts.rag_chat import ExcerptLine, MeetingBlock
from listener.llm.types import AudioPart, Message

ANA = Person(7, "Ana Souza", "CFO", "ACME")
BRUNO = Person(9, "Bruno Lima")
LINES = (
    Line(101, 0, "Ana Souza (CFO)", "Bom dia, vamos começar pelo orçamento.", "SPK_1"),
    Line(102, 65_000, "SPK_2", "Valeu, Ana. O corte de 8% em {marketing} fica?", "SPK_2"),
    Line(103, 125_500, "Voz 3", "Fica, e o prazo é sexta. {idioma} {} {x} }{", "SPK_3"),
    Line(104, 3_725_000, "Ana Souza (CFO)", 'Fechado: {"text": "ok"}.', "SPK_1"),
)


def meeting(language: str = "pt-BR", *, full: bool = True) -> MeetingCtx:
    if not full:
        return MeetingCtx(None, "2026-10-29", None, (), (), language)
    return MeetingCtx("Orçamento 2027 {Q1}", "2026-10-29", "Ana, Bruno e {fulano}", (ANA, BRUNO),
                      (Marker(1, 70_000, "corte {8%}"), Marker(2, 130_000, "")), language, glossary="ACME, OKR, {idioma}",
                      speakers=(ANA,))


MINUTES_JSON = '{"context": "Orçamento {2027}", "decisions": [{"text": "Corte de 8%", "segment_ids": [102]}]}'
PARTIAL_JSON = '{"summary": "Orçamento", "decisions": [], "actions": [{"text": "Enviar {planilha}", "caption_id": 3, "owner": null}]}'
HISTORY = tuple(("assistant" if i % 2 else "user", f"mensagem {i} {{x}}") for i in range(11))
AUDIO = AudioPart(b"\x00" * 64, "audio/flac", "janela 1 {idioma}")
BLOCKS = (
    MeetingBlock("R1", 5, "Orçamento {2027}", "2026-10-20", "Ana Souza — CFO",
                 (ExcerptLine(65_000, "O corte de 8% fica?", "Voz 2", 102),
                  ExcerptLine(None, "Corte de 8% aprovado", kind="decisao"),
                  ExcerptLine(125_000, "Enviar a planilha", kind="acao"))),
    MeetingBlock("R2", 6, "", "", "", (ExcerptLine(5_000, "Abertura {idioma}", "Ana Souza — CFO", 11),)),
)


def _cases() -> dict[str, tuple[ModuleType, str, Any]]:
    """Nome do caso -> (módulo, nome da função de montagem, contexto)."""
    p = prompts
    full, en, bare = meeting(), meeting("en"), meeting("pt-BR", full=False)
    return {
        "identify/full": (p.identify, "build", p.identify.IdentifyCtx(full, LINES, ("SPK_2",), (ANA, BRUNO))),
        "identify/bare-en": (p.identify, "build", p.identify.IdentifyCtx(meeting("en", full=False), LINES[:2], ("SPK_2", "SPK_3"), ())),
        "revise/context": (p.revise, "build", p.revise.ReviseCtx(full, LINES[:1], LINES[1:3], LINES[3:])),
        "revise/bare-en": (p.revise, "build", p.revise.ReviseCtx(meeting("en", full=False), (), LINES, ())),
        "title/summary": (p.title, "build", p.title.TitleCtx(full, "Corte de 8% aprovado {ok}")),
        "title/opening-en": (p.title, "build", p.title.TitleCtx(en, "#101 [00:00] Ana: Bom dia", opening=True)),
        "pre_summary/full": (p.pre_summary, "build", p.executive.SummaryCtx(full, MINUTES_JSON)),
        "pre_summary/bare-en": (p.pre_summary, "build", p.executive.SummaryCtx(meeting("en", full=False), "{}")),
        "executive/full": (p.executive, "build", p.executive.SummaryCtx(full, MINUTES_JSON)),
        "executive/en": (p.executive, "build", p.executive.SummaryCtx(en, MINUTES_JSON)),
        "minutes/direct": (p.minutes, "build", p.minutes.MinutesCtx(full, LINES, (ANA, BRUNO))),
        "minutes/block-en": (p.minutes, "build", p.minutes.MinutesCtx(meeting("en", full=False), LINES[2:], (), (1, 3))),
        "minutes/reduce": (p.minutes, "build_reduce", p.minutes.ReduceCtx(full, (MINUTES_JSON, "{}"), (ANA,))),
        "minutes/reduce-en": (p.minutes, "build_reduce", p.minutes.ReduceCtx(meeting("en", full=False), ("{}",), ())),
        "partial/first": (p.partial, "build", p.partial.PartialCtx(bare, None, LINES[:2])),
        "partial/next-en": (p.partial, "build", p.partial.PartialCtx(en, PARTIAL_JSON, LINES[2:])),
        "chat/history": (p.chat, "build", p.chat.ChatCtx(full, LINES, HISTORY, "Qual o prazo {x}?")),
        "chat/first-en": (p.chat, "build", p.chat.ChatCtx(meeting("en", full=False), LINES[:1], (), "Who opened?")),
        "rag_chat/blocks": (p.rag_chat, "build", p.rag_chat.RagChatCtx(BLOCKS, HISTORY, "O que decidimos {x}?", "pt-BR")),
        "rag_chat/empty-en": (p.rag_chat, "build", p.rag_chat.RagChatCtx((), (), "Anything?", "en")),
        "glossary/full": (p.glossary, "build", p.glossary.GlossaryCtx("Orçamento {2027}", "Ana, {Bia}", ("Bia",), (ANA,), "OKR", "pt-BR")),
        "glossary/bare-en": (p.glossary, "build", p.glossary.GlossaryCtx(None, None, (), (), "", "en")),
        "transcribe/hints": (p.transcribe, "build", p.transcribe.TranscribeCtx(AUDIO, "pt", "ACME, {idioma}, OKR")),
        "transcribe/bare": (p.transcribe, "build", p.transcribe.TranscribeCtx(AUDIO, None, "")),
    }


CASES = _cases()


def dump(messages: tuple[Message, ...]) -> list[list[Any]]:
    """Cada mensagem como [papel, texto, rótulos do áudio]: o que o provedor recebe, sem os bytes do áudio."""
    return [[str(m.role), m.text, [a.label for a in m.audio]] for m in messages]


def builder(name: str) -> Callable[..., tuple[Message, ...]]:
    module, fn, _ctx = CASES[name]
    return getattr(module, fn)


def render(name: str, *args: Any, **kwargs: Any) -> list[list[Any]]:
    return dump(builder(name)(CASES[name][2], *args, **kwargs))
