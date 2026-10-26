"""Regra do código-fonte: nenhum caractere invisível ou inválido escrito literalmente em `listener/`, `tests/` e `scripts/`
(formato como o U+200B, uso privado, surrogates, não caracteres como o U+FFFE, separadores de linha do Unicode e controles
salvo a tabulação). No fonte ele some da vista e o escape (`\\u200b`) diz o que é; a ferramenta de edição da LLM às vezes
grava o próprio caractere no lugar do escape (o ZWSP do U-033 e, na F5.1, U+FFFE, U+FFFF, U+FDD0 e U+E02A)."""
from __future__ import annotations

import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIDDEN = {"Cf", "Co", "Cs", "Cn", "Zl", "Zp"}             # formato, uso privado, surrogate, não atribuído, U+2028 e U+2029


def _hidden(c: str) -> bool:
    cat = unicodedata.category(c)
    return cat in HIDDEN or (cat == "Cc" and c != "\t")


def test_sources_have_no_literal_invisible_characters():
    offenders: list[str] = []
    for folder in ("listener", "tests", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            lines = path.read_text(encoding="utf-8").split("\n")      # não `splitlines`: ele corta no \x0c, \x85 e U+2028
            for n, line in enumerate(lines, 1):
                bad = [f"U+{ord(c):04X}" for c in line.removesuffix("\r") if _hidden(c)]
                if bad:
                    offenders.append(f"{path.relative_to(ROOT)}:{n} {' '.join(bad)}")
    assert offenders == []
