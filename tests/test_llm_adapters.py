"""Adaptadores Gemini, OpenAI e Anthropic (F3 L2) sem rede: contrato parametrizado contra o fake e contra cada adaptador com
cliente-dublê (tradução `Request -> chamada` e `resposta -> Completion`, streaming, embeddings, contagem, lista de modelos,
canário); tradução de erros por forma; com o SDK instalado (`importorskip`), os clientes reais nascem com `max_retries = 0`
e timeout e um transporte HTTP simulado prova a forma exata do pedido e da resposta nas majors instaladas."""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from listener.llm import adapters_common as common
from listener.llm.base import AuthError, LlmError, ProviderUnavailableError, RateLimitError, RequestTooLargeError, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.registry import NetworkConfig, RetryingProvider
from listener.llm.types import AudioPart, Capability, EmbedTask, LlmTask, Message, Reasoning, Request, Role

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok", "n"],
          "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer", "maximum": 10}}}
PAYLOAD = {"ok": True, "n": 3}


def run(coro):
    return asyncio.run(coro)


def _req(task: LlmTask = LlmTask.TITLE, schema: dict | None = SCHEMA, *, model: str = "m", audio: bool = False,
         reasoning: Reasoning = Reasoning.NONE, cache: bool = False) -> Request:
    parts = (AudioPart(b"\x00" * 1200, "audio/wav", "voz"),) if audio else ()
    return Request(task, model, (Message(Role.SYSTEM, "You are precise."), Message(Role.USER, "Responda em JSON.", parts),
                                 Message(Role.ASSISTANT, "ok"), Message(Role.USER, "de novo")),
                   schema, reasoning=reasoning, cache_prefix=cache)


async def _agen(items: list[Any]):
    for it in items:
        await asyncio.sleep(0)
        yield it


# ---------------------------------------------------------------- dublês dos SDKs (só a superfície que os adaptadores tocam)
class GeminiStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.aio = SimpleNamespace(models=self, aclose=self._aclose)
        self.closed = False

    async def _aclose(self) -> None:
        self.closed = True

    @staticmethod
    def _usage():
        return SimpleNamespace(prompt_token_count=10, candidates_token_count=5, cached_content_token_count=2, thoughts_token_count=1,
                               prompt_tokens_details=[SimpleNamespace(modality="MediaModality.AUDIO", token_count=3)])

    async def generate_content(self, *, model: str, contents: Any, config: Any):
        self.calls.append(("generate", {"model": model, "contents": contents, "config": config}))
        return SimpleNamespace(text=json.dumps(PAYLOAD), parsed=None, usage_metadata=self._usage(),
                               candidates=[SimpleNamespace(finish_reason="FinishReason.STOP")], model_version="gemini-2.5-flash-001",
                               response_id="g1")

    async def generate_content_stream(self, *, model: str, contents: Any, config: Any):
        self.calls.append(("stream", {"model": model, "contents": contents, "config": config}))
        return _agen([SimpleNamespace(text="um ", usage_metadata=None, candidates=[], model_version=None, response_id=None),
                      SimpleNamespace(text="dois ", usage_metadata=None, candidates=[], model_version=None, response_id=None),
                      SimpleNamespace(text="três", usage_metadata=self._usage(), candidates=[SimpleNamespace(finish_reason="STOP")],
                                      model_version="gemini-2.5-flash-001", response_id="g2")])

    async def embed_content(self, *, model: str, contents: Any, config: Any):
        self.calls.append(("embed", {"model": model, "contents": contents, "config": config}))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0]) for _ in contents])

    async def count_tokens(self, *, model: str, contents: Any, config: Any = None):
        self.calls.append(("count", {"model": model, "contents": contents}))
        return SimpleNamespace(total_tokens=42)

    async def list(self, config: Any = None):
        return _agen([SimpleNamespace(name="models/gemini-2.5-flash", display_name="Gemini 2.5 Flash", input_token_limit=1048576,
                                      output_token_limit=65536, supported_actions=["generateContent", "countTokens"]),
                      SimpleNamespace(name="models/gemini-embedding-001", display_name="Embedding", input_token_limit=2048,
                                      output_token_limit=1, supported_actions=["embedContent"]),
                      SimpleNamespace(name="models/imagen-4", display_name="Imagen", input_token_limit=1, output_token_limit=1,
                                      supported_actions=["predict"])])


class OpenAIStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = SimpleNamespace(create=self._create, input_tokens=SimpleNamespace(count=self._count))
        self.embeddings = SimpleNamespace(create=self._embed)
        self.models = SimpleNamespace(list=self._list)
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    @staticmethod
    def _response():
        return SimpleNamespace(id="o1", model="gpt-4.1-mini-2025", status="completed", output_text=json.dumps(PAYLOAD), output=[],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                     input_tokens_details=SimpleNamespace(cached_tokens=2)), incomplete_details=None)

    async def _create(self, **kw: Any):
        self.calls.append(("create", kw))
        if kw.get("stream"):
            return _agen([SimpleNamespace(type="response.output_text.delta", delta="um "),
                          SimpleNamespace(type="response.output_text.delta", delta="dois "),
                          SimpleNamespace(type="response.output_text.delta", delta="três"),
                          SimpleNamespace(type="response.completed", response=self._response())])
        return self._response()

    async def _count(self, **kw: Any):
        self.calls.append(("count", kw))
        return SimpleNamespace(input_tokens=42)

    async def _embed(self, **kw: Any):
        self.calls.append(("embed", kw))
        return SimpleNamespace(model=kw["model"], data=[SimpleNamespace(embedding=[0.6, 0.8]) for _ in kw["input"]],
                               usage=SimpleNamespace(prompt_tokens=7))

    async def _list(self):
        return _agen([SimpleNamespace(id="gpt-4.1-mini"), SimpleNamespace(id="gpt-4o-realtime-preview"), SimpleNamespace(id="whisper-1"),
                      SimpleNamespace(id="text-embedding-3-small")])


class AnthropicStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages = SimpleNamespace(create=self._create, count_tokens=self._count)
        self.models = SimpleNamespace(list=self._list)
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    @staticmethod
    def _usage():
        return SimpleNamespace(input_tokens=8, output_tokens=5, cache_read_input_tokens=2, cache_creation_input_tokens=0)

    async def _create(self, **kw: Any):
        self.calls.append(("create", kw))
        if kw.get("stream"):
            start = SimpleNamespace(type="message_start", message=SimpleNamespace(usage=self._usage(), model="claude-haiku-4-5", id="a2"))
            end = SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"),
                                  usage=SimpleNamespace(output_tokens=5))
            return _agen([start, *(SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))
                                   for t in ("um ", "dois ", "três")), end])
        if "tools" in kw:
            content = [SimpleNamespace(type="tool_use", name="emit_result", input=dict(PAYLOAD))]
        else:
            content = [SimpleNamespace(type="text", text="texto livre")]
        return SimpleNamespace(id="a1", model="claude-haiku-4-5-20251001", content=content, usage=self._usage(),
                               stop_reason="tool_use" if "tools" in kw else "end_turn")

    async def _count(self, **kw: Any):
        self.calls.append(("count", kw))
        return SimpleNamespace(input_tokens=42)

    async def _list(self, limit: int = 100):
        return _agen([SimpleNamespace(id="claude-haiku-4-5-20251001", display_name="Claude Haiku 4.5", max_input_tokens=200000,
                                      max_tokens=64000)])


def _build(name: str) -> tuple[Any, Any]:
    stub: Any
    if name == "fake":
        p = FakeProvider()
        p.script(LlmTask.TITLE, dict(PAYLOAD))
        p.script(LlmTask.CHAT, "um dois três")
        return p, None
    if name == "gemini":
        from listener.llm.gemini import GeminiProvider

        stub = GeminiStub()
        return GeminiProvider("k", client=stub), stub
    if name == "openai":
        from listener.llm.openai import OpenAIProvider

        stub = OpenAIStub()
        return OpenAIProvider("k", client=stub), stub
    from listener.llm.anthropic import AnthropicProvider

    stub = AnthropicStub()
    return AnthropicProvider("k", client=stub), stub


