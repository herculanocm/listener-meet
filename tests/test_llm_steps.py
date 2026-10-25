"""`LlmJobRunner` de ponta a ponta sobre uma reunião do pipeline falso (F3 L3): encadeamento pelo job da F2, CP11 nos pedidos
(só pendentes), evidência conferida, ata com ações e Markdown do projeto, título, resumos, transcrição revisada com
proteção, falha parcial e `retry` só das faltantes, `current_job_id` inalterado, custo em `llm_calls`, `config_json` sem
chave, hierárquico, semáforo, cancelamento, lease ignorada e pausa manual respeitada, reprocessamento com transporte,
`decision_source`, worker leve em processo com `LISTENER_LLM=fake`."""
from __future__ import annotations

import json

import pytest

from listener.capture.recorder import Recorder, SourceSpec
from listener.llm.base import RequestTooLargeError, TransientError
from listener.llm.fake import FakeProvider
from listener.llm.jobconfig import LlmJobConfig
from listener.llm.types import LlmTask
from listener.model import ClusterStatus, DecisionSource, JobKind, JobStatus, JobStep, SummaryKind
from listener.pipeline import llm_steps, review
from listener.pipeline.llm_steps import LlmJobRunner
from listener.pipeline.runner import JobRunner
from listener.store import Store
from tests.conftest import make_config
from tests.fake_pipeline import FakeDeps
from tests.test_runner import CFG

SCRIPT = {"R": [(0.2, 1.4, "bom dia Ana tudo bem"), (1.6, 2.9, "vamos fechar o orçamento de 2027 hoje")],
          "L": [(0.3, 1.2, "oi gente"), (2.0, 2.8, "sim claro vamos")]}
KEY = "AIzaSy-chave-de-teste-ABCDEFGH12345678"


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _record(platform, store: Store) -> int:
    mid = store.create_meeting("Planejamento 2027", "meeting", None, "Ana, Bia")
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(1.5)
    rec.switch_source(SourceSpec("system", label="Sistema"))
    platform.pump(1.5)
    rec.mark("ponto importante")
    rec.stop()
    return mid


def _pipeline(store: Store, mid: int, kind: JobKind = JobKind.MEETING) -> int:
    store.jobs.enqueue(mid, kind)
    job = store.jobs.claim(pid=1, kinds=("meeting", "reprocess"))
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


def _provider_ready(store: Store) -> None:
    store.providers.update_provider("gemini", api_key=KEY, fast_model="fake-fast", strong_model="fake-strong")
    store.providers.set_price("gemini", "fake-fast", 0.30, 2.50, 0.075)
    store.providers.set_price("gemini", "fake-strong", 1.25, 10.0, 0.31)


def _run_llm(store: Store, fake: FakeProvider | None = None, **kw) -> tuple[LlmJobRunner, JobStatus, FakeProvider]:
    fake = fake or FakeProvider()
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    assert job is not None, "nenhum job llm pendente"
    kw.setdefault("heartbeat_thread", False)
    runner = LlmJobRunner(store, job, provider=fake, **kw)
    return runner, runner.run(), fake


