"""F5.6 L2, revisão do diff (R-03, R-06): cada ponto de chamada da LLM manda as instruções do usuário da própria tarefa e
grava a marca `+u<hash>` do mesmo texto. Os mutantes que mandavam o prompt do app e gravavam a marca passavam na suíte:
identidade (as duas chamadas), título, revisada, ata, executivo, pré-resumo, a redução da ata em blocos, a transcrição pelo
áudio, o resumo parcial e o chat de todas as reuniões. A transcrição pelo áudio deixa o rastro no `timings_json` da etapa
(`prompt`), fora do nome do transcritor, que é a chave da velocidade medida."""
from __future__ import annotations

import json
from collections.abc import Iterator

import numpy as np
import pytest

from listener.config import prompts as prompt_settings
from listener.llm import prompts
from listener.llm.base import RequestTooLargeError
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask, ProviderKind
from listener.model import JobStatus
from listener.pipeline import llm_audio, llm_steps
from listener.pipeline.types import AudioWindow
from tests.test_llm_steps import _ask, _pipeline, _provider_ready, _record, _run_llm

JOB_TASKS = ("identify", "title", "revise", "minutes", "executive", "pre_summary")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


@pytest.fixture
def fresh_search() -> Iterator[None]:
    from listener.rag import search

    search.clear_caches()
    yield
    search.clear_caches()


def _mine(task: str) -> str:
    return f"MINHAS-{task} em {{idioma}}."


def _sent(task: str) -> str:
    return f"MINHAS-{task} em português do Brasil. "


@pytest.mark.usefixtures("no_summary_gate")
def test_every_job_call_sends_the_custom_instructions_it_stamps(platform, store):
    for t in JOB_TASKS:
        store.set_setting(prompt_settings.key(t), _mine(t))
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    fake = FakeProvider()
    assert _run_llm(store, fake)[1] == JobStatus.DONE                                      # o núcleo
    _ask(store, mid, ("minutes", "executive", "pre_summary"))
    assert _run_llm(store, fake)[1] == JobStatus.DONE
    seen: dict[str, list[str]] = {}
    for r in fake.requests:
        seen.setdefault(str(r.task), []).append(r.messages[0].text)
    for t in JOB_TASKS:
        assert seen.get(t), f"{t} não chamou"
        assert all(s.startswith(_sent(t)) for s in seen[t]), t
    for call in store.llm_calls.list_for_meeting(mid):
        assert call["prompt_version"].endswith(f"+u{prompt_settings.text_hash(_mine(call['task']))}"), call["task"]


@pytest.mark.usefixtures("no_summary_gate")
def test_the_identity_split_after_a_too_large_request_keeps_the_instructions(platform, store):
    """A segunda chamada da identidade: o pedido grande demais vira dois blocos, os dois com as instruções suas."""
    store.set_setting("prompt.identify", _mine("identify"))
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    fake = FakeProvider()
    fake.fail_next(RequestTooLargeError("request too large"), LlmTask.IDENTIFY)
    assert _run_llm(store, fake)[1] == JobStatus.DONE
    calls = fake.calls(LlmTask.IDENTIFY)
    assert len(calls) >= 2 and all(r.messages[0].text.startswith(_sent("identify")) for r in calls)
    strategies = {c["strategy"] for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "identify" and c["status"] == "ok"}
    assert strategies == {"map_reduce:2"}


