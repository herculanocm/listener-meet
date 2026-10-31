"""F5.6 L2 (U-092, A-24 a A-28, A-46): as instruções personalizadas de cada tarefa da LLM, em `settings` e sem schema novo
(A-47). `prompt.<tarefa>` guarda o texto do usuário (vazio = o padrão do app); `prompt.<tarefa>.base`, o hash do padrão do
app no momento da edição (a página avisa quando o padrão mudou depois dela). Aqui, sem Qt e sem a camada da LLM: as chaves,
o hash curto (o `+u<hash>` do `prompt_version`, A-28), a validação do Salvar (A-27, U-072), a leitura numa consulta e o que o
log mostra (só o hash, nunca o texto: A-46, LGPD). Quem sabe o padrão de cada tarefa é `llm/prompts` (o padrão entra aqui
como parâmetro, sem import circular)."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store

PREFIX = "prompt."
BASE_SUFFIX = ".base"
VARIABLE = "{idioma}"                          # a única variável: o nome do idioma da reunião (A-26)
MAX_CHARS = 4000
VERSION_HASH, BASE_HASH = 6, 12                # `minutes/1.2+u3fa9c1`; o `.base` com folga contra colisão
# as tarefas editáveis (A-25), pelo valor do `LlmTask`; o `test` fica fora. F5.10: `rag_sql` e `rag_answer` entram; o
# `rag_chat` saiu da página Prompts, mas fica aqui por último: o chat global antigo ainda lê o que estava salvo
TASKS = ("identify", "revise", "title", "pre_summary", "minutes", "executive", "partial", "chat", "rag_sql", "rag_answer",
         "glossary", "transcribe", "rag_chat")
# `{nome}`: chaves com um nome curto, sem aspas nem dois-pontos (um exemplo de JSON nas instruções não é variável)
FIELD_RE = re.compile(r"\{([^{}\"':\n]{1,40})\}")


def key(task: str) -> str:
    return f"{PREFIX}{task}"


def base_key(task: str) -> str:
    return f"{PREFIX}{task}{BASE_SUFFIX}"


def text_hash(text: str, size: int = VERSION_HASH) -> str:
    """O hash curto de um texto (SHA-256 em hexadecimal): o que a chamada, o `.base` e o log levam no lugar do texto."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def unknown_fields(text: str) -> list[str]:
    """Os `{nome}` do texto que não são `{idioma}`, na ordem, sem repetir."""
    found = [m.group(0) for m in FIELD_RE.finditer(text) if m.group(1).strip() and m.group(0) != VARIABLE]
    return list(dict.fromkeys(found))


def problem(text: str) -> str | None:
    """O que barra o Salvar (A-27), em PT-BR, ou None: texto vazio (só espaços), uma variável que não é `{idioma}` ou mais de
    4.000 caracteres. `{idioma}` é opcional."""
    if not text.strip():
        return "as instruções estão vazias. Escreva o que a LLM deve fazer ou clique em \"Restaurar padrão\"."
    unknown = unknown_fields(text)
    if unknown:
        return f"{unknown[0]} não é uma variável conhecida; só {VARIABLE} é aceita (vira o idioma da reunião)."
    if len(text) > MAX_CHARS:
        return f"as instruções têm {len(text):,} caracteres; o limite é {MAX_CHARS:,}.".replace(",", ".")
    return None


def stored(text: str, default: str) -> str:
    """O que vai para o banco: vazio quando o texto, sem os espaços das pontas, é o padrão do app."""
    return "" if text.strip() == default.strip() else text


def custom_prompts(store: Store) -> dict[str, str]:
    """As instruções personalizadas por tarefa (só as não vazias), numa consulta: o que o job congela no `config_json`."""
    rows = {str(r["key"]): str(r["value"]) for r in store._q("SELECT key, value FROM settings WHERE key LIKE ?", (f"{PREFIX}%",))}
    return {t: rows[key(t)] for t in TASKS if rows.get(key(t), "").strip()}


def clean(value: object) -> dict[str, str]:
    """As instruções de um `config_json` (o de um job antigo pode não ter; lixo é ignorado)."""
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v for k, v in value.items() if k in TASKS and isinstance(v, str) and v.strip()}


def save(store: Store, task: str, text: str, default: str) -> bool:
    """Grava as instruções de uma tarefa (o padrão grava vazio) e, quando o texto mudou, o hash do padrão de agora em
    `.base`. Devolve se mudou algo. Log só com a tarefa e o hash (A-46)."""
    value = stored(text, default)
    if value == store.get_setting(key(task)):
        return False
    store.set_setting(key(task), value)
    store.set_setting(base_key(task), text_hash(default, BASE_HASH) if value else "")
    return True


def restore(store: Store, tasks: Iterable[str]) -> list[str]:
    """"Restaurar o prompt padrão" (A-29): as tarefas voltam ao padrão do app; devolve as que estavam personalizadas."""
    done = [t for t in tasks if t in TASKS and store.get_setting(key(t)).strip()]
    for t in done:
        store.set_setting(key(t), "")
        store.set_setting(base_key(t), "")
    return done


def default_changed(store: Store, task: str, default: str) -> bool:
    """O padrão do app mudou depois da edição: há texto personalizado e o `.base` não é o hash do padrão de agora."""
    if not store.get_setting(key(task)).strip():
        return False
    return store.get_setting(base_key(task)) != text_hash(default, BASE_HASH)


def shown(setting: str, value: str | None) -> str:
    """O valor de uma chave `prompt.` para o log (A-46): o hash, nunca o texto."""
    if value is None or not value.strip():
        return "(padrão do app)"
    return value if setting.endswith(BASE_SUFFIX) else f"(instruções u{text_hash(value.strip())})"


__all__ = ["BASE_HASH", "BASE_SUFFIX", "MAX_CHARS", "PREFIX", "TASKS", "VARIABLE", "VERSION_HASH", "base_key", "clean",
           "custom_prompts", "default_changed", "key", "problem", "restore", "save", "shown", "stored", "text_hash",
           "unknown_fields"]
