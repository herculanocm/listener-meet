"""F5.14 L1: o adaptador Anthropic na API de hoje, sem rede nem chave. Por família, a forma do pedido (o esquema por
`output_config.format` sem as chaves que a API recusa, ou a ferramenta forçada; o pensamento adaptativo com o esforço, ou o
orçamento; sem `temperature`; o teto com a folga, até o limite da família); o esforço pelos Parâmetros; a recusa sem
reenvio e com a categoria no log; a janela estourada como "grande demais"; o fallback só nas famílias que o aceitam e
desligável, com o uso das partes somado e o modelo que respondeu; o erro no meio do streaming; o PEM como contexto TLS; as
sementes, o pedido longo do Haiku 5.5 e a troca dos padrões num banco v9 com preço editado; e, com o SDK instalado, o
cliente real com `MockTransport` no streaming."""
from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import logging
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from listener.llm import adapters_common as common
from listener.llm import costs
from listener.llm.anthropic import AnthropicProvider
from listener.llm.anthropic_request import FALLBACK_BETA, ClaudeParams, build
from listener.llm.base import RateLimitError, RefusalError, RequestTooLargeError, TransientError
from listener.llm.prompts import identify
from listener.llm.registry import NetworkConfig, RetryingProvider
from listener.llm.schema import PROVIDER_SOFT_KEYS, minimal_instance
from listener.llm.types import LlmTask, Message, Reasoning, Request, Role, Usage
from listener.store import Store

SCHEMA = identify.SCHEMA                                   # `maxLength`, `minimum`, `maximum` e `maxItems` dentro
PAYLOAD = minimal_instance(SCHEMA)
LOGGER = "listener.llm.anthropic"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _req(model: str, schema: Any = SCHEMA, *, reasoning: Reasoning = Reasoning.NONE, out: int = 2000,
         task: LlmTask = LlmTask.IDENTIFY) -> Request:
    return Request(task, model, (Message(Role.SYSTEM, "Responda só com o JSON do esquema."), Message(Role.USER, "a reunião")),
                   schema, max_output_tokens=out, reasoning=reasoning, timeout_s=90.0, cache_prefix=True)


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    return {k for v in node for k in _keys(v)} if isinstance(node, list) else set()


# ---------------------------------------------------------------- a forma do pedido por família (lógica pura)
@pytest.mark.parametrize(("model", "style", "thinking", "limit"), [
    ("claude-opus-5-5", "format", "adaptive", 128_000), ("claude-sonnet-5-5", "format", "adaptive", 128_000),
    ("claude-haiku-5-5", "format", "adaptive", 128_000), ("claude-fable-5-1", "format", "adaptive", 128_000),
    ("claude-opus-4-8", "format", "adaptive", 128_000), ("claude-opus-4-7", "tool", None, 128_000),
    ("claude-sonnet-4-6", "tool", None, 128_000), ("claude-sonnet-4-5-20250929", "tool", None, 64_000),
    ("claude-haiku-4-5", "format", None, 64_000), ("claude-opus-4-1", "format", None, 32_000),
    ("claude-novo-9", "format", "adaptive", 64_000)])
def test_request_shape_by_family(model: str, style: str, thinking: str | None, limit: int):
    kw = build(_req(model), ClaudeParams(), "anthropic")
    assert not {"temperature", "top_p", "top_k", "betas", "fallbacks", "stream"} & set(kw) and kw["timeout"] == 90.0
    assert kw["system"] == "Responda só com o JSON do esquema." and kw["messages"][0]["content"][0]["cache_control"]
    if style == "tool":                                     # a ferramenta forçada de antes: o esquema inteiro e sem pensamento
        assert kw["tool_choice"]["type"] == "tool" and kw["tools"][0]["input_schema"] == SCHEMA
        assert "thinking" not in kw and "output_config" not in kw and kw["max_tokens"] == 2000
        return
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "tools" not in kw and not PROVIDER_SOFT_KEYS & _keys(fmt["schema"])
    assert fmt["schema"]["properties"]["speakers"]["items"]["required"] == SCHEMA["properties"]["speakers"]["items"]["required"]
    if thinking == "adaptive":                              # NONE: esforço baixo, com a folga dele no teto
        assert kw["thinking"] == {"type": "adaptive"} and kw["output_config"]["effort"] == "low" and kw["max_tokens"] == 2000 + 2048
    else:                                                   # orçamento só em LOW e HIGH: NONE vai sem pensamento e sem folga
        assert "thinking" not in kw and "effort" not in kw["output_config"] and kw["max_tokens"] == 2000
    big = build(_req(model, out=limit - 1000), ClaudeParams(), "anthropic")
    assert big["max_tokens"] == (limit if thinking else limit - 1000)               # a folga para no teto da família


