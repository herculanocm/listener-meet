"""Chat e transcrição pela LLM (F3 L6): `StreamTask` (pedaços e cancelamento), reescrita de `[mm:ss]` para o segmento mais
próximo, `run_chat` com o fake (histórico, custo, `chat_messages`), painel de chat com clique que toca o trecho,
`LLMAudioTranscriber` (FLAC, palavras interpoladas, `speech_ms`, nome), `prepare_models` sem Whisper no modo `llm`, `deps_key`
com provedor e modelo, estimativa do job `llm` pela latência média."""
from __future__ import annotations

import pytest

from listener.llm import chat_task
from listener.llm.fake import FakeProvider
from listener.llm.prompts.common import Line
from listener.llm.types import Capability, ProviderKind
from listener.model import JobKind, JobStatus
from listener.pipeline import llm_audio
from listener.pipeline.types import AudioWindow
from listener.store import playback
from tests.test_llm_steps import KEY, _pipeline, _provider_ready, _record, _run_llm

pytest.importorskip("PySide6")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _processed(platform, store) -> int:
    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE
    return mid


def test_rewrite_citations_and_stream_task(qtbot):
    from listener.ui.tasks import run_stream

    lines = [Line(10, 0, "Ana", "oi"), Line(11, 723_000, "Bia", "prazo"), Line(12, 730_000, "Ana", "ok")]
    html, cites = chat_task.rewrite_citations("Prazo fechado [12:03] e <b>fim</b> [59:59].\nLinha 2 [12:10]", lines)
    assert '<a href="listener://play?seg=11">[12:03]</a>' in html and "&lt;b&gt;" in html and "[59:59]" in html and "seg=None" not in html
    assert html.endswith('<br>Linha 2 <a href="listener://play?seg=12">[12:10]</a>')
    assert [(c.t_ms, c.segment_id) for c in cites] == [(723_000, 11), (3_599_000, None), (730_000, 12)]
    assert chat_task.nearest_segment(735_001, lines) is None and chat_task.citation_segment("listener://play?seg=7") == 7
    assert chat_task.citation_segment("x") is None
    chunks: list[str] = []
    done: list[object] = []

    def slow(emit, cancelled):
        for i in range(50):
            if cancelled():
                return f"parou em {i}"
            emit(f"p{i} ")
        return "completo"

    task = run_stream(slow, None, chunks.append, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert done == ["completo"] and len(chunks) == 50
    import threading

    gate = threading.Event()
    chunks.clear()
    done.clear()

    def waits(emit, cancelled):
        emit("a")
        gate.wait(3.0)
        return "cancelado" if cancelled() else "não cancelado"

    task = run_stream(waits, None, chunks.append, done.append)
    qtbot.waitUntil(lambda: chunks == ["a"], timeout=3000)
    task.cancel()
    gate.set()
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert done == ["cancelado"] and task.cancelled


def test_run_chat_records_history_and_cost_and_panel_plays_citation(qtbot, platform, store, monkeypatch):
    from listener.ui.transcript import TranscriptDialog

    mid = _processed(platform, store)
    monkeypatch.setenv("LISTENER_LLM", "fake")
    with pytest.raises(ValueError, match="pergunta"):
        chat_task.run_chat(store, mid, "   ")
    pieces: list[str] = []
    answer = chat_task.run_chat(store, mid, "O que ficou decidido?", pieces.append)
    assert answer.error is None and "".join(pieces) == answer.text and answer.text.startswith("Resposta simulada")
    assert len(answer.citations) == 2 and answer.citations[0].segment_id is not None and answer.citations[1].segment_id is None
    assert 'href="listener://play?seg=' in answer.html and answer.cost_usd is not None and answer.call_id
    msgs = store.chat.list_for_meeting(mid)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["provider"] == "gemini" and msgs[1]["cost_usd"] == answer.cost_usd
    call = store.llm_calls.get(answer.call_id)
    assert call["task"] == "chat" and call["strategy"] == "stream" and call["job_id"] == store.segments.current_job_id(mid)
    second = chat_task.run_chat(store, mid, "E o prazo?")
    assert second.error is None and len(store.chat.list_for_meeting(mid)) == 4
    req, _ = store.llm_calls.payloads(second.call_id)
    assert req[1]["text"].startswith("Meeting date") and "O que ficou decidido?" in "".join(m["text"] for m in req)   # prefixo + histórico
    assert "Você:" in chat_task.history_html(store, mid, chat_task.transcript_lines(store, mid))
    # painel: pergunta em streaming, status com citações e custo, clique toca o trecho
    played: list[tuple[int, int]] = []
    monkeypatch.setattr(playback, "play", lambda samples, sr: played.append((len(samples), sr)))
    dlg = TranscriptDialog(store, mid)
    qtbot.addWidget(dlg)
    panel = dlg.chat
    assert "Você:" in panel.view.toPlainText() and panel.lines
    panel.question.setText("quem falou primeiro?")
    panel.ask()
    qtbot.waitUntil(lambda: bool(panel.answers), timeout=5000)
    assert "citação(ões) clicável(is)" in panel.status.text() and "US$" in panel.status.text() and panel.task is None
    from PySide6.QtCore import QUrl

    seg = next(c.segment_id for c in panel.answers[0].citations if c.segment_id is not None)
    panel._link_clicked(QUrl(f"listener://play?seg={seg}"))
    qtbot.waitUntil(lambda: bool(played), timeout=3000)
    assert panel.played and panel.played[0][0] in ("R", "L") and played[0][1] == 16000
    panel._link_clicked(QUrl("listener://play?seg=999999"))
    assert "não existe" in panel.status.text()
    dlg.close()
    # sem provedor: erro claro
    store.providers.set_active("gemini", llm=False)
    with pytest.raises(ValueError, match="provedor"):
        chat_task.run_chat(store, mid, "x")


def test_chat_panel_renders_the_partial_answer_while_streaming(qtbot, platform, store, monkeypatch):
    """R3-06: a resposta aparece na view a cada pedaço, antes do `done`; o `reload()` final troca pela versão com citações."""
    from listener.ui.chat import ChatPanel

    mid = _processed(platform, store)
    monkeypatch.setenv("LISTENER_LLM", "fake")
    panel = ChatPanel(store, mid)
    qtbot.addWidget(panel)
    seen: list[tuple[int, bool]] = []
    on_chunk = panel._on_chunk

    def spy(piece: str) -> None:
        on_chunk(piece)
        seen.append((len(panel.view.toPlainText()), bool(panel.answers)))

    panel._on_chunk = spy                                                  # type: ignore[method-assign]
    panel.question.setText("o que ficou decidido?")
    panel.ask()
    assert "Você: o que ficou decidido?" in panel.view.toPlainText()
    qtbot.waitUntil(lambda: bool(panel.answers), timeout=5000)
    sizes = [n for n, _done in seen]
    assert len(seen) >= 2 and not any(done for _n, done in seen) and sizes == sorted(sizes) and sizes[0] < sizes[-1]
    assert panel.answers[0].text[:20] in panel.view.toPlainText() and 'seg=' in panel.view.toHtml()


def test_llm_audio_transcriber_interpolates_words_and_models_skip_whisper(tmp_path, store, monkeypatch):
    import numpy as np

    from listener.pipeline import models, worker
    from listener.pipeline.base import PermanentError
    from listener.pipeline.types import JobConfig

    fake = FakeProvider()
    t = llm_audio.LLMAudioTranscriber(lambda: fake, ProviderKind.GEMINI, "fake-fast", "gemini")
    assert t.name == "llm:gemini:fake-fast"
    samples = (0.1 * np.sin(np.linspace(0, 2 * np.pi * 220 * 4, 16000 * 4))).astype(np.float32)
    win = AudioWindow(3, "R", 60_000, 64_000, samples)
    tw = t.transcribe(win, language=None, initial_prompt="SRM, FIDC")
    assert tw.idx == 3 and tw.language == "pt" and tw.language_prob == 0.9 and 0 < tw.speech_ms <= 4000
    assert [w.text for w in tw.words] == ["fala", "simulada", "um", "dois", "três", "quatro", "cinco", "seis"]
    assert tw.words[0].start_ms == 60_000 and tw.words[-1].end_ms <= 64_000
    assert all(a.end_ms <= b.start_ms + 1 for a, b in zip(tw.words, tw.words[1:], strict=False))
    assert t.calls[0][2] == "audio/flac" and 0 < t.calls[0][1] < 200_000 and fake.closed
    req = fake.requests[0]
    assert req.task == "transcribe" and req.messages[1].audio[0].mime == "audio/flac" and "SRM, FIDC" in req.messages[1].text
    # provedor sem FLAC: WAV em fatias; provedor sem áudio: erro permanente
    openai_fake = FakeProvider(kind=ProviderKind.OPENAI)
    t2 = llm_audio.LLMAudioTranscriber(lambda: openai_fake, ProviderKind.OPENAI, "gpt-4o-audio", "openai")
    tw2 = t2.transcribe(AudioWindow(0, "R", 0, 4000, samples), language="pt", initial_prompt="")
    assert t2.calls[0][2] == "audio/wav" and len(tw2.words) == 8
    # R3-14: FLAC acima do limite em linha (20 MB) cai para WAV em fatias, com os tempos de cada fatia
    monkeypatch.setattr(llm_audio, "MAX_INLINE_BYTES", 1000)
    monkeypatch.setattr(llm_audio, "WAV_WINDOW_S", 1)
    big = FakeProvider()
    t4 = llm_audio.LLMAudioTranscriber(lambda: big, ProviderKind.GEMINI, "fake-fast", "gemini")
    tw4 = t4.transcribe(AudioWindow(5, "R", 10_000, 14_000, samples), language="pt", initial_prompt="")
    assert [c[2] for c in t4.calls] == ["audio/wav"] * 4 and all(c[1] > 1000 for c in t4.calls)
    assert tw4.words[0].start_ms == 10_000 and tw4.words[-1].end_ms <= 14_000 and len(tw4.words) == 32
    assert [r.messages[1].audio[0].label for r in big.requests] == [f"janela parte {i}" for i in range(1, 5)]
    deaf = FakeProvider(kind=ProviderKind.ANTHROPIC, capabilities=frozenset({Capability.TEXT, Capability.JSON}))
    t3 = llm_audio.LLMAudioTranscriber(lambda: deaf, ProviderKind.ANTHROPIC, "claude", "anthropic")
    with pytest.raises(PermanentError, match="não aceita áudio"):
        t3.transcribe(win, language=None, initial_prompt="")
    raw = [{"start_s": 1, "end_s": 0.5, "text": "x"}, {"start_s": "a", "end_s": 1, "text": "y"}, {"start_s": 0, "end_s": 1, "text": ""},
           {"start_s": 0, "end_s": 1.0, "text": "a b"}]
    words, speech = llm_audio.interpolate_words(raw, 0, 1000)
    assert [w.text for w in words] == ["a", "b"] and speech == 1000 and words[0].end_ms == 499 and words[1].start_ms == 500
    # prepare_models não baixa o Whisper no modo llm; deps_key muda com transcritor, provedor e modelo
    cfg = JobConfig(models_dir=str(tmp_path / "m"), transcriber="llm")
    models.ready_mark(cfg, "pyannote").parent.mkdir(parents=True, exist_ok=True)
    models.ready_mark(cfg, "pyannote").write_text("ok")
    calls: list[str] = []
    monkeypatch.setattr(models, "download_whisper", lambda c, p: calls.append("whisper"))
    monkeypatch.setattr(models, "disable_hf_symlinks", lambda: None)
    models.prepare_models(cfg, "hf_x", lambda m, f: None)
    assert calls == []
    models.prepare_models(JobConfig(models_dir=str(tmp_path / "m")), "hf_x", lambda m, f: None)
    assert calls == ["whisper"]
    for k in ("HF_HOME", "HF_HUB_OFFLINE", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "CT2_VERBOSE", "TOKENIZERS_PARALLELISM"):
        monkeypatch.delenv(k, raising=False)
    k1 = models.deps_key(cfg, "hf", ("gemini", "fake-fast"))
    assert k1[5:] == ("llm", "gemini", "fake-fast") and models.deps_key(cfg, "hf", ("gemini", "outro")) != k1
    assert models.deps_key(JobConfig(), "hf", ("gemini", "fake-fast"))[5:] == ("whisper", "", "")
    assert worker.llm_transcriber_key(store, JobConfig()) == ("", "")
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast")
    assert worker.llm_transcriber_key(store, cfg) == ("gemini", "fake-fast")
    deps = models.real_deps(cfg, "hf_x", store)
    assert deps.transcriber().name == "llm:gemini:fake-fast"
    with pytest.raises(PermanentError, match="banco"):
        models.real_deps(cfg, "hf_x").transcriber()


def test_queue_estimates_llm_job_by_average_latency(store):
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert
    from listener.ui.queue import job_row_texts

    mid = store.create_meeting("r", None, None, None)
    store.finish_meeting(mid, 60_000)
    jid = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["identify", "minutes", "title"], "parent_job_id": 1})
    store.jobs.claim(pid=1, kinds=("llm",))
    store.jobs.set_progress(jid, "minutes", 0.33, {"llm": {"done": ["identify"], "cost_usd": 0}})
    job = store.jobs.get(jid)
    assert job_row_texts(job, None, "{}")[5] == "sem estimativa"
    for ms in (30_000, 90_000):
        store.llm_calls.insert(LlmCallInsert(mid, 1, "minutes", "gemini", "m", None, None, 1, 1, 0, 0, None, ms, LlmCallStatus.OK))
    assert job_row_texts(job, None, "{}", store.llm_calls.avg_latency_ms() / 1000)[5] == "≈ 2 min"       # 60 s x 2 tarefas que faltam
