"""Camada de LLM sem adaptadores (F3 L1): validador e verificador do subconjunto estrito, redação de chave, `RetryingProvider`
(429 com `Retry-After`, timeout, "request too large" sem retentativa, reenvio único quando o JSON não vale), custos,
tabela de modelos, `FakeProvider` (roteiro, falhas, streaming, paralelismo), registro com `LISTENER_LLM=fake`."""
from __future__ import annotations

import asyncio
import logging

import numpy as np
import pytest

from listener.llm import costs, models_table, redact, schema
from listener.llm.base import BadResponseError, LlmError, ProviderUnavailableError, RateLimitError, RequestTooLargeError, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.registry import NetworkConfig, RetryingProvider, load_provider
from listener.llm.types import Capability, EmbedTask, LlmTask, Message, ProviderKind, Request, Role, Usage

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "score", "tags", "owner"],
          "properties": {"title": {"type": "string", "maxLength": 20}, "score": {"type": "number", "minimum": 0, "maximum": 1},
                         "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
                         "owner": {"type": ["integer", "null"]}}}


def _req(task: LlmTask = LlmTask.TITLE, json_schema: dict | None = SCHEMA, text: str = "oi", **kw) -> Request:
    return Request(task, "fake-fast", (Message(Role.USER, text),), json_schema, **kw)


def run(coro):
    return asyncio.run(coro)


def test_validator_coerces_and_reports_paths():
    ok = schema.validate(SCHEMA, {"title": "x", "score": 1, "tags": ["a"], "owner": 3.0})
    assert ok == {"title": "x", "score": 1.0, "tags": ["a"], "owner": 3} and isinstance(ok["score"], float) and isinstance(ok["owner"], int)
    assert schema.validate(SCHEMA, {"title": "", "score": 0.5, "tags": [], "owner": None})["owner"] is None
    for bad, where in (({"title": "x" * 21, "score": 0, "tags": [], "owner": None}, "$.title"),
                       ({"title": "x", "score": 2, "tags": [], "owner": None}, "$.score"),
                       ({"title": "x", "score": 0, "tags": ["c"], "owner": None}, "$.tags[0]"),
                       ({"title": "x", "score": 0, "tags": [], "owner": 1.5}, "$.owner"),
                       ({"title": "x", "score": 0, "tags": []}, "$.owner"),
                       ({"title": "x", "score": 0, "tags": [], "owner": None, "extra": 1}, "$.extra"),
                       ({"title": 5, "score": 0, "tags": [], "owner": None}, "$.title"),
                       ({"title": "x", "score": True, "tags": [], "owner": None}, "$.score")):
        with pytest.raises(schema.SchemaError) as e:
            schema.validate(SCHEMA, bad)
        assert e.value.path == where
    with pytest.raises(schema.SchemaError, match="objeto"):
        schema.validate(SCHEMA, [1])
    assert schema.check_strict(SCHEMA) == []
    loose = {"type": "object", "properties": {"a": {"type": "string", "format": "date"}, "b": {"type": "array"}}, "required": ["a"]}
    problems = schema.check_strict(loose)
    assert any("format" in p for p in problems) and any("additionalProperties" in p for p in problems)
    assert any("required" in p for p in problems) and any("items" in p for p in problems)
    assert schema.check_strict({"type": ["string", "integer"]}) and schema.check_strict({"type": "string", "enum": []})
    assert schema.schema_hash(SCHEMA) == schema.schema_hash(dict(reversed(list(SCHEMA.items())))) and len(schema.schema_hash(SCHEMA)) == 12
    assert "maxLength" not in schema.provider_schema(SCHEMA)["properties"]["title"] and "maxLength" in SCHEMA["properties"]["title"]
    assert schema.parse_json_text('```json\n{"a": 1}\n```') == {"a": 1} and schema.parse_json_text('texto {"a": [1]} fim') == {"a": [1]}
    with pytest.raises(ValueError):
        schema.parse_json_text("nada aqui")
    assert schema.minimal_instance(SCHEMA) == {"title": "", "score": 0.0, "tags": [], "owner": None}


def test_redaction_hides_whole_key_and_tail_in_text_and_logs():
    redact.GLOBAL.clear()
    key = "AIzaSyD-chave-muito-secreta-1234WXYZ"
    assert redact.redact(f"erro 401 com {key} na URL", [key]) == "erro 401 com «chave» na URL"
    assert redact.redact("...1234WXYZ expirou", [key]) == "...«chave» expirou"                       # só o sufixo ecoado
    assert redact.redact("curta", ["abc"]) == "curta" and redact.redact(None) == ""
    redact.GLOBAL.add(key)
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("listener.redacao")
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    redact.install_log_filter("listener.redacao")                                                 # depois do handler: entra nele
    logger.info("chave %s vazou", key)
    logging.getLogger("listener.redacao.llm").warning("erro com %s no cabeçalho", key)          # filhos passam pelo handler do pai
    assert len(records) == 2 and all("«chave»" in r and key not in r for r in records)
    redact.GLOBAL.clear()
    logger.removeHandler(capture)
    logger.setLevel(logging.NOTSET)


