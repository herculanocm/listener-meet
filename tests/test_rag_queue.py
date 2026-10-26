"""F5 L1: a fila do índice (claim depois dos `llm`, um `index` pendente por reunião, filtros e linha agregada), o despacho da
classe leve por `kind`, `--once` sem lançar o leve, o controlador e a fila ignorando os `index` (F5A-01 a F5A-05, F5A-16)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from listener.model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, PIPELINE_JOB_KINDS, JobKind, JobStatus, JobStep
from listener.pipeline import estimate, worker
from listener.store import Store

T0 = "2026-10-25T10:00:00.000-03:00"


def _at(seconds: float) -> str:
    return (datetime.fromisoformat(T0) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _meeting(store: Store, title: str = "r") -> int:
    mid = store.create_meeting(title, None, None, None)
    store.finish_meeting(mid, 5000)
    return mid


def test_index_jobs_wait_for_llm_dedup_per_meeting_and_aggregate(store):
    """F5A-02: o `llm` passa na frente dos `index` (mesmo mais novo) e, entre os `index`, os da varredura vão por último
    (R5-08); F5A-04: um pendente por reunião, o rodando não conta, motivo em `params_json`; F5A-05: filtros da lista e o
    resumo agregado com os erros vigentes."""
    assert JobKind.INDEX in LIGHT_JOB_KINDS and JobKind.INDEX not in HEAVY_JOB_KINDS and JobKind.INDEX not in PIPELINE_JOB_KINDS
    mid, other = _meeting(store), _meeting(store)
    i1 = store.jobs.enqueue_index(mid, "pipeline")
    assert i1 is not None and store.jobs.enqueue_index(mid, "review") is None                   # já há um pendente
    assert json.loads(store.jobs.get(i1)["params_json"]) == {"reason": "pipeline"}
    i2 = store.jobs.enqueue_index(other, "sweep")
    j_llm = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": None})
    with pytest.raises(ValueError, match="motivo"):
        store.jobs.enqueue_index(mid, "qualquer")
    assert store.jobs.claim(pid=9, now=_at(0), kinds=HEAVY_JOB_KINDS) is None                    # o pesado nunca pega o índice
    assert store.jobs.claim(pid=1, now=_at(1), kinds=LIGHT_JOB_KINDS)["id"] == j_llm             # o llm, mais novo, primeiro
    store.jobs.finish(j_llm, JobStatus.DONE)
    got = store.jobs.claim(pid=1, now=_at(2), kinds=LIGHT_JOB_KINDS)
    assert got["id"] == i1 and got["kind"] == "index"
    assert store.jobs.claim(pid=2, now=_at(3), kinds=LIGHT_JOB_KINDS) is None                    # serial na classe leve
    i3 = store.jobs.enqueue_index(mid, "edit")                                                  # o rodando não conta
    assert i3 not in (None, i1) and store.jobs.enqueue_index(mid, "edit") is None
    assert store.jobs.index_summary() == {"pending": 2, "running": 1, "meetings": 2, "errors": 0}
    assert [j["kind"] for j in store.jobs.list_jobs(exclude_kinds=("index",))] == ["llm"]
    assert {j["id"] for j in store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))} == {i2, i3}
    store.jobs.finish(i1, JobStatus.ERROR, error="queda")
    assert store.jobs.index_summary()["errors"] == 0                                            # há um `index` mais novo na reunião
    assert store.jobs.claim(pid=1, now=_at(4), kinds=LIGHT_JOB_KINDS)["id"] == i3                # a edição antes da varredura
    store.jobs.finish(i2, JobStatus.ERROR, error="queda")
    assert store.jobs.index_summary() == {"pending": 0, "running": 1, "meetings": 1, "errors": 1}
    assert store.jobs.summary(exclude_kinds=("index",))["errors"] == 0 and store.jobs.summary()["errors"] == 2
    assert store.segments.current_job_id(mid) is None                                           # o índice não é job vigente
    assert estimate.remaining_seconds("index_vectors", 0.1, 3600, "{}", kind="index") is None     # sem estimativa no leve


def test_index_delay_is_durable_renewed_cleared_and_forced(store):
    """R5-07: a edição entra na fila já, com "não antes de" agora + espera; cada edição renova; um pedido sem espera não
    mexe nela; "Concluir" (espera 0) a tira; o claim só pega depois; o `force` do "reindexar" fica no pendente."""
    mid = _meeting(store)
    job = store.jobs.enqueue_index(mid, "edit", delay_s=30, now=_at(0))
    assert json.loads(store.jobs.get(job)["params_json"]) == {"reason": "edit", "not_before": _at(30)}
    assert store.jobs.claim(pid=1, now=_at(10), kinds=LIGHT_JOB_KINDS) is None
    assert store.jobs.delayed(LIGHT_JOB_KINDS, now=_at(10)) == 1 and store.jobs.delayed(HEAVY_JOB_KINDS, now=_at(10)) == 0
    assert store.jobs.enqueue_index(mid, "edit", delay_s=30, now=_at(20)) is None                # renova
    assert json.loads(store.jobs.get(job)["params_json"])["not_before"] == _at(50)
    assert store.jobs.claim(pid=1, now=_at(40), kinds=LIGHT_JOB_KINDS) is None
    assert store.jobs.enqueue_index(mid, "pipeline", now=_at(41)) is None                        # sem espera: não mexe
    assert json.loads(store.jobs.get(job)["params_json"])["not_before"] == _at(50)
    assert store.jobs.enqueue_index(mid, "reindex", force=True, now=_at(42)) is None
    assert store.jobs.enqueue_index(mid, "review", delay_s=0, now=_at(43)) is None               # "Concluir": tira a espera
    assert json.loads(store.jobs.get(job)["params_json"]) == {"reason": "edit", "force": True}
    assert store.jobs.delayed(LIGHT_JOB_KINDS, now=_at(43)) == 0
    assert store.jobs.claim(pid=1, now=_at(44), kinds=LIGHT_JOB_KINDS)["id"] == job
    store._x("UPDATE processing_jobs SET params_json = 'não é json' WHERE id = ?", (store.jobs.enqueue(mid, JobKind.LLM),))
    store.jobs.finish(job, JobStatus.DONE)
    assert store.jobs.claim(pid=1, now=_at(45), kinds=LIGHT_JOB_KINDS)["kind"] == "llm"           # JSON inválido não trava o claim


def test_light_worker_waits_for_a_delayed_index_instead_of_leaving(tmp_path, store):
    """R5-07: o leve não sai ocioso enquanto um `index` espera a edição assentar; quando vence, ele pega e conclui."""
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = _meeting(store)
    job = store.jobs.enqueue_index(mid, "edit", delay_s=0.15)
    assert worker._serve(store, once=False, idle_exit_s=0.0, poll_s=0.02, light=True) == 0
    assert store.jobs.get(job)["status"] == JobStatus.DONE


def test_light_worker_takes_an_index_that_comes_due_between_the_checks(tmp_path, store, monkeypatch):
    """O `index` que vence entre a consulta da espera e o claim é pego: com o claim antes da espera, ele não era pego nem
    esperado e o leve saía ocioso com ele pendente (o teste acima falhava ~1 vez em 5 na suíte do Windows, 25/10)."""
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = _meeting(store)
    job = store.jobs.enqueue_index(mid, "edit", delay_s=60.0)
    repo = type(store.jobs)
    real = repo.delayed

    def comes_due_now(self: Any, kinds: Any = None, now: Any = None) -> int:
        self.expedite_index(mid)                                    # vence exatamente aqui, entre as duas consultas
        monkeypatch.setattr(repo, "delayed", real)
        return real(self, kinds, now)

    monkeypatch.setattr(repo, "delayed", comes_due_now)
    assert worker._serve(store, once=False, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    assert store.jobs.get(job)["status"] == JobStatus.DONE


def test_light_worker_dispatches_by_kind(tmp_path, store, monkeypatch):
    """F5A-01: o `index` vai para o runner do índice; o `llm` continua no da LLM."""
    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = _meeting(store)
    job = store.jobs.enqueue_index(mid, "sweep")

    def no_llm(_store: Any, _job: Any) -> JobStatus:
        raise AssertionError("o index não pode cair no runner da LLM")

    monkeypatch.setattr(worker, "run_llm_job", no_llm)
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(job)
    assert row["status"] == JobStatus.DONE and row["worker_pid"] is None
    seen: list[int] = []

    def fake_llm(_store: Any, job: Any) -> JobStatus:
        seen.append(int(job["id"]))
        return JobStatus.DONE

    monkeypatch.setattr(worker, "run_llm_job", fake_llm)
    j_llm = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True)
    assert seen == [j_llm]


def test_once_worker_never_launches_the_light_worker(store, monkeypatch):
    """F5A-16: o pesado com `--once` (o worker da coleta dos testes) termina o job sem lançar o leve; sem `--once`, lança."""
    spawned: list[Any] = []

    def spawn(argv: list[str], _env: Any) -> int:
        spawned.append(argv)
        return 4242

    monkeypatch.setattr(worker, "_platform_hooks", lambda: (lambda: True, lambda: None, spawn))

    class DoneRunner:
        def __init__(self, store: Store, _deps: Any, job: Any, **_kw: Any):
            self.store, self.job = store, job

        def run(self) -> JobStatus:
            self.store.jobs.finish(int(self.job["id"]), JobStatus.DONE)
            self.store.jobs.enqueue_index(int(self.job["meeting_id"]), "pipeline")          # o que o `_finish_ok` faz (L2)
            return JobStatus.DONE

    monkeypatch.setattr(worker, "JobRunner", DoneRunner)
    monkeypatch.setattr(worker, "build_deps", lambda *_a, **_k: SimpleNamespace(release=lambda: None))
    monkeypatch.setenv("LISTENER_PIPELINE", "fake")
    try:
        mid = _meeting(store)
        store.jobs.enqueue(mid)
        assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0) == 0
        assert spawned == [] and store.jobs.has_pending(kinds=("index",))
        store.jobs.enqueue(_meeting(store))
        worker._serve(store, once=False, idle_exit_s=0.0, poll_s=0.0)
        assert len(spawned) == 1 and spawned[0][-1] == "--light"
    finally:
        sys.modules.pop("listener.pipeline.models", None)                        # o app nunca importa models.py (F2)


class _Tray:
    def __init__(self) -> None:
        self.tips: list[str] = []
        self.messages: list[str] = []

    def set_queue_tip(self, text: str) -> None:
        self.tips.append(text)

    def showMessage(self, _title: str, text: str) -> None:  # noqa: N802
        self.messages.append(text)

    def set_state(self, _state: Any) -> None:
        pass


def test_controller_ignores_index_jobs_when_detecting_done_and_in_the_tip(qapp, platform, store, monkeypatch):
    """F5A-05: com 60 `index` ativos, o pesado concluído ainda abre a revisão; a dica conta "índice N" à parte; um `index`
    concluído não avisa nem abre nada."""
    from listener.ui.controller import Controller

    meetings = [_meeting(store, f"m{i}") for i in range(60)]
    heavy_mid = _meeting(store, "Diretoria")
    for mid in meetings:
        store.jobs.enqueue_index(mid, "sweep")
    job = store.jobs.enqueue(heavy_mid)
    c = Controller(qapp, platform, store)
    tray = _Tray()
    c.tray = tray                                                                                   # type: ignore[assignment]
    opened: list[int] = []
    monkeypatch.setattr(c, "open_review", opened.append)
    monkeypatch.setattr(c, "launch_worker", lambda light=False: False)
    try:
        c._poll_queue()
        assert tray.tips[-1] == "1 na fila · índice 60" and opened == []
        store.jobs.claim(pid=7, kinds=HEAVY_JOB_KINDS)
        store.jobs.finish(job, JobStatus.DONE)
        c._poll_queue()
        assert opened == [heavy_mid] and tray.messages == ["Diretoria: pronta para revisão de falantes."]
        assert tray.tips[-1] == "índice 60"
        idx = store.jobs.list_jobs(1, kinds=("index",))[0]
        store.jobs.claim(pid=8, kinds=LIGHT_JOB_KINDS)
        store.jobs.finish(int(idx["id"]), JobStatus.DONE)
        c._on_job_done(store.jobs.get(int(idx["id"])))
        c._poll_queue()
        assert opened == [heavy_mid] and len(tray.messages) == 1                                    # o índice não avisa nem abre
    finally:
        c.emergency_stop()


def test_queue_dialog_shows_index_as_one_aggregated_row_and_retries_by_class(qtbot, store):
    from listener.ui.queue import QueueDialog, index_row_texts

    mid, other = _meeting(store, "Planejamento"), _meeting(store, "Comitê")
    j_llm = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    store.jobs.claim(pid=1, kinds=("llm",))
    store.jobs.finish(j_llm, JobStatus.ERROR, error="sem rede")
    i1 = store.jobs.enqueue_index(mid, "pipeline")
    store.jobs.enqueue_index(other, "sweep")
    store.jobs.claim(pid=2, kinds=LIGHT_JOB_KINDS)
    store.jobs.set_progress(i1, JobStep.INDEX_VECTORS, 0.4)
    launched: list[bool] = []
    dlg = QueueDialog(store, on_launch=launched.append)
    qtbot.addWidget(dlg)
    dlg._timer.stop()                                                                              # o banco fecha no fim do teste
    assert dlg.table.rowCount() == 2                                                                 # o llm e a linha agregada
    last = [dlg.table.item(1, c).text() for c in range(7)]
    assert last[:5] == ["Índice: 2 reuniões", "índice", "rodando", "índice: vetores", "40"]
    assert "pendente(s)" in dlg.status_label.text() and "0 ativo(s)" in dlg.status_label.text()       # o rodapé sem os `index`
    dlg.table.selectRow(1)
    assert dlg.selected_job_id() is None
    dlg.retry_selected()
    assert launched == []                                                                            # a linha agregada não age
    dlg.table.selectRow(0)
    dlg.retry_selected()
    assert launched == [True] and store.jobs.get(j_llm)["status"] == "pendente"                      # llm: o worker leve
    assert index_row_texts({"meetings": 0, "errors": 0, "pending": 0}, None) is None
    assert index_row_texts({"meetings": 1, "errors": 1, "pending": 1}, None)[:3] == ("Índice: 1 reunião (1 com erro)", "índice", "pendente")


def test_chat_threads_rating_and_meeting_chat_unchanged(store):
    """v8: conversas do chat global (Q3) com mensagens de `meeting_id` nulo e "citação certa?" (U-027); o chat de uma reunião
    da F3 só vê as mensagens dela."""
    from listener.model import ChatRole

    mid = _meeting(store)
    store.chat.add(mid, ChatRole.USER, "pergunta da reunião")
    first = store.chat.create_thread("qual o prazo do contrato?")
    second = store.chat.create_thread("quem cuida do orçamento?")
    q = store.chat.add(None, ChatRole.USER, "qual o prazo do contrato?", thread_id=first)
    a = store.chat.add(None, ChatRole.ASSISTANT, "sexta [R1 00:05]", "gemini", "fake-fast",
                       [{"alias": "R1", "meeting_id": mid, "t_ms": 5000, "segment_id": None}], 0.0004, thread_id=first)
    assert [m["id"] for m in store.chat.list_for_thread(first)] == [q, a] and store.chat.list_for_thread(second) == []
    assert [t["id"] for t in store.chat.list_threads()] == [first, second]                    # a com mensagem mais nova primeiro
    assert store.chat.list_threads()[0]["messages"] == 2 and store.chat.get_thread(second)["title"] == "quem cuida do orçamento?"
    assert store.chat.set_rating(a, 1) and store.chat.list_for_thread(first)[1]["rating"] == 1
    assert store.chat.set_rating(a, None) and store.chat.list_for_thread(first)[1]["rating"] is None
    with pytest.raises(ValueError, match="avaliação"):
        store.chat.set_rating(a, 5)
    assert [m["text"] for m in store.chat.list_for_meeting(mid)] == ["pergunta da reunião"]
    assert store.chat.clear(mid) == 1 and len(store.chat.list_for_thread(first)) == 2


def test_queue_estimates_llm_jobs_only_by_the_llm_job_tasks(qtbot, store, monkeypatch):
    """R5-05: os lotes de vetores (e o chat global) não entram na média que estima o job `llm` na fila."""
    from listener.llm.types import LLM_JOB_TASKS
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert
    from listener.ui.queue import QueueDialog

    mid = _meeting(store)
    for task, ms in [("minutes", 40_000), ("title", 40_000)] + [("rag_embed", 50)] * 18 + [("rag_chat", 900)]:
        store.llm_calls.insert(LlmCallInsert(mid, None, task, "gemini", "m", None, None, 1, 1, 0, 0, None, ms, LlmCallStatus.OK))
    assert store.llm_calls.avg_latency_ms(tasks=LLM_JOB_TASKS) == 40_000.0 and (store.llm_calls.avg_latency_ms() or 0) < 5000
    seen: list[Any] = []
    real = store.llm_calls.avg_latency_ms

    def spy(*a: Any, **k: Any) -> float | None:
        seen.append(k.get("tasks"))
        return real(*a, **k)

    monkeypatch.setattr(store.llm_calls, "avg_latency_ms", spy)
    store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()                                                                              # o banco fecha no fim do teste
    assert seen and tuple(seen[0]) == LLM_JOB_TASKS
