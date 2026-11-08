"""Capacidades, janelas e preço por família de modelo (dado do projeto, F3A-10/F3A-31): `list_models()` dos provedores
complementa (o Gemini informa limites e ações; OpenAI e Anthropic não). Capacidade efetiva = provedor ∩ modelo. Famílias
casam pelo prefixo mais longo do id; um modelo desconhecido ganha a família padrão do provedor (texto e JSON, janela
conservadora). Valores [exp] para as gerações de outubro de 2026; o canário do "testar conexão" confirma.
F5.14: as famílias do Claude de hoje [fato: skill `claude-api` de 06/10 e o SDK 2.3.0] dizem também o estilo da saída
estruturada (`output_config.format` ou a ferramenta forçada), o do pensamento (adaptativo com esforço ou o orçamento
`budget_tokens`), se aceitam o fallback do servidor e o preço multiplicado do pedido longo (o Haiku 5.5 acima de 100 mil
tokens); `claude-*` desconhecido usa o estilo mais novo (esquema e adaptativo)."""
from __future__ import annotations

from dataclasses import dataclass

from .types import Capability, ModelInfo, ProviderKind

TEXT_JSON = frozenset({Capability.TEXT, Capability.JSON, Capability.STREAMING})
ALL_GEMINI = TEXT_JSON | {Capability.AUDIO_IN, Capability.IMAGE_IN, Capability.COUNT_TOKENS, Capability.CACHE}
CLAUDE = TEXT_JSON | {Capability.IMAGE_IN, Capability.COUNT_TOKENS, Capability.CACHE}
GPT_VISION = TEXT_JSON | {Capability.IMAGE_IN, Capability.CACHE}          # F5.21 (A-12): as GPT que recebem imagem
EMBED_ONLY = frozenset({Capability.EMBEDDINGS})
G = ProviderKind.GEMINI
O = ProviderKind.OPENAI
A = ProviderKind.ANTHROPIC
L = ProviderKind.LOCAL
JSON_FORMAT, JSON_TOOL = "format", "tool"            # F5.14: `output_config.format` ou a ferramenta forçada (sem pensamento)
THINK_ADAPTIVE, THINK_BUDGET = "adaptive", "budget"  # `thinking: adaptive` com `output_config.effort`, ou `budget_tokens`
HAIKU_LONG = (100_000, 5.0)                          # o Haiku 5.5: acima de 100 mil tokens de entrada, 5 vezes o preço [fato]


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
    json_style: str = ""              # F5.14, só o Claude: `JSON_FORMAT` ou `JSON_TOOL`
    thinking: str = ""                # F5.14, só o Claude: `THINK_ADAPTIVE` ou `THINK_BUDGET`
    fallback: bool = False            # F5.14: aceita `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`)
    long_prompt: tuple[int, float] | None = None      # F5.14: (limiar de tokens de entrada, fator sobre o custo inteiro)
    xhigh: bool = True                # F5.14 (R-03): aceita o esforço `xhigh` (chegou com o Opus 4.7; os 4.6 vão até `max` sem ele)


def _claude(prefix: str, display: str, window: int, output: int, price_ref: str, json_style: str, thinking: str, *,
            fallback: bool = False, long_prompt: tuple[int, float] | None = None, xhigh: bool = True) -> Family:
    return Family(A, prefix, display, window, output, CLAUDE, price_ref, True, json_style=json_style, thinking=thinking,
                  fallback=fallback, long_prompt=long_prompt, xhigh=xhigh)