@pytest.mark.parametrize("name", ["fake", "gemini", "openai", "anthropic"])
def test_provider_contract_with_doubles(name: str):
    provider, stub = _build(name)
    rp = RetryingProvider(provider, sleep=lambda s: asyncio.sleep(0))
    model = {"fake": "fake-fast", "gemini": "gemini-2.5-flash", "openai": "gpt-4.1-mini", "anthropic": "claude-haiku-4-5"}[name]
    comp = run(rp.complete(_req(model=model, cache=True)))
    assert comp.data == PAYLOAD and comp.usage.input_tokens > 0 and comp.usage.output_tokens > 0 and comp.latency_ms >= 0
    assert comp.finish == "stop" and comp.model and comp.raw_id

    async def consume():
        s = provider.stream(_req(LlmTask.CHAT, None, model=model))
        parts = [p async for p in s]
        return parts, s.result

    parts, result = run(consume())
    assert len(parts) == 3 and "".join(parts) and result is not None and result.text == "".join(parts)
    if name == "anthropic":
        with pytest.raises(ProviderUnavailableError, match="embeddings"):
            run(provider.embed(["a"], model="x", dim=2, task=EmbedTask.DOCUMENT))
        with pytest.raises(ProviderUnavailableError, match="áudio"):
            run(provider.complete(_req(model=model, audio=True)))
    else:
        emb = run(provider.embed(["a", "b"], model="emb", dim=2, task=EmbedTask.QUERY))
        assert emb.vectors.shape == (2, 2) and np.allclose(np.linalg.norm(emb.vectors, axis=1), 1.0) and emb.dim == 2
    n = run(provider.count_tokens(_req(model=model)))
    assert n is None or n > 0
    models = run(provider.list_models())
    assert models and all(m.input_limit > 0 and m.capabilities for m in models)
    t = run(provider.test_connection(model))
    assert isinstance(t.ok, bool) and t.latency_ms >= 0 and t.model
    run(provider.aclose())
    if stub is None:
        return
    assert stub.closed is False                                     # cliente injetado não é fechado pelo adaptador
    kinds = [c[0] for c in stub.calls]
    assert (kinds[0] in ("generate", "create") and "stream" in kinds) or any(c[1].get("stream") for c in stub.calls)
    first = stub.calls[0][1]
    match name:
        case "gemini":
            cfg = first["config"]
            assert cfg["response_mime_type"] == "application/json" and cfg["response_json_schema"]["required"] == ["ok", "n"]
            assert cfg["system_instruction"] == "You are precise." and cfg["thinking_config"] == {"thinking_budget": 0}
            assert [c["role"] for c in first["contents"]] == ["user", "model", "user"] and first["contents"][0]["parts"][0]["text"]
            assert comp.usage.audio_tokens == 3 and comp.usage.cached_tokens == 2 and comp.usage.output_tokens == 6     # pensamento conta
            embed_cfg = next(c for k, c in stub.calls if k == "embed")["config"]
            assert embed_cfg == {"task_type": "RETRIEVAL_QUERY", "output_dimensionality": 2}
            assert [m.id for m in models] == ["gemini-2.5-flash", "gemini-embedding-001"]           # `predict` fora
            assert Capability.COUNT_TOKENS in models[0].capabilities and models[1].capabilities == {Capability.EMBEDDINGS}
            assert n == 42
        case "openai":
            fmt = first["text"]["format"]
            assert fmt["type"] == "json_schema" and fmt["strict"] is True and "maximum" not in fmt["schema"]["properties"]["n"]
            assert first["instructions"] == "You are precise." and first["store"] is False and first["temperature"] == 0.2
            assert first["prompt_cache_key"].startswith("listener-") and "reasoning" not in first
            assert [i["role"] for i in first["input"]] == ["user", "assistant", "user"]
            assert first["input"][1]["content"][0]["type"] == "output_text" and first["input"][0]["content"][0]["type"] == "input_text"
            assert comp.usage.cached_tokens == 2 and comp.model == "gpt-4.1-mini-2025"
            assert [m.id for m in models] == ["gpt-4.1-mini", "text-embedding-3-small"]
            assert n == 42
            audio = run(provider.complete(_req(model=model, audio=True)))
            assert audio.data is None and stub.calls[-1][1]["input"][0]["content"][1]["input_audio"]["format"] == "wav"
            flac = Message(Role.USER, "x", (AudioPart(b"1", "audio/flac", "v"),))
            with pytest.raises(ProviderUnavailableError, match="wav"):
                run(provider.complete(Request(LlmTask.TRANSCRIBE, model, (flac,))))
            reasoning = run(provider.complete(_req(model="gpt-5-mini", reasoning=Reasoning.LOW)))
            kw = stub.calls[-1][1]
            assert kw["reasoning"] == {"effort": "low"} and "temperature" not in kw and json.loads(reasoning.text) == PAYLOAD
        case "anthropic":
            assert first["tools"][0]["name"] == "emit_result" and first["tool_choice"]["type"] == "tool"
            assert first["system"] == "You are precise." and first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
            assert "cache_control" not in first["messages"][2]["content"][0] and first["max_tokens"] == 8192
            assert "temperature" not in first                                                      # a 2.x não aceita [fato]
            assert comp.usage.input_tokens == 10 and comp.usage.cached_tokens == 2                   # input + cache lidos
            assert models[0].input_limit == 200000 and models[0].output_limit == 64000
            assert n == 42
            thinking = run(provider.complete(_req(LlmTask.CHAT, None, model="claude-sonnet-4-5", reasoning=Reasoning.LOW)))
            kw = stub.calls[-1][1]
            assert kw["thinking"] == {"type": "enabled", "budget_tokens": 2048} and kw["max_tokens"] == 8192
            assert thinking.text == "texto livre"
            forced = run(provider.complete(_req(model="claude-sonnet-4-5", reasoning=Reasoning.LOW)))
            assert "thinking" not in stub.calls[-1][1] and forced.data == PAYLOAD                      # ferramenta forçada: sem pensamento


