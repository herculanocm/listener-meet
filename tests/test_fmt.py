"""F5.11 L0: o `plural` (U-109, A-27), sem Qt, e a função única dos rótulos da fila sem o prefixo da LLM (A-28): os três
lugares que tiravam o "LLM: " à mão (o pedido de ata, o aviso de erro do job e o erro parcial do job `llm`) passam por ela,
e a troca do prefixo (o L3) não os quebra."""
from __future__ import annotations

import pytest

from listener import model
from listener.fmt import plural
from listener.model import JOB_STEP_LABELS, LLM_STEPS, JobStep, step_task


def test_plural_agrees_with_the_number():
    assert [plural(n, "fala") for n in (0, 1, 2)] == ["0 falas", "1 fala", "2 falas"]          # zero vai no plural
    assert (plural(1, "voz", "vozes"), plural(3, "voz", "vozes")) == ("1 voz", "3 vozes")       # plural irregular
    assert plural(2, "ação aberta", "ações abertas") == "2 ações abertas"                       # a frase inteira concorda
    assert plural(1, "reunião", "reuniões") == "1 reunião"


def test_step_task_drops_only_the_llm_prefix_and_follows_a_new_one(monkeypatch):
    assert step_task(JobStep.MINUTES) == "ata" and step_task("identify") == "identidade por contexto"
    assert step_task(JobStep.DIARIZE) == "separando vozes" and step_task(JobStep.INDEX_TEXT) == "índice: trechos"
    assert step_task("desconhecida") == "desconhecida" and step_task("desconhecida", "") == ""
    assert all(JOB_STEP_LABELS[s] == model.LLM_STEP_PREFIX + step_task(s) for s in LLM_STEPS)
    monkeypatch.setattr(model, "LLM_STEP_PREFIX", "IA: ")
    monkeypatch.setitem(JOB_STEP_LABELS, JobStep.MINUTES, "IA: ata")
    assert step_task(JobStep.MINUTES) == "ata"                                                  # o prefixo novo também sai


@pytest.mark.parametrize("site", ["ui/llm_ask.py", "ui/notices.py", "pipeline/llm_steps.py"])
def test_no_site_strips_the_queue_prefix_by_hand(site):
    from pathlib import Path

    text = (Path(model.__file__).parent / site).read_text(encoding="utf-8")
    assert 'removeprefix("LLM' not in text and "step_task(" in text


def test_the_vocabulary_of_question_2():
    assert (model.AI, model.BY_VOICE, model.BY_CONTEXT) == ("IA", "pela voz", "pelo contexto")