F, T, AD, BU = JSON_FORMAT, JSON_TOOL, THINK_ADAPTIVE, THINK_BUDGET
M1, K128, K200, K64, K32 = 1_000_000, 128_000, 200_000, 64_000, 32_000
CLAUDE_DEFAULT = _claude("claude", "Claude", K200, K64, "claude-sonnet-5-5", F, AD)      # id fora da tabela: o estilo mais novo
CLAUDE_FAMILIES: tuple[Family, ...] = (
    _claude("claude-fable-5-1", "Claude Fable 5.1", M1, K128, "claude-fable-5-1", F, AD, fallback=True),
    _claude("claude-fable-5", "Claude Fable 5", M1, K128, "claude-fable-5", F, AD),
    _claude("claude-opus-5-5", "Claude Opus 5.5", M1, K128, "claude-opus-5-5", F, AD, fallback=True),
    _claude("claude-opus-5", "Claude Opus 5", M1, K128, "claude-opus-5", F, AD, fallback=True),
    _claude("claude-opus-4-8", "Claude Opus 4.8", M1, K128, "claude-opus-5", F, AD),
    _claude("claude-opus-4-7", "Claude Opus 4.7", M1, K128, "claude-opus-5", T, AD),
    _claude("claude-opus-4-6", "Claude Opus 4.6", M1, K128, "claude-opus-5", T, AD, xhigh=False),
    _claude("claude-opus-4-5", "Claude Opus 4.5", K200, K64, "claude-opus-5", F, BU),
    _claude("claude-opus-4-1", "Claude Opus 4.1", K200, K32, "claude-opus-4-1", F, BU),
    _claude("claude-opus-4", "Claude Opus 4", K200, K32, "claude-opus-4-1", T, BU),
    _claude("claude-sonnet-5-5", "Claude Sonnet 5.5", M1, K128, "claude-sonnet-5-5", F, AD, fallback=True),
    _claude("claude-sonnet-5", "Claude Sonnet 5", M1, K128, "claude-sonnet-5-5", F, AD),
    _claude("claude-sonnet-4-6", "Claude Sonnet 4.6", M1, K128, "claude-sonnet-4-6", T, AD, xhigh=False),
    _claude("claude-sonnet-4-5", "Claude Sonnet 4.5", K200, K64, "claude-sonnet-4-5", T, BU),
    _claude("claude-sonnet-4", "Claude Sonnet 4", K200, K64, "claude-sonnet-4-5", T, BU),
    _claude("claude-haiku-5-5", "Claude Haiku 5.5", M1, K128, "claude-haiku-5-5", F, AD, long_prompt=HAIKU_LONG),
    _claude("claude-haiku-4-5", "Claude Haiku 4.5", K200, K64, "claude-haiku-4-5", F, BU),
    _claude("claude-haiku-4", "Claude Haiku 4.x", K200, K64, "claude-haiku-4-5", F, BU),
    CLAUDE_DEFAULT,
)