def test_effort_by_reasoning_the_parameters_and_the_budget(caplog: pytest.LogCaptureFixture):
    for reasoning, effort, headroom in ((Reasoning.NONE, "low", 2048), (Reasoning.LOW, "medium", 8192), (Reasoning.HIGH, "high", 16384)):
        kw = build(_req("claude-opus-5-5", reasoning=reasoning), ClaudeParams(), "anthropic")
        assert kw["output_config"]["effort"] == effort and kw["max_tokens"] == 2000 + headroom
    params = ClaudeParams.parse({"effort_fast": "medium", "effort_strong": " XHigh "}, "anthropic")
    fast = build(_req("claude-haiku-5-5"), params, "anthropic")
    strong = build(_req("claude-opus-5-5", reasoning=Reasoning.LOW), params, "anthropic")
    assert (fast["output_config"]["effort"], fast["max_tokens"]) == ("medium", 2000 + 8192)
    assert (strong["output_config"]["effort"], strong["max_tokens"]) == ("xhigh", 2000 + 32768)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        bad = ClaudeParams.parse({"effort_fast": "turbo", "effort_strong": 3, "fallbacks": "não"}, "anthropic")
    assert (bad.effort_fast, bad.effort_strong, bad.fallbacks) == (None, None, True)
    assert caplog.text.count("ignorado") == 2 and "fallbacks precisa ser true ou false" in caplog.text
    chat = build(_req("claude-sonnet-4-5", None, reasoning=Reasoning.LOW, out=8192), ClaudeParams(), "anthropic")
    assert chat["thinking"] == {"type": "enabled", "budget_tokens": 2048} and chat["max_tokens"] == 8192 + 2048
    haiku = build(_req("claude-haiku-4-5", reasoning=Reasoning.HIGH), ClaudeParams(effort_strong="max"), "anthropic")
    assert haiku["thinking"]["budget_tokens"] == 8192 and "effort" not in haiku["output_config"]   # o Haiku 4.5 recusa o esforço
    assert build(_req("claude-opus-4-1", reasoning=Reasoning.HIGH, out=31_000), ClaudeParams(), "anthropic")["max_tokens"] == 32_000
    tier = build(_req("claude-opus-5-5"), ClaudeParams.parse({"service_tier": "standard_only"}, "anthropic"), "anthropic")
    assert tier["service_tier"] == "standard_only"


# ---------------------------------------------------------------- o adaptador com um dublê do SDK
class _Final:
    def __init__(self, msg: Any) -> None:
        self.msg = msg

    async def __aenter__(self) -> _Final:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.msg


async def _agen(items: list[Any]) -> Any:
    for it in items:
        await asyncio.sleep(0)
        yield it


class ClaudeStub:
    """A superfície que o adaptador toca: `messages.stream` e `beta.messages.stream` (a mensagem final), `messages.create`
    com `stream=True` (os eventos brutos, para o chat); cada chamada fica em `calls` com o caminho."""

    def __init__(self, *replies: Any, events: list[Any] | None = None, fail: list[BaseException] | None = None) -> None:
        self.replies, self.events, self.fail = list(replies), events or [], list(fail or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages = SimpleNamespace(stream=functools.partial(self._final, "stream"), create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=functools.partial(self._final, "beta")))

    def _final(self, path: str, **kw: Any) -> _Final:
        self.calls.append((path, kw))
        if self.fail:
            raise self.fail.pop(0)
        return _Final(self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])

    async def _create(self, **kw: Any) -> Any:
        self.calls.append(("create", kw))
        return _agen(self.events)


