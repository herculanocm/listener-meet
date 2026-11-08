"""F5.20: as sugestões ao vivo do que falar (U-153 a U-159). Quem pede (nome, cargo e empresa da ficha "Você"), os
participantes, o glossário e os marcadores (o cabeçalho), o dossiê, o memorando, o objetivo, o resumo parcial, o fio da
sugestão anterior, as avaliações desta reunião, a linha "Como sugerir agora" e as legendas compactadas
(`llm/suggest_compact.py`) viram de 2 a 4 opções em JSON, cada uma com o tipo, a fala e os `caption_id` que a sustentam,
e o fio da reunião até aqui em prosa; o leitor incremental (`llm/suggest_parse.py`) entrega cada opção ao cartão enquanto
a resposta chega. O "Me situar" troca as regras: três itens, Assunto, Decidido e Pendente. Modelo rápido; no idioma da
reunião (F5.3 A-02).

- F5.6 L2 (A-24): as instruções são do usuário (a página Prompts); o contrato, o esquema, os blocos e as citações ficam
  (CP2). O contrato começa sempre pelo mesmo trecho (`CONTRACT`) e segue com as regras do modo (`CONTRACT_SPEAK` e
  `CONTRACT_ORIENT` são os inteiros): as instruções do usuário precedem o mesmo contrato nos dois modos.
- Os limites do esquema (`TEXT_LIMIT`, `SUMMARY_LIMIT`) ficam acima do que o contrato pede (`MAX_TEXT`, `MAX_SUMMARY`), como
  no resumo parcial: a OpenAI e a Anthropic não recebem o `maxLength`, e uma fala um pouco longa não derruba a sugestão
  (D-101)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...model import SuggestKind, SuggestMode
from ..types import Message
from .common import VOICE_LABELS, Line, MeetingCtx, Person, header, render_lines, system, system_text, user

PROMPT_VERSION = "1.0"
MAX_TEXT = 240                          # o que o contrato pede por fala
MAX_SUMMARY = 400                       # o que o contrato pede no fio da reunião
TEXT_LIMIT = MAX_TEXT + 160             # o teto do esquema (a margem do parcial)
SUMMARY_LIMIT = MAX_SUMMARY + 200
MAX_OPTIONS = 4
MAX_FACTS = 6
KINDS_SPEAK: tuple[SuggestKind, ...] = (SuggestKind.ASK, SuggestKind.POSITION, SuggestKind.NEXT_STEP, SuggestKind.ALIGN,
                                        SuggestKind.PAUSE)
KINDS_ORIENT: tuple[SuggestKind, ...] = (SuggestKind.TOPIC, SuggestKind.DECIDED, SuggestKind.PENDING)
LINES_LABEL = "Legendas recentes (formato: #caption_id [mm:ss] falante: texto)"

OPTION_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["kind", "text", "facts"],
    "properties": {
        "kind": {"type": "string", "enum": [str(k) for k in SuggestKind]},
        "text": {"type": "string", "maxLength": TEXT_LIMIT},
        "facts": {"type": "array", "maxItems": MAX_FACTS, "items": {"type": "integer"}},
    },
}
SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["options", "thread_summary"],
    "properties": {
        "options": {"type": "array", "maxItems": MAX_OPTIONS, "items": OPTION_SCHEMA},
        "thread_summary": {"type": "string", "maxLength": SUMMARY_LIMIT},
    },
}
INSTRUCTIONS = ("Você é o assessor silencioso de quem pede a sugestão, numa reunião que está acontecendo agora. Proponha o que essa "
                "pessoa pode dizer a seguir, em primeira pessoa e no tom do cargo dela: direto, cordial, concreto e curto, sem rodeios "
                "e sem jargão.")
CONTRACT = ("O pedido traz quem pede a sugestão (nome, cargo e empresa), os participantes, o glossário, os marcadores que a pessoa "
            "pôs durante a reunião, o objetivo dela nesta reunião quando houver, o dossiê (ações abertas e reuniões anteriores com "
            "estas pessoas), o que ela prefere nas sugestões (memorando), o resumo parcial da reunião (JSON) e o fio da reunião até "
            "a sugestão anterior quando houver, as avaliações que ela deu às sugestões desta reunião e as legendas recentes no "
            "formato '#caption_id [mm:ss] falante: texto'. As legendas são automáticas: podem ter erros de reconhecimento, e o "
            "rótulo do falante pode estar errado ou ser só 'Participante'; as linhas com o rótulo de quem pede são o que ela já "
            "disse.")
RULES_SPEAK = ("Devolva exatamente o número de opções pedido na última linha, cada uma com 'kind' entre: "
               f"{', '.join(KINDS_SPEAK)}; tipos diferentes entre si quando possível. 'text' é a fala em primeira pessoa, pronta "
               f"para ser dita, com até duas frases e {MAX_TEXT} caracteres, sem saudação, sem markdown, sem aspas e sem o nome de "
               "quem fala. 'facts' são os caption_id (o número depois de '#') das legendas que sustentam a fala, só os que estão no "
               "pedido; lista vazia quando a fala não se apoia em legenda nenhuma. Nunca invente fatos, números, nomes ou prazos que "
               "não estejam nas legendas, no resumo ou no dossiê. Não repita o que quem pede já disse. Siga a linha 'Como sugerir "
               "agora' e o objetivo quando existirem. As avaliações dizem o que a pessoa aprovou ('Boa', 'Usei') e reprovou ('Ruim', "
               "com o motivo) nesta reunião: ajuste tamanho, tom e tipo de acordo. 'thread_summary' é o fio da reunião até aqui, em "
               f"prosa corrida de até {MAX_SUMMARY} caracteres: o assunto atual, o que foi decidido e o que falta decidir, sem citar "
               f"caption_id. {VOICE_LABELS} Exemplo de uma opção, em português do Brasil: "
               '{"kind": "Perguntar", "text": "Antes de fecharmos o corte, qual é o impacto no projeto de dados?", "facts": [41, 43]}.')
RULES_ORIENT = ("Devolva exatamente três opções, nesta ordem e com estes 'kind': 'Assunto' (do que estão falando agora), 'Decidido' "
                "(o que já foi decidido na reunião) e 'Pendente' (o que ainda falta decidir ou responder). 'text' em terceira pessoa "
                f"sobre a reunião, com até duas frases e {MAX_TEXT} caracteres, sem markdown. 'facts' são os caption_id das legendas "
                "que sustentam o item, só os que estão no pedido; lista vazia sem legenda. Nunca invente o que não está nas "
                f"legendas, no resumo ou no dossiê. 'thread_summary' é o fio da reunião até aqui, em prosa corrida de até "
                f"{MAX_SUMMARY} caracteres, sem citar caption_id. {VOICE_LABELS}")
CONTRACT_SPEAK = f"{CONTRACT} {RULES_SPEAK}"
CONTRACT_ORIENT = f"{CONTRACT} {RULES_ORIENT}"


@dataclass(frozen=True, slots=True)
class RatedOption:
    """Uma opção já avaliada, como vai ao pedido (as desta reunião) e ao memorando (todas)."""
    kind: str
    text: str
    rating: int | None            # 1 Boa, 0 Ruim, None sem nota
    reason: str | None            # o motivo do "Ruim"
    used: bool

    def render(self) -> str:
        """"Boa · Perguntar: <texto>", "Ruim (longa demais) · Posicionar: <texto>", "Usei · ...", "Boa e usei · ..."."""
        match self.rating:
            case 1:
                verdict = "Boa"
            case 0:
                verdict = f"Ruim ({self.reason})" if self.reason else "Ruim"
            case _:
                verdict = ""
        if self.used:
            verdict = f"{verdict} e usei" if verdict else "Usei"
        return f"{verdict or 'Sem nota'} · {self.kind}: {self.text}"


@dataclass(frozen=True, slots=True)
class SuggestCtx:
    meeting: MeetingCtx                # o cabeçalho: data, título, esperados, falantes com cargo, glossário e marcadores
    asker: Person                      # quem pede: a ficha "Você"
    mode: SuggestMode
    options: int                       # quantas opções no modo fala (o "Me situar" pede sempre 3)
    goal: str | None                   # "Meu objetivo nesta reunião" (U-156)
    dossier: str | None                # U-161
    memo: str | None                   # U-158: o que o assistente aprendeu
    partial_json: str | None           # o resumo parcial atual, sem os metadados do projeto
    thread_summary: str | None         # o fio da sugestão anterior (o resumo rolante)
    lines: Sequence[Line]              # as legendas compactadas: `segment_id` aqui é o id da legenda
    ratings: Sequence[RatedOption]     # as avaliações desta reunião, as mais recentes primeiro
    nudge: str | None                  # "Como sugerir agora" (U-157)


def contract_for(mode: SuggestMode) -> str:
    return CONTRACT_ORIENT if mode == SuggestMode.ORIENT else CONTRACT_SPEAK


def mode_line(ctx: SuggestCtx) -> str:
    """A última linha do pedido: o modo e quantas opções (o contrato manda devolver exatamente esse número)."""
    return "Modo: situar, 3 itens." if ctx.mode == SuggestMode.ORIENT else f"Modo: fala, {ctx.options} opções."


def build(ctx: SuggestCtx, instructions: str | None = None) -> tuple[Message, ...]:
    """O pedido de uma sugestão (C4): o sistema com as instruções e o contrato do modo; o usuário com os blocos nesta ordem,
    só os que existem."""
    text = system_text(INSTRUCTIONS, contract_for(ctx.mode), instructions, ctx.meeting.output_language)
    blocks = [header(ctx.meeting), f"Quem pede a sugestão: {ctx.asker.render()}"]
    if ctx.dossier:
        blocks.append(f"Dossiê dos participantes:\n{ctx.dossier}")
    if ctx.memo:
        blocks.append(f"O que esta pessoa prefere nas sugestões (memorando):\n{ctx.memo}")
    if ctx.goal:
        blocks.append(f"Objetivo dela nesta reunião: {ctx.goal}")
    blocks.append(f"Resumo parcial atual (JSON):\n{ctx.partial_json}" if ctx.partial_json else "Resumo parcial atual: nenhum")
    blocks.append(f"Fio da reunião até a sugestão anterior:\n{ctx.thread_summary}" if ctx.thread_summary
                  else "Fio da reunião até a sugestão anterior: nenhum (primeira sugestão)")
    if ctx.ratings:
        blocks.append("Avaliações dela nesta reunião:\n" + "\n".join(f"- {r.render()}" for r in ctx.ratings))
    if ctx.nudge:
        blocks.append(f"Como sugerir agora: {ctx.nudge}")
    blocks.append(f"{LINES_LABEL}:\n{render_lines(ctx.lines)}" if ctx.lines else "Legendas recentes: nenhuma nos últimos minutos")
    blocks.append(mode_line(ctx))
    return (system(text, ctx.meeting), user(*blocks))


__all__ = ["CONTRACT", "CONTRACT_ORIENT", "CONTRACT_SPEAK", "INSTRUCTIONS", "KINDS_ORIENT", "KINDS_SPEAK", "MAX_FACTS", "MAX_OPTIONS",
           "MAX_SUMMARY", "MAX_TEXT", "OPTION_SCHEMA", "PROMPT_VERSION", "SCHEMA", "SUMMARY_LIMIT", "TEXT_LIMIT", "RatedOption",
           "SuggestCtx", "build", "contract_for", "mode_line"]
