"""F5.14 L3 — a sonda do Claude (spec specs/features/F5.14-claude-anthropic.md, item 13). Não faz parte do produto.

Roda no venv do projeto (Windows, Linux ou WSL), com o extra `llm` instalado (o SDK `anthropic` e o `httpx2`).

  python scripts/llm_probe.py anthropic [--real] [--fast ID] [--strong ID]

Manda cada um dos 10 esquemas JSON do projeto (identify, minutes, executive, pre_summary, title, revise, glossary, partial,
rag_sql e test: o `SCHEMA` de cada módulo de `listener/llm/prompts/`), com o sistema "Responda só com o JSON do esquema." e
uma reunião sintética curta (nomes inventados, nada real), ao modelo da classe da tarefa (`TASK_CLASS`; rápido e forte de
`DEFAULT_MODELS`, ou `--fast` e `--strong`), com o raciocínio dela (`TASK_REASONING`) e o teto de saída que o app usa, pelo
`RetryingProvider` sobre o adaptador do projeto; a resposta é validada com `schema.validate` (o esquema inteiro). Uma linha
JSON por tarefa: ok ou o erro (redigido), tokens de entrada, saída, pensamento e cache lido, ms, custo pelas sementes de
preço e os reenvios; no fim, o total. Código de saída 1 se algum esquema falhar. Nunca imprime conteúdo nem a chave.

sem --real  o cliente real do SDK sobre um `httpx2.MockTransport` com respostas prontas no streaming (SSE, como a API manda):
            o caminho inteiro do pedido, sem rede nem chave (o teste da suíte roda assim).
--real      a API de verdade (o dono, com o OK e a chave dele; Parte 25 do roteiro): a chave vem de `ANTHROPIC_API_KEY` ou é
            pedida sem eco (`getpass`); nunca é impressa nem gravada, e entra na redação dos erros (`load_provider`). Rede
            padrão do sistema: os certificados do sistema (truststore) e o proxy do ambiente, se houver.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                    # o pacote deste checkout, não o instalado (o .pth do venv aponta para outro)

TASKS = ("identify", "minutes", "executive", "pre_summary", "title", "revise", "glossary", "partial", "rag_sql", "test")
SYSTEM = "Responda só com o JSON do esquema."
MEETING = """Reunião sintética (nomes inventados): Planejamento do trimestre, 02/11/2026 10:00, 3 min
Participantes esperados: Ana Prado (diretora financeira, Acme Fictícia); Bruno Lemos (analista, Acme Fictícia)
Pessoas cadastradas: 7 Ana Prado; 9 Bruno Lemos
Marcadores: #3 [01:10] decisão do orçamento
Voz pendente: SPK_2
Transcrição:
#101 [00:05] Ana Prado — diretora financeira: Bom dia. Hoje fechamos o orçamento do trimestre e a data do relatório.
#102 [00:21] SPK_2: Eu trouxe os números: o custo de nuvem subiu doze por cento e o de licenças caiu.
#103 [00:48] Ana Prado — diretora financeira: Então cortamos oito por cento da nuvem. Bruno, você revisa os contratos até sexta.
#104 [01:10] SPK_2: Combinado, eu reviso. Mando a planilha revisada na sexta, dia seis.
#105 [01:32] Ana Prado — diretora financeira: Fica em aberto a renovação do suporte; voltamos a isso na próxima reunião.
#106 [02:05] SPK_2: Encerrando: corte de oito por cento aprovado e contratos revisados até sexta."""
KEY_ENV = "ANTHROPIC_API_KEY"
SIM_KEY = "sk-ant-simulada"
SIM_THINKING = 64                                # tokens de pensamento das respostas simuladas com pensamento adaptativo


def out(stage: str, **kw: Any) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def ceilings() -> dict[str, int]:
    """O teto de saída que o app usa em cada tarefa (o job da LLM, o glossário, o resumo ao vivo, a rodada SQL e o canário)."""
    from listener.llm.adapters_common import TEST_MAX_TOKENS
    from listener.llm.partial_task import MAX_OUTPUT_TOKENS
    from listener.pipeline.llm_steps import MAX_OUTPUT
    from listener.rag.agent import SQL_OUTPUT_TOKENS

    out_by_task = {str(task): n for task, n in MAX_OUTPUT.items()}
    return {**out_by_task, "glossary": 1024, "partial": MAX_OUTPUT_TOKENS, "rag_sql": SQL_OUTPUT_TOKENS, "test": TEST_MAX_TOKENS}