def _usage(inp: int = 100, out: int = 40, iterations: Any = None) -> Any:
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                           iterations=iterations)


def _msg(*, stop: str = "end_turn", model: str = "claude-opus-5-5", category: str | None = None, content: Any = None,
         usage: Any = None) -> Any:
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(PAYLOAD))]
    details = SimpleNamespace(type="refusal", category=category) if stop == "refusal" else None
    return SimpleNamespace(id="msg_1", model=model, stop_reason=stop, stop_details=details,
                           content=blocks if content is None else content, usage=usage or _usage())


def _retrying(stub: ClaudeStub, **params: Any) -> RetryingProvider:
    return RetryingProvider(AnthropicProvider("sk-ant-teste", client=stub, params=params), sleep=lambda _s: asyncio.sleep(0))


def test_complete_reads_the_stream_and_the_canary_fits_after_thinking():
    stub = ClaudeStub(_msg(model="claude-haiku-5-5"))
    comp = run(_retrying(stub).complete(_req("claude-haiku-5-5")))
    assert comp.data == PAYLOAD and comp.finish == "stop" and comp.model == "claude-haiku-5-5" and comp.usage.output_tokens == 40
    stub.replies = [_msg(model="claude-haiku-5-5", content=[SimpleNamespace(type="text", text='{"ok": true}')])]
    test = run(AnthropicProvider("k", client=stub).test_connection("claude-haiku-5-5"))
    path, kw = stub.calls[-1]
    assert test.ok and path == "stream" and kw["max_tokens"] == common.TEST_MAX_TOKENS + 2048 and kw["output_config"]["effort"] == "low"


@pytest.mark.parametrize("category", ["cyber", None])
def test_refusal_is_permanent_without_resend_and_logs_the_category(caplog: pytest.LogCaptureFixture, category: str | None):
    stub = ClaudeStub(_msg(stop="refusal", category=category, content=[SimpleNamespace(type="text", text="segredo da reunião")]))
    with caplog.at_level(logging.WARNING, logger="listener"), pytest.raises(RefusalError) as e:
        run(_retrying(stub, fallbacks=False).complete(_req("claude-opus-5-5")))
    shown = category or "sem categoria"
    assert str(e.value) == f"o Claude recusou esta tarefa ({shown})" and e.value.permanent and not e.value.retryable
    assert len(stub.calls) == 1 and f"o Claude recusou identify em claude-opus-5-5 ({shown})" in caplog.text
    assert "segredo" not in caplog.text


def test_context_window_exceeded_is_too_large_and_mid_stream_errors_follow_the_body_type():
    stub = ClaudeStub(_msg(stop="model_context_window_exceeded"))
    with pytest.raises(RequestTooLargeError) as e:
        run(_retrying(stub).complete(_req("claude-opus-5-5")))
    assert e.value.too_large and len(stub.calls) == 1

    def sse_error(kind: str) -> Exception:
        err = type("APIStatusError", (Exception,), {})(f"{{'type': 'error', 'error': {{'type': '{kind}'}}}}")
        err.status_code, err.body = 200, {"type": "error", "error": {"type": kind, "message": "x"}}   # o 200 da resposta SSE
        return err

    assert isinstance(common.translate_error(sse_error("overloaded_error"), "anthropic"), TransientError)
    assert isinstance(common.translate_error(sse_error("rate_limit_error"), "anthropic"), RateLimitError)
    assert type(common.translate_error(sse_error("outro"), "anthropic")).__name__ == "LlmError"
    stub = ClaudeStub(_msg(), fail=[sse_error("overloaded_error"), sse_error("api_error")])
    rp = _retrying(stub)
    assert run(rp.complete(_req("claude-opus-5-5"))).data == PAYLOAD and rp.retries == 2 and len(stub.calls) == 3