def test_error_translation_by_shape():
    resp = SimpleNamespace(headers={"retry-after": "5"})
    rl = type("RateLimitError", (Exception,), {})("limite")
    rl.status_code, rl.response = 429, resp
    e = common.translate_error(rl, "openai")
    assert isinstance(e, RateLimitError) and e.retry_after_s == 5.0 and e.retryable
    g = type("ClientError", (Exception,), {})("429 RESOURCE_EXHAUSTED")
    g.code = 429
    assert isinstance(common.translate_error(g, "gemini"), RateLimitError)
    big = type("BadRequestError", (Exception,), {})("This model's maximum context length is 128000 tokens")
    big.status_code = 400
    assert isinstance(common.translate_error(big, "openai"), RequestTooLargeError)
    e413 = type("RequestTooLargeError", (Exception,), {})("x")
    e413.status_code = 413
    assert common.translate_error(e413, "anthropic").too_large
    auth = type("AuthenticationError", (Exception,), {})("chave AIzaSy-secreta-12345678 inválida")
    auth.status_code = 401
    from listener.llm import redact

    redact.GLOBAL.add("AIzaSy-secreta-12345678")
    t = common.translate_error(auth, "gemini")
    redact.GLOBAL.clear()
    assert isinstance(t, AuthError) and t.permanent and "AIzaSy-secreta-12345678" not in str(t) and "«chave»" in str(t)
    s503 = type("InternalServerError", (Exception,), {})("x")
    s503.status_code = 503
    assert isinstance(common.translate_error(s503, "openai"), TransientError)
    assert isinstance(common.translate_error(ConnectionError("rede"), "x"), TransientError)
    assert isinstance(common.translate_error(type("APITimeoutError", (Exception,), {})("t"), "x"), TransientError)
    over = type("OverloadedError", (Exception,), {})("x")
    over.status_code = 529
    assert isinstance(common.translate_error(over, "anthropic"), TransientError)
    other = common.translate_error(ValueError("qualquer"), "x")
    assert type(other) is LlmError and not other.retryable and not other.permanent
    assert common.retry_after_s({"retry-after": "abc"}) is None and common.retry_after_s(None) is None
    assert common.looks_too_large("Request Entity Too Large") and not common.looks_too_large("ok")
    kw = common.http_client_kwargs(NetworkConfig(proxy_url="http://p:3128", ca_bundle="/c.pem"), 30.0)
    assert kw == {"timeout": 30.0, "trust_env": True, "proxy": "http://p:3128", "verify": "/c.pem"}
    assert not common.needs_own_http_client(NetworkConfig()) and common.needs_own_http_client(NetworkConfig(proxy_url="x"))


