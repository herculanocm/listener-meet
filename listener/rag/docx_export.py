"""A reunião em .docx (F5.1 item 7, U-011, A-18; D-012: só por ação do usuário, não fere a CP9): as mesmas seções do .md (Q4)
na mesma ordem: o título, a ficha (data e hora, duração, participantes), a ata, o resumo executivo e o pré-resumo (o
Markdown deles lido pelo parser puro de `rag/markdown.py`: títulos, listas, negrito e itálico), as ações, os marcadores e
a transcrição "[mm:ss] Nome — Cargo: texto" com o falante em negrito. Texto do usuário (falas, notas, ações) entra como
texto, sem virar Markdown.

O `python-docx` (extra `docx`) só é importado dentro de `meeting_docx`, que roda numa `Task` da reunião aberta: o app abre
sem o extra, e sem ele o item do menu fica desabilitado com a dica (`docx_available`, que procura sem importar). O
documento sai em bytes; a escrita é a atômica do .md (`export.write_atomic`)."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING

from ..llm.prompts.common import stamp
from . import export
from .export import ExportMeeting
from .markdown import parse_markdown

if TYPE_CHECKING:
    from docx.document import Document
    from docx.text.paragraph import Paragraph

DOC_LANGUAGE = "pt-BR"           # a revisão ortográfica do Word em português (o modelo padrão vem em inglês)
AUTHOR = "listener-meet"         # o modelo padrão do python-docx assina "python-docx"
BULLET_STYLES = ("List Bullet", "List Bullet 2", "List Bullet 3")
NUMBER_INDENT_PT = 18            # recuo por nível da lista numerada (o número vai escrito: o Word continuaria a contagem)
SECTION_LEVEL = 1                # "Ata", "Ações", "Transcrição"…; os títulos de dentro da ata começam no 2


def docx_available() -> bool:
    """O extra `docx` instalado, sem importá-lo (o menu decide se o item fica habilitado)."""
    return importlib.util.find_spec("docx") is not None


def meeting_docx(m: ExportMeeting) -> bytes:
    """O .docx inteiro da reunião, em bytes."""
    import docx  # o extra `docx` só nesta ação (A-18): nunca no nível do módulo

    doc = docx.Document()
    _properties(doc, m)
    doc.add_heading(m.shown_title, level=0)
    for label, value in export.header_facts(m):
        p = doc.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(value)
    for title, md in (("Ata", m.minutes_md), ("Resumo executivo", m.executive), ("Pré-resumo", m.pre_summary)):
        if md.strip():
            doc.add_heading(title, level=SECTION_LEVEL)
            _markdown(doc, md)
    _list_section(doc, "Ações", [export.action_text(a) for a in m.actions])
    _list_section(doc, "Marcadores", [export.marker_text(t, note) for t, note in m.markers])
    if m.transcript:
        doc.add_heading("Transcrição", level=SECTION_LEVEL)
        for line in m.transcript:
            p = doc.add_paragraph()
            p.add_run(f"[{stamp(line.t_ms)}] ")
            p.add_run(line.speaker).bold = True
            p.add_run(f": {line.text}")
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def _properties(doc: Document, m: ExportMeeting) -> None:
    """Título, autor e datas do arquivo (o modelo padrão traz "python-docx" e 2013) e o idioma padrão do texto."""
    from docx.oxml.ns import qn

    props = doc.core_properties
    now = datetime.now(UTC)
    props.title, props.author, props.last_modified_by = m.shown_title, AUTHOR, AUTHOR
    props.created = props.modified = now
    lang = doc.styles.element.find(f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}/{qn('w:lang')}")
    if lang is not None:
        lang.set(qn("w:val"), DOC_LANGUAGE)


def _list_section(doc: Document, title: str, items: list[str]) -> None:
    if items:
        doc.add_heading(title, level=SECTION_LEVEL)
        for text in items:
            doc.add_paragraph(text, style=BULLET_STYLES[0])


def _markdown(doc: Document, md: str) -> None:
    """O Markdown da ata ou de um resumo: os títulos dele começam no nível 2 (debaixo da seção), qualquer que seja o primeiro
    nível usado ("##" na ata da LLM; "#" numa ata editada)."""
    from docx.shared import Pt

    blocks = parse_markdown(md)
    top = min((b.level for b in blocks if b.kind == "heading"), default=1)
    for block in blocks:
        p: Paragraph
        if block.kind == "heading":
            p = doc.add_heading("", level=max(SECTION_LEVEL + 1, min(9, block.level - top + SECTION_LEVEL + 1)))
        elif block.kind == "bullet":
            p = doc.add_paragraph(style=BULLET_STYLES[min(block.level, len(BULLET_STYLES) - 1)])
        else:
            p = doc.add_paragraph()
            if block.kind == "number":
                p.paragraph_format.left_indent = Pt(NUMBER_INDENT_PT * (block.level + 1))
                p.add_run(f"{block.number}. ")
        for run in block.runs:
            r = p.add_run(run.text)
            r.bold = run.bold or None                     # None = o do estilo (os títulos já são negrito)
            r.italic = run.italic or None


__all__ = ["docx_available", "meeting_docx"]
