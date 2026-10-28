"""F5.4 L1 (A-12, Aceite 6): teste estático por AST em todo `listener/`, como o de `tests/test_adapters.py`. Fora do tema
(`ui/theme.py` e `ui/tokens.py`, para onde os tokens saíram no L1b para o `theme.py` ficar abaixo de 400 linhas) nenhuma
string com cor (`#[0-9a-f]{3,8}`); fora do tema e dos ícones (`ui/icons/`), nenhum `QColor` importado; em todo lugar,
nenhum `Qt.GlobalColor` (salvo `transparent`) e nenhum `setStyleSheet` (nem local nem no app: [fato, 28/10] uma folha no
app deixou as janelas de 10 a 100 % mais lentas). A lista de exceções só encolhe: começou com o que sobrou do L1a e está
vazia desde o fim do L1b."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "listener"
HEX = re.compile(r"#[0-9a-f]{3,8}", re.IGNORECASE)
THEME = frozenset({"ui/theme.py", "ui/tokens.py"})                 # onde moram os tokens
COLOR_OK = THEME | {"ui/icons/__init__.py"}                        # quem pode montar `QColor`
GLOBAL_COLORS = frozenset({"color0", "color1", "black", "white", "darkGray", "gray", "lightGray", "red", "green", "blue", "cyan",
                           "magenta", "yellow", "darkRed", "darkGreen", "darkBlue", "darkCyan", "darkMagenta", "darkYellow"})
EXCEPTIONS: frozenset[str] = frozenset()                           # "arquivo: regra" ainda por migrar (A-12: zerou no L1b)


def _global_color(node: ast.Attribute) -> bool:
    """`Qt.GlobalColor.<cor>` (salvo `transparent`) ou o atalho antigo `Qt.<cor>`."""
    value = node.value
    if isinstance(value, ast.Attribute) and value.attr == "GlobalColor":
        return node.attr != "transparent"
    return isinstance(value, ast.Name) and value.id == "Qt" and node.attr in GLOBAL_COLORS


def offenses(source: str, rel: str) -> list[str]:
    """As infrações de um arquivo (`rel` relativo a `listener/`, com barras), uma por nó."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and rel not in THEME and HEX.search(node.value):
            found.append(f"{rel}: cor na string, linha {node.lineno}")
        elif isinstance(node, ast.ImportFrom) and rel not in COLOR_OK and any(a.name == "QColor" for a in node.names):
            found.append(f"{rel}: QColor importado, linha {node.lineno}")
        elif isinstance(node, ast.Attribute):
            if node.attr == "setStyleSheet":
                found.append(f"{rel}: setStyleSheet, linha {node.lineno}")
            elif node.attr == "QColor" and rel not in COLOR_OK:
                found.append(f"{rel}: QColor, linha {node.lineno}")
            elif _global_color(node):
                found.append(f"{rel}: Qt.GlobalColor, linha {node.lineno}")
    return found


def test_no_loose_color_style_sheet_or_global_color_in_the_app():
    found: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        found += [f for f in offenses(path.read_text(encoding="utf-8"), rel) if f.split(",")[0] not in EXCEPTIONS]
    assert found == []
    assert not EXCEPTIONS                                                  # a lista zerou no fim do L1 (A-12)


def test_the_checker_catches_each_rule():
    """O próprio verificador: cada regra acusa no arquivo comum e o tema e os ícones têm o que lhes cabe."""
    bad = ("from PySide6.QtGui import QColor\nfrom PySide6.QtCore import Qt\n"
           "w.setStyleSheet('x')\nc = Qt.GlobalColor.red\nd = Qt.white\nok = Qt.GlobalColor.transparent\n"
           "s = 'color: #e67e22'\nh = f'<span style=\"color:#888\">{x}</span>'\nq = QtGui.QColor\n")
    rules = sorted(f.split(": ")[1].split(",")[0] for f in offenses(bad, "ui/x.py"))
    assert rules == ["QColor", "QColor importado", "Qt.GlobalColor", "Qt.GlobalColor", "cor na string", "cor na string",
                     "setStyleSheet"]
    assert offenses("from PySide6.QtGui import QColor\nLIGHT = '#f3f3f3'\n", "ui/tokens.py") == []
    assert offenses("from PySide6.QtGui import QColor\n", "ui/icons/__init__.py") == []
    assert offenses("x = 'reunião 12'\ny = '#rrggbb'\nz = f'#{n:02x}'\n", "ui/x.py") == []          # sem cor de verdade
