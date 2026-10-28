"""F5.3 L1, idioma (U-044, U-045, A-01 a A-04, A-07; Aceite 1 com os fakes): as conversões num lugar só; o "Idioma padrão"
das Configurações gravado pelo código do combo, logo depois das chaves, e a adoção única do `llm.output_language = en`
antigo na abertura; o warm-up com o idioma da reunião (o glossário pedido nele) e a reunião nascendo com ele; a reunião em
inglês do warm-up até a ata, sem detecção, com toda saída da LLM em inglês (o pedido diz "English") e o snapshot antigo com
`output_language` carregando; a legenda ao vivo e a transcrição pela LLM no idioma da reunião desde a primeira janela; a
alta precisão em inglês sem desligar a opção (A-04); a ata, o .md e o .docx no idioma da ata (en-US)."""
from __future__ import annotations

import io
import json
import sys

import numpy as np
import pytest

from listener.capture.recorder import Recorder, SourceSpec
from listener.config import language
from listener.llm.fake import FakeProvider
from listener.llm.jobconfig import LlmJobConfig
from listener.llm.types import LlmTask, ProviderKind
from listener.model import ChunkKind, JobKind, JobStatus, SummaryKind
from listener.pipeline.runner import JobRunner
from listener.pipeline.types import AudioWindow, Word
from listener.store import Store
from tests.conftest import make_config
from tests.fake_pipeline import FakeDeps
from tests.test_llm_steps import _provider_ready, _run_llm
from tests.test_runner import CFG

SCRIPT_EN = {"R": [(0.2, 1.4, "good morning Ana how are you"), (1.6, 2.9, "let us close the 2027 budget today")],
             "L": [(0.3, 1.2, "hi everyone"), (2.0, 2.8, "yes sure let us go")]}
ENGLISH = "Write all free text in English"


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _record(platform, store: Store, lang: str | None = "en") -> int:
    """3 s de reunião (dois tons no R, um no L) criada com o idioma do warm-up."""
    mid = store.create_meeting("Budget 2027", "meeting", None, "Ana, Bia", language=lang)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(1.5)
    rec.switch_source(SourceSpec("system", label="Sistema"))
    platform.pump(1.5)
    rec.mark("key point")
    rec.stop()
    return mid


def _run_heavy(store: Store, deps: FakeDeps) -> tuple[int, JobStatus]:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None, "nenhum processamento pendente"
    return int(job["id"]), JobRunner(store, deps, job, config=CFG, heartbeat_thread=False).run()


# ---------------------------------------------------------------- conversões e Configurações
def test_the_conversions_live_in_one_place(store):
    codes = ("pt-BR", "pt", "PT_br", "en", "en-US", "EN", "", None, "es")
    assert [language.output_code(c) for c in codes] == ["pt-BR"] * 3 + ["en"] * 3 + ["pt-BR"] * 3
    assert [language.whisper_code(c) for c in ("pt-BR", "en-US", None)] == ["pt", "en", "pt"]
    assert (language.docx_code("en"), language.docx_code("pt")) == ("en-US", "pt-BR")
    assert language.label("en-GB") == "Inglês (en)" and language.label(None) == "Português (pt-BR)"
    assert language.other("pt") == "en" and language.other("en-US") == "pt-BR"
    assert language.default_language(store) == "pt-BR"
    store.set_setting("app.language", "en")
    assert language.default_language(store) == "en" and language.meeting_language(store, {"language": None}) == "en"
    assert language.meeting_language(store, {"language": "pt"}) == "pt-BR"
    assert language.meeting_language(store, {"language": None, "status": "processada"}) == "pt-BR"    # R-11: a antiga, pt
    store.set_setting("app.language", "fr")                                        # inválido no banco: o padrão
    assert language.default_language(store) == "pt-BR"
    mid = store.create_meeting("t", None, None, None, language="en-US")
    assert store.get_meeting(mid)["language"] == "en" and store.get_meeting(store.create_meeting("u", None, None, None))["language"] is None
    assert store.meetings.change_language(mid, "pt-BR") and not store.meetings.change_language(mid, "pt")   # igual: nada
    rev = store._q("SELECT field, before, after, author FROM revisions WHERE entity = 'meeting' AND entity_id = ?", (mid,))
    assert [tuple(r) for r in rev] == [("language", "en", "pt", "usuario")]


