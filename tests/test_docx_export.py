"""F5.1 L7, .docx (U-011, A-18, Q4; comportamento 5): o parser puro do Markdown da ata e dos resumos (títulos, listas com
aninhamento, parágrafos, negrito, itálico, código e link como texto, `snake_case` e escapes intactos, delimitador sem par),
a escrita atômica de bytes e o nome "AAAA-MM-DD título.docx", sem o python-docx; com ele (`importorskip`), o documento com
as seções do .md relido pelo próprio python-docx (título, ficha, ata com os títulos no nível 2, resumos, ações, marcadores
e a transcrição com o falante em negrito; texto do usuário sem virar Markdown; idioma pt-BR) e, na reunião aberta, o
"Salvar .docx…" numa `Task`, o aviso do U-030 com o anterior intacto, o erro inesperado e o item desabilitado sem o extra."""
from __future__ import annotations

import io

import pytest

from listener.model import SummaryKind
from listener.rag import export
from listener.rag.export import ExportAction, ExportLine, ExportMeeting, MdBlock, MdRun
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import _meeting, _people

ATA = ("## Contexto\n\nO fundo discutiu o **orçamento** de 2027\ncom a _auditoria_ externa.\n\n## Decisões\n\n"
       "- Aprovar a auditoria [01:10]\n- **Ana** — fechar o contrato _(sem evidência)_\n  - detalhe com snake_case_name\n"
       "    - mais fundo\n- volta ao primeiro nível\n\n1. primeiro\n2) segundo com `código **x**` e [link](http://x)\n")


def _data(minutes: str = ATA) -> ExportMeeting:
    return ExportMeeting(7, "Orçamento 2027", "2026-10-24T14:03:00.000-03:00", 3_900_000, ("Ana — CFO", "Bia"), minutes, "O fundo aprovou.",
                         "", (ExportAction("Mandar a planilha", "Bia", "2026-10-31", "aberta"), ExportAction("Ligar", "", "", "feita")),
                         ((70_000, "auditoria <b>sexta</b>"), (90_000, "")),
                         (ExportLine(0, "Ana — CFO", "Bom dia."), ExportLine(65_000, "Bia", "O *fluxo* de caixa # não é Markdown.")))


def _runs(block: MdBlock) -> list[tuple[str, bool, bool]]:
    return [(r.text, r.bold, r.italic) for r in block.runs]


# ---------------------------------------------------------------- sem o python-docx
def test_parse_markdown_reads_headings_lists_paragraphs_and_emphasis():
    blocks = export.parse_markdown(ATA)
    assert [(b.kind, b.level, b.number) for b in blocks] == [
        ("heading", 2, ""), ("paragraph", 0, ""), ("heading", 2, ""), ("bullet", 0, ""), ("bullet", 0, ""), ("bullet", 1, ""),
        ("bullet", 2, ""), ("bullet", 0, ""), ("number", 0, "1"), ("number", 0, "2")]
    assert _runs(blocks[1]) == [("O fundo discutiu o ", False, False), ("orçamento", True, False), (" de 2027 com a ", False, False),
                                ("auditoria", False, True), (" externa.", False, False)]          # linhas seguidas: um parágrafo
    assert _runs(blocks[4]) == [("Ana", True, False), (" — fechar o contrato ", False, False), ("(sem evidência)", False, True)]
    assert _runs(blocks[5]) == [("detalhe com snake_case_name", False, False)]                        # "_" dentro da palavra
    assert _runs(blocks[9]) == [("segundo com código **x** e link", False, False)]                    # código e link: texto
    nested = export.parse_markdown("# T\n\n* a\n    * b\n  * c\n\ntexto depois\n- novo\n---\n> citação *curta*\n```\n**cru**\n```")
    assert [(b.kind, b.level) for b in nested] == [("heading", 1), ("bullet", 0), ("bullet", 1), ("bullet", 1), ("paragraph", 0),
                                                    ("bullet", 0), ("paragraph", 0), ("paragraph", 0)]
    assert _runs(nested[6]) == [("citação ", False, False), ("curta", False, True)] and _runs(nested[7]) == [("**cru**", False, False)]
    assert export.parse_markdown("") == [] and export.parse_markdown("- —") == [MdBlock("bullet", (MdRun("—"),))]