def test_fallback_only_where_accepted_can_be_turned_off_and_sums_the_parts(caplog: pytest.LogCaptureFixture):
    for model, params, path in (("claude-opus-5-5", {}, "beta"), ("claude-sonnet-5-5", {}, "beta"), ("claude-haiku-5-5", {}, "stream"),
                                ("claude-opus-4-8", {}, "stream"), ("claude-opus-5-5", {"fallbacks": False}, "stream")):
        stub = ClaudeStub(_msg(model=model))
        run(_retrying(stub, **params).complete(_req(model)))
        (called, kw), = stub.calls
        expected = ([FALLBACK_BETA], "default") if path == "beta" else (None, None)
        assert called == path and (kw.get("betas"), kw.get("fallbacks")) == expected, model
    parts = [SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=900, output_tokens=0, cache_read_input_tokens=0,
                             cache_creation_input_tokens=0),
             SimpleNamespace(type="fallback_message", model="claude-opus-5", input_tokens=900, output_tokens=30,
                             cache_read_input_tokens=100, cache_creation_input_tokens=0)]
    block = SimpleNamespace(type="fallback", trigger=SimpleNamespace(type="refusal", category="cyber"))
    reply = _msg(model="claude-opus-5", content=[block, SimpleNamespace(type="text", text=json.dumps(PAYLOAD))],
                 usage=_usage(900, 30, parts))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        comp = run(_retrying(ClaudeStub(reply)).complete(_req("claude-opus-5-5")))
    assert comp.model == "claude-opus-5" and comp.data == PAYLOAD
    assert (comp.usage.input_tokens, comp.usage.output_tokens, comp.usage.cached_tokens) == (1900, 30, 100)
    assert "identify: resposta do modelo de reserva claude-opus-5 (recusa em claude-opus-5-5, cyber)" in caplog.text
    assert "claude-opus-5-5 entrada 900, saída 0" in caplog.text and json.dumps(PAYLOAD) not in caplog.text
    price = costs.price_for(list(costs.PRICE_SEEDS), "anthropic", comp.model)
    assert price is not None and price.model == "claude-opus-5"                   # o custo sai pelo modelo que respondeu


def test_the_chat_stream_carries_thinking_and_a_refusal_raises_at_the_end(caplog: pytest.LogCaptureFixture):
    start = SimpleNamespace(type="message_start", message=SimpleNamespace(usage=_usage(50, 1), model="claude-opus-5-5", id="m2"))
    think = SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="pensando"))
    text = SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="parte"))
    end = SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="bio")),
                          usage=SimpleNamespace(output_tokens=9))
    stub = ClaudeStub(events=[start, think, text, end])
    provider = AnthropicProvider("k", client=stub)
    parts: list[str] = []

    async def consume() -> Any:
        s = provider.stream(_req("claude-opus-5-5", None, task=LlmTask.CHAT))
        async for p in s:                                            # o que chegou antes da recusa chega ao chat
            parts.append(p)  # noqa: PERF401  (a recusa levanta no fim: uma compreensão perderia o que chegou)
        return s.result

    with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(RefusalError) as e:
        run(consume())
    kw = stub.calls[0][1]
    assert parts == ["parte"] and str(e.value) == "o Claude recusou esta tarefa (bio)" and e.value.permanent   # R-01: ao fim, erro
    assert kw["stream"] is True and kw["thinking"] == {"type": "adaptive"} and kw["output_config"] == {"effort": "low"}
    assert "fallbacks" not in kw and "o Claude recusou chat em claude-opus-5-5 (bio)" in caplog.text


# ---------------------------------------------------------------- rede: o PEM como contexto TLS (item 9)
def test_the_pem_becomes_a_tls_context_for_httpx2_only():
    certifi = pytest.importorskip("certifi")
    net = NetworkConfig(proxy_url="http://proxy.local:3128", ca_bundle=certifi.where(), system_certs=False)
    kw = common.httpx2_client_kwargs(net, 30.0)
    assert isinstance(kw["verify"], ssl.SSLContext) and kw["proxy"] == "http://proxy.local:3128" and kw["timeout"] == 30.0
    assert common.http_client_kwargs(net, 30.0)["verify"] == certifi.where()            # o do Gemini segue com o caminho
    assert "verify" not in common.httpx2_client_kwargs(NetworkConfig(proxy_url="http://p:1"), 5.0)