def test_settings_language_group_saves_the_code_of_the_combo(qtbot, store, platform):
    from listener.ui.keys import KEYS_TITLE
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    assert [dlg.nav.item(i).text() for i in range(2)] == [KEYS_TITLE, "Idioma e avisos"]   # F5.4 L3: a página logo depois
    combo = dlg.language_combo
    assert dlg.page("Idioma e avisos").isAncestorOf(combo) and dlg.editor("app.language").widget is combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Português (pt-BR)", "Inglês (en)"]
    assert combo.currentData() == "pt-BR" and "llm.output_language" not in dlg._editors
    combo.setCurrentIndex(combo.findData("en"))
    assert dlg.validation_error() is None
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted and store.get_setting("app.language") == "en"   # o código, não o rótulo
    again = SettingsDialog(store, platform, {})
    qtbot.addWidget(again)
    assert again.language_combo.currentText() == "Inglês (en)"


def test_the_old_english_output_language_is_adopted_once_on_opening(qtbot, qapp, store, platform):
    from listener.ui.controller import Controller

    store.set_setting("llm.output_language", "en")                                 # a linha da F3 (a chave saiu da tabela)
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        c.start()
        assert store.get_setting("app.language") == "en" and store.get_setting("llm.output_language") == "en"   # a antiga fica
        store.set_setting("app.language", "pt-BR")                                 # o usuário voltou: não adota de novo
        assert language.adopt_legacy(store) is False and store.get_setting("app.language") == "pt-BR"
        store._x("DELETE FROM settings WHERE key = ?", ("app.language",))
        for old in ("pt-BR", "auto", "es"):
            store.set_setting("llm.output_language", old)
            assert language.adopt_legacy(store) is False and store.get_setting("app.language", "") == ""
    finally:
        c.emergency_stop()


def test_an_old_llm_snapshot_with_output_language_still_loads():
    old = json.dumps({"provider_id": "gemini", "provider_kind": "gemini", "fast_model": "f", "output_language": "en", "max_parallel": 3})
    cfg = LlmJobConfig.from_json(old)
    assert cfg.ready and cfg.max_parallel == 3 and "output_language" not in cfg.to_json() and not hasattr(cfg, "output_language")


def test_an_old_processed_meeting_without_language_stays_portuguese(platform, store, caplog, no_summary_gate):
    """R-11: a reunião processada antes da F5.3 com o idioma nulo (a D-013 nunca detectou) vale português, não o padrão de
    agora: trocar o padrão para inglês não muda a ata, o chat nem a exportação dela; reprocessar grava o `pt`. A que ainda
    não foi processada segue o padrão de agora."""
    from listener.pipeline.llm_steps import build_context
    from listener.rag import export

    mid = _record(platform, store, None)
    store.jobs.enqueue(mid)
    _run_heavy(store, FakeDeps(SCRIPT_EN))
    store._x("UPDATE meetings SET language = NULL WHERE id = ?", (mid,))             # como ficou a processada antes da F5.3
    store.set_setting("app.language", "en")                                          # o padrão de agora mudou
    assert language.meeting_language(store, store.get_meeting(mid)) == "pt-BR"
    assert build_context(store, mid, store.segments.current_job_id(mid)).meeting_ctx.output_language == "pt-BR"
    data = export.gather(store, mid)
    assert data is not None and data.language == "pt-BR"
    new = store.create_meeting("nova", None, None, None)
    store.finish_meeting(new, 60_000)                                                # gravada, ainda sem processamento
    assert language.meeting_language(store, store.get_meeting(new)) == "en"
    store.jobs.enqueue(mid, JobKind.REPROCESS)
    deps = FakeDeps(SCRIPT_EN)
    with caplog.at_level("INFO", logger="listener.pipeline.runner"):
        job, status = _run_heavy(store, deps)
    assert status == JobStatus.DONE and {c[2] for c in deps.scripted.calls} == {"pt"} and store.get_meeting(mid)["language"] == "pt"
    assert f"job {job}: transcrição em pt (a reunião não tinha idioma: gravado agora)" in caplog.text


