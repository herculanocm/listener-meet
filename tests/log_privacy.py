"""F5.7 (Aceite 4, D-053): o detector, por AST, de título de janela, caminho ou nome de aparelho passado a `log.*` ou a
`print`. Olha os nomes de variável e de atributo nos argumentos (também dentro de f-string): o que se chama assim não sai
do processo pelo log nem pela sonda. `type(e).__name__` não conta (é o nome da classe da exceção). F5.10 (D8): os módulos de
"Perguntar à base" com os nomes proibidos próprios (a pergunta, o SQL, os `params`, as linhas, o objetivo, a resposta, o texto
e as células); ali, o que está dentro de `len(…)` e `type(…)` não conta (o tamanho e a classe não são o dado)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L1_MODULES = tuple(ROOT / p for p in ("listener/platform/win32/activity.py", "listener/platform/win32/consent.py",
                                       "listener/platform/win32/sessions.py", "scripts/detect_probe.py"))
L2_MODULES = (ROOT / "listener/ui/call_rules.py",)
L3_MODULES = (*(ROOT / "listener/ui" / f for f in ("call_answers.py", "call_config.py", "call_prompt.py", "call_sensor.py",
                                                    "call_watch.py", "settings_detect.py", "tray.py", "warmup_source.py")),
              ROOT / "scripts/probe_f57.py", ROOT / "tests/call_exit_scenario.py")
# D-067: a saída que acompanha o padrão do Windows (o player, a releitura, a escolha do aparelho padrão de agora)
D067_MODULES = tuple(ROOT / p for p in ("listener/ui/player.py", "listener/ui/player_route.py", "listener/ui/player_source.py",
                                         "listener/ui/devices.py", "listener/platform/common/output.py",
                                         "listener/platform/win32/output.py", "listener/platform/win32/endpoints.py"))
# F5.10 (D8): a sandbox, a camada semântica, o dicionário, o laço, os passos, os prompts, a volta da v9 e as telas
F510_MODULES = tuple(ROOT / p for p in ("listener/rag/sandbox.py", "listener/rag/sql_text.py", "listener/rag/semantic.py",
                                         "listener/rag/dictionary.py",
                                         "listener/rag/agent.py", "listener/rag/agent_results.py", "listener/rag/trace.py",
                                         "listener/llm/prompts/rag_sql.py", "listener/llm/prompts/rag_answer.py",
                                         "listener/store/downgrade.py", "listener/ui/history_ask.py", "listener/ui/ask_data.py"))
F510_FORBIDDEN = frozenset({"question", "sql", "params", "rows", "goal", "answer", "text", "cells"})
F510_ALLOWED_CALLS = frozenset({"len", "type"})
# o título (e o mapa pid -> título), o caminho, o nome da entrada do registro e o nome do aparelho
FORBIDDEN = frozenset({"title", "titles", "by_pid", "path", "paths", "name", "names", "device_name", "friendly"})
LOGGERS = frozenset({"log", "logger", "logging"})


def _is_output(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in LOGGERS


def _walk(node: ast.AST, allowed: frozenset[str]) -> list[ast.AST]:
    """Os nós de um argumento, sem descer nas chamadas liberadas (`len(sql)` não leva o SQL ao log)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in allowed:
        return [node]
    return [node, *(n for child in ast.iter_child_nodes(node) for n in _walk(child, allowed))]


def leaks_in(source: str, forbidden: frozenset[str] = FORBIDDEN, allowed: frozenset[str] = frozenset()) -> list[str]:
    """`linha: nome` de cada nome proibido nos argumentos de um `log.*` ou de um `print`."""
    out: list[str] = []
    for call in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _is_output(n)):
        for arg in (*call.args, *(k.value for k in call.keywords)):
            for node in _walk(arg, allowed):
                found = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
                if found in forbidden:
                    out.append(f"{call.lineno}: {found}")
    return out


def leaks(path: Path, forbidden: frozenset[str] = FORBIDDEN, allowed: frozenset[str] = frozenset()) -> list[str]:
    return leaks_in(path.read_text(encoding="utf-8"), forbidden, allowed)
