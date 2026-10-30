"""Identidade por contexto (handoff §4, F3 item 5, CP11): só vozes pendentes (`SPK_n`) entram; a LLM sugere `person_id` da
lista de candidatos ou `new_name`, com evidência `{segment_id, quote}` que o projeto confere contra o segmento. F5.6 L2
(A-24): as instruções (o que procurar) são do usuário; o contrato (os rótulos, um `person_id` ou um `new_name`, a evidência
copiada do segmento, nunca inventar nome) fica."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..types import Message
from .common import Line, MeetingCtx, Person, header, system, system_text, text_block, user

PROMPT_VERSION = "2.0"                  # D-071: em pt-BR; 1.1: o cabeçalho lista os falantes identificados
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["speakers"],
    "properties": {"speakers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["cluster", "person_id", "new_name", "confidence", "evidence"],
        "properties": {
            "cluster": {"type": "string"},
            "person_id": {"type": ["integer", "null"]},
            "new_name": {"type": ["string", "null"], "maxLength": 80},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "array", "maxItems": 5, "items": {
                "type": "object", "additionalProperties": False, "required": ["segment_id", "quote"],
                "properties": {"segment_id": {"type": "integer"}, "quote": {"type": "string", "maxLength": 200}}}}}}}},
}
INSTRUCTIONS = ("Identifique quem são os falantes sem nome na transcrição de uma reunião, usando só pistas do texto: pessoas "
                "que se chamam pelo nome, apresentações, cargos mencionados, quem responde a quem.")
CONTRACT = ("Os falantes rotulados SPK_n estão por resolver; os outros já são conhecidos. Para cada falante por resolver, "
            "devolva ou o person_id de um candidato listado ou um new_name (nunca os dois), uma confidence entre 0 e 1 e até "
            "5 evidências, cada uma com o segment_id e uma citação curta copiada literalmente daquele segmento. Sem pista, "
            "devolva person_id null, new_name null e confidence 0. Nunca invente nomes.")


@dataclass(frozen=True, slots=True)
class IdentifyCtx:
    meeting: MeetingCtx
    lines: Sequence[Line]             # `Context.identify_lines` (F5.5, A-14): a voz pendente pelo rótulo, como antes
    pending: Sequence[str]            # rótulos SPK_n por decidir (as outras vozes sem pessoa vêm como "Voz 2")
    candidates: Sequence[Person]      # pessoas cadastradas (id, nome, cargo, empresa)


def build(ctx: IdentifyCtx, instructions: str | None = None) -> tuple[Message, ...]:
    cands = "; ".join(p.render() for p in ctx.candidates) or "(nenhum)"
    text = system_text(INSTRUCTIONS, CONTRACT, instructions, ctx.meeting.output_language)
    return (system(text, ctx.meeting),
            user(header(ctx.meeting), f"Falantes por resolver: {', '.join(ctx.pending)}",
                 f"Candidatos (person_id) nome — cargo, empresa: {cands}", text_block("Transcrição", ctx.lines),
                 'Exemplo de uma entrada de falante: {"cluster": "SPK_2", "person_id": 7, "new_name": null, "confidence": 0.8, '
                 '"evidence": [{"segment_id": 143, "quote": "valeu, Rodrigo"}]}'))