# ---------------------------------------------------------------- warm-up
def test_the_warmup_brings_the_default_and_asks_the_glossary_in_the_combo_language(qtbot, store, platform, monkeypatch):
    from listener.llm.glossary_task import GlossaryResult
    from listener.ui import warmup as wu

    store.set_setting("app.language", "en")
    asked: list[str | None] = []

    def fake_glossary(_store, _title, _participants, language=None, should_stop=None):
        asked.append(language)
        return GlossaryResult(("Budget",), ())

    monkeypatch.setattr(wu, "request_glossary", fake_glossary)
    dlg = wu.WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    assert dlg.language_combo.currentData() == "en" and dlg.language_combo.currentText() == "Inglês (en)"
    dlg.title_edit.setText("Budget 2027")
    dlg.request_glossary()
    qtbot.waitUntil(lambda: "Budget" in dlg.glossary_edit.text(), timeout=3000)
    assert asked == ["en"]
    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("pt-BR"))      # trocar depois não refaz o glossário
    qtbot.wait(50)
    assert asked == ["en"] and dlg.glossary_requests == 1
    _cfg, fields = dlg.result_config()
    assert fields["language"] == "pt-BR"
    dlg.reject()


def test_from_the_warmup_combo_to_the_minutes_in_english(qtbot, qapp, store, platform, monkeypatch, no_summary_gate):
    """Aceite 1 com os fakes, de ponta a ponta: o combo do warm-up em Inglês, a reunião gravada pelo controlador já nasce em
    `en`, o processamento transcreve em `en` desde a primeira janela e a LLM escreve a ata em inglês. Sem o combo, o padrão."""
    from listener.capture.recorder import RecordConfig
    from listener.ui import controller as ctl
    from listener.ui import warmup as wu

    store.upsert_me("Me")
    dlg = wu.WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("en"))
    _cfg, fields = dlg.result_config()
    dlg.reject()
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, fields)
        mid = c.recorder.meeting_id
        platform.pump(1.5)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state.value == "idle", timeout=5000)
        assert store.get_meeting(mid)["language"] == "en"
        _provider_ready(store)
        deps = FakeDeps(SCRIPT_EN)
        _job, status = _run_heavy(store, deps)                                     # o job que o "Parar" pôs na fila
        assert status == JobStatus.DONE and deps.scripted.calls and {call[2] for call in deps.scripted.calls} == {"en"}
        _runner, status, fake = _run_llm(store)
        assert status == JobStatus.DONE and all(ENGLISH in r.messages[0].text for r in fake.requests)
        assert store.summaries.current(mid, SummaryKind.MINUTES)["text_llm"].startswith("## Context")
        assert c.start_recording(cfg, {"title": "sem o combo"})
        assert store.get_meeting(c.recorder.meeting_id)["language"] == "pt"
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state.value == "idle", timeout=5000)
    finally:
        c.emergency_stop()


# ---------------------------------------------------------------- Aceite 1 (fakes): inglês do warm-up até a ata
def test_an_english_meeting_comes_out_in_english_without_detection(platform, store, caplog, no_summary_gate):
    from listener.rag import export
    from listener.rag.chunker import parse_minutes

    store.upsert_me("Me", role="Manager")
    mid = _record(platform, store, "en")
    _provider_ready(store)
    deps = FakeDeps(SCRIPT_EN)                                                     # o transcritor falso "detecta" pt
    store.jobs.enqueue(mid)
    with caplog.at_level("INFO", logger="listener.pipeline"):
        parent, status = _run_heavy(store, deps)
    assert status == JobStatus.DONE and {c[2] for c in deps.scripted.calls} == {"en"}    # R e L desde a primeira janela
    assert store.get_meeting(mid)["language"] == "en" and f"job {parent}: transcrição em en (o da reunião)" in caplog.text
    assert "detect" not in caplog.text.lower()
    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM)
    old = json.dumps({"provider_id": "gemini", "provider_kind": "gemini", "fast_model": "fake-fast", "strong_model": "fake-strong",
                      "output_language": "pt-BR"})                                  # snapshot de antes da F5.3
    store._x("UPDATE processing_jobs SET config_json = ? WHERE id = ?", (old, llm_job["id"]))
    _runner, status, fake = _run_llm(store)
    assert status == JobStatus.DONE and fake.requests
    assert all(ENGLISH in r.messages[0].text for r in fake.requests)               # toda saída da LLM em inglês
    minutes = store.summaries.current(mid, SummaryKind.MINUTES)
    assert minutes["language"] == "en" and minutes["text_llm"].startswith("## Context") and "## Decisions" in minutes["text_llm"]
    assert "_(no evidence)_" in minutes["text_llm"] and "## Actions" in minutes["text_llm"]
    assert {store.summaries.current(mid, k)["language"] for k in (SummaryKind.EXECUTIVE, SummaryKind.PRE_SUMMARY)} == {"en"}
    items = parse_minutes(minutes["text_llm"])
    assert ChunkKind.DECISION in {k for k, _t, _ms in items} and all("no evidence" not in t for _k, t, _ms in items)   # o índice
    data = export.gather(store, mid)
    assert data is not None and data.language == "en" and {a.status for a in data.actions} == {"open"}
    assert export.meeting_markdown(data).count("## Minutes") == 1