def test_pipeline_enqueues_llm_job_and_runner_writes_everything_with_parent_job_id(platform, store):
    store.upsert_me("Eu", role="Gerente")
    ana = store.people.create_person("Ana", role="CFO")
    mid = _record(platform, store)
    assert llm_steps.enqueue_llm_job(store, mid, ["minutes"]) is None                         # sem provedor com chave: nada (comp. 6)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    jobs = store.jobs.for_meeting(mid)
    llm_job = next(j for j in jobs if j["kind"] == "llm")
    params = json.loads(llm_job["params_json"])
    assert params == {"tasks": ["identify", "minutes", "executive", "pre_summary", "title", "revise"], "parent_job_id": parent,
                      "auto": True}
    assert store.segments.current_job_id(mid) == parent
    runner, status, fake = _run_llm(store)
    assert status == JobStatus.DONE, store.jobs.get(llm_job["id"])["error"]
    row = store.jobs.get(llm_job["id"])
    cfg = json.loads(row["config_json"])
    assert cfg["provider_id"] == "gemini" and cfg["fast_model"] == "fake-fast" and KEY not in row["config_json"] and "api_key" not in cfg
    assert row["progress"] == 1.0 and store.segments.current_job_id(mid) == parent                   # F3A-04
    # CP11: só vozes pendentes nos pedidos de identidade; o L do usuário e nenhum `auto`/`confirmado` aparecem
    ident = fake.calls(LlmTask.IDENTIFY)
    prompt = ident[0].messages[1].text
    assert len(ident) == 1 and "Unresolved speakers: SPK_0, SPK_1" in prompt and "Eu (Gerente)" in prompt
    clusters = {c["cluster"]: c for c in store.segments.list_clusters(mid, parent)}
    spk0 = clusters["SPK_0"]
    assert spk0["llm_person_id"] == ana and spk0["llm_name"] == "Ana" and spk0["llm_confidence"] == 0.8
    ev = json.loads(clusters["SPK_0"]["llm_evidence_json"])
    assert ev[0]["quote"] == "Ana" and store.segments.get_cluster(clusters["SPK_0"]["id"])["status"] == "sem_nome"   # status intacto
    assert clusters["SPK_1"]["llm_name"] is None and clusters["L"]["llm_name"] is None
    # ata, executivo, pré-resumo com o job_id do pai; ações com evidência e marca "sem evidência"
    minutes = store.summaries.current(mid, SummaryKind.MINUTES)
    assert minutes["job_id"] == parent and minutes["provider"] == "gemini" and minutes["prompt_version"] == "minutes/1.0"
    assert minutes["call_id"] and minutes["language"] == "pt-BR" and minutes["text_llm"].startswith("## Contexto")
    assert "## Decisões" in minutes["text_llm"] and "[00:00]" in minutes["text_llm"] and "sem evidência" in minutes["text_llm"]
    assert "**ponto importante**" in minutes["text_llm"]                                              # destaque com a nota do marcador
    actions = store.actions.list_for_meeting(mid)
    assert len(actions) == 2 and actions[0]["summary_id"] == minutes["id"] and actions[0]["job_id"] == parent
    evidenced = next(a for a in actions if a["segment_id"] is not None)
    assert evidenced["due"] == "2026-10-30" and evidenced["t_ms"] is not None and evidenced["status"] == "aberta"
    assert next(a for a in actions if a["segment_id"] is None)["text"] == "Ação sem evidência"
    assert store.summaries.current(mid, SummaryKind.EXECUTIVE)["text_llm"].startswith("Resumo executivo simulado")
    assert store.summaries.current(mid, SummaryKind.PRE_SUMMARY)["text_llm"].startswith("Pré-resumo simulado")
    m = store.get_meeting(mid)
    assert m["title_llm"] and m["title"] == "Planejamento 2027"                                      # título do usuário intacto
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'meeting' AND field = 'title_llm'")[0][0] == 1
    # transcrição revisada só em text_llm; text original intacto (CP3)
    segs = store.segments.list_segments(mid, parent)
    assert all(s["text_llm"] and s["text_llm"][0].isupper() and s["text_llm"].endswith(".") for s in segs)
    assert next(s["text"] for s in segs) == "bom dia Ana tudo bem"
    # custo e chamadas com o job do pai; payloads comprimidos; eventos sem texto de prompt
    calls = store.llm_calls.list_for_meeting(mid)
    assert {c["task"] for c in calls} == {"identify", "minutes", "executive", "pre_summary", "title", "revise"}
    assert all(c["job_id"] == parent and c["status"] == "ok" and c["cost_usd"] > 0 for c in calls)
    total, missing = store.llm_calls.cost_for_meeting(mid)
    assert total == pytest.approx(runner.cost_usd) and missing == 0 and runner.calls == len(calls)
    req, resp = store.llm_calls.payloads(calls[0]["id"])
    assert req[0]["role"] == "system" and resp["data"]
    done = next(e for e in store.list_events(mid) if e["type"] == "job_done" and e["data"].get("kind") == "llm")
    assert done["data"]["calls"] == len(calls) and "cost_usd" in done["data"]
    assert all("bom dia" not in json.dumps(e["data"]) for e in store.list_events(mid))
    assert store._q("SELECT COUNT(*) FROM job_artifacts WHERE job_id = ?", (llm_job["id"],))[0][0] == 0
    assert fake.closed is False                                                   # provedor injetado: quem fecha é o teste
    timings = json.loads(row["timings_json"])
    assert timings["llm"]["calls"] == len(calls) and set(timings["llm"]["done"]) == set(params["tasks"])


