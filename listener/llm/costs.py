"""Custo das chamadas (U-009, U-020; contrato "Custo"): tabela `llm_prices` com sementes do projeto por família de modelo
(editáveis na sub-aba), fórmula `input * p_in + cached * p_cached + cache_write * p_in * 1,25 + output * p_out +
audio * p_audio` (preços por milhão de tokens), estimativa prévia por caracteres ÷ 3,5 quando o provedor não conta
tokens. Sem linha de preço o custo é nulo e a tela diz "sem preço cadastrado". Valores das sementes são [chute] de
outubro de 2026: o dono confere contra a fatura."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import Usage

CHARS_PER_TOKEN = 3.5
AUDIO_TOKENS_PER_SECOND = 32          # Gemini [fato]; os outros provedores informam `audio_tokens` na resposta
CACHE_WRITE_FACTOR = 1.25             # escrita no cache da Anthropic (TTL de 5 min) custa 1,25 vez a entrada [fato]


@dataclass(frozen=True, slots=True)
class Price:
    provider: str
    model: str                        # id exato ou prefixo de família (casa pelo prefixo mais longo)
    input_usd_m: float
    output_usd_m: float
    cached_usd_m: float = 0.0
    audio_usd_m: float = 0.0


PRICE_SEEDS: tuple[Price, ...] = (
    Price("gemini", "gemini-2.5-flash-lite", 0.10, 0.40, 0.025, 0.30),
    Price("gemini", "gemini-2.5-flash", 0.30, 2.50, 0.075, 1.00),
    Price("gemini", "gemini-2.5-pro", 1.25, 10.00, 0.31, 1.25),
    Price("gemini", "gemini-embedding-001", 0.15, 0.0),
    Price("openai", "gpt-4.1-mini", 0.40, 1.60, 0.10),
    Price("openai", "gpt-4.1", 2.00, 8.00, 0.50),
    Price("openai", "gpt-5-mini", 0.25, 2.00, 0.025),
    Price("openai", "gpt-5", 1.25, 10.00, 0.125),
    Price("openai", "gpt-4o-audio-preview", 2.50, 10.00, 1.25, 40.00),
    Price("openai", "text-embedding-3-small", 0.02, 0.0),
    Price("openai", "text-embedding-3-large", 0.13, 0.0),
    Price("anthropic", "claude-haiku-4-5", 1.00, 5.00, 0.10),
    Price("anthropic", "claude-sonnet-4-5", 3.00, 15.00, 0.30),
    Price("anthropic", "claude-opus-4-1", 15.00, 75.00, 1.50),
)


def estimate_tokens(text: str | int) -> int:
    chars = text if isinstance(text, int) else len(text)
    return int(chars / CHARS_PER_TOKEN + 0.5)


def price_for(prices: list[Any], provider: str, model: str) -> Price | None:
    """Linha de preço do modelo: id exato, senão o prefixo mais longo da mesma família (`gemini-2.5-flash-001` casa
    `gemini-2.5-flash`). `prices` aceita `Price` ou linhas do banco (dict)."""
    rows = [p if isinstance(p, Price) else Price(str(p["provider"]), str(p["model"]), float(p["input_usd_m"] or 0),
                                                 float(p["output_usd_m"] or 0), float(p.get("cached_usd_m") or 0),
                                                 float(p.get("audio_usd_m") or 0)) for p in prices]
    model_id = model.removeprefix("models/")
    best: Price | None = None
    for p in rows:
        if p.provider != provider:
            continue
        if p.model == model_id:
            return p
        if model_id.startswith(p.model) and (best is None or len(p.model) > len(best.model)):
            best = p
    return best


def cost_usd(usage: Usage, price: Price | None) -> float | None:
    """Entrada simples, lida do cache, gravada no cache (`CACHE_WRITE_FACTOR` vezes a entrada, R3-10), áudio e saída."""
    if price is None:
        return None
    plain_input = max(0, usage.input_tokens - usage.cached_tokens - usage.audio_tokens - usage.cache_write_tokens)
    total = (plain_input * price.input_usd_m + usage.cached_tokens * price.cached_usd_m + usage.output_tokens * price.output_usd_m
             + usage.cache_write_tokens * price.input_usd_m * CACHE_WRITE_FACTOR
             + usage.audio_tokens * (price.audio_usd_m or price.input_usd_m)) / 1_000_000
    return round(total, 6)


def estimate_cost(input_tokens: int, output_tokens: int, price: Price | None, audio_tokens: int = 0) -> float | None:
    """`input_tokens` é o total de entrada (texto mais áudio), como os provedores contam; `audio_tokens` é a parte de áudio."""
    return cost_usd(Usage(input_tokens, output_tokens, 0, audio_tokens), price)


def fmt_usd(value: float | None) -> str:
    """"US$ 0,32"; None = "sem preço cadastrado"."""
    if value is None:
        return "sem preço cadastrado"
    return f"US$ {value:.2f}".replace(".", ",")
