"""Textos com número para quem usa o app (F5.11, U-109, A-27), sem Qt: a interface e o índice da busca (`rag/index.py`)
usam o mesmo. Nada de "voz(es)": o número e as palavras concordam ("1 voz", "2 vozes", "1 ação aberta")."""
from __future__ import annotations


def plural(n: int, one: str, many: str | None = None) -> str:
    """"1 fala", "0 falas", "2 vozes": o número e a forma que concorda com ele. `one` e `many` podem ser frases inteiras
    ("ação aberta", "ações abertas"); sem `many`, o plural regular (`one` + "s")."""
    form = one if abs(n) == 1 else (many if many is not None else f"{one}s")
    return f"{n} {form}"


__all__ = ["plural"]