# ---------------------------------------------------------------- preços e o banco (itens 7 e 8)
def test_seeds_family_prices_and_the_long_haiku_prompt():
    seeds = {p.model: p for p in costs.PRICE_SEEDS if p.provider == "anthropic"}
    assert {"claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"} < set(seeds)              # as antigas ficam
    opus, haiku_seed = seeds["claude-opus-5-5"], seeds["claude-haiku-5-5"]
    assert (opus.input_usd_m, opus.output_usd_m, opus.cached_usd_m) == (4, 20, 0.2)
    assert (haiku_seed.input_usd_m, haiku_seed.cached_usd_m, seeds["claude-fable-5"].cached_usd_m) == (0.1, 0.01, 1)
    rows = list(costs.PRICE_SEEDS)
    for model, row in (("claude-sonnet-5", "claude-sonnet-5-5"), ("claude-opus-4-8", "claude-opus-5"),
                       ("claude-opus-5-20260301", "claude-opus-5"), ("claude-opus-5-5-20261001", "claude-opus-5-5"),
                       ("claude-fable-5-1", "claude-fable-5-1"), ("claude-fable-5", "claude-fable-5"),
                       ("claude-opus-4-20250514", "claude-opus-4-1"), ("claude-3-5-haiku-latest", "claude-sonnet-5-5"),
                       ("claude-sonnet-4-6", "claude-sonnet-4-6"), ("claude-haiku-4-5-20251001", "claude-haiku-4-5")):
        price = costs.price_for(rows, "anthropic", model)
        assert price is not None and price.model == row, model
    haiku = costs.price_for(rows, "anthropic", "claude-haiku-5-5")
    assert haiku is not None and haiku.long_prompt == (100_000, 5.0)
    base = costs.cost_usd(Usage(100_000, 1000), haiku)
    assert base == pytest.approx((100_000 * 0.10 + 1000 * 0.50) / 1e6)                  # até 100 mil, o preço da tabela
    long = costs.cost_usd(Usage(100_001, 1000, 60_000), haiku)                          # acima: 5 vezes tudo, o cache também
    assert long == pytest.approx(5 * (40_001 * 0.10 + 60_000 * 0.01 + 1000 * 0.50) / 1e6, abs=1e-6)
    edited = costs.price_for([{"provider": "anthropic", "model": "claude-haiku-5-5", "input_usd_m": 1.0, "output_usd_m": 1.0}],
                             "anthropic", "claude-haiku-5-5-20261101")
    assert edited is not None and edited.long_prompt == (100_000, 5.0)                   # a regra é da família, não da linha
    assert costs.price_for(rows, "anthropic", "claude-opus-5-5").long_prompt is None


def test_the_long_prompt_rule_is_per_call_in_a_summed_usage():
    """Revisão de 03/11: o reenvio e o fallback somam o uso das chamadas, e o pedido longo do Haiku 5.5 se cobra por chamada:
    duas de 60 mil tokens não viram uma de 120 mil, 5 vezes mais cara."""
    haiku = costs.price_for(list(costs.PRICE_SEEDS), "anthropic", "claude-haiku-5-5")
    one = (60_000 * 0.10 + 500 * 0.50) / 1e6
    resent = Usage(60_000, 500) + Usage(60_000, 500)                                    # como o `RetryingProvider` soma
    assert resent == Usage(120_000, 1000)                                               # os tokens seguem somados
    assert costs.cost_usd(resent, haiku) == pytest.approx(2 * one)
    mixed = sum((Usage(60_000, 500), Usage(120_000, 500)), Usage())                     # como o fallback soma as partes
    assert len(mixed.parts) == 2
    assert costs.cost_usd(mixed, haiku) == pytest.approx(one + 5 * (120_000 * 0.10 + 500 * 0.50) / 1e6)
    assert costs.cost_usd(Usage(120_000, 1000), haiku) == pytest.approx(5 * (120_000 * 0.10 + 1000 * 0.50) / 1e6)


