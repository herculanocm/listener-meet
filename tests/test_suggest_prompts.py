"""F5.20 L1 (C4, C5): os prompts da sugestão ao vivo e do memorando. Os esquemas no subconjunto estrito, a classe rápida e as
versões; os blocos do pedido na ordem do contrato, com e sem cada opcional; o "Me situar" com as regras dele e o mesmo começo
de contrato; a margem entre o que o contrato pede e o teto do esquema; o `RatedOption.render`; as instruções do usuário com a
marca `+u<hash>`; o memorando sem reunião, no idioma dado."""
from __future__ import annotations

from listener.config import prompts as prompt_settings
from listener.llm import prompts
from listener.llm.prompts.common import Line, MeetingCtx, Person, language_instruction
from listener.llm.prompts.suggest import (CONTRACT, CONTRACT_ORIENT, CONTRACT_SPEAK, INSTRUCTIONS, KINDS_ORIENT, KINDS_SPEAK, MAX_SUMMARY,
                                          MAX_TEXT, SUMMARY_LIMIT, TEXT_LIMIT, RatedOption, SuggestCtx)
from listener.llm.prompts.suggest_memo import MemoCtx
from listener.llm.schema import check_strict, validate
from listener.llm.types import TASK_CLASS, TASK_REASONING, LlmTask, ModelClass, Reasoning, Role
from listener.model import SuggestKind, SuggestMode

MEETING = MeetingCtx("Orçamento", "2026-11-08", "Ana", (Person(7, "Ana Souza", "CFO", "ACME"),), (), "pt-BR")
ME = Person(1, "Carla", "CTO", "Beta")
LINES = (Line(41, 65_000, "Ana Souza — CFO", "O corte é de oito por cento."), Line(43, 70_000, "Carla — CTO", "Precisamos de exceção."))
RATED = (RatedOption("Perguntar", "Qual o prazo?", 1, None, False), RatedOption("Posicionar", "Sou contra.", 0, "tom errado", True))


def _ctx(**kw: object) -> SuggestCtx:
    base: dict[str, object] = {"meeting": MEETING, "asker": ME, "mode": SuggestMode.SPEAK, "options": 3, "goal": "fechar o corte",
                               "dossier": "- Ação aberta: x", "memo": "Prefere perguntas.", "partial_json": '{"summary": "s"}',
                               "thread_summary": "o fio", "lines": LINES, "ratings": RATED, "nudge": "mais curta"}
    base.update(kw)
    return SuggestCtx(**base)  # type: ignore[arg-type]


def _user(ctx: SuggestCtx) -> str:
    msgs = prompts.suggest.build(ctx)
    assert [m.role for m in msgs] == [Role.SYSTEM, Role.USER]
    return msgs[1].text


def test_the_schemas_are_strict_fast_and_versioned():
    assert check_strict(prompts.suggest.SCHEMA) == [] and check_strict(prompts.suggest_memo.SCHEMA) == []
    assert TASK_CLASS[LlmTask.SUGGEST] == TASK_CLASS[LlmTask.SUGGEST_MEMO] == ModelClass.FAST
    assert TASK_REASONING[LlmTask.SUGGEST] == Reasoning.NONE
    assert prompts.prompt_version(LlmTask.SUGGEST) == "suggest/1.0" and prompts.prompt_version(LlmTask.SUGGEST_MEMO) == "suggest_memo/1.0"
    assert prompts.EDITABLE.index(LlmTask.SUGGEST) == prompts.EDITABLE.index(LlmTask.PARTIAL) + 1
    assert prompt_settings.TASKS.index("suggest_memo") == prompt_settings.TASKS.index("suggest") + 1
    kinds = prompts.suggest.OPTION_SCHEMA["properties"]["kind"]["enum"]
    assert kinds == [str(k) for k in SuggestKind] and set(KINDS_SPEAK) | set(KINDS_ORIENT) == set(SuggestKind)


def test_the_schema_ceiling_leaves_room_over_what_the_contract_asks():
    assert TEXT_LIMIT > MAX_TEXT and SUMMARY_LIMIT > MAX_SUMMARY
    assert f"{MAX_TEXT} caracteres" in CONTRACT_SPEAK and f"{MAX_SUMMARY} caracteres" in CONTRACT_SPEAK
    long = {"options": [{"kind": "Perguntar", "text": "x" * (MAX_TEXT + 20), "facts": []}], "thread_summary": "y" * (MAX_SUMMARY + 50)}
    assert validate(prompts.suggest.SCHEMA, long) == long                      # um pouco além do pedido não derruba a sugestão


