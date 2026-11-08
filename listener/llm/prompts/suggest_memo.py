"""F5.20 (U-158): o memorando do que o usuário prefere nas sugestões ao vivo, refeito ao parar a gravação com 10 avaliações
novas ou mais. Entra o memorando atual e as opções avaliadas (as mais recentes primeiro, com o tipo, o texto, a nota, o
motivo e se foi usada); sai um texto curto só sobre estilo e abordagem, que o pedido de cada sugestão leva e a página
Prompts mostra, editável e com "Limpar". Global, sem reunião (como o chat sobre todas): o idioma é o padrão do app. F5.6 L2
(A-24): as instruções são do usuário; o contrato e o esquema ficam (CP2)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message, Role
from .common import language_instruction, system_text, user
from .suggest import RatedOption

PROMPT_VERSION = "1.0"
MAX_MEMO = 600

SCHEMA: dict = {"type": "object", "additionalProperties": False, "required": ["memo"],
                "properties": {"memo": {"type": "string", "maxLength": MAX_MEMO}}}
INSTRUCTIONS = ("Descreva como esta pessoa prefere as sugestões de fala que recebe durante as reuniões: tamanho, tom, tipo de "
                "abordagem, o que ela aprova e o que evita.")
CONTRACT = ("O pedido traz o memorando atual (ou nenhum) e as opções que a pessoa avaliou, as mais recentes primeiro, cada uma com o "
            "tipo, o texto, a nota ('Boa', 'Ruim' com o motivo) e se foi usada. Devolva 'memo' com até "
            f"{MAX_MEMO} caracteres, só sobre estilo e abordagem: nunca copie nomes, números, prazos, fatos ou trechos das "
            "reuniões. Comece pelo que mais importa; se as avaliações se contradizem, prevalecem as mais recentes; o memorando "
            "atual só muda no que as avaliações novas justificam.")


@dataclass(frozen=True, slots=True)
class MemoCtx:
    current: str | None                # o memorando de agora (vazio na primeira vez)
    rated: Sequence[RatedOption]       # as opções avaliadas, as mais recentes primeiro
    output_language: str               # o idioma padrão do app


def build(ctx: MemoCtx, instructions: str | None = None) -> tuple[Message, ...]:
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.output_language)
    current = f"Memorando atual:\n{ctx.current}" if ctx.current else "Memorando atual: nenhum"
    rated = ("Opções avaliadas (as mais recentes primeiro):\n" + "\n".join(f"- {r.render()}" for r in ctx.rated) if ctx.rated
             else "Opções avaliadas: nenhuma")
    return (Message(Role.SYSTEM, f"{text}\n{language_instruction(ctx.output_language)}"), user(current, rated))


__all__ = ["CONTRACT", "INSTRUCTIONS", "MAX_MEMO", "PROMPT_VERSION", "SCHEMA", "MemoCtx", "build"]
