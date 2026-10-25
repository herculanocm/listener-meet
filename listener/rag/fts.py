"""Busca por palavra, puro (F5 item 2, F5A-28): a consulta do usuário vira uma expressão FTS5 segura e o `snippet()` vira
HTML seguro. O usuário nunca escreve a sintaxe do FTS5: cada palavra vai entre aspas (aspas internas dobradas), então `AND`,
`OR`, `NOT`, `NEAR`, `:`, `^`, `-`, `*`, `+` e parênteses viram texto; E implícito entre as palavras e `*` (prefixo) na
última; com menos de 10 resultados a busca refaz com OU (`rag.search`). Palavra só de símbolos sai (o tokenizador `unicode61`
não guarda símbolo) e consulta vazia ou só de símbolos não vai ao FTS. Palavras vazias comuns (artigos, preposições,
pronomes interrogativos) saem quando sobra outra: a pergunta do chat "o que foi decidido sobre o orçamento?" vira
`"decidido" "orçamento?"*`, que acha mais com E e não varre o índice inteiro com OU.

O destaque troca os marcadores do `snippet()` por negrito depois do `html.escape` (nada do texto vira tag). O trecho achado
só pelo sentido não tem `snippet()`: `preview` destaca as mesmas palavras em Python, sem acento e sem caixa como o FTS."""
from __future__ import annotations

import functools
import html
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from ..store.rag import SNIPPET_CLOSE, SNIPPET_OPEN

MAX_TERMS = 32                   # uma consulta colada (um parágrafo) não vira uma expressão gigante; ficam as últimas
MIN_PREFIX = 3                   # "de*" casaria meio vocabulário (140 ms com 20 mil trechos): prefixo a partir de 3
PREVIEW_CHARS = 200

# Comparadas sem acento e em minúsculas (`fold`); "não" fica de fora de propósito (muda o sentido da busca)
_STOPWORDS = """
a ao aos as o os um uma uns umas de da das do dos em na nas no nos num numa por pela pelas pelo pelos para pra com sem sob
sobre entre ate e ou mas que se ja foi era ser sao esta estao este esse essa isso isto aquele aquela aquilo qual quais quem
como quando onde porque eu tu ele ela eles elas voce voces me te lhe vos meu minha meus minhas seu sua seus suas
the an of to in on at for and or is are was were be been what which who whom how when where why did do does about with that
this these those it its by from as
"""
STOPWORDS = frozenset(_STOPWORDS.split())

WORD_RE = re.compile(r"\w+")


def fold(text: str) -> str:
    """Minúsculas e sem acento ("Orçamento" -> "orcamento"), como o `remove_diacritics` do FTS."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _core(term: str) -> str:
    return fold("".join(ch for ch in term if ch.isalnum()))


def quote(term: str) -> str:
    """Uma palavra do usuário como frase literal do FTS5 (aspas internas dobradas)."""
    return '"' + term.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class FtsQuery:
    terms: tuple[str, ...]       # palavras do usuário que valem (sem as vazias e sem as só de símbolos), na ordem
    all_terms: str               # E implícito, `*` na última
    any_term: str                # OU (igual ao E com uma palavra só)


def terms(text: str) -> list[str]:
    """Palavras da consulta: sem as só de símbolos, sem repetição (sem acento e sem caixa) e, quando sobra alguma, sem as
    palavras vazias; até `MAX_TERMS`, as últimas (no seguimento do chat, a pergunta atual vem depois da anterior)."""
    words: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").split():
        core = _core(raw)
        if not core or core in seen:
            continue
        seen.add(core)
        words.append(raw)
    content = [w for w in words if _core(w) not in STOPWORDS]
    return (content or words)[-MAX_TERMS:]


def build_query(text: str) -> FtsQuery | None:
    """Consulta do usuário -> expressão FTS5 segura; None quando não sobra palavra (vazia ou só símbolos)."""
    words = terms(text)
    if not words:
        return None
    quoted = [quote(w) for w in words]
    if len(_core(words[-1])) >= MIN_PREFIX:
        quoted[-1] += "*"
    return FtsQuery(tuple(words), " ".join(quoted), " OR ".join(quoted))


def snippet_html(raw: str) -> str:
    """O `snippet()` como HTML: texto escapado e só depois os marcadores viram negrito; quebras de linha viram espaço."""
    return html.escape(" ".join(raw.split())).replace(SNIPPET_OPEN, "<b>").replace(SNIPPET_CLOSE, "</b>")


@functools.cache
def _fold_char(ch: str) -> str:
    return fold(ch)


def _fold_map(text: str) -> tuple[str, list[int] | None]:
    """O texto sem acento e sem caixa e, para cada caractere dele, a posição no original ("ß" vira "ss": duas posições);
    None quando é um para um (o caso comum: letra acentuada vira uma letra)."""
    parts = [_fold_char(ch) for ch in text]
    folded = "".join(parts)
    if len(folded) == len(text) and all(len(p) == 1 for p in parts):
        return folded, None
    return folded, [i for i, p in enumerate(parts) for _ in p]


def _pattern(words: Sequence[str]) -> re.Pattern[str] | None:
    """Cada palavra da busca como as palavras que o FTS compara (pedaços de letras e dígitos, sem acento), em sequência; a
    última como prefixo."""
    alts: list[str] = []
    for i, w in enumerate(words):
        tokens = WORD_RE.findall(fold(w).replace("_", " "))
        if not tokens:
            continue
        body = r"\W+".join(re.escape(t) for t in tokens)
        prefix = i == len(words) - 1 and len(_core(w)) >= MIN_PREFIX
        alts.append(rf"(?<!\w){body}" + (r"\w*" if prefix else r"(?!\w)"))
    return re.compile("|".join(alts)) if alts else None


def preview(text: str, words: Sequence[str], width: int = PREVIEW_CHARS) -> str:
    """HTML de um trecho sem `snippet()` (achado só pelo sentido): até `width` caracteres em volta da primeira palavra da
    busca que aparece, as palavras em negrito; sem nenhuma, o começo do texto. Escapado, como o `snippet_html`."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    folded, index = _fold_map(flat)
    pat = _pattern(words)
    spans = [(index[m.start()], index[m.end() - 1] + 1) if index is not None else (m.start(), m.end())
             for m in pat.finditer(folded) if m.end() > m.start()] if pat else []
    start = 0
    if spans and spans[0][0] > width // 3:
        start = max(0, spans[0][0] - width // 3)
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < spans[0][0] else start
    end = min(len(flat), start + width)
    if end < len(flat):
        space = flat.rfind(" ", start, end)
        end = space if space > start + width // 2 else end
    out: list[str] = ["…" if start > 0 else ""]
    pos = start
    for s0, s1 in spans:
        if s1 <= pos or s0 >= end:
            continue
        a, b = max(s0, pos), min(s1, end)
        out += [html.escape(flat[pos:a]), f"<b>{html.escape(flat[a:b])}</b>"]
        pos = b
    out += [html.escape(flat[pos:end]), "…" if end < len(flat) else ""]
    return "".join(out)


__all__ = ["STOPWORDS", "FtsQuery", "build_query", "fold", "preview", "quote", "snippet_html", "terms"]