FAMILIES: tuple[Family, ...] = (
    Family(G, "gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-flash-lite", True),
    Family(G, "gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-flash", True),
    Family(G, "gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-pro", True),
    Family(G, "gemini-2.0-flash", "Gemini 2.0 Flash", 1_048_576, 8_192, ALL_GEMINI, "gemini-2.5-flash"),
    # D-055: a geração 3 pensa por padrão e o pensamento conta no teto de saída (sem `thinking_config`, o teste de 64 tokens
    # voltava vazio); os preços da geração 3 ainda não estão nas sementes [chute: os do 2.5; confira contra a fatura]
    Family(G, "gemini-3.1-pro", "Gemini 3.1 Pro", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-pro", True),
    Family(G, "gemini-3", "Gemini 3.x", 1_048_576, 65_536, ALL_GEMINI, "gemini-2.5-flash", True),
    Family(G, "gemini-embedding", "Gemini Embedding", 2_048, 0, EMBED_ONLY, "gemini-embedding-001", embedding=True),
    Family(G, "gemini", "Gemini", 1_048_576, 8_192, ALL_GEMINI, "gemini-2.5-flash"),
    Family(O, "gpt-4.1-mini", "GPT-4.1 mini", 1_047_576, 32_768, GPT_VISION, "gpt-4.1-mini"),
    Family(O, "gpt-4.1", "GPT-4.1", 1_047_576, 32_768, GPT_VISION, "gpt-4.1"),
    Family(O, "gpt-5-mini", "GPT-5 mini", 400_000, 128_000, GPT_VISION, "gpt-5-mini", True),
    Family(O, "gpt-5", "GPT-5", 400_000, 128_000, GPT_VISION, "gpt-5", True),
    Family(O, "gpt-4o-audio", "GPT-4o audio", 128_000, 16_384, TEXT_JSON | {Capability.AUDIO_IN}, "gpt-4o-audio-preview"),
    Family(O, "gpt-4o-mini", "GPT-4o mini", 128_000, 16_384, GPT_VISION, "gpt-4.1-mini"),
    Family(O, "gpt-4o", "GPT-4o", 128_000, 16_384, GPT_VISION, "gpt-4.1"),
    Family(O, "o4-mini", "o4-mini", 200_000, 100_000, GPT_VISION, "gpt-5-mini", True),
    Family(O, "o3", "o3", 200_000, 100_000, GPT_VISION, "gpt-5", True),
    Family(O, "text-embedding-3-small", "text-embedding-3-small", 8_191, 0, EMBED_ONLY, "text-embedding-3-small", embedding=True),
    Family(O, "text-embedding-3-large", "text-embedding-3-large", 8_191, 0, EMBED_ONLY, "text-embedding-3-large", embedding=True),
    Family(O, "gpt", "GPT", 128_000, 16_384, TEXT_JSON, "gpt-4.1-mini"),
    *CLAUDE_FAMILIES,
    Family(L, "multilingual-e5", "e5-small (local)", 512, 0, EMBED_ONLY, embedding=True),    # F5.1: corte em 512 tokens, sem custo
)

# modelo rápido e forte sugeridos por provedor (a sub-aba deixa trocar; `list_models()` mostra os reais)
DEFAULT_MODELS: dict[ProviderKind, tuple[str, str]] = {
    # D-055: o 2.5 deixou de servir contas novas em outubro de 2026 (404 "no longer available to new users"); os dois são
    # os que o próprio erro sugere, conferidos com uma chave nova pelo "testar conexão"
    ProviderKind.GEMINI: ("gemini-3.8-flash", "gemini-3.1-pro-preview"),
    ProviderKind.OPENAI: ("gpt-4.1-mini", "gpt-5"),
    ProviderKind.ANTHROPIC: ("claude-haiku-5-5", "claude-opus-5-5"),              # F5.14 (D-084): os de 2025 saem
}
DEFAULT_EMBED_MODELS: dict[ProviderKind, str] = {
    ProviderKind.GEMINI: "gemini-embedding-001", ProviderKind.OPENAI: "text-embedding-3-small",
    ProviderKind.LOCAL: "multilingual-e5-small",                                         # F5.1: o local tem um modelo só
}
PROVIDER_CAPABILITIES: dict[ProviderKind, frozenset[Capability]] = {
    ProviderKind.GEMINI: ALL_GEMINI | {Capability.EMBEDDINGS},
    ProviderKind.OPENAI: TEXT_JSON | {Capability.AUDIO_IN, Capability.IMAGE_IN, Capability.EMBEDDINGS, Capability.CACHE,
                                      Capability.COUNT_TOKENS},
    ProviderKind.ANTHROPIC: CLAUDE,                                                     # sem embeddings nem áudio [fato]; com imagem
    ProviderKind.LOCAL: EMBED_ONLY,                                                     # F5.1 (A-02): só os vetores da busca
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


def claude_family(model_id: str) -> Family:
    """F5.14: a família do Claude do modelo; um id de fora da tabela ganha a do `claude` desconhecido (esquema e adaptativo)."""
    return family_for(A, model_id) or CLAUDE_DEFAULT


def supports_reasoning(kind: ProviderKind | str, model_id: str) -> bool:
    f = family_for(kind, model_id)
    return bool(f and f.reasoning)


def is_embedding_model(kind: ProviderKind | str, model_id: str) -> bool:
    f = family_for(kind, model_id)
    return bool(f and f.embedding)


def effective_capabilities(kind: ProviderKind | str, info: ModelInfo) -> frozenset[Capability]:
    return frozenset(info.capabilities & PROVIDER_CAPABILITIES[ProviderKind(str(kind))])