def test_partial_failure_keeps_done_tasks_and_retry_redoes_only_the_missing(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    fake = FakeProvider()
    for _ in range(3):
        fake.fail_next(TransientError("sem rede"), LlmTask.MINUTES)
    from listener.llm.registry import RetryingProvider

    retrying = RetryingProvider(fake, sleep=lambda s: __import__("asyncio").sleep(0))
    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    status = LlmJobRunner(store, job, provider=retrying, heartbeat_thread=False).run()
    row = store.jobs.get(llm_job["id"])
    assert status == JobStatus.ERROR and row["attempts"] == 1
    assert row["error"].startswith("ata, resumo executivo e pré-resumo falharam: ") and "sem rede" in row["error"]
    assert store.summaries.current(mid, SummaryKind.MINUTES) is None and store.get_meeting(mid)["title_llm"]   # título: pelas falas
    assert all(s["text_llm"] for s in store.segments.list_segments(mid))                                     # revisão ficou
    assert set(store.jobs.artifact_indexes(llm_job["id"], JobStep.REVISE)) == {0}                           # marcas das prontas
    errors = [c for c in store.llm_calls.list_for_meeting(mid) if c["status"] == "erro"]
    assert len(errors) == 1 and errors[0]["task"] == "minutes" and "sem rede" in errors[0]["error"]
    before = len(fake.requests)
    assert store.jobs.retry(int(llm_job["id"]), reset_config=True)
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    status = LlmJobRunner(store, job, provider=retrying, heartbeat_thread=False).run()
    assert status == JobStatus.DONE
    redone = [r.task for r in fake.requests[before:]]
    assert sorted(redone) == ["executive", "minutes", "pre_summary"]                                        # só as faltantes
    assert store.summaries.current(mid, SummaryKind.MINUTES) and store.summaries.current(mid, SummaryKind.EXECUTIVE)
    assert store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0


def test_hierarchical_minutes_by_window_and_on_request_too_large(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    llm_steps_tasks = ["minutes", "identify"]
    store.jobs.request_cancel(next(j["id"] for j in store.jobs.for_meeting(mid) if j["kind"] == "llm"))
    assert llm_steps.enqueue_llm_job(store, mid, llm_steps_tasks)
    store.providers.update_provider("gemini", models_json=[{"id": "fake-strong", "input_limit": 600}])   # cache de list_models()
    small = FakeProvider(input_limit=600)                                                                  # janela minúscula: blocos
    runner, status, _ = _run_llm(store, small)
    assert status == JobStatus.DONE, store.jobs.get(runner.job_id)["error"]
    calls = [c for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "minutes"]
    assert len(calls) >= 2 and all(c["strategy"].startswith("map_reduce:") for c in calls)
    assert store.summaries.current(mid, SummaryKind.MINUTES)["text_llm"].count("Decidido:") >= 1
    # "request too large" no caminho direto também aciona o hierárquico
    assert llm_steps.enqueue_llm_job(store, mid, ["minutes"])
    store.providers.update_provider("gemini", models_json=[])
    big = FakeProvider()
    big.fail_next(RequestTooLargeError("request too large"), LlmTask.MINUTES)
    runner, status, _ = _run_llm(store, big)
    assert status == JobStatus.DONE
    strategies = [c["strategy"] for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "minutes"][len(calls):]
    assert strategies[0] == "direct" and all(s.startswith("map_reduce") for s in strategies[1:]) and len(strategies) >= 2
    assert store.summaries.get(store.summaries.current(mid, SummaryKind.MINUTES)["id"])["status"] == "atual"
    assert store._q("SELECT COUNT(*) FROM summaries WHERE kind = 'minutes' AND status = 'superada'")[0][0] == 1  # gerar de novo marca


def test_cost_alert_skips_optional_tasks_and_semaphore_limits_parallelism(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    store.set_setting("llm.cost_alert_usd", 0.0000001)
    store.set_setting("llm.max_parallel", 1)
    _pipeline(store, mid)
    fake = FakeProvider(delay_s=0.002)
    runner, status, _ = _run_llm(store, fake)
    assert status == JobStatus.DONE
    timings = json.loads(store.jobs.get(runner.job_id)["timings_json"])["llm"]
    assert set(timings["skipped"]) == {"executive", "revise"} and "acima do alerta" in timings["skipped"]["executive"]
    assert timings["estimated_usd"] > 0 and store.summaries.current(mid, SummaryKind.EXECUTIVE) is None
    assert all(not s["text_llm"] for s in store.segments.list_segments(mid)) and fake.peak_parallel == 1
    # com o alerta alto e 2 em paralelo, as três tarefas do grupo andam juntas até o limite do semáforo
    store.set_setting("llm.cost_alert_usd", 100)
    store.set_setting("llm.max_parallel", 2)
    assert llm_steps.enqueue_llm_job(store, mid, ["executive", "pre_summary", "title"])
    fake2 = FakeProvider(delay_s=0.005)
    _, status, _ = _run_llm(store, fake2)
    assert status == JobStatus.DONE and fake2.peak_parallel == 2
    # "gerar de novo" por fora do alerta: job de uma tarefa não é automático e não pula nada
    assert llm_steps.enqueue_llm_job(store, mid, ["executive"])
    store.set_setting("llm.cost_alert_usd", 0.0000001)
    _, status, _ = _run_llm(store, FakeProvider())
    assert status == JobStatus.DONE and store.summaries.current(mid, SummaryKind.EXECUTIVE) is not None


def test_cancel_manual_pause_and_ignored_lease(platform, store):
    from datetime import datetime, timedelta

    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    store.set_setting("pipeline.paused_until", (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="milliseconds"))
    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    store.jobs.request_cancel(int(llm_job["id"]))                                                          # rodando: cooperativo
    fake = FakeProvider()
    status = LlmJobRunner(store, job, provider=fake, heartbeat_thread=False, sleep=lambda s: pytest.fail("não devia esperar a lease")).run()
    assert status == JobStatus.CANCELLED and fake.requests == [] and store.jobs.get(llm_job["id"])["status"] == "cancelado"
    assert "job_cancel" in [e["type"] for e in store.list_events(mid)]
    assert llm_steps.enqueue_llm_job(store, mid, ["title"])
    polls: list[int] = []

    def release(seconds: float) -> None:
        polls.append(1)
        store.set_setting("pipeline.paused_manual", False)

    store.set_setting("pipeline.paused_manual", True)
    _runner, status, fake = _run_llm(store, sleep=release)
    assert status == JobStatus.DONE and polls == [1] and len(fake.requests) == 1                         # lease ignorada, pausa respeitada
    store.set_setting("pipeline.paused_until", "")


def test_reprocess_keeps_minutes_moves_text_llm_and_relinks_actions(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    first = _pipeline(store, mid)
    _, status, _ = _run_llm(store)
    assert status == JobStatus.DONE
    old_minutes = store.summaries.current(mid, SummaryKind.MINUTES)
    old_action = next(a for a in store.actions.list_for_meeting(mid) if a["segment_id"] is not None)
    old_seg_ids = {s["id"] for s in store.segments.list_segments(mid)}
    rep = _pipeline(store, mid, JobKind.REPROCESS)
    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente")
    params = json.loads(llm_job["params_json"])
    assert params["tasks"] == ["identify", "revise"] and params["parent_job_id"] == rep
    segs = store.segments.list_segments(mid)
    assert {s["job_id"] for s in segs} == {rep} and not ({s["id"] for s in segs} & old_seg_ids)
    assert all(s["text_llm"] for s in segs)                                                               # transportada (F3A-09)
    action = store.actions.get(old_action["id"])
    assert action["segment_id"] in {s["id"] for s in segs} and action["segment_id"] != old_action["segment_id"]
    assert store.summaries.current(mid, SummaryKind.MINUTES)["id"] == old_minutes["id"]                   # ata antiga fica (P7)
    assert store.summaries.stale_after_reprocess(mid, rep) == ["minutes", "executive", "pre_summary"]
    _runner, status, fake = _run_llm(store)
    assert status == JobStatus.DONE and [r.task for r in fake.requests] == ["identify"]                     # revise: nada sem text_llm
    assert store.summaries.current(mid, SummaryKind.MINUTES)["job_id"] == first


def test_decision_source_and_accept_llm(platform, store):
    store.upsert_me("Eu")
    ana = store.people.create_person("Ana")
    bia = store.people.create_person("Bia")
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    _, status, _ = _run_llm(store)
    assert status == JobStatus.DONE
    items = review.review_items(store, mid)
    with_llm = next(i for i in items if i.llm_name == "Ana")
    other = next(i for i in items if i.cluster_id != with_llm.cluster_id)
    assert with_llm.has_llm and with_llm.llm_person_id == ana and with_llm.llm_evidence[0][1] == "Ana"
    assert with_llm.summary().startswith("sem nome · LLM: Ana (0,80)") and "aos 00:00" in with_llm.summary()
    assert "Enter ou L aceita a LLM" in with_llm.summary() and not other.has_llm
    review.accept_llm(store, with_llm.cluster_id)                                                           # tecla L
    c = store.segments.get_cluster(with_llm.cluster_id)
    assert c["status"] == "confirmado" and c["person_id"] == ana and c["decision_source"] == DecisionSource.USER_LLM
    assert c["llm_name"] == "Ana"                                                                            # a sugestão fica (mede o G3)
    with pytest.raises(ValueError, match="sugestão da LLM"):
        review.accept_llm(store, other.cluster_id)
    review.confirm(store, other.cluster_id, bia)
    assert store.segments.get_cluster(other.cluster_id)["decision_source"] == DecisionSource.USER
    # pyannote sugeriu (ambíguo) e o usuário deu Enter -> usuario_pyannote; escolheu outra -> usuario_outro
    store.segments.update_cluster(other.cluster_id, status=ClusterStatus.AMBIGUOUS, person_id=ana)
    review.confirm(store, other.cluster_id)
    assert store.segments.get_cluster(other.cluster_id)["decision_source"] == DecisionSource.USER_PYANNOTE
    store.segments.update_cluster(other.cluster_id, status=ClusterStatus.AMBIGUOUS, person_id=ana)
    review.confirm(store, other.cluster_id, bia)
    assert store.segments.get_cluster(other.cluster_id)["decision_source"] == DecisionSource.USER_OTHER
    # R3-08: card `sem_nome` com o "parecida com" do pyannote aceito -> usuario_pyannote
    store.segments.update_cluster(other.cluster_id, status=ClusterStatus.UNNAMED, person_id=ana)
    review.confirm(store, other.cluster_id, ana)
    assert store.segments.get_cluster(other.cluster_id)["decision_source"] == DecisionSource.USER_PYANNOTE
    # sem_nome só com sugestão da LLM por nome novo: Enter aceita e cria a pessoa
    store.segments.update_cluster(with_llm.cluster_id, status=ClusterStatus.UNNAMED, person_id=None)
    store.segments.set_llm_suggestion(with_llm.cluster_id, None, "Rodrigo", 0.7, "[]")
    pid = review.confirm(store, with_llm.cluster_id)
    assert store.people.get_person(pid)["name"] == "Rodrigo"
    assert store.segments.get_cluster(with_llm.cluster_id)["decision_source"] == DecisionSource.USER_LLM
    assert store.segments.current_job_id(mid) == parent


def test_light_worker_in_process_with_fake_provider(tmp_path, store, platform, monkeypatch):
    from listener import log as applog
    from listener.pipeline import worker

    monkeypatch.setenv("LISTENER_LLM", "fake")
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(llm_job["id"])
    assert row["status"] == "concluído", row["error"]
    assert json.loads(row["config_json"])["provider_id"] == "gemini" and KEY not in row["config_json"]
    assert store.summaries.current(mid, SummaryKind.MINUTES) is not None
    applog.flush()
    text = (tmp_path / "logs" / "listener-llm.log").read_text(encoding="utf-8")
    assert KEY not in text and f"job llm {llm_job['id']} concluído" in text


def test_pure_helpers_blocks_revision_guard_and_config():
    from listener.llm.prompts.common import Line

    lines = tuple(Line(i, i * 1000, "A" if i % 4 else "B", "palavra " * 10, "SPK_0" if i % 4 else "SPK_1") for i in range(40))
    blocks = llm_steps.split_blocks(lines, 60)
    assert len(blocks) >= 3 and sum(len(b) for b in blocks) == 40 and all(b[-1].speaker != b[0].speaker or len(b) <= 4 for b in blocks[:1])
    assert llm_steps.split_blocks((), 10) == [()]
    near = llm_steps.neighbourhood(lines, {"SPK_1"}, radius=1)
    assert len(near) < 40 and all(any(ln.label == "SPK_1" for ln in near[max(0, i - 1):i + 2]) for i in range(len(near)))
    assert llm_steps.accept_revision("bom dia pessoal", "Bom dia, pessoal.") and not llm_steps.accept_revision("bom dia pessoal", "")
    assert not llm_steps.accept_revision("fechou em 12 de maio", "fechou em 2 de maio")                   # dígitos mudaram
    assert not llm_steps.accept_revision("vamos fechar o orçamento hoje com a equipe", "we will close the budget today with the team")
    assert llm_steps.accept_revision("ok", "Ok.") and not llm_steps.accept_revision("sim claro vamos", "texto totalmente diferente aqui")
    people = {1: {"name": "Ana Souza"}, 2: {"name": "Rodrigo Lima"}, 3: {"name": "Ana Paula"}}
    assert llm_steps.match_person("rodrigo lima", people) == 2 and llm_steps.match_person("Rodrigo", people) == 2
    assert llm_steps.match_person("Ana", people) is None and llm_steps.match_person("", people) is None
    assert llm_steps.resolve_language("auto", "pt") == "pt-BR" and llm_steps.resolve_language("auto", "en") == "en"
    assert llm_steps.resolve_language("es", "pt") == "es" and llm_steps.resolve_language("auto", None) == "pt-BR"
    raw = '{"provider_id": "gemini", "provider_kind": "gemini", "fast_model": "f", "api_key": "x", "params": {"key": "y", "top_p": 1}}'
    cfg = LlmJobConfig.from_json(raw)
    assert cfg.ready and cfg.params == {"top_p": 1} and "api_key" not in cfg.to_json() and LlmJobConfig.from_json("lixo") == LlmJobConfig()
    assert cfg.model_for(strong=True) == "f" and LlmJobConfig.from_json(cfg.to_json()) == cfg


def test_test_job_via_worker_records_canary_call(platform, store):
    """"Testar pelo worker" (F3 item 12): job `llm` de tarefa `test` sem job pai; `llm_calls` ganha a linha do canário."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    assert llm_steps.enqueue_test_job(store) is not None
    runner, status, fake = _run_llm(store)
    assert status == JobStatus.DONE and [r.task for r in fake.requests] == [] and runner.parent_job_id == 0
    calls = store.llm_calls.list_for_meeting(mid)
    assert len(calls) == 1 and calls[0]["task"] == "test" and calls[0]["strategy"] == "canary" and calls[0]["status"] == "ok"
    assert llm_steps.enqueue_test_job(store, mid)
    bad = FakeProvider()
    bad.fail_next(TransientError("sem rede"), LlmTask.TEST)
    runner, status, _ = _run_llm(store, bad)
    assert status == JobStatus.ERROR and "teste de conexão falhou: sem rede" in store.jobs.get(runner.job_id)["error"]
    with Store(store.base_dir) as empty:
        empty._x("DELETE FROM meetings")
        assert llm_steps.enqueue_test_job(empty) is None                                                   # a fila é por reunião


# ---------------------------------------------------------------- F3.1 (correções da revisão do diff da F3)
def test_retry_after_reprocess_fails_permanently_without_calls(platform, store):
    """R3-02: um job `llm` que falhou e é retentado depois de um reprocessamento rodaria sobre segmentos apagados; termina em
    erro permanente antes de qualquer chamada paga, sem contar tentativa."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    first = _pipeline(store, mid)
    flaky = FakeProvider()
    flaky.fail_next(TransientError("sem rede"), LlmTask.IDENTIFY)
    runner, status, _ = _run_llm(store, flaky)
    assert status == JobStatus.ERROR and runner.parent_job_id == first
    old_llm = runner.job_id
    rep = _pipeline(store, mid, JobKind.REPROCESS)
    pending = [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente"]
    assert len(pending) == 1 and json.loads(pending[0]["params_json"])["parent_job_id"] == rep
    assert store.jobs.retry(old_llm, reset_config=True)
    calls_before = len(store.llm_calls.list_for_meeting(mid))
    fake = FakeProvider()
    runner, status, _ = _run_llm(store, fake)                                            # o mais antigo sai primeiro da fila
    row = store.jobs.get(old_llm)
    assert runner.job_id == old_llm and status == JobStatus.ERROR and fake.requests == []
    assert llm_steps.STALE_TRANSCRIPT in row["error"] and row["attempts"] == 0                          # permanente: não conta
    assert len(store.llm_calls.list_for_meeting(mid)) == calls_before                                  # nenhuma chamada paga
    _, status, fake = _run_llm(store)                                                                  # o job do pai novo segue
    assert status == JobStatus.DONE and {r.task for r in fake.requests} == {"identify"}


def test_meeting_without_speech_gets_no_llm_job_and_manual_job_fails_without_calls(platform, store):
    """R3-02: reunião sem fala não enfileira job `llm`; um "gerar de novo" sobre ela termina em erro permanente sem chamadas."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    store.jobs.enqueue(mid, JobKind.MEETING)
    job = store.jobs.claim(pid=1, kinds=("meeting", "reprocess"))
    assert JobRunner(store, FakeDeps({"R": [], "L": []}), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    assert store.segments.count_segments(mid) == 0 and not [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm"]
    assert llm_steps.enqueue_llm_job(store, mid, ["minutes"]) is not None                               # manual: a fila aceita
    runner, status, fake = _run_llm(store)
    row = store.jobs.get(runner.job_id)
    assert status == JobStatus.ERROR and fake.requests == [] and llm_steps.STALE_TRANSCRIPT in row["error"]
    assert row["attempts"] == 0 and store.summaries.current(mid, SummaryKind.MINUTES) is None
    assert store.llm_calls.list_for_meeting(mid) == []


def test_two_pending_llm_jobs_of_one_meeting_run_in_order(platform, store):
    """R3-14: o job automático e um "gerar de novo" pendentes da mesma reunião rodam um depois do outro, sem se atrapalhar."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    regen = llm_steps.enqueue_llm_job(store, mid, ["title"])
    pending = [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente"]
    assert len(pending) == 2
    first, status1, fake1 = _run_llm(store)
    second, status2, fake2 = _run_llm(store)
    assert (status1, status2) == (JobStatus.DONE, JobStatus.DONE) and first.job_id < second.job_id == regen
    assert [r.task for r in fake2.requests] == ["title"] and len(fake1.requests) >= 6
    assert first.parent_job_id == second.parent_job_id == parent
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'meeting' AND field = 'title_llm'")[0][0] == 2
    assert store.summaries.current(mid, SummaryKind.MINUTES)["job_id"] == parent


def test_reprocess_cancels_stale_pending_llm_jobs_and_keeps_what_never_ran(platform, store):
    """R3-02: o reprocessamento cancela os jobs `llm` pendentes feitos para o pai antigo (o de teste fica) e o job novo leva,
    além de identidade e revisão (P7), o que a reunião ainda não tem: um job que nunca rodou não a deixa sem ata."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    stale = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    regen = llm_steps.enqueue_llm_job(store, mid, ["title"])
    test_job = llm_steps.enqueue_test_job(store, mid)
    rep = _pipeline(store, mid, JobKind.REPROCESS)
    assert store.jobs.get(stale["id"])["status"] == "cancelado" and store.jobs.get(regen)["status"] == "cancelado"
    assert store.jobs.get(test_job)["status"] == "pendente"
    new = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente" and j["id"] != test_job)
    params = json.loads(new["params_json"])
    assert params["parent_job_id"] == rep and params["tasks"] == ["identify", "minutes", "executive", "pre_summary", "title", "revise"]
    for _ in range(2):
        _, status, _fake = _run_llm(store)
        assert status == JobStatus.DONE
    assert store.summaries.current(mid, SummaryKind.MINUTES)["job_id"] == rep
    assert llm_steps.auto_tasks_for(store, mid, JobKind.REPROCESS) == ["identify", "revise"]          # agora tudo existe (P7)
    # "gerar de novo" pendente durante outro reprocessamento: a tarefa passa para o job novo, mesmo sem tarefas automáticas
    store.set_setting("llm.auto_tasks", False)
    manual = llm_steps.enqueue_llm_job(store, mid, ["executive"])
    rep2 = _pipeline(store, mid, JobKind.REPROCESS)
    assert store.jobs.get(manual)["status"] == "cancelado"
    moved = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente" and j["id"] != test_job)
    assert json.loads(moved["params_json"]) == {"tasks": ["executive"], "parent_job_id": rep2, "auto": False}


def test_hierarchical_minutes_resumes_from_saved_block_notes(platform, store):
    """R3-14: a nota do bloco já gravada (`MINUTES` idx 1) não é pedida de novo; só a redução roda, com a nota salva."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    store.jobs.request_cancel(next(j["id"] for j in store.jobs.for_meeting(mid) if j["kind"] == "llm"))
    job_id = llm_steps.enqueue_llm_job(store, mid, ["minutes"])
    store.providers.update_provider("gemini", models_json=[{"id": "fake-strong", "input_limit": 600}])   # força o hierárquico
    note = {"context": "NOTA-SALVA-DO-BLOCO", "decisions": [], "actions": [], "risks": [], "open_questions": [], "summary": ""}
    store.jobs.put_artifact(job_id, JobStep.MINUTES, 1, json.dumps(note).encode("utf-8"))
    runner, status, fake = _run_llm(store, FakeProvider(input_limit=600))
    assert runner.job_id == job_id and status == JobStatus.DONE, store.jobs.get(job_id)["error"]
    minutes = fake.calls(LlmTask.MINUTES)
    assert len(minutes) == 1 and "Block 1 notes" in minutes[0].messages[-1].text and "NOTA-SALVA-DO-BLOCO" in minutes[0].messages[-1].text
    assert store.summaries.current(mid, SummaryKind.MINUTES) is not None
    assert not store.jobs.artifact_indexes(job_id, JobStep.MINUTES)                                 # sucesso limpa


def test_identify_blocks_merge_by_best_confidence():
    """R3-14: no `identify` em blocos, cada voz fica com a sugestão de maior confiança entre os blocos."""
    from listener.llm.types import Completion, Usage

    def comp(*speakers: dict) -> Completion:
        return Completion("", {"speakers": list(speakers)}, Usage(), "m", 1)

    best: dict[str, dict] = {}
    LlmJobRunner._merge_identify(best, comp({"cluster": "SPK_0", "new_name": "Ana", "confidence": 0.4},
                                            {"cluster": "SPK_1", "new_name": "Bia", "confidence": 0.9}))
    LlmJobRunner._merge_identify(best, comp({"cluster": "SPK_0", "new_name": "Carla", "confidence": 0.7},
                                            {"cluster": "SPK_1", "new_name": "Dani", "confidence": 0.5}, {"cluster": "", "confidence": 1}))
    assert {k: v["new_name"] for k, v in best.items()} == {"SPK_0": "Carla", "SPK_1": "Bia"}


def test_controller_warns_once_per_session_without_provider(qapp, platform, store):
    """R3-13: job pesado concluído sem provedor com chave -> "ata não gerada" uma vez por sessão; reunião sem falas não avisa."""
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    mid = _record(platform, store)
    job = _pipeline(store, mid)
    assert llm_steps.auto_skip_reason(store, mid, job) == llm_steps.NO_PROVIDER
    assert llm_steps.auto_skip_reason(store, mid, job + 1000) == llm_steps.NO_SPEECH
    store.set_setting("llm.auto_tasks", False)
    assert llm_steps.auto_skip_reason(store, mid, job) is None
    store.set_setting("llm.auto_tasks", True)
    c = Controller(qapp, platform, store)
    c.tray = None
    notes: list[str] = []
    c.notify = notes.append                                                                       # type: ignore[method-assign,assignment]
    c.open_review = lambda m: None                                                                # type: ignore[method-assign,assignment,return-value]
    try:
        c._on_job_done(store.jobs.get(job))
        c._on_job_done(store.jobs.get(job))
        assert notes.count(llm_steps.NO_PROVIDER) == 1 and len(notes) == 3
        _provider_ready(store)
        c._no_llm_warned = False
        c._on_job_done(store.jobs.get(job))
        assert notes.count(llm_steps.NO_PROVIDER) == 1                                              # com chave: sem aviso
    finally:
        c.emergency_stop()