def test_parse_inline_handles_nesting_escapes_and_unpaired_delimiters():
    assert export.parse_inline("***tudo*** e **negrito _itálico_**") == (MdRun("tudo", True, True), MdRun(" e "),
                                                                         MdRun("negrito ", True), MdRun("itálico", True, True))
    assert export.parse_inline(r"\*literal\* e **sem par") == (MdRun("*literal* e **sem par"),)
    assert export.parse_inline("2 * 3 * 4 e a_b_c") == (MdRun("2 * 3 * 4 e a_b_c"),)
    assert export.parse_inline("__forte__ e _leve_") == (MdRun("forte", True), MdRun(" e "), MdRun("leve", False, True))


def test_write_atomic_takes_bytes_and_the_name_takes_the_extension(tmp_path):
    assert export.default_filename(_data(), ".docx") == "2026-10-24 Orçamento 2027.docx"
    assert export.default_filename(_data(), "docx") == "2026-10-24 Orçamento 2027.docx" and export.default_filename(_data()).endswith(".md")
    target = tmp_path / "ata.docx"
    assert export.write_atomic(target, b"PK\x03\x04bytes") == target and target.read_bytes() == b"PK\x03\x04bytes"
    assert export.write_atomic(tmp_path / "t.md", "ç\n").read_bytes() == "ç\n".encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ata.docx", "t.md"]


def test_the_docx_writer_is_lazy_and_reports_the_extra(monkeypatch):
    from listener.rag import docx_export

    monkeypatch.setattr(docx_export.importlib.util, "find_spec", lambda name: None)
    assert docx_export.docx_available() is False


# ---------------------------------------------------------------- com o python-docx
def test_the_docx_has_the_sections_of_the_md_with_titles_lists_and_speakers():
    docx = pytest.importorskip("docx")
    from listener.rag import docx_export

    doc = docx.Document(io.BytesIO(docx_export.meeting_docx(_data())))
    paras = [(p.style.name, p.text) for p in doc.paragraphs]
    assert paras[0] == ("Title", "Orçamento 2027")
    assert paras[1:4] == [("Normal", "Data: 24/10/2026 14:03"), ("Normal", "Duração: 1 h 05 min"),
                          ("Normal", "Participantes: Ana — CFO, Bia")]
    assert [t for s, t in paras if s == "Heading 1"] == ["Ata", "Resumo executivo", "Ações", "Marcadores", "Transcrição"]   # Q4
    assert [t for s, t in paras if s == "Heading 2"] == ["Contexto", "Decisões"]                     # os "##" da ata no nível 2
    assert ("List Bullet 2", "detalhe com snake_case_name") in paras and ("List Bullet 3", "mais fundo") in paras
    assert ("List Bullet", "Mandar a planilha · dono: Bia · prazo: 2026-10-31 · aberta") in paras
    assert ("List Bullet", "[01:10] auditoria <b>sexta</b>") in paras and ("List Bullet", "[01:30] (sem nota)") in paras
    numbered = next(p for p in doc.paragraphs if p.text.startswith("1. "))
    assert numbered.text == "1. primeiro" and numbered.paragraph_format.left_indent is not None
    bullet = next(p for p in doc.paragraphs if p.text.startswith("Ana — fechar"))
    assert [(r.text, r.bold, r.italic) for r in bullet.runs] == [("Ana", True, None), (" — fechar o contrato ", None, None),
                                                                  ("(sem evidência)", None, True)]
    line = doc.paragraphs[-1]
    assert [(r.text, r.bold) for r in line.runs] == [("[01:05] ", None), ("Bia", True), (": O *fluxo* de caixa # não é Markdown.", None)]
    assert doc.core_properties.title == "Orçamento 2027" and doc.core_properties.author == "listener-meet"
    lang = doc.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:lang")[0]
    assert lang.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") == "pt-BR"
    edited = docx.Document(io.BytesIO(docx_export.meeting_docx(_data("# Decisões\n\n## Detalhe\n\ntexto"))))
    assert [(p.style.name, p.text) for p in edited.paragraphs if p.style.name.startswith("Heading")][:3] == [
        ("Heading 1", "Ata"), ("Heading 2", "Decisões"), ("Heading 3", "Detalhe")]         # ata editada com "#": também no 2
    empty = docx.Document(io.BytesIO(docx_export.meeting_docx(ExportMeeting(3, "", "", None))))
    assert [p.text for p in empty.paragraphs] == ["Reunião 3"]


