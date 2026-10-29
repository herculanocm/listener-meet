"""F5.7 (Aceite 4, D-053): o detector, por AST, de título de janela, caminho ou nome de aparelho passado a `log.*` ou a
`print`. Olha os nomes de variável e de atributo nos argumentos (também dentro de f-string): o que se chama assim não sai
do processo pelo log nem pela sonda. `type(e).__name__` não conta (é o nome da classe da exceção)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L1_MODULES = tuple(ROOT / p for p in ("listener/platform/win32/activity.py", "listener/platform/win32/consent.py",
                                       "listener/platform/win32/sessions.py", "scripts/detect_probe.py"))
L2_MODULES = (ROOT / "listener/ui/call_rules.py",)
# o título (e o mapa pid -> título), o caminho, o nome da entrada do registro e o nome do aparelho
FORBIDDEN = frozenset({"title", "titles", "by_pid", "path", "paths", "name", "names", "device_name", "friendly"})
LOGGERS = frozenset({"log", "logger", "logging"})


def _is_output(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in LOGGERS


def leaks_in(source: str) -> list[str]:
    """`linha: nome` de cada nome proibido nos argumentos de um `log.*` ou de um `print`."""
    out: list[str] = []
    for call in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _is_output(n)):
        for arg in (*call.args, *(k.value for k in call.keywords)):
            for node in ast.walk(arg):
                found = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
                if found in FORBIDDEN:
                    out.append(f"{call.lineno}: {found}")
    return out


def leaks(path: Path) -> list[str]:
    return leaks_in(path.read_text(encoding="utf-8"))
