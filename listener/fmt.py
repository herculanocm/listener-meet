"""Textos com número para quem usa o app (F5.11, U-109, A-27), sem Qt: a interface e o índice da busca (`rag/index.py`)
usam o mesmo. Nada de "voz(es)": o número e as palavras concordam ("1 voz", "2 vozes", "1 ação aberta"). F5.25 (U-173,
U-177): a nota da voz em percentual e a margem em pontos, na revisão e na escolha da pessoa."""
from __future__ import annotations

import math


def plural(n: int, one: str, many: str | None = None) -> str:
    """"1 fala", "0 falas", "2 vozes": o número e a forma que concorda com ele. `one` e `many` podem ser frases inteiras
    ("ação aberta", "ações abertas"); sem `many`, o plural regular (`one` + "s")."""
    form = one if abs(n) == 1 else (many if many is not None else f"{one}s")
    return f"{n} {form}"


def _hundredths(x: float) -> int:
    """x vezes 100, sem casas, arredondado para cima a partir de meio."""
    return math.floor(x * 100 + 0.5)


def percent(score: float | None) -> str:
    """U-173: a nota (o cosseno) vezes 100 ("58 %"); negativo vira 0 %; sem nota, nada."""
    if score is None or not math.isfinite(score):
        return ""
    return f"{max(0, _hundredths(score))} %"


def points(margin: float) -> str:
    """U-177: a margem entre as duas notas em pontos percentuais ("3 pontos", "1 ponto")."""
    return plural(_hundredths(margin), "ponto", "pontos")


__all__ = ["percent", "plural", "points"]