def _meeting_window(qtbot, store):
    from listener.ui.meeting import MeetingWindow

    who = _people(store)
    mid, job = _meeting(store, who)
    minutes = "## Decisões\n\n- Aprovar a **auditoria** [01:10]\n"
    store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", minutes, "gemini", "m", "minutes/1.0", job, None, "pt-BR"))
    store.add_marker(mid, 70_000, "auditoria")
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    return win, mid


def test_save_docx_runs_in_a_task_and_warns_like_the_md(qtbot, store, tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    from PySide6.QtWidgets import QMessageBox

    from listener.rag import docx_export

    win, _mid = _meeting_window(qtbot, store)
    assert win.docx.action.text() == "Salvar .docx…" and win.docx.action.isEnabled()
    assert win.docx.action in win.btn_export.menu().actions()
    task = win.docx.save(str(tmp_path / "ata"))
    assert task is not None and not win.docx.action.isEnabled() and win.docx.save(str(tmp_path / "outra")) is None   # um por vez
    qtbot.waitUntil(lambda: win.docx.task is None, timeout=10000)
    out = tmp_path / "ata.docx"
    assert win.exported[-1] == str(out) and win.message.text() == f"Salvo em {out}." and win.docx.action.isEnabled()
    texts = [p.text for p in docx.Document(str(out)).paragraphs]
    assert texts[0] == "Orçamento 2027" and "Decisões" in texts and "Aprovar a auditoria [01:10]" in texts
    assert any(t.startswith("[01:40] Bia: Concordo") for t in texts)
    before = out.read_bytes()
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(str(a[2]))))

    def locked(*_a: object, **_k: object) -> None:          # o Word segura o arquivo: a troca do temporário falha (U-030)
        raise PermissionError(13, "[WinError 5] Acesso negado: '.ata-x.tmp'")

    monkeypatch.setattr(export, "write_atomic", locked)
    win.docx.save(str(out))
    qtbot.waitUntil(lambda: win.docx.task is None, timeout=10000)
    assert "aberto em outro programa" in warned[-1] and "WinError" not in warned[-1] and out.read_bytes() == before
    def broken(_m: ExportMeeting) -> bytes:                  # um defeito do python-docx: o motivo aparece, nada trava
        raise ValueError("quebrou")

    monkeypatch.setattr(docx_export, "meeting_docx", broken)
    win.docx.save(str(out))
    qtbot.waitUntil(lambda: win.docx.task is None, timeout=10000)
    assert warned[-1].startswith("Não consegui gerar o .docx") and "quebrou" in warned[-1] and win.docx.action.isEnabled()


def test_the_app_exit_waits_for_the_docx_and_the_item_is_disabled_without_the_extra(qtbot, store, tmp_path, monkeypatch):
    pytest.importorskip("docx")
    from listener.rag import docx_export
    from listener.ui.docx_save import DOCX_MISSING

    win, _mid = _meeting_window(qtbot, store)
    task = win.docx.save(str(tmp_path / "saida.docx"))
    win.shutdown()                                           # a saída do app espera o .docx antes de o banco fechar
    assert task is not None and task.isFinished() and (tmp_path / "saida.docx").exists()
    monkeypatch.setattr(docx_export, "docx_available", lambda: False)
    bare, _ = _meeting_window(qtbot, store)
    assert not bare.docx.action.isEnabled() and bare.docx.action.toolTip() == DOCX_MISSING
    assert bare.btn_export.menu().toolTipsVisible()