def test_seeds_and_new_claude_defaults_on_a_v9_database_with_an_edited_price(tmp_path: Path):
    db = tmp_path / "db"
    with Store(db) as s:
        s._x("DELETE FROM llm_prices WHERE provider = 'anthropic' AND model IN ('claude-opus-5-5', 'claude-haiku-5-5')")  # antes da F5.14
        s.providers.set_price("anthropic", "claude-haiku-4-5", 9.0, 9.0, 0.9)                 # preço editado
        s.providers.set_price("anthropic", "claude-sonnet-5-5", 7.0, 7.0, 0.7)                # semente nova já cadastrada à mão
        s.providers.update_provider("anthropic", fast_model="claude-haiku-4-5", strong_model="claude-sonnet-4-5")
        s.providers.update_provider("openai", fast_model="gpt-4.1-mini", strong_model="claude-sonnet-4-5")
        version = s.schema_version()
    with Store.open_readonly(db) as ro:                                                       # o `--check` não grava
        assert ro.providers.get("anthropic")["fast_model"] == "claude-haiku-4-5"
        assert "claude-opus-5-5" not in {p["model"] for p in ro.providers.list_prices("anthropic")}
    with Store(db) as s:
        prices = {p["model"]: p for p in s.providers.list_prices("anthropic")}
        row, other = s.providers.get("anthropic"), s.providers.get("openai")
    assert version == 9 and prices["claude-opus-5-5"]["input_usd_m"] == 4.0 and prices["claude-haiku-5-5"]["cached_usd_m"] == 0.01
    assert prices["claude-haiku-4-5"]["input_usd_m"] == 9.0 and prices["claude-sonnet-5-5"]["input_usd_m"] == 7.0
    assert (row["fast_model"], row["strong_model"]) == ("claude-haiku-5-5", "claude-opus-5-5")
    assert other["strong_model"] == "claude-sonnet-4-5"                                       # só a linha do Claude


def test_the_claude_defaults_change_only_without_a_key_and_column_by_column(tmp_path: Path):
    with Store(tmp_path / "a") as s:
        s.providers.update_provider("anthropic", fast_model="claude-haiku-4-5", strong_model="claude-opus-4-1")
    with Store(tmp_path / "a") as s:
        row = s.providers.get("anthropic")
    assert (row["fast_model"], row["strong_model"]) == ("claude-haiku-5-5", "claude-opus-4-1")       # cada coluna por si
    with Store(tmp_path / "b") as s:
        s.providers.update_provider("anthropic", api_key="sk-ant-x", fast_model="claude-haiku-4-5", strong_model="claude-sonnet-4-5")
    with Store(tmp_path / "b") as s:
        row = s.providers.get("anthropic")
    assert (row["fast_model"], row["strong_model"]) == ("claude-haiku-4-5", "claude-sonnet-4-5")     # com chave, a escolha fica