def test_the_blocks_go_in_the_contract_order_and_only_the_ones_that_exist():
    text = _user(_ctx())
    order = ["Data da reunião: 2026-11-08", "Quem pede a sugestão: (1) Carla — CTO, Beta", "Dossiê dos participantes:\n- Ação aberta: x",
             "O que esta pessoa prefere nas sugestões (memorando):\nPrefere perguntas.", "Objetivo dela nesta reunião: fechar o corte",
             'Resumo parcial atual (JSON):\n{"summary": "s"}', "Fio da reunião até a sugestão anterior:\no fio",
             "Avaliações dela nesta reunião:\n- Boa · Perguntar: Qual o prazo?\n- Ruim (tom errado) e usei · Posicionar: Sou contra.",
             "Como sugerir agora: mais curta",
             "Legendas recentes (formato: #caption_id [mm:ss] falante: texto):\n#41 [01:05] Ana Souza — CFO: O corte é de oito por cento.",
             "Modo: fala, 3 opções."]
    positions = [text.index(block) for block in order]
    assert positions == sorted(positions) and text.endswith("Modo: fala, 3 opções.")
    bare = _user(_ctx(goal=None, dossier=None, memo=None, partial_json=None, thread_summary=None, ratings=(), nudge=None, lines=()))
    assert "Dossiê" not in bare and "memorando" not in bare and "Objetivo" not in bare and "Avaliações" not in bare
    assert "Resumo parcial atual: nenhum" in bare and "Fio da reunião até a sugestão anterior: nenhum (primeira sugestão)" in bare
    assert "Legendas recentes: nenhuma nos últimos minutos" in bare and "Como sugerir agora" not in bare


def test_orient_keeps_the_same_contract_head_and_asks_for_three_items():
    speak, orient = prompts.suggest.build(_ctx()), prompts.suggest.build(_ctx(mode=SuggestMode.ORIENT, options=2))
    assert speak[0].text.startswith(f"{INSTRUCTIONS} {CONTRACT_SPEAK}") and orient[0].text.startswith(f"{INSTRUCTIONS} {CONTRACT_ORIENT}")
    assert CONTRACT_SPEAK.startswith(CONTRACT) and CONTRACT_ORIENT.startswith(CONTRACT)
    assert "'Assunto'" in CONTRACT_ORIENT and "'Pendente'" in CONTRACT_ORIENT and "Perguntar" not in CONTRACT_ORIENT
    assert orient[1].text.endswith("Modo: situar, 3 itens.") and language_instruction("pt-BR") in orient[0].text


def test_rated_option_renders_the_verdict():
    assert RatedOption("Perguntar", "a", 1, None, False).render() == "Boa · Perguntar: a"
    assert RatedOption("Perguntar", "a", 0, "longa demais", False).render() == "Ruim (longa demais) · Perguntar: a"
    assert RatedOption("Perguntar", "a", 0, None, False).render() == "Ruim · Perguntar: a"
    assert RatedOption("Perguntar", "a", None, None, True).render() == "Usei · Perguntar: a"
    assert RatedOption("Perguntar", "a", 1, None, True).render() == "Boa e usei · Perguntar: a"
    assert RatedOption("Perguntar", "a", None, None, False).render() == "Sem nota · Perguntar: a"


def test_custom_instructions_come_first_and_stamp_the_version():
    mine = "Sugira em {idioma}, como um CFO."
    msgs = prompts.build(LlmTask.SUGGEST, _ctx(), mine)
    assert msgs[0].text.startswith(f"Sugira em português do Brasil, como um CFO. {CONTRACT_SPEAK}")
    assert prompts.prompt_version(LlmTask.SUGGEST, mine) == f"suggest/1.0+u{prompt_settings.text_hash(mine)}"


def test_the_memo_has_no_meeting_and_writes_in_the_given_language():
    msgs = prompts.suggest_memo.build(MemoCtx("Prefere curtas.", RATED, "en"))
    assert msgs[0].text.endswith(language_instruction("en")) and msgs[0].text.startswith(prompts.suggest_memo.INSTRUCTIONS)
    assert msgs[1].text.startswith("Memorando atual:\nPrefere curtas.") and "- Boa · Perguntar: Qual o prazo?" in msgs[1].text
    empty = prompts.suggest_memo.build(MemoCtx(None, (), "pt-BR"))[1].text
    assert "Memorando atual: nenhum" in empty and "Opções avaliadas: nenhuma" in empty
