"""As chamadas das sub-abas de provedor que rodam numa `Task` (CP8: "testar conexão" e "listar modelos") e o cache da lista
em `providers.models_json`; saíram de `ui/providers.py` na F5.14, que passava de 400 linhas, e seguem importadas de lá (o
teste troca `providers.test_connection`). Cada chamada cria e fecha o cliente dentro do `run_async()` (contrato
"Decoradores e registro"); nada aqui importa SDK."""
from __future__ import annotations

from typing import Any

from ..llm.aio import run_async
from ..llm.registry import NetworkConfig, load_provider
from ..llm.types import ConnectionTest, ModelInfo


def test_connection(kind: str, api_key: str, model: str, net: NetworkConfig) -> ConnectionTest:
    """Roda numa `Task`: cliente criado e fechado dentro do `run_async()` (contrato "Decoradores e registro")."""
    async def go() -> ConnectionTest:
        provider = load_provider(kind, api_key, net)
        try:
            return await provider.test_connection(model)
        finally:
            await provider.aclose()
    return run_async(go())


def list_models(kind: str, api_key: str, net: NetworkConfig) -> tuple[ModelInfo, ...]:
    async def go() -> tuple[ModelInfo, ...]:
        provider = load_provider(kind, api_key, net)
        try:
            return await provider.list_models()
        finally:
            await provider.aclose()
    return run_async(go())


def models_cache(infos: tuple[ModelInfo, ...]) -> list[dict[str, Any]]:
    return [{"id": m.id, "display_name": m.display_name, "input_limit": m.input_limit, "output_limit": m.output_limit,
             "capabilities": sorted(str(c) for c in m.capabilities)} for m in infos]