def test_retrying_provider_backs_off_and_resends_once_on_invalid_json():
    fake = FakeProvider()
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    rp = RetryingProvider(fake, sleep=sleep, rand=lambda: 0.5)
    fake.fail_next(RateLimitError("429", retry_after_s=4.0))
    fake.fail_next(TransientError("502"))
    fake.script(LlmTask.TITLE, {"title": "ok", "score": 0.5, "tags": ["a"], "owner": None})
    comp = run(rp.complete(_req()))
    assert comp.data == {"title": "ok", "score": 0.5, "tags": ["a"], "owner": None} and slept == [4.0, 3.0] and rp.retries == 2
    assert len(fake.requests) == 3 and comp.usage.input_tokens > 0 and comp.raw_id
    # 3 falhas retentáveis esgotam: o erro sobe; "request too large" e permanente não repetem
    fake.failures = [(None, TransientError("500"))] * 3
    with pytest.raises(TransientError):
        run(rp.complete(_req()))
    fake.fail_next(RequestTooLargeError("request too large"))
    with pytest.raises(RequestTooLargeError) as e:
        run(rp.complete(_req()))
    assert e.value.too_large and len(fake.requests) == 7
    fake.fail_next(ProviderUnavailableError("chave ausente"))
    with pytest.raises(ProviderUnavailableError):
        run(rp.complete(_req()))
    # JSON fora do esquema: um reenvio com a mensagem de erro; na segunda falha, BadResponseError
    answers = iter([{"title": "x" * 30, "score": 0.5, "tags": [], "owner": None}, {"title": "curto", "score": 0.5, "tags": [], "owner": 7}])
    fake.script(LlmTask.TITLE, lambda req: next(answers))
    comp = run(rp.complete(_req()))
    assert comp.data["owner"] == 7 and rp.resends == 1
    fix = fake.requests[-1]
    assert len(fix.messages) == 3 and fix.messages[1].role == Role.ASSISTANT and "$.title" in fix.messages[2].text
    fake.script(LlmTask.TITLE, "isto não é json")
    with pytest.raises(BadResponseError):
        run(rp.complete(_req()))
    # timeout por tentativa: um adaptador lento vira TransientError e a retentativa segue
    slow = FakeProvider(delay_s=0.05)
    slow.script(LlmTask.TITLE, {"title": "t", "score": 0.0, "tags": [], "owner": None})
    rp2 = RetryingProvider(slow, sleep=sleep, attempts=2)
    with pytest.raises(TransientError, match="tempo"):
        run(rp2.complete(_req(timeout_s=0.001)))
    assert run(rp2.complete(_req(timeout_s=5.0))).data["title"] == "t"
    # sem esquema: texto passa direto; `count_tokens` que falha vira None
    assert run(rp.complete(_req(LlmTask.CHAT, None))).text.startswith("Resposta simulada")
    assert run(rp.count_tokens(_req())) == 0
    assert rp.id == "fake" and rp.kind == ProviderKind.GEMINI and Capability.JSON in rp.capabilities
    run(rp.aclose())
    assert fake.closed


def test_fake_provider_streams_embeds_and_counts_parallelism():
    fake = FakeProvider(capabilities=frozenset({Capability.TEXT, Capability.JSON}))
    fake.script(LlmTask.CHAT, "uma resposta em pedaços para o chat")

    async def go():
        stream = fake.stream(_req(LlmTask.CHAT, None))
        parts = [p async for p in stream]
        return parts, stream.result

    parts, result = run(go())
    assert len(parts) == 3 and "".join(parts) == "uma resposta em pedaços para o chat" and result is not None
    assert result.text == "".join(parts) and result.usage.output_tokens >= 1
    assert run(fake.count_tokens(_req())) is None                                                     # sem COUNT_TOKENS
    emb = run(fake.embed(["a", "b", "a"], model="fake-emb", dim=16, task=EmbedTask.DOCUMENT))
    assert emb.vectors.shape == (3, 16) and np.allclose(emb.vectors[0], emb.vectors[2]) and not np.allclose(emb.vectors[0], emb.vectors[1])
    assert np.allclose(np.linalg.norm(emb.vectors, axis=1), 1.0) and fake.embed_calls == [(3, "fake-emb", 16, "document")]
    models = run(fake.list_models())
    assert [m.id for m in models] == ["fake-fast", "fake-strong"] and models[0].input_limit == 1_000_000
    assert run(fake.test_connection("fake-fast")).ok
    fake.fail_next(LlmError("sem rede"), LlmTask.TEST)
    t = run(fake.test_connection("fake-fast"))
    assert not t.ok and t.error == "sem rede"

    async def parallel(limit: int) -> int:
        slow = FakeProvider(delay_s=0.005)
        sem = asyncio.Semaphore(limit)

        async def one() -> None:
            async with sem:
                await slow.complete(_req(json_schema=None))

        async with asyncio.TaskGroup() as tg:
            for _ in range(5):
                tg.create_task(one())
        return slow.peak_parallel

    assert run(parallel(2)) == 2 and run(parallel(1)) == 1
    cached = FakeProvider()
    run(cached.complete(_req(text="prefixo estável " * 100, cache_prefix=True)))
    second = run(cached.complete(_req(text="prefixo estável " * 100, cache_prefix=True)))
    assert second.usage.cached_tokens > 0


