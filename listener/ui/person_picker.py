"""A escolha da pessoa na revisão de vozes (F5.25, U-171 a U-176): "Outra pessoa…" (O) e "Escolher pessoa…" (o Enter na voz
sem candidato) abrem esta janela no lugar da caixa de escolha de antes.

A parte pura, sem Qt: `fold` (sem acento e sem maiúscula), `top_by_voice` (as até 3 de "Pela voz", U-136), `PersonIndex.view`
(o filtro, a ordem e as seções) e `percent` (a nota da voz em percentual, U-173). A busca olha o nome, o cargo e a empresa,
nunca o e-mail; cada palavra digitada aparece em qualquer posição de um deles. A escolha vai sempre pelo id da linha
(homônimos, A-11)."""
from __future__ import annotations

import difflib
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from ..pipeline.review_suggest import FLOOR

TOP_BY_VOICE = 3                 # U-136: "Pela voz" traz até 3, com nota de `FLOOR` (0,45, D-089) ou mais
SIMILAR_MIN_LETTERS = 3          # U-175: "Nomes parecidos" só com 3 letras ou mais
SIMILAR_CUTOFF = 0.75            # U-175: a semelhança mínima do `difflib`
SIMILAR_MAX = 3
BY_VOICE_TITLE, EXPECTED_TITLE, ALL_TITLE, SIMILAR_TITLE = "Pela voz", "Esperadas nesta reunião", "Todas", "Nomes parecidos"
_WORD = re.compile(r"\w+")


def fold(text: str) -> str:
    """O texto para comparar: `casefold` e NFKD sem os sinais combinantes ("Conceição" vira "conceicao")."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def percent(score: float | None) -> str:
    """U-173: o cosseno vezes 100, sem casas, arredondado para cima a partir de meio; negativo vira 0 %; sem nota, nada."""
    if score is None or not math.isfinite(score):
        return ""
    return f"{max(0, math.floor(score * 100 + 0.5))} %"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Uma pessoa na janela. `label` é a linha de `person_labels` ("Nome — Cargo · Empresa", com "(#id)" nos homônimos);
    `score`, a nota pela voz (None sem perfil no modelo da voz); `taken`, a voz onde ela já foi decidida ("Voz 2")."""
    person_id: int
    label: str
    name: str
    role: str = ""
    company: str = ""
    score: float | None = None
    expected: bool = False
    taken: str = ""


@dataclass(frozen=True, slots=True)
class Section:
    title: str                           # vazio: sem título (a lista com texto)
    rows: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class View:
    """O que a lista mostra para um texto: as seções, quantas pessoas casaram (com o campo vazio, todas), se a primeira
    linha vem selecionada (U-176) e se as linhas são "Nomes parecidos" (U-175)."""
    sections: tuple[Section, ...]
    matched: int
    select_first: bool
    similar: bool = False

    @property
    def rows(self) -> list[Candidate]:
        return [c for s in self.sections for c in s.rows]


def top_by_voice(ranked: Sequence[tuple[int, float]], suggested: int | None, known: set[int] | frozenset[int]) -> list[int]:
    """U-136: até 3 pessoas com nota de 0,45 ou mais (D-089), na ordem da nota, sem a já sugerida e só entre as `known`."""
    near = [pid for pid, s in ranked if s >= FLOOR and pid != suggested and pid in known]
    return near[:TOP_BY_VOICE]


@dataclass(frozen=True, slots=True)
class _Entry:
    order: int                           # a posição de `list_people`: você primeiro, depois a ordem alfabética
    person: Candidate
    fields: tuple[str, ...]              # nome, cargo e empresa, normalizados
    name_words: tuple[str, ...]
    full_name: str
    first_name: str


class PersonIndex:
    """As pessoas normalizadas uma vez, na abertura; `view` roda em memória a cada tecla (nenhuma consulta ao banco)."""

    def __init__(self, people: Sequence[Candidate], top: Sequence[int]) -> None:
        self.entries: list[_Entry] = []
        for i, p in enumerate(people):
            name = fold(p.name)
            words = tuple(_WORD.findall(name))
            self.entries.append(_Entry(i, p, (name, fold(p.role), fold(p.company)), words, " ".join(name.split()),
                                       words[0] if words else ""))
        known = {p.person_id for p in people}
        self.top = [pid for pid in top if pid in known]
        self._rank = {pid: i for i, pid in enumerate(self.top)}

    def __len__(self) -> int:
        return len(self.entries)

    def view(self, text: str) -> View:
        words = fold(text).split()
        if not words:
            return self._sections()
        hits = [e for e in self.entries if all(any(w in f for f in e.fields) for w in words)]
        if hits:
            hits.sort(key=lambda e: self._key(e, words))
            return View((Section("", tuple(e.person for e in hits)),), len(hits), True)
        similar = self._similar(" ".join(words))
        if similar:
            return View((Section(SIMILAR_TITLE, tuple(similar)),), 0, True, similar=True)
        return View((), 0, False)

    def _sections(self) -> View:
        """U-174: "Pela voz", "Esperadas nesta reunião" e "Todas", cada pessoa uma vez; a primeira de "Pela voz" vem
        selecionada e, sem ela, nenhuma (U-176)."""
        by_id = {e.person.person_id: e.person for e in self.entries}
        rest = [e.person for e in self.entries if e.person.person_id not in self._rank]
        sections = (Section(BY_VOICE_TITLE, tuple(by_id[pid] for pid in self.top)),
                    Section(EXPECTED_TITLE, tuple(p for p in rest if p.expected)),
                    Section(ALL_TITLE, tuple(p for p in rest if not p.expected)))
        return View(tuple(s for s in sections if s.rows), len(self.entries), bool(self.top))

    def _key(self, e: _Entry, words: list[str]) -> tuple[int, int, int]:
        """Comportamento 4: quem estaria em "Pela voz", na ordem da nota; depois quem tem todas as palavras no começo de
        uma palavra do nome; por fim os demais. Nos dois últimos, as esperadas antes, e então a ordem de `list_people`."""
        rank = self._rank.get(e.person.person_id)
        if rank is not None:
            return (0, rank, 0)
        prefix = all(any(n.startswith(w) for n in e.name_words) for w in words)
        return (1 if prefix else 2, 0 if e.person.expected else 1, e.order)

    def _similar(self, typed: str) -> list[Candidate]:
        """U-175: com 3 letras ou mais, até 3 pessoas cujo nome inteiro ou primeiro nome se pareça com o texto (`difflib`,
        0,75 ou mais), da mais parecida para a menos."""
        if sum(c.isalnum() for c in typed) < SIMILAR_MIN_LETTERS:
            return []
        scored: list[tuple[float, int, Candidate]] = []
        for e in self.entries:
            best = max(difflib.SequenceMatcher(None, typed, target).ratio() for target in (e.full_name, e.first_name))
            if best >= SIMILAR_CUTOFF:
                scored.append((-best, e.order, e.person))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [p for _, _, p in scored[:SIMILAR_MAX]]


__all__ = ["ALL_TITLE", "BY_VOICE_TITLE", "EXPECTED_TITLE", "SIMILAR_TITLE", "TOP_BY_VOICE", "Candidate", "PersonIndex",
           "Section", "View", "fold", "percent", "top_by_voice"]
