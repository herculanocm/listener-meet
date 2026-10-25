"""F5 L4, exportar (item 12, U-011 parcial, D-012) e "copiar com citação" (U-029), sem Qt: o Markdown determinístico da
reunião (ficha, ata com os títulos um nível abaixo, resumos, ações, marcadores e a transcrição "[mm:ss] Nome — Cargo:
texto"), a leitura do banco (revisada quando existe, ações da ata atual), o nome do arquivo que o Windows aceita, a escrita
atômica (o anterior fica inteiro quando a escrita falha) e as citações de fala, trecho e resposta."""
from __future__ import annotations

import pytest

from listener.model import SummaryKind
from listener.rag import export
from listener.rag.export import ExportAction, ExportLine, ExportMeeting
from listener.store.actions import ActionInsert
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import _meeting, _people


def _data() -> ExportMeeting:
    return ExportMeeting(7, "Orçamento 2027", "2026-10-24T14:03:00.000-03:00", 3_900_000, ("Ana — CFO", "Bia"),
                         "## Decisões\n\n- Aprovar a auditoria [01:10]\n", "O fundo aprovou.", "", (
                             ExportAction("Mandar a planilha", "Bia", "2026-10-31", "aberta"), ExportAction("Ligar", "", "", "feita")),
                         ((70_000, "auditoria"), (90_000, "")),
                         (ExportLine(0, "Ana — CFO", "Bom dia."), ExportLine(65_000, "Bia", "O fluxo de caixa.")))


def test_meeting_markdown_is_deterministic_and_complete():
    md = export.meeting_markdown(_data())
    assert md == export.meeting_markdown(_data())
    assert md.startswith("# Orçamento 2027\n\n- **Data:** 24/10/2026 14:03\n- **Duração:** 1 h 05 min\n"
                         "- **Participantes:** Ana — CFO, Bia\n")
    assert "## Ata\n\n### Decisões\n\n- Aprovar a auditoria [01:10]\n" in md                     # títulos da ata um nível abaixo
    assert "## Resumo executivo\n\nO fundo aprovou.\n" in md and "Pré-resumo" not in md           # seção vazia não aparece
    assert "## Ações\n\n- Mandar a planilha · dono: Bia · prazo: 2026-10-31 · aberta\n- Ligar · feita\n" in md
    assert "## Marcadores\n\n- [01:10] auditoria\n- [01:30] (sem nota)\n" in md
    assert md.endswith("## Transcrição\n\n[00:00] Ana — CFO: Bom dia.\n\n[01:05] Bia: O fluxo de caixa.\n")
    assert export.minutes_markdown(_data()).endswith("## Ata\n\n### Decisões\n\n- Aprovar a auditoria [01:10]\n")
    assert "Transcrição" in export.transcript_markdown(_data()) and "Decisões" not in export.transcript_markdown(_data())
    empty = ExportMeeting(3, "", "", None)
    assert export.minutes_markdown(empty).startswith("# Reunião 3\n") and "_(ata ainda não gerada)_" in export.minutes_markdown(empty)
    assert export.fmt_duration(45_000) == "45 s" and export.fmt_duration(35 * 60_000) == "35 min" and export.fmt_duration(None) == ""
    assert export.shift_headings("# A\n## B\ntexto # não\n###### C") == "## A\n### B\ntexto # não\n###### C"


def test_gather_reads_the_reading_view_and_current_actions(store):
    who = _people(store)
    mid, job = _meeting(store, who)
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Decisões\n\n- Aprovar.\n", "gemini", "m", "minutes/1.0",
                                               job, None, "pt-BR"))
    store.summaries.set_text_edited(sid, "## Decisões\n\n- Aprovar (editado).\n")
    store.actions.insert_many([ActionInsert(mid, sid, "Mandar a planilha", None, who["bia"], "2026-10-31", 200_000, None, job)])
    seg = store.segments.list_segments(mid, job)[0]
    store.segments.set_text_llm(mid, job, [(int(seg["id"]), "Bom dia, vamos falar do orçamento (revisado).")])
    store.add_marker(mid, 70_000, "auditoria")
    data = export.gather(store, mid)
    assert data is not None and data.title == "Orçamento 2027" and data.minutes_md.endswith("(editado).\n")
    assert data.actions == (ExportAction("Mandar a planilha", "Bia", "2026-10-31", "aberta"),)
    assert data.transcript[0] == ExportLine(0, "Ana — CFO", "Bom dia, vamos falar do orçamento (revisado).")
    assert data.participants == ("Ana — CFO", "Bia") and data.markers == ((70_000, "auditoria"),)
    original = export.gather(store, mid, prefer_llm=False)
    assert original is not None and original.transcript[0].text == "Bom dia, vamos falar do orçamento de 2027."
    assert export.gather(store, 9999) is None


def test_filename_is_safe_for_windows_and_write_is_atomic(tmp_path, monkeypatch):
    assert export.default_filename(_data()) == "2026-10-24 Orçamento 2027.md"
    assert export.safe_filename('a<b>c:d"e/f\\g|h?i*j\x01. ') == "a b c d e f g h i j"
    assert export.safe_filename("CON") == "_CON" and export.safe_filename("nul.txt") == "_nul.txt"
    assert export.safe_filename("...") == "reuniao"
    assert len(export.safe_filename("x" * 300)) == export.NAME_MAX
    target = tmp_path / "ata.md"
    target.write_text("versão anterior", encoding="utf-8")
    assert export.write_atomic(target, "nova\n") == target and target.read_bytes() == b"nova\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ata.md"]                       # nada de temporário sobrando

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("disco cheio")

    monkeypatch.setattr(export.os, "fsync", broken)
    with pytest.raises(OSError, match="disco cheio"):
        export.write_atomic(target, "parcial")
    assert target.read_text(encoding="utf-8") == "nova\n" and sorted(p.name for p in tmp_path.iterdir()) == ["ata.md"]


def test_quote_with_citation_for_a_line_a_hit_and_an_answer():
    plain, rich = export.quote("  Precisamos aprovar\n a auditoria. ", "Ana — CFO", "Orçamento <2027>", "2026-10-24T10:00:00-03:00", 70_500)
    assert plain == '"Precisamos aprovar a auditoria." — Ana — CFO, Orçamento <2027> (24/10/2026) [01:10]'
    assert rich == "“Precisamos aprovar a auditoria.” — <b>Ana — CFO</b>, <i>Orçamento &lt;2027&gt;</i> (24/10/2026) [01:10]"
    assert export.quote("Resumo.", "", "Reunião", None, None)[0] == '"Resumo." — Reunião'
    saved = [{"alias": "R1", "meeting_id": 5, "t_ms": 723_000, "segment_id": 1, "cited_ms": 723_000}]
    plain, rich = export.quote_answer("Ficou aprovado [R1 12:03].\n<fim>", saved, {5: ("Orçamento 2027", "2026-10-24")})
    assert plain == "Ficou aprovado [Orçamento 2027, 24/10, 12:03].\n<fim>"
    assert rich == "Ficou aprovado [Orçamento 2027, 24/10, 12:03].<br>&lt;fim&gt;" and "listener://" not in rich