def test_hierarchical_minutes_carry_the_custom_instructions_to_the_reduce(platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    store.jobs.request_cancel(next(j["id"] for j in store.jobs.for_meeting(mid) if j["kind"] == "llm"))
    store.set_setting("prompt.minutes", "Foque nos valores, em {idioma}.")
    assert llm_steps.enqueue_llm_job(store, mid, ["minutes"], requested=True)
    store.providers.update_provider("gemini", models_json=[{"id": "fake-strong", "input_limit": 600}])
    small = FakeProvider(input_limit=600)
    runner, status, _ = _run_llm(store, small)
    assert status == JobStatus.DONE, store.jobs.get(runner.job_id)["error"]
    systems = [r.messages[0].text for r in small.calls(LlmTask.MINUTES)]
    assert len(systems) >= 2 and all(s.startswith("Foque nos valores, em português do Brasil. ") for s in systems)
    assert systems[-1].startswith(f"Foque nos valores, em português do Brasil. {prompts.minutes.REDUCE_SYSTEM}")


def _window() -> AudioWindow:
    samples = (0.1 * np.sin(np.linspace(0, 2 * np.pi * 220 * 4, 16000 * 4))).astype(np.float32)
    return AudioWindow(0, "R", 0, 4000, samples)


def test_the_audio_transcriber_sends_the_custom_instructions_and_exposes_the_mark():
    fake = FakeProvider()
    t = llm_audio.LLMAudioTranscriber(lambda: fake, ProviderKind.GEMINI, "fake-fast", "gemini", " Transcreva em {idioma}. ")
    t.transcribe(_window(), language="pt", initial_prompt="")
    assert fake.requests[0].messages[0].text == f"Transcreva em português. {prompts.transcribe.CONTRACT}"
    assert t.prompt_version == f"transcribe/2.0+u{prompt_settings.text_hash('Transcreva em {idioma}.')}"
    assert t.name == "llm:gemini:fake-fast"                                    # o nome segue a chave da velocidade medida
    plain = llm_audio.LLMAudioTranscriber(lambda: fake, ProviderKind.GEMINI, "fake-fast", "gemini")
    assert plain.prompt_version == "" and plain.name == t.name


def test_the_meeting_job_records_the_transcription_mark_in_its_timings(platform, store):
    """R-06: a transcrição pela LLM com instruções suas deixa o rastro por reunião no `timings_json` da etapa."""
    from tests.fake_pipeline import FakeDeps
    from tests.test_language import SCRIPT_EN, _run_heavy
    from tests.test_language import _record as record_en

    mid = record_en(platform, store, "en")
    fake = FakeProvider()
    deps = FakeDeps(SCRIPT_EN)
    deps._transcriber_factory = lambda: llm_audio.LLMAudioTranscriber(lambda: fake, ProviderKind.GEMINI, "fake-fast", "gemini",
                                                                      "Sem cortes.")
    store.jobs.enqueue(mid)
    job_id, status = _run_heavy(store, deps)
    timings = json.loads(store.jobs.get(job_id)["timings_json"])
    mark = f"transcribe/2.0+u{prompt_settings.text_hash('Sem cortes.')}"
    assert status == JobStatus.DONE and fake.calls(LlmTask.TRANSCRIBE)
    assert timings["transcribe_r"]["prompt"] == mark and timings["transcribe_r"]["model"] == "llm:gemini:fake-fast"
    assert "prompt" not in timings["diarize"]
    rtf = json.loads(store.get_setting("pipeline.rtf_json"))
    assert "transcribe_r:llm:gemini:fake-fast" in rtf and not any("+u" in k for k in rtf)


def test_the_live_partial_sends_the_custom_instructions_it_stamps(store, monkeypatch):
    from listener.llm.partial_task import Outcome, run_partial
    from tests.test_live_partial import _caption, _ready

    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.upsert_me("Eu")
    mid = store.create_meeting("Orçamento", None, None, "Ana")
    _caption(store, mid, 1000, "vamos cortar oito por cento")
    _ready(store)
    store.set_setting("prompt.partial", _mine("partial"))
    r = run_partial(store, mid)
    assert r.outcome == Outcome.DONE
    req, _resp = store.llm_calls.payloads(r.call_id)
    assert req[0]["text"].startswith(f"{_sent('partial')}{prompts.partial.CONTRACT}")
    assert store.llm_calls.get(r.call_id)["prompt_version"] == f"partial/2.0+u{prompt_settings.text_hash(_mine('partial'))}"


@pytest.mark.usefixtures("fresh_search")
def test_the_global_chat_sends_the_custom_instructions_it_stamps(store, monkeypatch):
    from listener.rag import chat_global
    from tests.test_rag_chat import QUESTION, _two_meetings

    monkeypatch.setenv("LISTENER_LLM", "fake")
    _two_meetings(store)
    store.set_setting("prompt.rag_chat", _mine("rag_chat"))
    ans = chat_global.run_global_chat(store, None, QUESTION, None)
    assert ans.error is None and ans.call_id is not None
    req, _resp = store.llm_calls.payloads(ans.call_id)
    assert req[0]["text"].startswith(f"{_sent('rag_chat')}{prompts.rag_chat.CONTRACT}")
    assert store.llm_calls.get(ans.call_id)["prompt_version"] == f"rag_chat/2.0+u{prompt_settings.text_hash(_mine('rag_chat'))}"
