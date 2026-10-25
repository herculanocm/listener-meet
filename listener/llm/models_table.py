"""Capacidades, janelas e preço por família de modelo (dado do projeto, F3A-10/F3A-31): `list_models()` dos provedores
complementa (o Gemini informa limites e ações; OpenAI e Anthropic não). Capacidade efetiva = provedor ∩ modelo. Famílias
casam pelo prefixo mais longo do id; um modelo desconhecido ganha a família padrão do provedor (texto e JSON, janela
conservadora). Valores [exp] para as gerações de outubro de 2026; o canário do "testar conexão" confirma."""
from __future__ import annotations

from dataclasses import dataclass

from .types import Capability, ModelInfo, ProviderKind

TEXT_JSON = frozenset({Capability.TEXT, Capability.JSON, Capability.STREAMING})
ALL_GEMINI = TEXT_JSON | {Capability.AUDIO_IN, Capability.COUNT_TOKENS, Capability.CACHE}
CLAUDE = TEXT_JSON | {Capability.COUNT_TOKENS, Capability.CACHE}
EMBED_ONLY = frozenset({Capability.EMBEDDINGS})
G = ProviderKind.GEMINI
O = ProviderKind.OPENAI
A = ProviderKind.ANTHROPIC


@dataclass(frozen=True, slots=True)
class Family:
    kind: ProviderKind
    prefix: str
    display: str
    input_limit: int
    output_limit: int
    capabilities: frozenset[Capability]
    price_ref: str = ""
    reasoning: bool = False           # aceita orçamento ou esforço de raciocínio
    embedding: bool = False


FAMILIES: tuple[Family, ...] = (
    Family(G, "gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-flash-lite", True),
    Family(G, "gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-flash", True),
    Family(G, "gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-pro", True),
    Family(G, "gemini-2.0-flash", "Gemini 2.0 Flash", 1_048_576, 8_192, ALL_GEMINI, "gemini-2.5-flash"),
    Family(G, "gemini-embedding", "Gemini Embedding", 2_048, 0, EMBED_ONLY, "gemini-embedding-001", embedding=True),
    Family(G, "gemini", "Gemini", 1_048_576, 8_192, ALL_GEMINI, "gemini-2.5-flash"),
    Family(O, "gpt-4.1-mini", "GPT-4.1 mini", 1_047_576, 32_768, TEXT_JSON | {Capability.CACHE}, "gpt-4.1-mini"),
    Family(O, "gpt-4.1", "GPT-4.1", 1_047_576, 32_768, TEXT_JSON | {Capability.CACHE}, "gpt-4.1"),
    Family(O, "gpt-5-mini", "GPT-5 mini", 400_000, 128_000, TEXT_JSON | {Capability.CACHE}, "gpt-5-mini", True),
    Family(O, "gpt-5", "GPT-5", 400_000, 128_000, TEXT_JSON | {Capability.CACHE}, "gpt-5", True),
    Family(O, "gpt-4o-audio", "GPT-4o audio", 128_000, 16_384, TEXT_JSON | {Capability.AUDIO_IN}, "gpt-4o-audio-preview"),
    Family(O, "gpt-4o-mini", "GPT-4o mini", 128_000, 16_384, TEXT_JSON | {Capability.CACHE}, "gpt-4.1-mini"),
    Family(O, "gpt-4o", "GPT-4o", 128_000, 16_384, TEXT_JSON | {Capability.CACHE}, "gpt-4.1"),
    Family(O, "o4-mini", "o4-mini", 200_000, 100_000, TEXT_JSON | {Capability.CACHE}, "gpt-5-mini", True),
    Family(O, "o3", "o3", 200_000, 100_000, TEXT_JSON | {Capability.CACHE}, "gpt-5", True),
    Family(O, "text-embedding-3-small", "text-embedding-3-small", 8_191, 0, EMBED_ONLY, "text-embedding-3-small", embedding=True),
    Family(O, "text-embedding-3-large", "text-embedding-3-large", 8_191, 0, EMBED_ONLY, "text-embedding-3-large", embedding=True),
    Family(O, "gpt", "GPT", 128_000, 16_384, TEXT_JSON, "gpt-4.1-mini"),
    Family(A, "claude-haiku-4", "Claude Haiku 4.x", 200_000, 64_000, CLAUDE, "claude-haiku-4-5", True),
    Family(A, "claude-sonnet-4", "Claude Sonnet 4.x", 200_000, 64_000, CLAUDE, "claude-sonnet-4-5", True),
    Family(A, "claude-opus-4", "Claude Opus 4.x", 200_000, 32_000, CLAUDE, "claude-opus-4-1", True),
    Family(A, "claude", "Claude", 200_000, 8_192, CLAUDE, "claude-sonnet-4-5"),
)

# modelo rápido e forte sugeridos por provedor (a sub-aba deixa trocar; `list_models()` mostra os reais)
DEFAULT_MODELS: dict[ProviderKind, tuple[str, str]] = {
    ProviderKind.GEMINI: ("gemini-2.5-flash", "gemini-2.5-pro"),
    ProviderKind.OPENAI: ("gpt-4.1-mini", "gpt-5"),
    ProviderKind.ANTHROPIC: ("claude-haiku-4-5", "claude-sonnet-4-5"),
}
DEFAULT_EMBED_MODELS: dict[ProviderKind, str] = {ProviderKind.GEMINI: "gemini-embedding-001", ProviderKind.OPENAI: "text-embedding-3-small"}
PROVIDER_CAPABILITIES: dict[ProviderKind, frozenset[Capability]] = {
    ProviderKind.GEMINI: ALL_GEMINI | {Capability.EMBEDDINGS},
    ProviderKind.OPENAI: TEXT_JSON | {Capability.AUDIO_IN, Capability.EMBEDDINGS, Capability.CACHE, Capability.COUNT_TOKENS},
    ProviderKind.ANTHROPIC: CLAUDE,                                                     # sem embeddings nem áudio [fato]
}


def clean_id(model_id: str) -> str:
    return model_id.removeprefix("models/").strip()


def family_for(kind: ProviderKind | str, model_id: str) -> Family | None:
    mid = clean_id(model_id)
    best: Family | None = None
    for f in FAMILIES:
        if str(f.kind) == str(kind) and mid.startswith(f.prefix) and (best is None or len(f.prefix) > len(best.prefix)):
            best = f
    return best


def model_info(kind: ProviderKind | str, model_id: str, *, display_name: str | None = None, input_limit: int | None = None,
               output_limit: int | None = None, capabilities: frozenset[Capability] | None = None) -> ModelInfo:
    """`ModelInfo` da tabela, complementado pelo que `list_models()` informou (limites e capacidades do provedor)."""
    mid = clean_id(model_id)
    f = family_for(kind, mid)
    caps = capabilities if capabilities is not None else (f.capabilities if f else TEXT_JSON)
    return ModelInfo(mid, display_name or (f.display if f and f.prefix == mid else mid), input_limit or (f.input_limit if f else 32_000),
                     output_limit or (f.output_limit if f else 4_096), frozenset(caps), f.price_ref if f else "")


def supports_reasoning(kind: ProviderKind | str, model_id: str) -> bool:
    f = family_for(kind, model_id)
    return bool(f and f.reasoning)


def is_embedding_model(kind: ProviderKind | str, model_id: str) -> bool:
    f = family_for(kind, model_id)
    return bool(f and f.embedding)


def effective_capabilities(kind: ProviderKind | str, info: ModelInfo) -> frozenset[Capability]:
    return frozenset(info.capabilities & PROVIDER_CAPABILITIES[ProviderKind(str(kind))])
