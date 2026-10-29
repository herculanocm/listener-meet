"""F5.6 L2 (U-092; A-24 a A-29, A-46; Aceite 3): as instruções de cada tarefa da LLM separadas do contrato, sem tela.

- O padrão sai igual, byte a byte, ao de `4a51600` (a cópia congelada de `tests/data/prompts_4a51600.json`, gerada de
  `tests/prompt_cases.py` no código de antes), nos onze módulos e na redução da ata; só mudam o chat e o chat de todas as
  reuniões, pelo "Be concise." que veio para as instruções (versões 1.3 e 1.2), e o título, em duas frases (1.3, R-07).
- As instruções do usuário chegam à chamada com o contrato, o esquema e os dados intactos; `{idioma}` vira o nome do
  idioma; chaves soltas não quebram nem injetam campos.
- O Salvar barra texto vazio, `{nome}` desconhecido e mais de 4.000 caracteres; `{idioma}` é opcional.
- O job congela no `config_json`, na primeira vez que roda, só as instruções das tarefas dele (R-08); a chamada grava
  `<versão>+u<hash>` em `llm_calls` e na ata; o log nunca leva o texto, só o hash.
- "Falhou" fora do esquema com instruções do usuário diz quais tarefas restaurar: só as que falharam elas mesmas fora do
  esquema (R-01) e com o texto salvo igual ao usado (R-02); restaurar volta ao padrão."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from listener.config import prompts as prompt_settings
from listener.llm import prompts
from listener.llm.base import OFF_SCHEMA, BadResponseError, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.jobconfig import LlmJobConfig
from listener.llm.prompts.common import ORIGINAL_LANGUAGE, language_instruction, own
from listener.llm.types import LlmTask, Role
from listener.model import JobStatus, SummaryKind
from listener.pipeline import llm_requests, llm_steps
from listener.pipeline.llm_requests import Species, State
from tests import prompt_cases as pc
from tests.test_llm_steps import _ask, _pipeline, _provider_ready, _record, _run_llm

FROZEN = json.loads((Path(__file__).parent / "data" / "prompts_4a51600.json").read_text(encoding="utf-8"))
# A-24: o "Be concise." saiu do meio do contrato para o fim das instruções (as duas versões sobem)
MOVED = {"chat": "transcript below.", "rag_chat": "grouped by meeting."}
CUT = (", at most", ". At most")                     # revisão do L2 (R-07): o título 1.3 em duas frases
MINE = "Escreva em {idioma}, em tópicos curtos. Chaves soltas: {x} } { {0} {title} {{a}}"
MARKER_TEXT = "PALAVRA-MARCADA-DO-PROMPT-91"          # o texto que o log nunca pode levar


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _expected(name: str) -> list[list[object]]:
    """A cópia de `4a51600` com as diferenças conhecidas, uma por versão que subiu: no chat e no chat de todas as reuniões,
    o "Be concise." movido (A-24); no título, as instruções e o contrato em duas frases (R-07). O JSON fica como está."""
    out = json.loads(json.dumps(FROZEN["cases"][name]))
    family, text = name.split("/", maxsplit=1)[0], str(out[0][1])
    anchor = MOVED.get(family)
    if anchor is not None:
        assert text.count(" Be concise.") == 1
        out[0][1] = text.replace(" Be concise.", "", 1).replace(anchor, f"{anchor} Be concise.", 1)
    if family == "title":
        assert text.count(CUT[0]) == 1
        out[0][1] = text.replace(*CUT)
    return out


def _language(name: str) -> str:
    """O nome do idioma que `{idioma}` vira no caso (a transcrição pelo áudio usa o idioma dela, ou nenhum)."""
    module, _fn, ctx = pc.CASES[name]
    if module is prompts.transcribe:
        return "Portuguese" if ctx.language == "pt" else ORIGINAL_LANGUAGE
    code = ctx.output_language if module in (prompts.rag_chat, prompts.glossary) else ctx.meeting.output_language
    return {"pt-BR": "Brazilian Portuguese", "en": "English"}[code]


# ---------------------------------------------------------------- o padrão, byte a byte (desvio consciente do A-26)
def test_the_frozen_copy_covers_every_editable_module_and_the_reduce():
    assert FROZEN["head"] == "4a51600"
    covered = {pc.CASES[name][0].__name__.rsplit(".", 1)[-1] for name in pc.CASES}
    assert covered == {str(t) for t in prompts.EDITABLE} and set(FROZEN["cases"]) == set(pc.CASES)
    assert {pc.CASES[name][1] for name in pc.CASES if name.startswith("minutes/")} == {"build", "build_reduce"}


@pytest.mark.parametrize("name", sorted(pc.CASES))
def test_the_default_prompt_is_byte_identical_to_4a51600(name):
    module, fn, ctx = pc.CASES[name]
    assert pc.render(name) == _expected(name)
    assert pc.render(name, None) == pc.render(name, "   ") == pc.render(name, module.INSTRUCTIONS) == pc.render(name)
    assert getattr(module, fn)(ctx) == getattr(module, fn)(ctx, None)


def test_the_versions_only_move_where_the_text_moved():
    versions = {str(t): prompts.prompt_version(t) for t in prompts.EDITABLE}
    assert versions == {"identify": "identify/1.1", "revise": "revise/1.1", "title": "title/1.3", "pre_summary": "pre_summary/1.2",
                        "minutes": "minutes/1.2", "executive": "executive/1.2", "partial": "partial/1.0", "chat": "chat/1.3",
                        "rag_chat": "rag_chat/1.2", "glossary": "glossary/1.0", "transcribe": "transcribe/1.0"}
    assert tuple(str(t) for t in prompts.EDITABLE) == prompt_settings.TASKS and LlmTask.TEST not in prompts.EDITABLE


# ---------------------------------------------------------------- Aceite 3: as instruções do usuário, com o resto intacto
@pytest.mark.parametrize("name", sorted(pc.CASES))
def test_custom_instructions_reach_the_call_with_contract_and_data_intact(name):
    module, fn, _ctx = pc.CASES[name]
    default, mine = pc.render(name), pc.render(name, MINE)
    system = str(mine[0][1])
    filled = MINE.replace("{idioma}", _language(name))
    contract = module.REDUCE_SYSTEM if fn == "build_reduce" else module.CONTRACT
    tail = str(default[0][1]) if fn == "build_reduce" else str(default[0][1]).removeprefix(f"{module.INSTRUCTIONS} ")
    assert mine[0][0] == str(Role.SYSTEM) and tail.startswith(contract)
    assert system == f"{filled} {tail}"                                    # as instruções suas, depois o contrato e o idioma
    assert "{x} } { {0} {title} {{a}}" in system and "{idioma}" not in system           # só `{idioma}` é trocado
    assert module.INSTRUCTIONS not in system
    assert mine[1:] == default[1:]                                                         # o cabeçalho, a transcrição, a pergunta
    if module is not prompts.transcribe:
        assert language_instruction(_language_code(name)) in system


def _language_code(name: str) -> str:
    module, _fn, ctx = pc.CASES[name]
    return ctx.output_language if module in (prompts.rag_chat, prompts.glossary) else ctx.meeting.output_language


def test_multiline_instructions_end_in_a_line_break_and_the_contract_keeps_the_last_word():
    ctx = pc.CASES["minutes/direct"][2]
    text = prompts.minutes.build(ctx, "  Linha um.\nLinha dois.  \n")[0].text
    assert text.startswith(f"Linha um.\nLinha dois.\n{prompts.minutes.CONTRACT}\n")
    reduce = prompts.minutes.build_reduce(pc.CASES["minutes/reduce"][2], "Foque nos valores.")[0].text
    assert reduce.startswith(f"Foque nos valores. {prompts.minutes.REDUCE_SYSTEM}\n")


def test_idioma_is_the_only_variable_and_braces_never_break():
    assert own("padrão", "Escreva em {idioma}.", "pt-BR") == "Escreva em Brazilian Portuguese."
    assert own("padrão", "{idioma}/{idioma}", "en") == "English/English"
    assert own("padrão", "Em {idioma}.", None) == f"Em {ORIGINAL_LANGUAGE}."
    assert own("padrão", "{Idioma} {0} {title} {} }{ {meeting.title}", "pt-BR") == "{Idioma} {0} {title} {} }{ {meeting.title}"
    assert own("padrão", None, "pt-BR") == own("padrão", "", "en") == own("padrão", " \n\t", "pt-BR") == "padrão"


# ---------------------------------------------------------------- A-27: o Salvar
def test_the_save_refuses_empty_unknown_variables_and_long_text():
    assert "vazias" in (prompt_settings.problem("") or "") and "vazias" in (prompt_settings.problem("  \n\t") or "")
    assert (prompt_settings.problem("Ata para {cliente} em {idioma}") or "").startswith("{cliente} não é uma variável")
    assert (prompt_settings.problem("{ idioma }") or "").startswith("{ idioma } não é")
    assert (prompt_settings.problem("{Idioma}") or "").startswith("{Idioma} não é")
    assert "4.001 caracteres; o limite é 4.000" in (prompt_settings.problem("a" * 4001) or "")
    for fine in ("Ata em {idioma}.", "Sem variável.", '{"text": "exemplo", "segment_ids": [1]}', "a" * 4000, "{} e }{", "{x: 1}"):
        assert prompt_settings.problem(fine) is None, fine
    assert prompt_settings.unknown_fields("{a} {b} {a} {idioma}") == ["{a}", "{b}"]


def test_restore_and_text_equal_to_the_default_store_empty(store):
    default = prompts.default_instructions(LlmTask.MINUTES)
    assert prompt_settings.save(store, "minutes", "Minha ata.", default)
    assert store.get_setting("prompt.minutes") == "Minha ata."
    assert store.get_setting("prompt.minutes.base") == hashlib.sha256(default.encode()).hexdigest()[:12]
    assert not prompt_settings.default_changed(store, "minutes", default)
    assert prompt_settings.default_changed(store, "minutes", default + " (nova versão do app)")
    assert not prompt_settings.save(store, "minutes", "Minha ata.", default)               # nada mudou: nada gravado
    assert prompt_settings.save(store, "minutes", f"  {default}\n", default)              # igual ao padrão: vazio
    assert store.get_setting("prompt.minutes") == "" and store.get_setting("prompt.minutes.base") == ""
    prompt_settings.save(store, "chat", "Meu chat.", prompts.default_instructions(LlmTask.CHAT))
    assert prompt_settings.restore(store, ["chat", "minutes", "inexistente"]) == ["chat"]
    assert prompt_settings.custom_prompts(store) == {} and LlmJobConfig.from_store(store).prompt(LlmTask.CHAT) is None


# ---------------------------------------------------------------- A-26, A-28: o job congela; a chamada marca
def test_the_job_config_reads_the_custom_prompts_and_survives_the_json(store):
    store.set_setting("prompt.minutes", MINE)
    store.set_setting("prompt.chat", "   ")                                                 # só espaços: o padrão
    store.set_setting("prompt.minutes.base", "abc")
    cfg = LlmJobConfig.from_store(store)
    assert cfg.prompts == {"minutes": MINE} and cfg.prompt(LlmTask.CHAT) is None
    assert LlmJobConfig.from_json(cfg.to_json()) == cfg
    assert cfg.version(LlmTask.MINUTES) == f"minutes/1.2+u{hashlib.sha256(MINE.encode()).hexdigest()[:6]}"
    assert cfg.version(LlmTask.CHAT) == "chat/1.3"
    junk = LlmJobConfig.from_json(json.dumps({"prompts": {"minutes": 3, "bogus": "x", "chat": "Meu chat."}}))
    assert junk.prompts == {"chat": "Meu chat."} and LlmJobConfig.from_json('{"prompts": "x"}').prompts == {}
    assert LlmJobConfig.from_json(json.dumps({"provider_id": "gemini"})).prompts == {}       # job de antes do L2


def _ready_for_minutes(platform, store) -> int:
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)                                                                          # o núcleo, com o padrão
    return mid


@pytest.mark.usefixtures("no_summary_gate")
def test_the_job_freezes_only_its_own_text_on_the_first_run_and_marks_llm_calls(platform, store):
    """A-26 e revisão do L2 (R-08): o claim leva o snapshot sem instruções; na primeira vez que roda, o job congela só as das
    tarefas dele (a ata, nunca as do chat) e as usa até o fim: trocadas depois nas Configurações, a nova tentativa segue com
    o texto congelado. A chamada e a ata levam `+u<hash>` dele."""
    mid = _ready_for_minutes(platform, store)
    assert {c["prompt_version"] for c in store.llm_calls.list_for_meeting(mid)} <= {"identify/1.1", "title/1.3", "revise/1.1"}
    first = f"Ata do comitê em {{idioma}}. {MARKER_TEXT}"
    store.set_setting("prompt.minutes", first)
    store.set_setting("prompt.chat", "Meu chat.")
    _ask(store, mid, ("minutes",))
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    assert "prompts" not in json.loads(job["config_json"]) and MARKER_TEXT not in job["config_json"]
    fake = FakeProvider()
    fake.fail_next(TransientError("rede caiu"), LlmTask.MINUTES)
    assert llm_steps.LlmJobRunner(store, job, provider=fake, heartbeat_thread=False).run() == JobStatus.ERROR
    assert json.loads(store.jobs.get(int(job["id"]))["config_json"])["prompts"] == {"minutes": first}   # só a da ata
    store.set_setting("prompt.minutes", "Outro texto, depois do job pegar o dele.")             # tarde: o job já congelou
    assert store.jobs.retry(int(job["id"]))                                                    # nova tentativa, sem zerar
    runner, status, again = _run_llm(store)
    assert runner.job_id == int(job["id"]) and status == JobStatus.DONE
    sent = again.calls(LlmTask.MINUTES)[0].messages[0].text
    assert sent.startswith(f"Ata do comitê em Brazilian Portuguese. {MARKER_TEXT} {prompts.minutes.CONTRACT}") and "Outro texto" not in sent
    version = f"minutes/1.2+u{hashlib.sha256(first.encode()).hexdigest()[:6]}"
    calls = [c for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "minutes"]
    assert [c["prompt_version"] for c in calls] == [version, version]                         # a falha e a que deu certo
    assert store.summaries.current(mid, SummaryKind.MINUTES)["prompt_version"] == version


@pytest.mark.usefixtures("no_summary_gate")
def test_give_back_and_a_dead_worker_keep_the_frozen_text_and_the_queue_retry_takes_the_new(platform, store):
    """R-08: devolvido à fila (falha fora do runner) ou com o worker morto (heartbeat vencido), o job relê o texto congelado;
    o "Tentar de novo" da fila zera o snapshot e o próximo claim congela o texto de agora."""
    mid = _ready_for_minutes(platform, store)
    store.set_setting("prompt.minutes", "Texto A.")
    _ask(store, mid, ("minutes",))
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    assert LlmJobConfig.for_job(store, job, ["minutes"]).prompts == {"minutes": "Texto A."}   # o runner, ao começar
    store.set_setting("prompt.minutes", "Texto B.")
    assert store.jobs.give_back(int(job["id"]), "falhou fora do runner")
    back = store.jobs.claim(pid=3, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    assert back["id"] == job["id"] and LlmJobConfig.for_job(store, back, ["minutes"]).prompts == {"minutes": "Texto A."}
    dead = store.jobs.claim(pid=4, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",), stale_s=-60)
    assert dead["id"] == job["id"] and LlmJobConfig.for_job(store, dead, ["minutes"]).prompts == {"minutes": "Texto A."}
    store.jobs.finish(int(job["id"]), JobStatus.ERROR, error="x")
    assert store.jobs.retry(int(job["id"]), reset_config=True)                                 # o "Tentar de novo" da fila
    fresh = store.jobs.claim(pid=5, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    assert LlmJobConfig.for_job(store, fresh, ["minutes"]).prompts == {"minutes": "Texto B."}


def test_index_jobs_and_the_claim_snapshot_carry_no_instructions(platform, store):
    """R-08 (minimização): o snapshot do worker leve, que também pega os `index`, não leva nenhuma instrução."""
    from listener.model import JobKind
    from listener.pipeline import worker

    _provider_ready(store)
    store.set_setting("prompt.minutes", f"Ata. {MARKER_TEXT}")
    snap = worker.llm_snapshot(store)
    assert "prompts" not in json.loads(snap) and MARKER_TEXT not in snap
    mid = store.create_meeting("Comitê", None, None, None)
    store.jobs.enqueue(mid, JobKind.INDEX)
    job = store.jobs.claim(pid=2, config_json=snap, kinds=("index",))
    assert job is not None and MARKER_TEXT not in str(job["config_json"])
    assert LlmJobConfig.from_store(store).prompts == {"minutes": f"Ata. {MARKER_TEXT}"}         # fora do job, a leitura vale


def test_the_version_hashes_the_stripped_text():
    mark = prompt_settings.text_hash("Ata objetiva.")
    assert prompts.prompt_version(LlmTask.MINUTES, "  Ata objetiva.\n\t") == f"minutes/1.2+u{mark}"
    assert LlmJobConfig(prompts={"minutes": "\nAta objetiva.  "}).version(LlmTask.MINUTES) == f"minutes/1.2+u{mark}"


@pytest.mark.usefixtures("no_summary_gate")
def test_the_log_never_carries_the_prompt_text(platform, store, caplog):
    mid = _ready_for_minutes(platform, store)
    with caplog.at_level(logging.DEBUG, logger="listener"):
        prompt_settings.save(store, "minutes", f"Ata. {MARKER_TEXT}", prompts.default_instructions(LlmTask.MINUTES))
        _ask(store, mid, ("minutes",))
        _runner, status, _fake = _run_llm(store)
        prompt_settings.restore(store, ["minutes"])
    assert status == JobStatus.DONE
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert MARKER_TEXT not in text and "Ata. " not in text
    assert f"(instruções u{prompt_settings.text_hash(f'Ata. {MARKER_TEXT}')})" in text and "prompt.minutes: (instruções u" in text


def test_chat_and_glossary_calls_carry_the_mark(platform, store, monkeypatch):
    from listener.llm import chat_task, glossary_task

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.set_setting("prompt.chat", "Responda em tópicos, em {idioma}.")
    store.set_setting("prompt.glossary", "Só siglas.")
    answer = chat_task.run_chat(store, mid, "Quem falou?")
    req, _resp = store.llm_calls.payloads(answer.call_id)
    assert req[0]["text"].startswith(f"Responda em tópicos, em Brazilian Portuguese. {prompts.chat.CONTRACT}")
    mark = prompt_settings.text_hash("Responda em tópicos, em {idioma}.")
    assert store.llm_calls.get(answer.call_id)["prompt_version"] == f"chat/1.3+u{mark}"
    glossary = glossary_task.request_glossary(store, "Comitê", "Ana")
    assert store.llm_calls.get(glossary.call_id)["prompt_version"] == f"glossary/1.0+u{prompt_settings.text_hash('Só siglas.')}"
    sent, _resp = store.llm_calls.payloads(glossary.call_id)
    assert sent[0]["text"].startswith(f"Só siglas. {prompts.glossary.CONTRACT}")                # o texto vai junto da marca


def test_the_audio_transcriber_gets_the_custom_instructions(store, monkeypatch):
    from listener.pipeline import llm_audio

    store.providers.update_provider("gemini", api_key="AIza-chave-de-teste", fast_model="fake-fast")
    store.set_setting("prompt.transcribe", "Transcreva em {idioma}, sem cortes.")
    t = llm_audio.llm_transcriber(store)
    assert t.instructions == "Transcreva em {idioma}, sem cortes."
    msgs = prompts.transcribe.build(pc.CASES["transcribe/bare"][2], t.instructions)
    assert msgs[0].text == f"Transcreva em {ORIGINAL_LANGUAGE}, sem cortes. {prompts.transcribe.CONTRACT}"


# ---------------------------------------------------------------- A-29: "falhou" fora do esquema com instruções suas
@pytest.mark.usefixtures("no_summary_gate")
def test_off_schema_failure_with_custom_instructions_names_the_tasks_to_restore(platform, store):
    mid = _ready_for_minutes(platform, store)
    store.set_setting("prompt.minutes", "Ata em versos.")
    _ask(store, mid, ("minutes",))
    fake = FakeProvider()
    fake.fail_next(BadResponseError(f"{OFF_SCHEMA} após reenvio: $.decisions: esperava lista"), LlmTask.MINUTES)
    _runner, status, _ = _run_llm(store, fake)
    assert status == JobStatus.ERROR
    rs = llm_requests.request_states(store, mid)[Species.MINUTES]
    assert rs.state == State.FAILED and OFF_SCHEMA in rs.error and rs.custom == ("minutes",) and rs.job_id is not None
    assert llm_requests.request_states(store, mid)[Species.SUMMARY].custom == ()
    assert prompt_settings.restore(store, ["minutes"]) == ["minutes"]
    after = llm_requests.request_states(store, mid)[Species.MINUTES]
    assert after.state == State.FAILED and after.custom == ()                                 # restaurado: nada a oferecer


@pytest.mark.usefixtures("no_summary_gate")
def test_off_schema_in_the_default_minutes_does_not_blame_the_custom_executive(platform, store):
    """Revisão do L2 (R-01): o resumo pedido sem ata leva a ata junto; a ata (prompt do app) sai fora do esquema e o executivo
    (personalizado) nem chama a LLM. Nenhuma das duas espécies culpa as instruções do executivo."""
    mid = _ready_for_minutes(platform, store)
    store.set_setting("prompt.executive", "Resumo para o conselho.")
    _ask(store, mid, ("executive", "pre_summary"))
    fake = FakeProvider()
    for _ in range(5):
        fake.fail_next(BadResponseError(f"{OFF_SCHEMA} após reenvio: $.decisions: esperava lista"), LlmTask.MINUTES)
    assert _run_llm(store, fake)[1] == JobStatus.ERROR and fake.calls(LlmTask.EXECUTIVE) == []
    states = llm_requests.request_states(store, mid)
    assert states[Species.SUMMARY].state == State.FAILED and OFF_SCHEMA in states[Species.SUMMARY].error
    assert states[Species.SUMMARY].custom == () and states[Species.MINUTES].custom == ()


@pytest.mark.usefixtures("no_summary_gate")
def test_new_instructions_after_the_failure_are_not_blamed(platform, store):
    """R-02: a ata falhou com um texto; o usuário trocou por outro, ainda não usado: o novo não leva a culpa (restaurar o
    apagaria). De volta ao texto que falhou, a culpa volta."""
    mid = _ready_for_minutes(platform, store)
    store.set_setting("prompt.minutes", "Ata em versos.")
    _ask(store, mid, ("minutes",))
    fake = FakeProvider()
    fake.fail_next(BadResponseError(f"{OFF_SCHEMA} após reenvio: $.decisions: esperava lista"), LlmTask.MINUTES)
    assert _run_llm(store, fake)[1] == JobStatus.ERROR
    store.set_setting("prompt.minutes", "Ata objetiva, em tópicos.")
    assert llm_requests.request_states(store, mid)[Species.MINUTES].custom == ()
    store.set_setting("prompt.minutes", "  Ata em versos.\n")
    assert llm_requests.request_states(store, mid)[Species.MINUTES].custom == ("minutes",)


@pytest.mark.usefixtures("no_summary_gate")
@pytest.mark.parametrize("case", ["padrão", "outra falha"])
def test_failures_without_custom_instructions_or_off_schema_offer_nothing(platform, store, case):
    mid = _ready_for_minutes(platform, store)
    if case == "outra falha":
        store.set_setting("prompt.minutes", "Ata em versos.")
    _ask(store, mid, ("minutes",))
    fake = FakeProvider()
    error = BadResponseError(f"{OFF_SCHEMA}: $.x") if case == "padrão" else TransientError("tempo esgotado")
    for _ in range(5):
        fake.fail_next(error, LlmTask.MINUTES)
    _run_llm(store, fake)
    rs = llm_requests.request_states(store, mid)[Species.MINUTES]
    assert rs.state == State.FAILED and rs.custom == ()


def test_the_retrying_provider_error_starts_with_the_marker_the_screen_reads():
    import inspect

    from listener.llm import registry

    source = inspect.getsource(registry.RetryingProvider.complete)
    assert source.count("{OFF_SCHEMA}") == 2 and OFF_SCHEMA == "resposta fora do esquema"


def test_new_transcription_instructions_rebuild_the_worker_transcriber(store):
    """O worker pesado guarda o transcritor enquanto a chave (provedor, modelo) não muda e só sai ocioso depois de 60 s:
    instruções novas da transcrição pelo áudio mudam a chave (pelo hash, nunca o texto), e o próximo job usa as novas."""
    from listener.pipeline import worker
    from listener.pipeline.types import JobConfig

    store.providers.update_provider("gemini", api_key="AIza-chave-de-teste", fast_model="fake-fast")
    cfg = JobConfig(transcriber="llm")
    assert worker.llm_transcriber_key(store, cfg) == ("gemini", "fake-fast")
    store.set_setting("prompt.transcribe", f"Transcreva tudo. {MARKER_TEXT}")
    key = worker.llm_transcriber_key(store, cfg)
    assert key == ("gemini", f"fake-fast+u{prompt_settings.text_hash(f'Transcreva tudo. {MARKER_TEXT}')}")
    assert MARKER_TEXT not in str(key) and worker.llm_transcriber_key(store, JobConfig()) == ("", "")