def test_the_chat_partial_and_glossary_of_an_english_meeting_speak_english(platform, store, monkeypatch, no_summary_gate):
    from listener.llm import chat_task, glossary_task, partial_task
    from listener.store.captions import CaptionInsert

    mid = _record(platform, store, "en")
    _provider_ready(store)
    store.jobs.enqueue(mid)
    _run_heavy(store, FakeDeps(SCRIPT_EN))
    monkeypatch.setenv("LISTENER_LLM", "fake")
    answer = chat_task.run_chat(store, mid, "What was decided?")
    req, _ = store.llm_calls.payloads(answer.call_id)
    assert answer.error is None and ENGLISH in req[0]["text"]
    store.captions.insert(CaptionInsert(mid, "R", 0, 1500, "let us close the budget"))
    fake = FakeProvider()
    result = partial_task.run_partial(store, mid, provider=fake)
    assert result.outcome == partial_task.Outcome.DONE and ENGLISH in fake.requests[0].messages[0].text
    assert store.summaries.current(mid, SummaryKind.PARTIAL)["language"] == "en"
    gloss = glossary_task.request_glossary(store, "Budget 2027", "Ana", language="en")
    greq, _ = store.llm_calls.payloads(gloss.call_id)
    assert ENGLISH in greq[0]["text"]
    default = glossary_task.request_glossary(store, "Orçamento", "Ana")              # sem o combo: o padrão (pt-BR)
    dreq, _ = store.llm_calls.payloads(default.call_id)
    assert "Brazilian Portuguese" in dreq[0]["text"]


def test_the_global_chat_answers_in_the_default_language(platform, store, monkeypatch, no_summary_gate):
    from listener.rag import chat_global
    from listener.rag.index import sync_text

    mid = _record(platform, store, "en")
    _provider_ready(store)
    store.jobs.enqueue(mid)
    _run_heavy(store, FakeDeps(SCRIPT_EN))
    sync_text(store, mid)
    monkeypatch.setenv("LISTENER_LLM", "fake")
    answer = chat_global.run_global_chat(store, None, "budget")
    req, _ = store.llm_calls.payloads(answer.call_id)
    assert "Brazilian Portuguese" in req[0]["text"]                               # a reunião é em inglês; o padrão vale aqui
    store.set_setting("app.language", "en")
    again = chat_global.run_global_chat(store, answer.thread_id, "and the deadline?")
    req, _ = store.llm_calls.payloads(again.call_id)
    assert ENGLISH in req[0]["text"]


# ---------------------------------------------------------------- ao vivo e transcrição pela LLM
def test_the_live_captions_use_the_meeting_language_from_the_first_window(tmp_path):
    from listener.live import proc
    from listener.live import protocol as pr
    from tests.fake_live import FakeEngines, session_input, tone

    db = tmp_path / "db"
    with Store(db) as s:
        mid = s.create_meeting("live", None, None, None, language="en")
    data = session_input(pr.Hello(1000.0, names=False), [("L", tone(1.5, 440), 0), ("R", 24000, pr.SILENCED), ("L", 32000, pr.SILENCED),
                                                         ("R", tone(2.0), 0), ("R", 24000, pr.GAP), ("L", 16000 * 3, pr.GAP)])
    engines = FakeEngines()
    with Store.open_worker(db) as w:
        assert proc.serve(io.BytesIO(data), io.BytesIO(), w, mid, engines, clock=lambda: 1010.0) == 0
        started = next(e for e in w.list_events(mid) if e["type"] == "live_started")
    calls = engines.transcribers[0].calls
    assert len(calls) == 2 and {c[1] for c in calls} == {"en"} and started["data"]["language"] == "en"