# ---------------------------------------------------------------- o cliente real do SDK com `MockTransport` (SSE)
def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _reply(model: str, text: str, *, stop: str = "end_turn", details: Any = None, iterations: Any = None, fallback: Any = None) -> bytes:
    usage = {"input_tokens": 9, "output_tokens": 1, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 0}
    head = {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": [], "model": model,
                                                 "stop_reason": None, "usage": usage}}
    blocks: list[dict[str, Any]] = []
    if fallback is not None:
        blocks += [{"type": "content_block_start", "index": 0, "content_block": fallback}, {"type": "content_block_stop", "index": 0}]
    i = len(blocks) // 2
    blocks += [{"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
               {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": "assinatura"}},
               {"type": "content_block_stop", "index": i},
               {"type": "content_block_start", "index": i + 1, "content_block": {"type": "text", "text": ""}},
               {"type": "content_block_delta", "index": i + 1, "delta": {"type": "text_delta", "text": text}},
               {"type": "content_block_stop", "index": i + 1}]
    end_usage: dict[str, Any] = {"output_tokens": 31, "output_tokens_details": {"thinking_tokens": 20}}
    if iterations is not None:
        end_usage["iterations"] = iterations
    tail = {"type": "message_delta", "delta": {"stop_reason": stop, "stop_details": details}, "usage": end_usage}
    return _sse(head, *blocks, tail, {"type": "message_stop"})


@pytest.mark.lento                                                  # importa o SDK (~1 s)
def test_real_sdk_streams_the_request_with_schema_thinking_fallback_and_refusal():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen: list[dict[str, Any]] = []
    replies: list[Any] = []

    def handler(request: Any) -> Any:
        seen.append({"url": str(request.url), "body": json.loads(request.content), "beta": request.headers.get("anthropic-beta")})
        reply = replies.pop(0)
        if isinstance(reply, int):
            return httpx2.Response(reply, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=reply)

    part = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    iterations = [{"type": "message", "model": "claude-opus-5-5", "input_tokens": 900, "output_tokens": 0, **part},
                  {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 900, "output_tokens": 31, **part}]
    swap = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"},
            "trigger": {"type": "refusal", "category": "cyber"}}
    replies += [_reply("claude-opus-5-5", json.dumps(PAYLOAD), iterations=iterations, fallback=swap),
                _reply("claude-haiku-5-5", json.dumps(PAYLOAD)), 529, _reply("claude-haiku-5-5", json.dumps(PAYLOAD)),
                _reply("claude-haiku-5-5", "", stop="refusal", details={"type": "refusal", "category": "bio"})]

    async def main() -> Any:
        client = anthropic.AsyncAnthropic(api_key="sk-ant-teste", max_retries=0, timeout=5.0,
                                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        rp = RetryingProvider(AnthropicProvider("sk-ant-teste", client=client), sleep=lambda _s: asyncio.sleep(0))
        try:
            strong = await rp.complete(_req("claude-opus-5-5"))
            fast = await rp.complete(dataclasses.replace(_req("claude-haiku-5-5"), task=LlmTask.TITLE))
            retried = await rp.complete(_req("claude-haiku-5-5"))
            with pytest.raises(RefusalError, match="bio"):
                await rp.complete(_req("claude-haiku-5-5"))
            return strong, fast, retried, rp.retries
        finally:
            await client.close()

    strong, fast, retried, retries = run(main())
    assert strong.data == PAYLOAD and strong.model == "claude-opus-5" and strong.usage.input_tokens == 1800
    assert fast.data == retried.data == PAYLOAD and fast.usage.input_tokens == 13 and fast.usage.cached_tokens == 4 and retries == 1
    body = seen[0]["body"]
    assert seen[0]["beta"] == FALLBACK_BETA and body["fallbacks"] == "default" and body["stream"] is True and "temperature" not in body
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"]["effort"] == "low" and body["max_tokens"] == 2000 + 2048
    assert not PROVIDER_SOFT_KEYS & _keys(body["output_config"]["format"]["schema"]) and "tools" not in body
    assert seen[1]["beta"] is None and "fallbacks" not in seen[1]["body"] and seen[1]["url"].endswith("/v1/messages")
    assert len(seen) == 5                                                       # o 529 repetiu uma vez; a recusa, nenhuma


@pytest.mark.lento
def test_real_sdk_forced_tool_family_and_the_raw_chat_stream():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen: list[dict[str, Any]] = []
    use = {"type": "tool_use", "id": "t", "name": "emit_result", "input": {}}
    tool = _sse({"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                                      "model": "claude-sonnet-4-6", "stop_reason": None,
                                                      "usage": {"input_tokens": 5, "output_tokens": 1}}},
                {"type": "content_block_start", "index": 0, "content_block": use},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(PAYLOAD)}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}, {"type": "message_stop"})
    replies = [tool, _reply("claude-opus-5-5", "resposta do chat")]

    def handler(request: Any) -> Any:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=replies.pop(0))

    async def main() -> Any:
        client = anthropic.AsyncAnthropic(api_key="sk-ant-teste", max_retries=0,
                                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        provider = AnthropicProvider("sk-ant-teste", client=client)
        try:
            comp = await provider.complete(_req("claude-sonnet-4-6"))
            s = provider.stream(_req("claude-opus-5-5", None, task=LlmTask.CHAT))
            parts = [p async for p in s]
            return comp, parts, s.result
        finally:
            await client.close()

    comp, parts, result = run(main())
    assert comp.data == PAYLOAD and seen[0]["tool_choice"]["name"] == "emit_result" and "thinking" not in seen[0]
    assert seen[0]["max_tokens"] == 2000 and "output_config" not in seen[0]                        # sem pensamento, sem folga
    assert "".join(parts) == "resposta do chat" and result.finish == "stop" and result.usage.output_tokens == 31
    assert seen[1]["thinking"] == {"type": "adaptive"} and "fallbacks" not in seen[1] and seen[1]["stream"] is True