def test_canary_reports_failures_in_portuguese_and_without_the_key():
    fake = FakeProvider()
    ok = run(common.canary(fake, "fake-fast"))
    assert ok.ok and ok.model == "fake-fast"
    fake.script(LlmTask.TEST, {"ok": False})
    assert not run(common.canary(fake, "fake-fast")).ok
    fake.script(LlmTask.TEST, "sem json")
    bad = run(common.canary(fake, "fake-fast"))
    assert not bad.ok and bad.error
    fake.fail_next(RateLimitError("429 limite"), LlmTask.TEST)
    t = run(common.canary(fake, "fake-fast"))
    assert not t.ok and "429" in (t.error or "")


# ---------------------------------------------------------------- com os SDKs instalados (célula `llm` do CI, LISTENER_SDK_TESTS=1):
# importar os três SDKs custa ~2 s; fora do CI rodam só quando pedidos, para a suíte ficar abaixo de 8 s (aceite da F3)
sdk_tests = pytest.mark.skipif(not os.environ.get("LISTENER_SDK_TESTS"), reason="LISTENER_SDK_TESTS=1 liga os testes com os SDKs reais")


@sdk_tests
def test_real_gemini_client_has_no_sdk_retries_and_mock_transport_round_trip():
    genai = pytest.importorskip("google.genai")
    httpx = pytest.importorskip("httpx")
    from google.genai import types

    from listener.llm.gemini import GeminiProvider

    seen: dict[str, Any] = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        if seen.get("fail"):
            return httpx.Response(429, headers={"retry-after": "2"},
                                  json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(PAYLOAD)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4,
                              "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 11}]},
            "modelVersion": "gemini-2.5-flash", "responseId": "r-1"})

    async def main():
        p = GeminiProvider("AIza-teste", net=NetworkConfig(system_certs=False, timeout_s=7.0))
        real = p._client()
        opts = real._api_client._http_options
        assert opts.retry_options.attempts == 1 and opts.timeout == 7000                   # sem retentativa do SDK; ms
        await p.aclose()
        client = genai.Client(api_key="AIza-teste", http_options=types.HttpOptions(
            httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_options={"attempts": 1}))
        p2 = GeminiProvider("AIza-teste", client=client)
        comp = await RetryingProvider(p2).complete(_req(model="gemini-2.5-flash"))
        seen["fail"] = True
        with pytest.raises(RateLimitError) as e:
            await p2.complete(_req(model="gemini-2.5-flash"))
        await client.aio.aclose()
        return comp, e.value

    comp, err = run(main())
    assert comp.data == PAYLOAD and comp.usage.input_tokens == 11 and comp.model == "gemini-2.5-flash" and comp.raw_id == "r-1"
    assert "gemini-2.5-flash:generateContent" in seen["url"]
    gen = seen["body"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json" and gen["responseJsonSchema"]["required"] == ["ok", "n"]
    budget = gen["thinkingConfig"].get("thinkingBudget", gen["thinkingConfig"].get("thinking_budget"))   # o SDK manda snake_case [fato]
    assert budget == 0 and seen["body"]["systemInstruction"]["parts"][0]["text"] == "You are precise."
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]
    assert err.retry_after_s == 2.0 and err.status == 429