def test_costs_models_table_and_registry_fake():
    price = costs.price_for(list(costs.PRICE_SEEDS), "gemini", "models/gemini-2.5-flash-001")
    assert price is not None and price.model == "gemini-2.5-flash"
    assert costs.price_for(list(costs.PRICE_SEEDS), "gemini", "gemini-2.5-flash-lite-preview").model == "gemini-2.5-flash-lite"
    assert costs.price_for(list(costs.PRICE_SEEDS), "anthropic", "claude-sonnet-4-5-20260101").model == "claude-sonnet-4-5"
    assert costs.price_for(list(costs.PRICE_SEEDS), "openai", "desconhecido") is None
    rows = [{"provider": "gemini", "model": "gemini-2.5-pro", "input_usd_m": 1.25, "output_usd_m": 10.0, "cached_usd_m": 0.31,
             "audio_usd_m": 1.25}]
    p = costs.price_for(rows, "gemini", "gemini-2.5-pro")
    assert costs.cost_usd(Usage(1_000_000, 100_000, 200_000, 0), p) == pytest.approx(0.8 * 1.25 + 0.2 * 0.31 + 0.1 * 10.0)
    # D-056: sem linha própria, a família de preço da tabela de modelos (`price_ref`); a linha própria ganha
    assert costs.price_for(list(costs.PRICE_SEEDS), "gemini", "gemini-3.8-flash").model == "gemini-2.5-flash"
    assert costs.price_for(list(costs.PRICE_SEEDS), "gemini", "models/gemini-3.1-pro-preview").model == "gemini-2.5-pro"
    own = [*rows, {"provider": "gemini", "model": "gemini-3.1-pro", "input_usd_m": 2.0, "output_usd_m": 12.0}]
    assert costs.price_for(own, "gemini", "gemini-3.1-pro-preview").input_usd_m == 2.0
    assert costs.price_for(rows, "gemini", "gemini-3.8-flash") is None                    # a referência também precisa de linha
    # R3-10: escrita no cache (Anthropic) cobra 1,25 vez a entrada; a leitura, o preço de cache
    write = Usage(1_000_000, 0, 200_000, 0, 300_000)
    assert costs.cost_usd(write, p) == pytest.approx(0.5 * 1.25 + 0.2 * 0.31 + 0.3 * 1.25 * 1.25)
    assert (write + Usage(1, 0, 0, 0, 5)).cache_write_tokens == 300_005
    from types import SimpleNamespace

    from listener.llm.anthropic import AnthropicProvider

    u = AnthropicProvider._usage(SimpleNamespace(input_tokens=10, cache_read_input_tokens=200, cache_creation_input_tokens=300,
                                                 output_tokens=7))
    assert (u.input_tokens, u.cached_tokens, u.cache_write_tokens, u.output_tokens) == (510, 200, 300, 7)
    assert costs.cost_usd(Usage(1000, 10), None) is None and costs.estimate_tokens("a" * 350) == 100 and costs.estimate_tokens(35) == 10
    assert costs.fmt_usd(0.3249) == "US$ 0,32" and costs.fmt_usd(None) == "sem preço cadastrado"
    assert costs.estimate_cost(1_000_000, 0, p, audio_tokens=1_000_000) == pytest.approx(1.25)
    info = models_table.model_info("gemini", "models/gemini-2.5-flash")
    assert info.id == "gemini-2.5-flash" and info.display_name == "Gemini 2.5 Flash" and Capability.AUDIO_IN in info.capabilities
    assert models_table.model_info("anthropic", "claude-haiku-4-5-20251001").input_limit == 200_000
    assert Capability.AUDIO_IN not in models_table.model_info("anthropic", "claude-sonnet-4-5").capabilities
    assert Capability.AUDIO_IN in models_table.model_info("openai", "gpt-4o-audio-preview").capabilities
    assert Capability.AUDIO_IN not in models_table.model_info("openai", "gpt-4.1-mini").capabilities
    unknown = models_table.model_info("openai", "modelo-novo")
    assert unknown.input_limit == 32_000 and Capability.JSON in unknown.capabilities
    assert models_table.supports_reasoning("openai", "gpt-5-mini") and not models_table.supports_reasoning("openai", "gpt-4.1")
    assert models_table.is_embedding_model("gemini", "gemini-embedding-001")
    assert not models_table.is_embedding_model("gemini", "gemini-2.5-pro")
    claude = models_table.model_info("anthropic", "claude-sonnet-4-5")
    assert models_table.effective_capabilities("anthropic", claude) == info.capabilities - {Capability.AUDIO_IN}
    assert models_table.DEFAULT_MODELS[ProviderKind.GEMINI] == ("gemini-3.8-flash", "gemini-3.1-pro-preview")   # D-055
    for mid in ("gemini-3.8-flash", "gemini-3.1-pro-preview"):                 # a geração 3 pensa: o adaptador manda o orçamento
        assert models_table.supports_reasoning("gemini", mid) and models_table.model_info("gemini", mid).output_limit == 65_536
    assert models_table.family_for("gemini", "gemini-3.1-pro-preview").price_ref == "gemini-2.5-pro"