def api_key(environ: Mapping[str, str], ask: Callable[[str], str]) -> str:
    """A chave do `--real`: a do ambiente ou a digitada sem eco; nunca vai para a tela nem para o disco."""
    return (environ.get(KEY_ENV) or ask("Chave da Anthropic (não aparece na tela): ")).strip()


# ---------------------------------------------------------------- o transporte simulado (sem --real)
def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode("utf-8")


def simulated_payload(schema: Mapping[str, Any]) -> Any:
    """A resposta pronta de um esquema: a instância mínima válida (o fake do projeto usa a mesma)."""
    from listener.llm.schema import minimal_instance

    return minimal_instance(schema)


def simulated_reply(body: Mapping[str, Any]) -> bytes:
    """A resposta da API no streaming para o pedido: o pensamento (vazio, como a API devolve por padrão) quando o pedido o
    liga, e o JSON no texto (`output_config.format`) ou na entrada da ferramenta forçada; o uso estimado pelo tamanho."""
    from listener.llm.costs import estimate_tokens

    tool = bool(body.get("tools"))
    schema = body["tools"][0]["input_schema"] if tool else body["output_config"]["format"]["schema"]
    payload = json.dumps(simulated_payload(schema), ensure_ascii=False)
    thinking = SIM_THINKING if body.get("thinking") else 0
    usage = {"input_tokens": estimate_tokens(len(json.dumps(body, ensure_ascii=False))), "output_tokens": 1,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    events: list[dict[str, Any]] = [{"type": "message_start", "message": {
        "id": "msg_simulada", "type": "message", "role": "assistant", "content": [], "model": body["model"], "stop_reason": None,
        "usage": usage}}]
    blocks: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    if thinking:
        blocks.append(({"type": "thinking", "thinking": "", "signature": ""}, {"type": "signature_delta", "signature": "simulada"}))
    if tool:
        blocks.append(({"type": "tool_use", "id": "toolu_simulada", "name": body["tools"][0]["name"], "input": {}},
                       {"type": "input_json_delta", "partial_json": payload}))
    else:
        blocks.append(({"type": "text", "text": ""}, {"type": "text_delta", "text": payload}))
    for i, (start, delta) in enumerate(blocks):
        events += [{"type": "content_block_start", "index": i, "content_block": start},
                   {"type": "content_block_delta", "index": i, "delta": delta}, {"type": "content_block_stop", "index": i}]
    end_usage = {"output_tokens": estimate_tokens(payload) + thinking, "output_tokens_details": {"thinking_tokens": thinking}}
    events += [{"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn"}, "usage": end_usage},
               {"type": "message_stop"}]
    return _sse(*events)


