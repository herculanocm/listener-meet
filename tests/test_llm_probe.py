"""F5.14 L3: a sonda do Claude (`scripts/llm_probe.py`) no modo simulado, sem rede nem chave: os 10 esquemas do projeto
pelo SDK real sobre o `MockTransport`, ao modelo e com o esforço de cada tarefa, uma linha JSON por tarefa e o total; um
esquema que não valida faz a sonda sair com 1; a chave do `--real` vem do ambiente ou do `getpass`, sem eco."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llm_probe", ROOT / "scripts" / "llm_probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


@pytest.mark.lento                                                   # importa o SDK, o job da LLM e o agente (~2 s)
def test_the_simulated_probe_sends_the_ten_schemas_through_the_real_sdk(capsys: pytest.CaptureFixture[str]):
    pytest.importorskip("anthropic")
    pytest.importorskip("httpx2")
    probe = _load()
    assert probe.main(["anthropic"]) == 0
    lines = _lines(capsys)
    tasks = {line["tarefa"]: line for line in lines if line["stage"] == "tarefa"}
    assert list(tasks) == list(probe.TASKS) and all(t["ok"] and t["custo_usd"] > 0 and t["entrada"] > 0 for t in tasks.values())
    assert all(t["pensamento"] == probe.SIM_THINKING and t["reenvios"] == {"rede": 0, "esquema": 0, "teto": 0} for t in tasks.values())
    minutes, title = tasks["minutes"], tasks["title"]
    assert (minutes["modelo"], minutes["esforco"], minutes["teto_enviado"]) == ("claude-opus-5-5", "medium", 8192 + 8192)
    assert (title["modelo"], title["esforco"], title["teto_enviado"]) == ("claude-haiku-5-5", "low", 128 + 2048)
    assert tasks["test"]["teto"] == 512 and tasks["rag_sql"]["teto"] == 4096 and tasks["glossary"]["teto"] == 1024
    total = lines[-1]
    assert total["stage"] == "total" and total["ok"] == 10 and total["falhas"] == [] and probe.SIM_KEY not in json.dumps(lines)


@pytest.mark.lento
def test_a_schema_that_does_not_validate_fails_the_probe(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("httpx2")
    probe = _load()
    original = probe.simulated_payload

    def long_title(schema: Any) -> Any:                             # o título acima dos 80 caracteres, duas vezes
        return {"title": "x" * 200} if set(schema.get("properties", {})) == {"title"} else original(schema)

    monkeypatch.setattr(probe, "simulated_payload", long_title)
    assert probe.main(["anthropic"]) == 1
    lines = _lines(capsys)
    title = next(line for line in lines if line.get("tarefa") == "title")
    assert not title["ok"] and "fora do esquema após reenvio" in title["erro"] and "xxxxx" not in title["erro"]
    assert title["reenvios"] == {"rede": 0, "esquema": 1, "teto": 0}                    # o reenvio único com o erro do esquema
    assert lines[-1]["falhas"] == ["title"] and lines[-1]["ok"] == 9


def test_the_real_key_comes_from_the_environment_or_a_prompt_without_echo():
    probe = _load()
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return " sk-ant-digitada "

    assert probe.api_key({"ANTHROPIC_API_KEY": " sk-ant-do-ambiente "}, ask) == "sk-ant-do-ambiente" and asked == []
    assert probe.api_key({}, ask) == "sk-ant-digitada" and asked == ["Chave da Anthropic (não aparece na tela): "]
