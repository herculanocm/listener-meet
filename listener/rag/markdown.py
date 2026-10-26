"""O subconjunto do Markdown que a ata e os resumos usam, em blocos para o .docx (F5.1 item 7, U-011, A-18), puro e sem
dependência: títulos, listas com marcador e numeradas (o aninhamento pelo recuo), parágrafos e, dentro deles, negrito e
itálico; o resto fica como texto. O escritor do .docx (`rag/docx_export.py`) só monta o documento a partir dos blocos."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MdRun:
    text: str
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True, slots=True)
class MdBlock:
    kind: str                    # "heading", "bullet", "number" ou "paragraph"
    runs: tuple[MdRun, ...]
    level: int = 0               # título: 1 a 6 (o número de "#"); lista: o aninhamento (0 = primeiro nível)
    number: str = ""             # lista numerada: o número como escrito (a numeração do Word continuaria de outra lista)


MD_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
MD_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
MD_NUMBER = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
MD_RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
MD_FENCE = re.compile(r"^ {0,3}(?:```|~~~)")
MD_QUOTE = re.compile(r"^ {0,3}>\s?")
MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
ESCAPED_BASE = 0xE000            # um caractere escapado ("\*") vira um da área privada até o fim: não abre ênfase
MD_INLINE = re.compile(
    r"(?P<tick>`+)(?P<code>.+?)(?P=tick)"                     # código: o texto como está
    r"|\[(?P<link>[^\]\n]+)\]\([^)\s]*\)"                     # link: só o texto
    r"|\*\*\*(?P<bi>\S(?:.*?\S)?)\*\*\*"                      # negrito e itálico
    r"|\*\*(?P<b1>\S(?:.*?\S)?)\*\*|(?<!\w)__(?P<b2>\S(?:.*?\S)?)__(?!\w)"
    r"|\*(?P<i1>\S(?:.*?\S)?)\*|(?<!\w)_(?P<i2>\S(?:.*?\S)?)_(?!\w)")    # "_" dentro de palavra (snake_case) não é ênfase


def _protect(text: str) -> str:
    return MD_ESCAPE.sub(lambda m: chr(ESCAPED_BASE + ord(m.group(1))), text)


def _restore(text: str) -> str:
    return "".join(chr(ord(c) - ESCAPED_BASE) if ESCAPED_BASE <= ord(c) < ESCAPED_BASE + 128 else c for c in text)


def _inline(text: str, bold: bool, italic: bool) -> list[MdRun]:
    out: list[MdRun] = []
    pos = 0
    for m in MD_INLINE.finditer(text):
        if m.start() > pos:
            out.append(MdRun(text[pos:m.start()], bold, italic))
        g = m.groupdict()
        if g["code"] is not None or g["link"] is not None:
            out.append(MdRun(g["code"] if g["code"] is not None else g["link"], bold, italic))
        elif g["bi"] is not None:
            out += _inline(g["bi"], True, True)
        elif g["b1"] is not None or g["b2"] is not None:
            out += _inline(g["b1"] if g["b1"] is not None else g["b2"], True, italic)
        else:
            out += _inline(g["i1"] if g["i1"] is not None else g["i2"], bold, True)
        pos = m.end()
    if pos < len(text):
        out.append(MdRun(text[pos:], bold, italic))
    return out


def parse_inline(text: str) -> tuple[MdRun, ...]:
    """Negrito (`**`, `__`), itálico (`*`, `_`), os dois (`***`), código e links como texto; os trechos vizinhos com a mesma
    marca se juntam; delimitador sem par fica como está; `\\*` é um asterisco."""
    merged: list[MdRun] = []
    for run in _inline(_protect(text), False, False):
        if not run.text:
            continue
        if merged and (merged[-1].bold, merged[-1].italic) == (run.bold, run.italic):
            merged[-1] = MdRun(merged[-1].text + run.text, run.bold, run.italic)
        else:
            merged.append(run)
    return tuple(MdRun(_restore(r.text), r.bold, r.italic) for r in merged)


def _list_level(indents: list[int], indent: int) -> int:
    """O aninhamento de um item pelo recuo, contra os recuos das listas abertas (2 ou 4 espaços, tanto faz)."""
    while indents and indent < indents[-1]:
        indents.pop()
    if not indents or indent > indents[-1]:
        indents.append(indent)
    return len(indents) - 1


class _MdBlocks:
    """O acumulador do `parse_markdown`: o parágrafo e o item de lista em curso."""

    def __init__(self) -> None:
        self.blocks: list[MdBlock] = []
        self.lines: list[str] = []
        self.item: tuple[str, int, str] | None = None        # (tipo, nível, número) do item em curso
        self.indents: list[int] = []

    def flush(self) -> None:
        text = " ".join(self.lines).strip()
        if self.item is not None:
            kind, level, number = self.item
            self.blocks.append(MdBlock(kind, parse_inline(text), level, number))
        elif text:
            self.blocks.append(MdBlock("paragraph", parse_inline(text)))
        self.lines, self.item = [], None

    def line(self, line: str) -> None:
        if not line.strip() or MD_RULE.match(line):
            self.flush()                                      # linha em branco: fecha o parágrafo e o item, não a lista
            return
        if h := MD_HEADING.match(line):
            self.flush()
            self.indents.clear()
            self.blocks.append(MdBlock("heading", parse_inline(h.group(2)), len(h.group(1))))
            return
        if m := MD_BULLET.match(line):
            self._item("bullet", m.group(1), "", m.group(2))
        elif m := MD_NUMBER.match(line):
            self._item("number", m.group(1), m.group(2), m.group(3))
        else:
            if self.item is None and not self.lines:
                self.indents.clear()                          # um parágrafo depois da lista a encerra
            self.lines.append(MD_QUOTE.sub("", line).strip())  # citação vira texto; continuação do item junta

    def _item(self, kind: str, indent: str, number: str, text: str) -> None:
        self.flush()
        self.item = (kind, _list_level(self.indents, len(indent.expandtabs(4))), number)
        self.lines = [text.strip()]


def parse_markdown(md: str) -> list[MdBlock]:
    """Blocos do Markdown da ata e dos resumos (A-18): títulos (`#` a `######`), itens de lista (`-`, `*`, `+` e `1.`)
    com o aninhamento pelo recuo, parágrafos (linhas seguidas viram um, como no Markdown) e o texto de dentro com negrito e
    itálico. Régua some; citação e tabela viram texto; bloco de código fica linha a linha, sem ênfase."""
    acc = _MdBlocks()
    fenced = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if MD_FENCE.match(line):
            acc.flush()
            fenced = not fenced
        elif fenced:
            if line.strip():
                acc.blocks.append(MdBlock("paragraph", (MdRun(line),)))
        else:
            acc.line(line)
    acc.flush()
    return acc.blocks


__all__ = ["MdBlock", "MdRun", "parse_inline", "parse_markdown"]