def simulated_provider() -> tuple[Any, Any]:
    """O adaptador do projeto sobre o cliente real do SDK com o `MockTransport` (o mesmo caminho do pedido, sem rede)."""
    import anthropic
    import httpx2

    from listener.llm.anthropic import AnthropicProvider

    def handler(request: Any) -> Any:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=simulated_reply(json.loads(request.content)))

    client = anthropic.AsyncAnthropic(api_key=SIM_KEY, max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    return AnthropicProvider(SIM_KEY, client=client, provider_id="anthropic"), client


# ---------------------------------------------------------------- a medida
def watch_thinking(provider: Any) -> dict[str, int]:
    """Os tokens de pensamento de cada tarefa (`usage.output_tokens_details`), que o `Completion` não traz: a sonda embrulha a
    conversão da mensagem final do adaptador nesta instância (só aqui; o app não usa o número)."""
    seen: dict[str, int] = {}
    original = provider._completion

    def completion(req: Any, msg: Any, latency_ms: int) -> Any:
        details = getattr(getattr(msg, "usage", None), "output_tokens_details", None)
        seen[str(req.task)] = seen.get(str(req.task), 0) + int(getattr(details, "thinking_tokens", 0) or 0)
        return original(req, msg, latency_ms)

    provider._completion = completion
    return seen


async def probe_task(rp: Any, task: str, model: str, max_out: int, thinking: dict[str, int]) -> dict[str, Any]:
    """Uma tarefa: o pedido como o app manda (classe, raciocínio, teto e prazo da tarefa), a validação e o custo; a linha
    diz também o esforço e o teto enviados (a folga do pensamento é [chute]: o pensamento medido a calibra)."""
    from listener.llm import costs, prompts, redact
    from listener.llm.anthropic_request import build
    from listener.llm.types import TASK_CLASS, TASK_REASONING, LlmTask, Message, ModelClass, Request, Role

    kind = LlmTask(task)
    strong = TASK_CLASS[kind] == ModelClass.STRONG
    req = Request(kind, model, (Message(Role.SYSTEM, SYSTEM), Message(Role.USER, f"Tarefa: {task}.\n\n{MEETING}")),
                  prompts.schema_for(kind), max_output_tokens=max_out, reasoning=TASK_REASONING[kind],
                  timeout_s=240.0 if strong else 120.0, cache_prefix=True)
    sent = build(req, rp.inner.settings, rp.id)
    before = (rp.retries, rp.resends, rp.grown)
    t0 = time.perf_counter()
    try:
        comp, error = await rp.complete(req), ""
    except Exception as e:  # a sonda segue para a próxima tarefa e diz o erro (redigido)
        comp, error = None, f"{type(e).__name__}: {redact.GLOBAL.redact(str(e))[:300]}"
    line: dict[str, Any] = {"tarefa": task, "modelo": model, "raciocinio": str(req.reasoning), "teto": max_out,
                            "esforco": sent.get("output_config", {}).get("effort"), "teto_enviado": sent["max_tokens"],
                            "ms": int((time.perf_counter() - t0) * 1000), "reenvios": {
                                "rede": rp.retries - before[0], "esquema": rp.resends - before[1], "teto": rp.grown - before[2]}}
    if comp is None:
        return {**line, "ok": False, "erro": error}
    u = comp.usage
    price = costs.price_for(list(costs.PRICE_SEEDS), "anthropic", comp.model)
    return {**line, "ok": True, "respondeu": comp.model, "fim": comp.finish, "entrada": u.input_tokens, "saida": u.output_tokens,
            "pensamento": thinking.get(task, 0), "cache_lido": u.cached_tokens, "custo_usd": costs.cost_usd(u, price)}


async def run_probe(provider: Any, fast: str, strong: str) -> list[dict[str, Any]]:
    from listener.llm.registry import RetryingProvider
    from listener.llm.types import TASK_CLASS, LlmTask, ModelClass

    thinking = watch_thinking(provider)
    rp = RetryingProvider(provider)
    limits = ceilings()
    lines = []
    for task in TASKS:
        model = strong if TASK_CLASS[LlmTask(task)] == ModelClass.STRONG else fast
        line = await probe_task(rp, task, model, limits[task], thinking)
        out("tarefa", **line)
        lines.append(line)
    return lines


async def main_async(real: bool, fast: str, strong: str) -> list[dict[str, Any]]:
    if real:
        from listener.llm.registry import NetworkConfig, load_provider

        os.environ.pop("LISTENER_LLM", None)                     # o fake do projeto nunca responde no lugar da API
        key = api_key(os.environ, getpass.getpass)
        if not key:
            raise SystemExit(f"llm_probe: sem chave (defina {KEY_ENV} ou digite quando ela for pedida)")
        provider, client = load_provider("anthropic", key, NetworkConfig(), provider_id="anthropic"), None
    else:
        provider, client = simulated_provider()
    try:
        return await run_probe(provider, fast, strong)
    finally:
        await (client.close() if client is not None else provider.aclose())


def main(argv: list[str] | None = None) -> int:
    from listener.llm.models_table import DEFAULT_MODELS
    from listener.llm.types import ProviderKind

    fast_default, strong_default = DEFAULT_MODELS[ProviderKind.ANTHROPIC]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("provider", choices=["anthropic"])
    ap.add_argument("--real", action="store_true", help="a API de verdade, com a chave (sem ela: o transporte simulado)")
    ap.add_argument("--fast", default=fast_default, help=f"o modelo rápido (padrão {fast_default})")
    ap.add_argument("--strong", default=strong_default, help=f"o modelo forte (padrão {strong_default})")
    args = ap.parse_args(argv)
    out("env", python=sys.version.split()[0], platform=sys.platform, modo="real" if args.real else "simulado",
        rapido=args.fast, forte=args.strong)
    lines = asyncio.run(main_async(args.real, args.fast, args.strong))
    failed = [line["tarefa"] for line in lines if not line["ok"]]
    out("total", tarefas=len(lines), ok=len(lines) - len(failed), falhas=failed,
        custo_usd=round(sum(line.get("custo_usd") or 0.0 for line in lines), 6),
        pensamento=sum(line.get("pensamento", 0) for line in lines))
    return 1 if failed else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
