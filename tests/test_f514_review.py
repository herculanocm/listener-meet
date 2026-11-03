"""F5.14, a rodada da revisão do diff (sem rede nem chave; o SDK trocado por um dublê).

- R-01: a recusa no streaming vira `RefusalError` com a categoria ao fim dos eventos; no chat da reunião, a chamada é gravada
  com erro, a tela recebe o motivo e nenhuma resposta (nem a parte que chegou) entra na conversa; a resposta vazia já
  gravada (de uma recusa antiga ou do Gemini) e qualquer mensagem em branco ficam fora do pedido, e o primeiro turno é do
  usuário.
- R-02: o reenvio da resposta cortada cresce ao menos 8.192 tokens (a folga do pensamento é fixa por cima) e as mensagens
  dizem que o número é o teto da resposta; o "já é o limite do modelo" segue.
- R-03: o `xhigh` dos Parâmetros vira `high` no Opus 4.6 e no Sonnet 4.6, com um aviso por família em cada provedor criado."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from listener.llm import chat_task
from listener.llm.anthropic import AnthropicProvider
from listener.llm.anthropic_request import ClaudeParams, build, conversation
from listener.llm.base import BadResponseError
from listener.llm.prompts import title
from listener.llm.registry import GROW_MIN, RetryingProvider
from listener.llm.types import LlmTask, Message, Reasoning, Request, Role
from listener.model import ChatRole, LlmCallStatus
from tests.test_llm_chat_audio import _processed

LOGGER = "listener.llm.anthropic"


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _usage(out: int = 0) -> Any:
    return SimpleNamespace(input_tokens=400, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0)


async def _events(items: list[Any]) -> Any:
    for it in items:
        await asyncio.sleep(0)
        yield it


class StreamStub:
    """O `messages.create(stream=True)` do SDK: cada chamada devolve a próxima lista de eventos e guarda o pedido."""

    def __init__(self, *scripts: list[Any]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return _events(self.scripts.pop(0))


def _start(model: str = "claude-haiku-5-5") -> Any:
    return SimpleNamespace(type="message_start", message=SimpleNamespace(usage=_usage(), model=model, id="m1"))


def _text(piece: str) -> Any:
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=piece))


def _end(stop: str, category: str | None = None) -> Any:
    details = SimpleNamespace(type="refusal", category=category) if stop == "refusal" else None
    return SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop, stop_details=details),
                           usage=SimpleNamespace(output_tokens=7))


def _blank_blocks(kw: dict[str, Any]) -> list[tuple[str, str]]:
    return [(m["role"], b["text"]) for m in kw["messages"] for b in m["content"] if not b["text"].strip()]


# ---------------------------------------------------------------- R-01: a recusa no chat e a mensagem em branco
def test_a_chat_refusal_is_an_error_with_the_reason_and_blank_answers_stay_out(platform, store, monkeypatch, caplog):
    mid = _processed(platform, store)
    store.providers.update_provider("anthropic", api_key="sk-ant-teste", fast_model="claude-haiku-5-5", strong_model="claude-opus-5-5")
    store.providers.set_role("llm", "anthropic")
    store.chat.add(mid, ChatRole.USER, "Quem abriu a reunião?")
    store.chat.add(mid, ChatRole.ASSISTANT, "")                      # a resposta vazia gravada antes (recusa antiga ou o Gemini)
    stub = StreamStub([_start(), _text("Sobre o pentest, "), _end("refusal", "cyber")],
                      [_start(), _text("O prazo é sexta [00:05]."), _end("end_turn")])
    row = store.providers.get("anthropic")
    monkeypatch.setattr(chat_task, "build_provider", lambda _s, _pid: (AnthropicProvider("sk-ant-teste", client=stub), row))
    pieces: list[str] = []
    with caplog.at_level(logging.WARNING, logger="listener"):
        refused = chat_task.run_chat(store, mid, "Como foi o pentest?", pieces.append)
    assert refused.error == "o Claude recusou esta tarefa (cyber)" and refused.text == "Sobre o pentest, " and pieces
    call = store._q("SELECT status, error, model FROM llm_calls ORDER BY id DESC LIMIT 1")[0]
    assert (call["status"], call["error"]) == (LlmCallStatus.ERROR, refused.error) and "(cyber)" in caplog.text
    saved = [(str(r["role"]), str(r["text"])) for r in store.chat.list_for_meeting(mid)]
    assert saved[-1] == (ChatRole.USER, "Como foi o pentest?")       # a parte que chegou não vira resposta gravada
    first = stub.calls[0]
    assert _blank_blocks(first) == [] and first["messages"][0]["role"] == "user"
    assert first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}      # o prefixo da transcrição
    answer = chat_task.run_chat(store, mid, "E o prazo?", pieces.append)
    assert answer.error is None and answer.text == "O prazo é sexta [00:05]." and _blank_blocks(stub.calls[1]) == []
    assert [str(r["text"]) for r in store.chat.list_for_meeting(mid)][-1] == "O prazo é sexta [00:05]."


def test_blank_messages_stay_out_of_the_request_and_the_first_turn_is_the_user():
    msgs = (Message(Role.SYSTEM, "s"), Message(Role.ASSISTANT, "resposta sem a pergunta (histórico cortado)"),
            Message(Role.USER, "q1"), Message(Role.ASSISTANT, ""), Message(Role.USER, "  "), Message(Role.ASSISTANT, "\n"),
            Message(Role.USER, "q2"))
    system, out = conversation(Request(LlmTask.CHAT, "claude-haiku-5-5", msgs, None, cache_prefix=True), "anthropic")
    assert system == "s" and [(m["role"], m["content"][0]["text"]) for m in out] == [("user", "q1"), ("user", "q2")]
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in out[1]["content"][0]


# ---------------------------------------------------------------- R-02: o reenvio da resposta cortada
@contextlib.asynccontextmanager
async def _final(msg: Any) -> Any:
    async def get_final_message() -> Any:
        return msg
    yield SimpleNamespace(get_final_message=get_final_message)


def _cutting(sent: list[int], below: int) -> Any:
    """O `messages.stream` que corta no teto (`max_tokens`) todo pedido com `max_tokens` abaixo de `below`."""
    def stream(**kw: Any) -> Any:
        sent.append(kw["max_tokens"])
        cut = kw["max_tokens"] < below
        answer = [] if cut else [SimpleNamespace(type="text", text='{"title": "Orçamento"}')]
        content = [SimpleNamespace(type="thinking", thinking=""), *answer]
        return _final(SimpleNamespace(id="m", model=kw["model"], stop_reason="max_tokens" if cut else "end_turn", stop_details=None,
                                      usage=_usage(kw["max_tokens"] if cut else 30), content=content))
    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def _title_req(model: str, out: int) -> Request:
    return Request(LlmTask.TITLE, model, (Message(Role.USER, "a reunião"),), title.SCHEMA, max_output_tokens=out)


def test_a_cut_answer_grows_at_least_one_headroom_and_the_message_names_the_answer_ceiling():
    sent: list[int] = []
    rp = RetryingProvider(AnthropicProvider("k", client=_cutting(sent, 10_000)), sleep=lambda _s: asyncio.sleep(0))
    comp = run(rp.complete(_title_req("claude-haiku-5-5", 128)))
    assert comp.data == {"title": "Orçamento"} and rp.grown == 1
    assert sent == [128 + 2048, 128 + GROW_MIN + 2048] and GROW_MIN == 8192                 # antes: 2.176 e só 2.304
    sent.clear()
    rp = RetryingProvider(AnthropicProvider("k", client=_cutting(sent, 10**9)), sleep=lambda _s: asyncio.sleep(0))
    with pytest.raises(BadResponseError, match=r"cortada mesmo com o teto da resposta em 8320 tokens \(sem a folga do pensamento\)"):
        run(rp.complete(_title_req("claude-haiku-5-5", 128)))
    sent.clear()
    with pytest.raises(BadResponseError, match="cortada mesmo com o teto da resposta em 100000"):   # o dobro, quando é maior
        run(rp.complete(_title_req("claude-haiku-5-5", 50_000)))
    assert sent == [50_000 + 2048, 100_000 + 2048]
    sent.clear()
    with pytest.raises(BadResponseError, match="já é o limite do modelo"):                   # o Opus 4.1 sai até 32 mil
        run(rp.complete(_title_req("claude-opus-4-1", 32_000)))
    assert sent == [32_000]


# ---------------------------------------------------------------- R-03: o `xhigh` nas famílias 4.6
def test_xhigh_becomes_high_on_the_4_6_families_with_one_warning_per_family(caplog: pytest.LogCaptureFixture):
    req = Request(LlmTask.CHAT, "m", (Message(Role.USER, "oi"),), None, max_output_tokens=2000)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        params = ClaudeParams.parse({"effort_fast": "xhigh"}, "anthropic")
        for model in ("claude-opus-4-6", "claude-opus-4-6", "claude-sonnet-4-6-20260101", "claude-sonnet-4-6"):
            kw = build(Request(req.task, model, req.messages, None, max_output_tokens=2000), params, "anthropic")
            assert (kw["output_config"]["effort"], kw["max_tokens"]) == ("high", 2000 + 16384), model
        for model in ("claude-opus-4-7", "claude-opus-5-5", "claude-sonnet-5-5"):
            kw = build(Request(req.task, model, req.messages, None, max_output_tokens=2000), params, "anthropic")
            assert (kw["output_config"]["effort"], kw["max_tokens"]) == ("xhigh", 2000 + 32768), model
    assert caplog.text.count("não aceita o esforço xhigh") == 2
    assert "o Claude Opus 4.6 não aceita o esforço xhigh; vai high" in caplog.text and "Claude Sonnet 4.6" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):                                    # outro provedor: avisa de novo
        again = AnthropicProvider("k", client=None, params={"effort_strong": "xhigh"})
        strong = again._kwargs(Request(req.task, "claude-opus-4-6", req.messages, None, max_output_tokens=2000, reasoning=Reasoning.LOW))
    assert strong["output_config"]["effort"] == "high" and caplog.text.count("não aceita o esforço xhigh") == 1