@sdk_tests
def test_real_openai_client_has_no_sdk_retries_and_mock_transport_round_trip():
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    from listener.llm.openai import OpenAIProvider

    seen: dict[str, Any] = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        if seen.get("fail"):
            return httpx2.Response(400, json={"error": {"message": "This model's maximum context length is 128000 tokens.",
                                                        "code": "context_length_exceeded", "type": "invalid_request_error"}})
        return httpx2.Response(200, json={
            "id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-4.1-mini-2025-04-14", "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(PAYLOAD), "annotations": []}]}],
            "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16, "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}, "parallel_tool_calls": True, "tool_choice": "auto", "tools": []})

    async def main():
        p = OpenAIProvider("sk-teste", net=NetworkConfig(system_certs=False, timeout_s=9.0))
        real = p._client()
        assert real.max_retries == 0 and real.timeout == 9.0
        await p.aclose()
        client = openai.AsyncOpenAI(api_key="sk-teste", max_retries=0, timeout=5.0,
                                    http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        p2 = OpenAIProvider("sk-teste", client=client)
        comp = await RetryingProvider(p2).complete(_req(model="gpt-4.1-mini"))
        seen["fail"] = True
        with pytest.raises(RequestTooLargeError):
            await p2.complete(_req(model="gpt-4.1-mini"))
        await client.close()
        return comp

    comp = run(main())
    assert comp.data == PAYLOAD and comp.usage.input_tokens == 12 and comp.raw_id == "resp_1" and comp.model == "gpt-4.1-mini-2025-04-14"
    assert seen["url"].endswith("/v1/responses") and seen["auth"] == "Bearer sk-teste"
    body = seen["body"]
    assert body["text"]["format"]["strict"] is True and body["text"]["format"]["name"] == "title" and body["store"] is False
    assert body["instructions"] == "You are precise." and body["input"][1]["content"][0]["type"] == "output_text"


@sdk_tests
def test_real_anthropic_client_has_no_sdk_retries_and_mock_transport_round_trip():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from listener.llm.anthropic import AnthropicProvider

    seen: dict[str, Any] = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen.setdefault("body", json.loads(request.content))                       # o primeiro pedido (com prefixo em cache)
        seen["key"] = request.headers.get("x-api-key") or request.headers.get("authorization")   # a 2.x usa Bearer [fato]
        if seen.get("fail"):
            return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "tool_use", "id": "tu_1", "name": "emit_result", "input": PAYLOAD}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 3, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 0}})

    async def main():
        p = AnthropicProvider("sk-ant-teste", net=NetworkConfig(system_certs=False, timeout_s=11.0))
        real = p._client()
        assert real.max_retries == 0 and real.timeout == 11.0
        await p.aclose()
        client = anthropic.AsyncAnthropic(api_key="sk-ant-teste", max_retries=0, timeout=5.0,
                                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        p2 = AnthropicProvider("sk-ant-teste", client=client)
        comp = await RetryingProvider(p2).complete(_req(model="claude-haiku-4-5", cache=True))
        seen["fail"] = True
        with pytest.raises(AuthError):
            await p2.complete(_req(model="claude-haiku-4-5"))
        await client.close()
        return comp

    comp = run(main())
    assert comp.data == PAYLOAD and comp.usage.input_tokens == 13 and comp.usage.cached_tokens == 4 and comp.finish == "stop"
    assert seen["url"].endswith("/v1/messages") and "sk-ant-teste" in (seen["key"] or "")
    body = seen["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "emit_result", "disable_parallel_tool_use": True}
    assert body["tools"][0]["input_schema"]["properties"]["n"]["maximum"] == 10 and body["system"] == "You are precise."
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"} and body["max_tokens"] == 8192