def test_the_llm_transcription_gets_the_meeting_language(platform, store):
    from listener.pipeline.llm_audio import LLMAudioTranscriber

    mid = _record(platform, store, "en")
    fake = FakeProvider()
    deps = FakeDeps(SCRIPT_EN)
    deps._transcriber_factory = lambda: LLMAudioTranscriber(lambda: fake, ProviderKind.GEMINI, "fake-fast", "gemini")
    store.jobs.enqueue(mid)
    _job, status = _run_heavy(store, deps)
    hints = [r.messages[1].text for r in fake.calls(LlmTask.TRANSCRIBE)]
    assert status == JobStatus.DONE and hints and all(h.startswith("The language is en.") for h in hints)
    assert store.get_meeting(mid)["language"] == "en"                              # a resposta diz "pt": ignorada


# ---------------------------------------------------------------- alta precisão (A-04)
@pytest.fixture
def whisper_module():
    from listener.pipeline import whisper

    yield whisper
    sys.modules.pop("listener.pipeline.whisper", None)                             # o app não carrega whisper.py (F2)


def test_high_precision_in_english_keeps_whisper_times_and_stays_on(tmp_path, whisper_module, caplog):
    t = whisper_module.FasterWhisperTranscriber(tmp_path / "faster-whisper-small", "small", hq=True)
    words = (Word(0, 400, "hello"), Word(450, 900, "there"))
    samples = np.zeros(16000, np.float32)

    def run(channel: str, t0: int) -> tuple[Word, ...]:
        w = AudioWindow(0, channel, t0, t0 + 1000, samples)
        t._new_pass(w)                                                              # o que o `transcribe` faz antes do Whisper
        return t._align_hq([], words, samples, w, "en")

    with caplog.at_level("INFO", logger="listener.pipeline.whisper"):
        assert [run(ch, t0) for ch, t0 in (("R", 0), ("R", 1000), ("R", 2000), ("L", 0))] == [words] * 4
        assert run("R", 0) == words                                                 # outro job no mesmo worker
    lines = [r.getMessage() for r in caplog.records if "sem modelo de alinhamento para en" in r.getMessage()]
    assert len(lines) == 2 and not t._align_failed and t.hq                        # uma linha por job; a opção segue ligada


# ---------------------------------------------------------------- exportação (A-07)
def test_the_md_and_the_docx_follow_the_minutes_language():
    from listener.llm.render import render_minutes
    from listener.rag import export
    from listener.rag.export import ExportAction, ExportLine, ExportMeeting

    md_en = render_minutes({"decisions": [{"text": "Approve", "segment_ids": []}]}, {}, "en")
    assert md_en.startswith("## Decisions\n\n- Approve _(no evidence)_") and "## Actions" in md_en
    m = ExportMeeting(7, "", "2026-10-24T14:03:00.000-03:00", 3_900_000, ("Ana",), "## Decisions\n\n- Approve [01:10]\n", "Approved.", "",
                      (ExportAction("Send the sheet", "Bia", "2026-10-31", "open"),), ((90_000, ""),),
                      (ExportLine(0, "Ana", "Good morning."),), "en")
    md = export.meeting_markdown(m)
    assert md.startswith("# Meeting 7\n\n- **Date:** 24/10/2026 14:03\n- **Duration:** 1 h 05 min\n- **Participants:** Ana\n")
    assert "## Minutes\n\n### Decisions" in md and "## Executive summary\n\nApproved." in md
    assert "## Actions\n\n- Send the sheet · owner: Bia · due: 2026-10-31 · open\n" in md and "## Markers\n\n- [01:30] (no note)\n" in md
    assert md.endswith("## Transcript\n\n[00:00] Ana: Good morning.\n")
    assert "_(minutes not generated yet)_" in export.minutes_markdown(ExportMeeting(3, "", "", None, language="en"))
    assert export.default_filename(m).endswith("Meeting 7.md")
    docx = pytest.importorskip("docx")
    from listener.rag import docx_export

    doc = docx.Document(io.BytesIO(docx_export.meeting_docx(m)))
    assert [p.text for p in doc.paragraphs if p.style.name == "Heading 1"] == ["Minutes", "Executive summary", "Actions", "Markers",
                                                                                "Transcript"]
    assert doc.paragraphs[1].text == "Date: 24/10/2026 14:03"
    lang = doc.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:lang")[0]
    assert lang.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") == "en-US"
