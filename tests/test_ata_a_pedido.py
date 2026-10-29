"""F5.6 L1a, Aceite 1 e 2 (A-45): a matriz sem pedido. Em cada caminho que antes gerava a ata sozinho (processar com 0 e com 2
vozes pendentes, revisar até zerar e depois o Concluir, reprocessar, trocar o idioma, o ao vivo sem a caixa do parcial com o
relógio falso, os nomes mudados sem clique) nenhuma chamada de ata, executivo, pré-resumo ou resumo parcial, contadas no
`FakeProvider` compartilhado do caminho e em `llm_calls`; identidade, título e revisada saem sem clique. E o teste por AST: só
`llm_requests` passa `requested`, o único jeito de `enqueue_llm_job` aceitar ata, executivo e pré-resumo."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from listener.llm.fake import FakeProvider
from listener.model import EventType, JobKind, JobStatus, SummaryKind
from listener.model import JobStep
from listener.pipeline import llm_requests, llm_steps, reprocess, review, summary_gate
from listener.pipeline.runner import JobRunner
from listener.store.summaries import SummaryInsert
from tests.fake_pipeline import FakeDeps
from tests.test_llm_steps import SCRIPT, _provider_ready, _record, _run_llm
from tests.test_runner import CFG

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"minutes", "executive", "pre_summary", "partial"}


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()                                   # um só por caminho: todas as chamadas dele contam


def _heavy(store: Any, script: dict = SCRIPT) -> int:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None, "nenhum processamento pendente"
    assert JobRunner(store, FakeDeps(script), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


def _heavy_kind(store: Any, mid: int, kind: JobKind) -> int:
    store.jobs.enqueue(mid, kind)
    return _heavy(store)


def _processed(platform: Any, store: Any, script: dict = SCRIPT) -> int:
    mid = _record(platform, store)
    _provider_ready(store)
    store.jobs.enqueue(mid)
    _heavy(store, script)
    return mid


def _drain(store: Any, fake: FakeProvider) -> int:
    """Roda os jobs `llm` da fila, em ordem, com o fake do caminho; devolve quantos."""
    n = 0
    while store.jobs.has_pending(kinds=(JobKind.LLM,)):
        _runner, status, _ = _run_llm(store, fake)
        assert status == JobStatus.DONE
        n += 1
    return n


def _core_only(store: Any, fake: FakeProvider, *, identify: bool = True) -> None:
    sent = {str(r.task) for r in fake.requests}
    recorded = {str(r["task"]) for r in store._q("SELECT task FROM llm_calls")}
    assert not (sent | recorded) & FORBIDDEN, (sent, recorded)
    need = {"title", "revise"} | ({"identify"} if identify else set())
    assert need <= sent and need <= recorded, (sent, recorded)
    queued = {t for j in store._q("SELECT params_json FROM processing_jobs WHERE kind = 'llm'")
              for t in json.loads(j[0] or "{}").get("tasks") or ()}
    assert not queued & FORBIDDEN and not [e for e in store._q("SELECT type FROM events") if e[0] == EventType.LLM_REQUESTED]


def test_processing_with_no_voice_to_confirm(platform, store, fake, no_summary_gate):
    """Nenhuma voz pendente para a regra da revisão (antes, a ata saía na hora, sem adiamento)."""
    mid = _processed(platform, store)
    assert summary_gate.pending_voices(store, mid) == 0
    assert _drain(store, fake) == 1
    _core_only(store, fake)
    done = json.loads(store._q("SELECT timings_json FROM processing_jobs WHERE kind = 'llm'")[0][0])["llm"]["done"]
    assert set(done) == {"identify", "title", "revise"}


def test_processing_with_two_voices_to_confirm(platform, store, fake):
    mid = _processed(platform, store)
    assert summary_gate.pending_voices(store, mid) == 2 and summary_gate.deferred(store, mid) is None
    assert _drain(store, fake) == 1
    _core_only(store, fake)


def test_reviewing_until_no_voice_is_left_and_concluding(qtbot, platform, store, fake):
    from listener.ui.review import ReviewDialog

    mid = _processed(platform, store)
    _drain(store, fake)
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    assert dlg.create_person("Ana", "CFO") and dlg.create_person("Bia")                      # a última voz resolvida
    assert summary_gate.pending_voices(store, mid) == 0
    dlg.finish()                                                                              # e o Concluir
    assert launched == [] and _drain(store, fake) == 0
    _core_only(store, fake)


def test_reprocessing(platform, store, fake):
    mid = _processed(platform, store)
    _drain(store, fake)
    store.jobs.enqueue(mid, JobKind.REPROCESS)
    _heavy(store)
    assert _drain(store, fake) == 1
    _core_only(store, fake)


def test_changing_the_language(platform, store, fake):
    mid = _processed(platform, store)
    _drain(store, fake)
    assert reprocess.request_reprocess(store, mid, "en").job_id is not None
    _heavy(store)
    assert store.get_meeting(mid)["language"] == "en" and _drain(store, fake) == 1
    _core_only(store, fake)


def test_names_changed_without_a_click(qtbot, platform, store, fake):
    """A ata existe (gravada aqui sem a LLM) e um nome muda: o aviso acende, nada sai sozinho."""
    from listener.ui.llm_tabs import LlmTabsGroup

    mid = _processed(platform, store)
    _drain(store, fake)
    parent = store.segments.current_job_id(mid)
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "# Ata", "gemini", "m", "minutes/1.2", parent, None,
                                               "pt-BR"))
    store.add_event(mid, None, EventType.JOB_STEP, {"step": "minutes_done", "summary_id": sid, "speakers_v": summary_gate.SPEAKERS_V,
                                                    "speakers": summary_gate.speakers_digest(store, mid)})
    first, _second = (c.cluster_id for c in review.pending(review.review_items(store, mid)))
    review.create_person(store, first, "Ana", "CFO")
    assert summary_gate.names_changed(store, mid)
    group = LlmTabsGroup(store, mid)
    qtbot.addWidget(group.text_tabs[str(SummaryKind.MINUTES)])
    group.reload()
    assert group.text_tabs[str(SummaryKind.MINUTES)].btn_extra.text() == "Atualizar a ata com os nomes"
    assert llm_requests.release(store, mid, "revisão sem voz pendente") is None and _drain(store, fake) == 0
    assert llm_requests.request_state(store, mid, llm_requests.Species.MINUTES).reasons == ("nomes",)
    _core_only(store, fake)


def test_live_without_the_partial_box_on_the_fake_clock(qtbot, qapp, store, platform, monkeypatch, fake):
    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.llm import partial_task
    from listener.model import AppState
    from listener.store.captions import CaptionInsert
    from listener.ui import controller as ctl

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.set_setting("live.enabled", True)
    _provider_ready(store)
    real = partial_task.run_partial
    monkeypatch.setattr(partial_task, "run_partial", lambda s, m, **kw: real(s, m, **{**kw, "provider": fake}))
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    monkeypatch.setattr(c, "notify", lambda _text: None)
    now = [1000.0]
    c.monotonic = lambda: now[0]
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "ao vivo"})
        mid = c.recorder.meeting_id
        assert c.live is not None and c._partial_meeting == mid and c._partial_timer.isActive()   # o disparo existe...
        for i in range(4):                                                    # 4 intervalos vencidos, cada um com legenda nova
            store.captions.insert(CaptionInsert(mid, "R", i * 5000, i * 5000 + 4000, f"fala {i}", None, None, None, "fake:small", 6000))
            now[0] += 6 * 60
            c._partial_tick()
        assert not c._partial_tasks
        platform.pump(1.5)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
        _heavy(store)                                                         # o processamento que o "Parar" pôs na fila
        assert _drain(store, fake) == 1
        _core_only(store, fake)
    finally:
        c.emergency_stop()


def test_the_core_runs_alone_and_reprocessing_never_adds_the_missing_minutes(platform, store):
    """F5.6 (A-05): o processamento enfileira só o núcleo (identidade, título, revisada) e o reprocessamento de uma reunião
    sem ata nem título leva identidade, título e revisada, nunca a ata, o executivo e o pré-resumo que faltam (antes eram as
    "faltantes", R3-02); `enqueue_llm_job` recusa essas três sem `requested`."""
    mid = _record(platform, store)
    _provider_ready(store)
    _heavy_kind(store, mid, JobKind.MEETING)
    core = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm")
    assert llm_steps.tasks_of(core) == [JobStep.IDENTIFY, JobStep.TITLE, JobStep.REVISE]
    store.jobs.request_cancel(int(core["id"]))                                                 # nunca rodou: sem ata nem título
    assert llm_steps.auto_tasks_for(store, mid, JobKind.REPROCESS) == ["identify", "title", "revise"]
    rep = _heavy_kind(store, mid, JobKind.REPROCESS)
    new = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente")
    assert json.loads(new["params_json"]) == {"tasks": ["identify", "title", "revise"], "parent_job_id": rep, "auto": True}
    assert llm_steps.enqueue_llm_job(store, mid, ["minutes"]) is None                          # só a pedido
    assert llm_steps.enqueue_llm_job(store, mid, ["title", "executive"]) is None
    assert [j["id"] for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente"] == [new["id"]]


def test_the_title_reads_the_opening_lines_cut_at_a_speaker_change(store):
    """A-07: sem pré-resumo nem ata (o núcleo roda antes delas), o título lê as falas de abertura, ~1.500 tokens cortados na
    troca de falante (antes, as 40 primeiras), com o rótulo e a versão do prompt novos."""
    from listener.llm import prompts
    from listener.llm.costs import estimate_tokens
    from listener.llm.prompts.common import Line, MeetingCtx
    from listener.llm.types import LlmTask
    from listener.store.segments import SegmentInsert
    from tests.test_rag_index import _pipeline_job

    lines = tuple(Line(i, i * 1000, "Ana" if (i // 3) % 2 else "Bia", "palavra " * 40) for i in range(60))
    cut = prompts.title.opening(lines)
    assert 10 < len(cut) < 40 and cut[-1].speaker != lines[len(cut)].speaker
    assert 1200 < sum(estimate_tokens(ln.render()) + 1 for ln in cut) <= 1500 * 1.15
    meeting = MeetingCtx("x", "2026-10-29", None, (), (), "pt-BR")
    user = prompts.title.build(prompts.title.TitleCtx(meeting, "falas", True))[1].text
    assert "Opening of the transcript:\nfalas" in user and "Summary:" not in user and "from its summary:" not in prompts.title.SYSTEM
    assert prompts.prompt_version(LlmTask.TITLE) == "title/1.2"
    ana, bia = store.people.create_person("Ana"), store.people.create_person("Bia")
    mid = store.create_meeting("Longa", None, None, None)
    store.finish_meeting(mid, 60_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 1000, i * 1000 + 900, f"fala{i} " + "palavra " * 40, "pyannote",
                                                  "final", None, None, ana if (i // 3) % 2 else bia) for i in range(60)])
    _provider_ready(store)
    assert llm_steps.enqueue_llm_job(store, mid, ["title"], job) is not None
    _runner, status, fake = _run_llm(store)
    sent = fake.calls(LlmTask.TITLE)[0].messages[1].text.split("Opening of the transcript:\n", 1)[1]
    assert status == JobStatus.DONE and len(sent.splitlines()) == len(cut) and "fala0 " in sent and f"fala{len(cut)} " not in sent


# ---------------------------------------------------------------- Aceite 1: quem enfileira a ata (AST, R-07)
# o caminho da ata: o módulo e as funções dele que pedem, liberam, encadeiam ou enfileiram. F5.6 L1b: o `ask_now` das abas
# saiu; a tela pede por `llm_ask.ask` (o popup antes) e só `llm_ask.submit` chama `request`
TARGETS: dict[str, frozenset[str]] = {
    "llm_requests": frozenset({"request", "release", "after_pipeline", "_enqueue_or_join", "enqueue_or_join"}),
    "llm_ask": frozenset({"ask", "submit"}), "llm_steps": frozenset({"enqueue_llm_job"})}
# a lista fechada de quem chama (`chamado <- arquivo:Classe.função`): um chamador novo falha o teste até entrar aqui
ALLOWED: frozenset[str] = frozenset({
    "after_pipeline <- runner.py:JobRunner._enqueue_llm", "after_pipeline <- llm_requests.py:request",
    "release <- review.py:ReviewDialog.release_summary", "request <- llm_ask.py:submit", "submit <- llm_ask.py:ask",
    "ask <- llm_tabs.py:LlmTabsGroup.ask", "ask <- summary.py:SummaryDialog.ask", "ask <- meeting_language.py:MeetingLanguage._ask_llm",
    "_enqueue_or_join <- llm_requests.py:_apply", "_enqueue_or_join <- llm_requests.py:release",
    "enqueue_llm_job+requested <- llm_requests.py:_enqueue_or_join", "enqueue_llm_job <- llm_steps.py:enqueue_after_pipeline",
    "enqueue_llm_job <- llm_tabs.py:request_llm_step",
    "job llm direto <- llm_steps.py:enqueue_llm_job", "job llm direto <- llm_steps.py:enqueue_test_job"})


def _name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _forbidden_literals(node: ast.Call) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "JobStep":
            out |= {sub.attr.lower()} & FORBIDDEN
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out |= {sub.value} & FORBIDDEN
    return out


def _is_llm(value: ast.expr) -> bool:
    return (isinstance(value, ast.Attribute) and value.attr == "LLM") or (isinstance(value, ast.Constant) and value.value == "llm")


def _bindings(tree: ast.Module, stem: str) -> tuple[dict[str, str], dict[str, str]]:
    """(nome solto -> função do caminho, apelido -> módulo do caminho): as funções do próprio módulo e as importadas."""
    bare = {n: n for n in TARGETS.get(stem, ())}
    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[-1]
            for a in node.names:
                if a.name in TARGETS.get(mod, ()):
                    bare[a.asname or a.name] = a.name
                elif a.name in TARGETS:
                    alias[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            alias |= {a.asname: a.name.split(".")[-1] for a in node.names if a.asname and a.name.split(".")[-1] in TARGETS}
    return bare, alias


def _callee(call: ast.Call, bare: dict[str, str], alias: dict[str, str]) -> str | None:
    f = call.func
    name = bare.get(f.id) if isinstance(f, ast.Name) else None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.attr in TARGETS.get(alias.get(f.value.id, ""), ()):
        name = f.attr
    if name == "enqueue_llm_job":
        flag = next((k.value for k in call.keywords if k.arg == "requested"), None)
        asked = flag is not None and not (isinstance(flag, ast.Constant) and flag.value is False)
        return name + ("+requested" if asked else "+literal" if _forbidden_literals(call) else "")
    if name is None and _name(f) in ("enqueue", "enqueue_if_meeting") and any(
            _is_llm(v) for v in (*call.args, *(k.value for k in call.keywords if k.arg == "kind"))):
        return "job llm direto"
    return name


def _calls(filename: str, source: str) -> set[str]:
    """Quem chama o caminho da ata num módulo: `chamado <- arquivo:Classe.função` (R-07)."""
    tree = ast.parse(source)
    bare, alias = _bindings(tree, filename.removesuffix(".py"))
    out: set[str] = set()

    def visit(node: ast.AST, qual: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = f"{qual}.{child.name}".strip(".") if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else qual
            callee = _callee(child, bare, alias) if isinstance(child, ast.Call) else None
            if callee:
                out.add(f"{callee} <- {filename}:{qual or '<módulo>'}")
            visit(child, inner)

    visit(tree, "")
    return out


def test_only_the_closed_list_asks_for_the_minutes_executive_and_pre_summary():
    """`enqueue_llm_job` recusa ata, executivo e pré-resumo sem `requested` (`test_llm_steps`); no código do app, só a lista
    fechada chama `request`, `release`, `after_pipeline`, `ask`, `submit`, `_enqueue_or_join` e `enqueue_llm_job` (só
    `_enqueue_or_join` passa `requested`, ninguém escreve essas tarefas na chamada) e só `llm_steps` põe um job `llm` direto
    na fila (`kind` por posição, por palavra-chave ou o literal)."""
    found: set[str] = set()
    for path in sorted((ROOT / "listener").rglob("*.py")):
        found |= _calls(path.name, path.read_text(encoding="utf-8"))
    assert found == ALLOWED, (sorted(found - ALLOWED), sorted(ALLOWED - found))


def test_a_new_automatic_caller_fails_the_closed_list():
    """R-07: o chamador novo do revisor (um fim de job que pede a ata sozinho) e um job `llm` direto por palavra-chave ou
    pelo literal aparecem fora da lista."""
    src = ("from ..pipeline import llm_requests\n"
           "from ..pipeline.llm_requests import release as solta\n"
           "def _finish(store, mid, parent):\n"
           "    llm_requests.enqueue_or_join(store, mid, ['minutes'], parent)\n"
           "    llm_requests.request(store, mid, ['minutes'], 'now', 'auto')\n"
           "    solta(store, mid, 'auto', force=True)\n"
           "    store.jobs.enqueue(mid, kind=JobKind.LLM, params={})\n"
           "    store.jobs.enqueue_if_meeting(mid, 'llm', {})\n")
    assert _calls("controller.py", src) == {f"{n} <- controller.py:_finish" for n in ("enqueue_or_join", "request", "release",
                                                                                     "job llm direto")}
    assert _calls("tasks.py", "from .tasks import release\ndef f():\n    release()\n") == set()   # outro `release`
