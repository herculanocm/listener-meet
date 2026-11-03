"""Montagem do pedido do adaptador Anthropic (F5.14 L1; contrato `specs/llm-contrato.md`): lógica pura, sem SDK nem rede,
separada de `anthropic.py`. Por família (`models_table.claude_family`): a saída estruturada vai por `output_config.format`
(o esquema sem as chaves que a API recusa, `schema.PROVIDER_SOFT_KEYS`; o projeto valida com o inteiro) ou, nas famílias
que não a aceitam, pela ferramenta forçada de antes, sem pensamento (os dois não combinam); o pensamento é adaptativo com
o esforço explícito (o `Reasoning` do pedido: NONE `low`, LOW `medium`, HIGH `high`; os Parâmetros `effort_fast`, dos
pedidos NONE, e `effort_strong`, dos LOW e HIGH, trocam) ou, nas famílias antigas, o orçamento (`budget_tokens`) só em LOW
e HIGH; `disabled` nunca (o Opus 5.5 recusa). O `max_tokens` enviado é o da tarefa mais a folga do pensamento, que conta
nele (o título de 128 tokens acabava antes da resposta, como na D-055), até o teto de saída da família. O fallback do
servidor (`fallbacks: "default"`) só nas famílias que o aceitam, desligável com `{"fallbacks": false}`.
Revisão do diff: a mensagem em branco fica fora do pedido e o primeiro turno é sempre do usuário (R-01); o `xhigh` vira
`high` nas famílias que não o aceitam, com o aviso uma vez por família em cada provedor criado (R-03)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger
from . import models_table
from .adapters_common import has_audio, split_system
from .base import ProviderUnavailableError
from .models_table import Family
from .schema import provider_schema
from .types import Reasoning, Request, Role

log = get_logger("llm.anthropic")

TOOL_NAME = "emit_result"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_BY_REASONING = {Reasoning.NONE: "low", Reasoning.LOW: "medium", Reasoning.HIGH: "high"}
# folga do pensamento no `max_tokens`, pelo esforço efetivo [chute: a sonda real mede o pensamento]; no orçamento, ele mesmo
EFFORT_HEADROOM = {"low": 2048, "medium": 8192, "high": 16384, "xhigh": 32768, "max": 32768}
THINKING_BUDGET = {Reasoning.LOW: 2048, Reasoning.HIGH: 8192}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODE = "default"                   # o servidor escolhe o modelo de reserva pela categoria da recusa
FAST_KEY, STRONG_KEY, FALLBACK_KEY, TIER_KEY = "effort_fast", "effort_strong", "fallbacks", "service_tier"


def _effort(value: Any, key: str, provider_id: str) -> str | None:
    """O esforço dos Parâmetros, ou None (ausente ou fora da lista, com o aviso no log)."""
    if value is None:
        return None
    effort = value.strip().lower() if isinstance(value, str) else ""
    if effort in EFFORTS:
        return effort
    log.warning("%s: Parâmetros: %s = %r fora de low, medium, high, xhigh e max; ignorado", provider_id, key, str(value)[:40])
    return None


@dataclass(frozen=True, slots=True)
class ClaudeParams:
    """Os Parâmetros do provedor (`providers.params`, JSON) que o adaptador lê, conferidos uma vez ao criá-lo. `warned` guarda
    as famílias já avisadas do `xhigh` trocado (R-03), para o aviso sair uma vez por provedor criado."""
    effort_fast: str | None = None
    effort_strong: str | None = None
    fallbacks: bool = True
    service_tier: Any = None
    provider_id: str = "anthropic"
    warned: set[str] = field(default_factory=set, compare=False, repr=False)

    @classmethod
    def parse(cls, params: Mapping[str, Any], provider_id: str) -> ClaudeParams:
        fallbacks = params.get(FALLBACK_KEY, True)
        if not isinstance(fallbacks, bool):
            log.warning("%s: Parâmetros: %s precisa ser true ou false; segue ligado", provider_id, FALLBACK_KEY)
            fallbacks = True
        return cls(_effort(params.get(FAST_KEY), FAST_KEY, provider_id), _effort(params.get(STRONG_KEY), STRONG_KEY, provider_id),
                   fallbacks, params.get(TIER_KEY), provider_id)

    def effort(self, reasoning: Reasoning, family: Family) -> str:
        """O esforço do pedido na família; o `xhigh` dos Parâmetros vira `high` onde ele não existe (o Opus 4.6 e o Sonnet 4.6
        respondem 400 [exp]), com o aviso na primeira vez."""
        chosen = self.effort_fast if reasoning == Reasoning.NONE else self.effort_strong
        effort = chosen or EFFORT_BY_REASONING[reasoning]
        if effort != "xhigh" or family.xhigh:
            return effort
        if family.prefix not in self.warned:
            self.warned.add(family.prefix)
            log.warning("%s: Parâmetros: o %s não aceita o esforço xhigh; vai high", self.provider_id, family.display)
        return "high"

    def fallback_for(self, model: str) -> bool:
        return self.fallbacks and models_table.claude_family(model).fallback


def conversation(req: Request, provider_id: str) -> tuple[str | None, list[dict[str, Any]]]:
    """O sistema e as mensagens só com texto; o prefixo estável (a primeira mensagem do usuário) com `cache_control`. R-01: a
    mensagem em branco (a resposta vazia gravada no chat, de uma recusa ou de outro provedor) fica de fora, porque a API recusa
    bloco de texto só com espaço; os turnos seguidos do mesmo papel ela junta, mas o primeiro precisa ser do usuário, então a
    resposta que sobra no começo (o histórico cortado no meio de um par) também sai."""
    if has_audio(req):
        raise ProviderUnavailableError(f"{provider_id} não aceita áudio nativo")
    system, rest = split_system(req)
    out: list[dict[str, Any]] = []
    for m in rest:
        if not m.text.strip() or (not out and m.role == Role.ASSISTANT):
            continue
        block: dict[str, Any] = {"type": "text", "text": m.text}
        if req.cache_prefix and not out:                             # a primeira que fica é sempre do usuário
            block["cache_control"] = {"type": "ephemeral"}
        out.append({"role": "assistant" if m.role == Role.ASSISTANT else "user", "content": [block]})
    return system, out


def _thinking(kw: dict[str, Any], req: Request, family: Family, params: ClaudeParams) -> int:
    """O pensamento da família no pedido e a folga que ele pede no `max_tokens` (0 sem pensamento)."""
    if family.thinking == models_table.THINK_ADAPTIVE:
        effort = params.effort(req.reasoning, family)
        kw["thinking"] = {"type": "adaptive"}
        kw["output_config"] = {"effort": effort}
        return EFFORT_HEADROOM[effort]
    budget = THINKING_BUDGET.get(req.reasoning)
    if family.thinking == models_table.THINK_BUDGET and budget is not None:
        kw["thinking"] = {"type": "enabled", "budget_tokens": budget}
        return budget
    return 0


def build(req: Request, params: ClaudeParams, provider_id: str) -> dict[str, Any]:
    """Os argumentos de `messages.stream`/`create` (sem `stream`, `betas` nem `fallbacks`), com o prazo da chamada."""
    system, messages = conversation(req, provider_id)
    family = models_table.claude_family(req.model)
    kw: dict[str, Any] = {"model": req.model, "messages": messages, "timeout": req.timeout_s}  # por chamada (R3-03)
    if system:
        kw["system"] = system
    if params.service_tier is not None:
        kw["service_tier"] = params.service_tier
    forced = req.json_schema is not None and family.json_style == models_table.JSON_TOOL
    headroom = 0
    if forced:
        kw["tools"] = [{"name": TOOL_NAME, "description": "Devolve o resultado estruturado desta tarefa.",
                        "input_schema": provider_schema(req.json_schema or {}, frozenset())}]
        kw["tool_choice"] = {"type": "tool", "name": TOOL_NAME, "disable_parallel_tool_use": True}
    else:
        headroom = _thinking(kw, req, family, params)
        if req.json_schema is not None:
            kw.setdefault("output_config", {})["format"] = {"type": "json_schema", "schema": provider_schema(req.json_schema)}
    kw["max_tokens"] = min(req.max_output_tokens + headroom, max(family.output_limit, req.max_output_tokens))
    return kw


__all__ = ["EFFORTS", "EFFORT_BY_REASONING", "EFFORT_HEADROOM", "FALLBACK_BETA", "FALLBACK_MODE", "THINKING_BUDGET", "TOOL_NAME",
           "ClaudeParams", "build", "conversation"]
