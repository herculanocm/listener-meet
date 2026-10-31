"""F5.10 (D1 e revisão do diff, D-075): o que a sandbox lê no texto do SQL antes de compilá-lo, sem executá-lo: a primeira
palavra (só SELECT ou WITH) e as palavras USING e NATURAL, que a sandbox recusa porque a coluna de uma junção com elas nasce
fora do `lookupName` e o SQLite não chama o autorizador para ela. A leitura pula literais (`'…'` com `''`, inclusive `X'…'`),
identificadores entre aspas (`"…"`, `[…]`, `` `…` ``) e comentários (`--`, `/* */`), e separa as palavras como o tokenizador do
SQLite (todo caractere acima de 0x7f é de nome). Puro, sem banco."""
from __future__ import annotations

import re

FIRST_WORD_RE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*(\w+)", re.DOTALL)
READ_ONLY_WORDS = frozenset({"select", "with"})
JOIN_WORDS = frozenset({"using", "natural"})


def first_word(sql: str) -> str:
    """A primeira palavra do SQL, depois de espaços e comentários, em minúsculas ("" sem palavra)."""
    m = FIRST_WORD_RE.match(sql or "")
    return m.group(1).lower() if m else ""


def _word_char(c: str) -> bool:
    return c.isalnum() or c in "_$" or ord(c) > 127          # como o tokenizador do SQLite: todo byte acima de 0x7f é de nome


def _skip(sql: str, i: int) -> int:
    """O fim do que começa em `i` e não é palavra do SQL: literal (`'…'` com `''`, inclusive `X'…'`), identificador entre aspas
    (`"…"`, `[…]`, `` `…` ``) ou comentário (`--`, `/* */`); `i` quando não é nada disso."""
    if sql.startswith("--", i):
        end = sql.find("\n", i)
        return len(sql) if end < 0 else end + 1
    if sql.startswith("/*", i):
        end = sql.find("*/", i + 2)
        return len(sql) if end < 0 else end + 2
    if sql[i] not in "'\"`[":
        return i
    close, j = ("]" if sql[i] == "[" else sql[i]), i + 1
    while j < len(sql):
        if sql[j] == close and close != "]" and sql[j + 1:j + 2] == close:
            j += 2                                             # a aspa dobrada continua o literal
        elif sql[j] == close:
            return j + 1
        else:
            j += 1
    return len(sql)


def join_words(sql: str) -> bool:
    """USING ou NATURAL como palavra do SQL, sem diferenciar caixa, fora de literais, identificadores entre aspas e comentários."""
    i = 0
    while i < len(sql):
        j = _skip(sql, i)
        if j > i:
            i = j
        elif _word_char(sql[i]):
            j = i
            while j < len(sql) and _word_char(sql[j]):
                j += 1
            if sql[i:j].lower() in JOIN_WORDS:
                return True
            i = j
        else:
            i += 1
    return False


__all__ = ["JOIN_WORDS", "READ_ONLY_WORDS", "first_word", "join_words"]