def test_registry_loads_fake_or_refuses_without_key(monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    p = load_provider("openai", None, provider_id="openai")
    assert isinstance(p, FakeProvider) and p.kind == ProviderKind.OPENAI and p.id == "openai"
    monkeypatch.delenv("LISTENER_LLM")
    with pytest.raises(ProviderUnavailableError, match="chave"):
        load_provider("gemini", "", NetworkConfig(system_certs=False))
    net = NetworkConfig(proxy_url="http://proxy:3128", ca_bundle="/x.pem", system_certs=False)
    assert net.verify() == "/x.pem" and NetworkConfig().verify() is True
    from listener.llm import registry

    assert registry.prepare_ssl(NetworkConfig(system_certs=False)) is False


def test_redaction_reaches_traceback_chain_and_proxy_password(tmp_path, store):
    """R3-04: a chave no `__cause__` de um `raise ... from e` some do arquivo de log (o traceback inteiro passa pelo redator);
    a senha de `llm.proxy_url` entra na redação ao ler a configuração de rede."""
    from listener import log as applog
    from listener.llm.base import TransientError

    key = "AIzaSyD-chave-do-traceback-9876QRST"
    redact.GLOBAL.clear()
    redact.GLOBAL.add(key)
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "logs" / "listener.log")

    def sdk_call() -> None:
        raise ValueError(f"403 for url https://api.exemplo/v1?key={key}")

    try:
        try:
            sdk_call()
        except ValueError as e:
            raise TransientError("rede: falhou") from e
    except TransientError as err:
        applog.get_logger("pipeline.llm").error("tarefa falhou", exc_info=err)
    applog.get_logger("llm.registry").warning("sem cadeia, com a chave %s na mensagem", key, stack_info=True)
    applog.flush()
    text = path.read_text(encoding="utf-8")
    assert "Traceback" in text and "The above exception was the direct cause" in text and "sdk_call" in text
    assert key not in text and key[-8:] not in text and text.count("«chave»") >= 2
    redact.GLOBAL.clear()
    # senha do proxy: a pura, a decodificada e o par usuário:senha
    store.set_setting("llm.proxy_url", "http://ana.lima:s3nh4%40proxy-corp@proxy.local:3128")
    net = NetworkConfig.from_store(store)
    assert net.proxy_url.startswith("http://ana.lima:") and net.timeout_s == 600.0                       # teto do cliente (R3-03)
    leaked = redact.GLOBAL.redact("407 em http://ana.lima:s3nh4%40proxy-corp@proxy.local:3128 (senha s3nh4@proxy-corp)")
    assert "s3nh4" not in leaked and "proxy-corp" not in leaked and "proxy.local:3128" in leaked
    redact.GLOBAL.clear()
    from listener.llm import registry

    registry.register_proxy_secret("proxy.local:3128")                                                    # sem senha: nada
    registry.register_proxy_secret("usuario:outra-senha-123@proxy.local:3128")                           # sem esquema
    assert redact.GLOBAL.redact("outra-senha-123") == "«chave»"
    redact.GLOBAL.clear()
