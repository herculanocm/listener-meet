"""F5.1 L6, o embedder local no índice, na busca e nas telas, com a sessão e o tokenizer falsos e a pasta de modelos no tmp
(A-13): estado e chave `local/multilingual-e5-small@384` (A-02), lotes de 8 um por vez (A-14) e custo 0 (A-04), estimativa sem
custo com 0,19 s por trecho até medir o próprio modelo (A-05), vetores adiados com a lease e completados pela varredura sem
modelo confirmado (A-06, A-07), o local nunca completa as reuniões antigas sem o "Reindexar agora" (Q3), o vetor da consulta
sem custo, o worker leve com 2 threads e as versões no log (A-03), as Configurações (combo, um `UPDATE` por papel, o grupo
"Busca" com o preparo e o progresso de cada grupo, A-01, A-08), a pré-carga e a soltura no Histórico (A-11), a pergunta do
fim do preparo (A-12) e a varredura ao parar e cancelar a gravação."""
from __future__ import annotations

import json
import logging
import threading
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from listener.llm import local_embed
from listener.model import AppState, JobStatus
from listener.pipeline.index_steps import IndexJobRunner
from listener.rag import index, search
from listener.store import Store
from listener.store.segments import SegmentInsert
from tests.fake_local import Factories, ready_folder
from tests.test_rag_index import _people, _pipeline_job

KEY = "local/multilingual-e5-small@384"


class Clock:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """O CI instala só o `[dev]`: o extra `local` conta como instalado (as fábricas da sessão são falsas)."""
    monkeypatch.setattr(local_embed, "extra_installed", lambda: True)
    monkeypatch.delenv("LISTENER_LLM", raising=False)


class Local:
    def __init__(self, factories: Factories, clock: Clock, folder: Path):
        self.f = factories
        self.clock = clock
        self.folder = folder


@pytest.fixture
def local(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """O local ativo e pronto, com a pasta de modelos no tmp e a sessão falsa no lugar da do processo."""
    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    folder = ready_folder(models)
    factories, clock = Factories(), Clock()
    monkeypatch.setattr(local_embed, "_SESSION", factories.local_session(clock=clock))
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    search.clear_caches()
    yield Local(factories, clock, folder)
    search.clear_caches()


def _long_meeting(store: Store, n: int, title: str = "Comitê do fundo") -> int:
    """Reunião processada com `n` falas de 90 s alternando duas pessoas: `n` trechos de transcrição mais o da reunião."""
    who = _people(store)
    mid = store.create_meeting(title, None, None, None)
    store.finish_meeting(mid, n * 90_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 90_000, (i + 1) * 90_000, f"Fala {i}: o orçamento {i} e o prazo {i}.",
                                                  "pyannote", "final", person_id=who["ana" if i % 2 else "bia"]) for i in range(n)])
    return mid


def _index_job(store: Store, mid: int) -> tuple[JobStatus, dict[str, Any]]:
    job_id = index.enqueue_index(store, mid, "edit")
    job = store.jobs.claim(pid=3, kinds=("index",))
    assert job is not None and job["id"] == job_id
    status = IndexJobRunner(store, job, heartbeat_thread=False).run()
    return status, json.loads(store.jobs.get(job_id)["timings_json"] or "{}")["index"]


def test_embedder_status_key_and_reasons_of_the_local(store, tmp_path, monkeypatch):
    from listener.ui.history import friendly_reason

    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    st = index.embedder_status(store)
    assert (st.ready, st.reason, st.model_key, st.provider_id) == (False, local_embed.NOT_DOWNLOADED, KEY, "local")
    ready_folder(models)
    assert index.embedder_status(store) == index.EmbedderStatus(True, "", KEY, "local")
    monkeypatch.setattr(local_embed, "extra_installed", lambda: False)
    assert index.embedder_status(store).reason == local_embed.NO_EXTRA
    assert index.model_key("local", "multilingual-e5-small") == KEY and index.model_key("gemini", "g") == "gemini/g@768"
    assert index.is_local(KEY) and not index.is_local("gemini/g@768") and not index.is_local(None)
    assert friendly_reason(local_embed.NOT_DOWNLOADED) == "a busca local ainda não foi baixada: prepare em Configurações (Busca)"
    assert "extra local" in friendly_reason(local_embed.NO_EXTRA)
    for reason in (local_embed.LOAD_FAILED, local_embed.RUN_FAILED):
        assert friendly_reason(reason) == "a busca local falhou: prepare de novo em Configurações (Busca)"


def test_fill_vectors_with_the_local_goes_in_small_batches_one_at_a_time_and_free(store, local):
    mid = _long_meeting(store, 40)
    synced = index.sync_text(store, mid)
    assert synced.total == 41 and local_embed.BATCH == 8                       # A-14: o lote de 32 passou de 500 MB no leve
    filled = index.fill_vectors(store, mid)                                     # o provedor sai do registro: o local da pasta
    assert (filled.model_key, filled.written, filled.missing, filled.cost_usd, filled.note) == (KEY, 41, 0, 0.0, "")
    calls = [c for c in store.llm_calls.list_for_meeting(mid) if c["task"] == "rag_embed"]
    assert [c["strategy"] for c in calls] == ["batch:8"] * 5 + ["batch:1"] and {c["cost_usd"] for c in calls} == {0.0}
    assert {(c["provider"], c["model"]) for c in calls} == {("local", "multilingual-e5-small")} and calls[0]["input_tokens"] > 0
    assert local.f.session is not None and len(local.f.session.feeds) == 6 and local.f.sessions[0][1] == 1   # uma sessão, 1 thread
    assert local.f.tokenizer is not None and local.f.tokenizer.batches[0][0].startswith("passage: Comitê do fundo")
    assert [tuple(r) for r in store._q("SELECT DISTINCT model, dim FROM rag_vectors")] == [(KEY, 384)]
    assert store.rag.get_model(KEY) is not None and store.rag.get_model(KEY)["mu"].shape == (384,)


def test_reindex_estimate_for_the_local_is_free_with_seconds_per_chunk(store, local):
    from listener.llm.types import LlmTask
    from listener.model import LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert

    mid = _long_meeting(store, 40)
    index.sync_text(store, mid)
    est = index.reindex_estimate(store)
    assert (est.model_key, est.ready, est.chunks, est.meetings, est.cost_usd, est.batches) == (KEY, True, 41, 1, 0.0, 6)
    assert est.seconds == pytest.approx(41 * 0.19) and est.local and not est.measured
    text = est.text()
    assert text == "41 trechos sem vetor em 1 reunião · sem custo · ≈ 8 s nesta máquina (pela estimativa de 0,19 s por trecho)"
    for latency, n in ((6400, 32), (1800, 9), (900, 0)):                       # medido no próprio modelo: 8.200 ms em 41 trechos
        store.llm_calls.insert(LlmCallInsert(mid, None, str(LlmTask.RAG_EMBED), "local", "multilingual-e5-small", None, None, 1, 0, 0, 0,
                                             0.0, latency, LlmCallStatus.OK, None, f"batch:{n}"), store_payloads=False)
    store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.RAG_EMBED), "gemini", "gemini-embedding-001", None, None, 1, 0, 0, 0,
                                         0.0, 99_000, LlmCallStatus.OK, None, "batch:1"), store_payloads=False)   # outro modelo: fora
    measured = index.reindex_estimate(store)
    assert measured.measured and measured.seconds == pytest.approx(41 * 8.2 / 41) and "estimativa" not in measured.text()
    assert index._duration(30) == "30 s" and index._duration(150) == "2 min"


def test_index_job_with_the_local_defers_vectors_while_recording_and_the_sweep_completes(store, local):
    from datetime import datetime, timedelta

    mid = _long_meeting(store, 4)
    until = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="milliseconds")
    store.set_setting("pipeline.paused_until", until)                         # a lease da gravação (pause_on_record ou ao vivo)
    status, info = _index_job(store, mid)
    assert status == JobStatus.DONE and store.rag.count_chunks(mid) == 5 and info["vectors"] == 0     # trechos e FTS na hora
    assert info["deferred"] is True and info["note"] == "vetores adiados: gravando (5 trechos)" and local.f.sessions == []
    assert store.jobs.deferred_index_meetings() == [mid] and not index.model_confirmed(store, KEY)
    assert index.sweep(store) == 1                                            # sem modelo confirmado: a reunião adiada volta
    store.set_setting("pipeline.paused_until", "")
    job = store.jobs.claim(pid=4, kinds=("index",))
    assert job is not None and IndexJobRunner(store, job, heartbeat_thread=False).run() == JobStatus.DONE
    info = json.loads(store.jobs.get(int(job["id"]))["timings_json"])["index"]
    assert (info["vectors"], info["deferred"], info["note"]) == (5, False, "") and store.jobs.deferred_index_meetings() == []
    # um pago com a lease não adia (não disputa a CPU)
    from listener.llm.fake import FakeProvider

    other = _long_meeting(store, 2, "Outra")
    index.sync_text(store, other)
    store.set_setting("pipeline.paused_until", until)
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-ABCDEFGH12345678")
    store.providers.set_role("embed", "gemini")
    paid = index.fill_vectors(store, other, provider=FakeProvider(), defer=lambda: True)
    assert paid.written == 3 and not paid.deferred


def test_the_local_stops_between_batches_when_the_recording_starts(store, local):
    mid = _long_meeting(store, 40)
    index.sync_text(store, mid)
    lease = {"on": False}
    filled = index.fill_vectors(store, mid, defer=lambda: lease["on"], on_progress=lambda _done, _total: lease.update(on=True))
    assert (filled.written, filled.missing, filled.deferred) == (8, 33, True) and filled.reason == index.DEFERRED
    assert filled.note == "vetores adiados: gravando (33 trechos)"


def test_the_sweep_completes_old_meetings_with_the_local_only_after_reindex_now(store, local):
    old = _long_meeting(store, 3, "Antiga")
    index.sync_text(store, old)
    assert store.rag.count_vectors() == 0 and store.rag.model_confirmed(KEY)    # seria o primeiro modelo do índice...
    assert index.sweep(store) == 0 and not index.model_confirmed(store, KEY)     # ...mas o local espera o "Reindexar agora" (Q3)
    assert index.reindex_all(store) == 1 and index.model_confirmed(store, KEY)
    job = store.jobs.claim(pid=5, kinds=("index",))
    assert job is not None and IndexJobRunner(store, job, heartbeat_thread=False).run() == JobStatus.DONE
    newer = _long_meeting(store, 2, "Nova")
    index.sync_text(store, newer)
    assert index.sweep(store) == 1                                              # confirmado: a varredura completa as outras


def test_query_vector_of_the_local_is_free_logged_and_the_search_is_hybrid(store, local, caplog):
    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid)
    with caplog.at_level(logging.INFO, logger="listener.rag.search"):
        qv = search.embed_query(store, "orçamento 2")
    assert qv.vector is not None and qv.vector.shape == (384,) and qv.model_key == KEY and qv.cost_usd == 0.0 and qv.reason == ""
    assert "vetor da consulta (local/multilingual-e5-small@384) em" in caplog.text
    (call,) = [dict(r) for r in store._q("SELECT provider, model, cost_usd, strategy FROM llm_calls WHERE strategy = 'query'")]
    assert call == {"provider": "local", "model": "multilingual-e5-small", "cost_usd": 0.0, "strategy": "query"}
    assert local.f.tokenizer is not None and local.f.tokenizer.batches[-1] == ["query: orçamento 2"]
    result = search.search(store, "orçamento", query_vector=qv)
    assert result.hybrid and result.hits


def test_a_local_that_does_not_load_leaves_a_note_without_network_jargon(store, tmp_path, monkeypatch):
    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    ready_folder(models)
    broken = Factories(fail_session=OSError("[WinError 126] o módulo especificado não foi encontrado"))
    monkeypatch.setattr(local_embed, "_SESSION", broken.local_session())
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    mid = _long_meeting(store, 4)
    status, info = _index_job(store, mid)
    assert status == JobStatus.DONE and info["note"] == f"5 trechos sem vetor: {local_embed.LOAD_FAILED}" and "rede" not in info["note"]
    errors = [c for c in store.llm_calls.list_for_meeting(mid) if c["status"] == "erro"]
    assert len(errors) == 1 and errors[0]["error"] == local_embed.LOAD_FAILED


def test_the_light_worker_uses_two_threads_and_logs_the_versions(store, local, tmp_path):
    from listener import log as applog
    from listener.pipeline import worker

    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = _long_meeting(store, 3)
    job_id = index.enqueue_index(store, mid, "edit")
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(job_id)
    assert row["status"] == JobStatus.DONE and json.loads(row["timings_json"])["index"]["vectors"] == 4
    assert local.f.sessions and {threads for _path, threads in local.f.sessions} == {worker.LOCAL_EMBED_THREADS}
    applog.flush()
    text = (tmp_path / "logs" / "listener-llm.log").read_text(encoding="utf-8")
    for dist in ("onnxruntime", "tokenizers"):
        try:
            assert f"{dist} {metadata.version(dist)}" in text
        except metadata.PackageNotFoundError:                                  # o CI instala só o [dev]
            continue


# ---------------------------------------------------------------- telas
def test_providers_offer_local_always_and_save_one_update_per_role(qtbot, store, platform, monkeypatch, tmp_path):
    from listener.ui import settings_min
    from listener.ui.settings_min import SettingsDialog

    store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))      # a pasta real já tem o e5 (A-13)
    asked: list[str] = []

    def ask(_parent: Any, text: str, _yes: str, _no: str) -> bool:
        asked.append(text)
        return False

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    tabs = dlg.provider_tabs
    i = tabs.active_embed.findData("local")
    assert i >= 0 and tabs.active_embed.itemText(i) == "Local (e5-small, sem custo)" and store.providers.get("local") is None
    assert tabs.active_embed.findData("anthropic") < 0 and tabs.active_llm.findData("local") < 0
    tabs.active_embed.setCurrentIndex(i)
    dlg._save()                                                                # não baixada: nada a oferecer
    assert store.providers.active_provider("embed")["id"] == "local" and store.providers.active_provider("llm")["id"] == "gemini"
    assert asked == [] and dlg.result() == dlg.DialogCode.Accepted
    dlg2 = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg2)
    assert dlg2.provider_tabs.active_embed.currentData() == "local" and dlg2.local_status.text() == "não baixada"
    dlg2.provider_tabs.active_embed.setCurrentIndex(0)                         # "nenhum"
    dlg2._save()
    assert store.providers.active_provider("embed") is None and store.providers.active_provider("llm")["id"] == "gemini"


def test_switching_to_a_ready_local_offers_reindexing_with_minutes_and_no_cost(qtbot, store, platform, local, monkeypatch):
    from listener.ui import settings_min
    from listener.ui.settings_min import SettingsDialog, offer_local_reindex

    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    store.providers.set_role("embed", "gemini")
    asked: list[str] = []
    answer = {"value": False}

    def ask(_parent: Any, text: str, yes: str, no: str) -> bool:
        asked.append(f"{text}|{yes}|{no}")
        return answer["value"]

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    assert dlg.local_status.text().startswith("pronta")
    dlg.provider_tabs.active_embed.setCurrentIndex(dlg.provider_tabs.active_embed.findData("local"))
    dlg._save()
    assert len(asked) == 1 and "agora é a busca local (e5-small, sem custo)" in asked[0] and asked[0].endswith("|Reindexar agora|Depois")
    assert "sem custo · ≈ 1 s nesta máquina" in asked[0] and "US$" not in asked[0] and store.jobs.active(kinds=("index",)) == []
    launched: list[bool] = []
    answer["value"] = True
    assert offer_local_reindex(store, None, launched.append) == 1 and launched == [True]               # A-12
    assert "A busca local está pronta" in asked[1] and "calculados nesta máquina, sem custo" in asked[1]
    assert index.model_confirmed(store, KEY)
    store.jobs.request_cancel(int(store.jobs.active(kinds=("index",))[0]["id"]))
    index.fill_vectors(store, mid)
    assert offer_local_reindex(store, None, launched.append) == 0 and len(asked) == 2                  # nada a indexar: não pergunta


def _rag_child(models: Path, gate: threading.Event):
    from listener.live.protocol import encode_line

    def run(_stdin: BinaryIO, stdout: BinaryIO) -> int:
        stdout.write(encode_line({"type": "progress", "model": "modelo", "status": "baixando", "fraction": 0.0}))
        stdout.flush()
        gate.wait(5.0)
        ready_folder(models)
        for line in ({"type": "progress", "model": "vocabulário", "status": "pronto", "fraction": 1.0},
                     {"type": "done", "models_dir": str(models / "multilingual-e5-small")}):
            stdout.write(encode_line(line))
        stdout.flush()
        return 0
    return run


def test_settings_prepares_the_local_and_each_group_shows_its_own_download(qtbot, store, platform, monkeypatch, tmp_path):
    from listener.ui.live_prepare import ModelPreparer
    from listener.ui.settings_min import SettingsDialog
    from tests.fake_live import ThreadChild

    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    prep = ModelPreparer()
    gate = threading.Event()
    spawned: list[list[str]] = []

    def spawn(argv: list[str], _env: Any = None) -> ThreadChild:
        spawned.append(argv)
        return ThreadChild(_rag_child(models, gate), pid=7300, argv=argv)

    monkeypatch.setattr(platform, "spawn_child", spawn)
    dlg = SettingsDialog(store, platform, {}, model_preparer=prep)
    qtbot.addWidget(dlg)
    assert dlg.local_btn.text() == "Preparar busca local (135 MB)" and dlg.local_btn.isEnabled() and dlg.local_bar.isHidden()
    assert dlg.local_status.text() == "não baixada"
    try:
        dlg.local_btn.click()
        qtbot.waitUntil(lambda: prep.state.get("model") == "modelo", timeout=5000)
        assert spawned[0][-5:] == ["--prepare", "--what", "rag", "--db", str(store.base_dir)] and prep.state["what"] == "rag"
        assert dlg.local_status.text() == "modelo: baixando…" and not dlg.local_btn.isEnabled() and not dlg.local_bar.isHidden()
        assert not dlg.prepare_btn.isEnabled() and "outro download em curso" in dlg.prepare_status.text()     # um por vez (A-08)
        assert "baixando" not in dlg.prepare_status.text() and dlg.prepare_bar.isHidden()                       # cada grupo, o seu
        dlg.prepare_models()
        dlg.prepare_local()
        assert prep.starts == 1 and len(spawned) == 1
    finally:
        gate.set()
    qtbot.waitUntil(lambda: prep.state["state"] == "done", timeout=5000)
    assert dlg.local_status.text().startswith("pronta") and dlg.local_bar.value() == 100 and dlg.prepare_btn.isEnabled()
    dlg3 = SettingsDialog(store, platform, {}, model_preparer=prep, recording=True)
    qtbot.addWidget(dlg3)
    assert not dlg3.local_btn.isEnabled() and dlg3.local_status.text().endswith("prepare depois de parar a gravação")


def test_history_preloads_the_local_session_and_releases_it_after_ten_idle_minutes(qtbot, store, local):
    from listener.ui.history import LOCAL_IDLE_S, HistoryPage

    page = HistoryPage(store)
    qtbot.addWidget(page)
    try:
        page.refresh()
        qtbot.waitUntil(local_embed.loaded, timeout=5000)
        assert local.f.sessions == [(local.folder / local_embed.ONNX_FILE, 1)] and page._local_timer.isActive()
        qtbot.waitUntil(lambda: not page._preloading, timeout=5000)
        assert page.preload_local() is False                                     # já aberta: nada a fazer
        local.clock.t += LOCAL_IDLE_S - 1
        page._release_local()
        assert local_embed.loaded() and page._local_timer.isActive()
        local.clock.t += 2
        page._release_local()
        assert not local_embed.loaded() and not page._local_timer.isActive()
        page.search.setText("orçamento")
        page.run_search(with_vector=True)                                        # pode reabrir a sessão: a conferência volta
        assert page._local_timer.isActive()
        qtbot.waitUntil(lambda: page.result is not None, timeout=5000)
        store.providers.set_role("embed", "gemini")
        assert page.preload_local() is False and not local_embed.loaded()       # só com o local ativo e pronto
    finally:
        page.shutdown()


def test_controller_offers_reindex_when_the_local_prepare_ends_and_sweeps_after_recordings(qtbot, qapp, platform, store, local,
                                                                                           monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.ui import controller as ctl
    from listener.ui import settings_min
    from listener.ui.live_prepare import ModelPreparer

    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    prep = ModelPreparer()
    monkeypatch.setattr(ctl, "preparer", lambda: prep)
    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)                  # nada de PortAudio aqui
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    asked: list[tuple[Any, ...]] = []
    answer = {"value": False}
    launched: list[bool] = []

    def ask(*args: Any) -> bool:
        asked.append(args)
        return answer["value"]

    def launch(light: bool = False) -> bool:
        launched.append(light)
        return True

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(c, "launch_worker", launch)
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)

    def defer_last_index() -> None:
        job = store.jobs.enqueue_index(mid, "edit") or 0
        assert store.jobs.claim(pid=6, kinds=("index",))["id"] == job
        store.jobs.finish(job, JobStatus.DONE, timings={"index": {"deferred": True, "vectors": 0}})

    try:
        c.start()
        assert local_embed.shared().threads == ctl.APP_LOCAL_THREADS
        for state in ({"state": "done", "what": "live"}, {"state": "running", "what": "rag"}, {"state": "error", "what": "rag"}):
            prep.changed.emit(state)
        qtbot.wait(50)
        assert asked == []
        prep.changed.emit({"state": "done", "what": "rag"})                    # "Depois", no próximo giro do laço
        assert asked == []
        qtbot.waitUntil(lambda: len(asked) == 1, timeout=5000)
        assert asked[0][2:] == ("Reindexar agora", "Depois") and "sem custo" in asked[0][1]
        assert store.jobs.active(kinds=("index",)) == []
        answer["value"] = True
        prep.changed.emit({"state": "done", "what": "rag"})                    # "Reindexar agora"
        qtbot.waitUntil(lambda: len(asked) == 2, timeout=5000)
        assert [int(j["meeting_id"]) for j in store.jobs.active(kinds=("index",))] == [mid] and True in launched
        store.jobs.request_cancel(int(store.jobs.active(kinds=("index",))[0]["id"]))
        index.fill_vectors(store, mid)                                         # a varredura só volta por causa do adiamento
        # parar e cancelar a gravação rodam a varredura: o `index` adiado pela gravação volta à fila (A-07)
        for finish in (c.stop_recording, c.cancel_recording):
            defer_last_index()
            launched.clear()
            assert c.start_recording(cfg, {"title": "gravando"})
            platform.pump(0.6)
            finish()
            qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=5000)
            qtbot.waitUntil(lambda: True in launched, timeout=5000)
            pending = store.jobs.active(kinds=("index",))
            assert [int(j["meeting_id"]) for j in pending] == [mid] and json.loads(pending[0]["params_json"])["reason"] == "sweep"
            store.jobs.request_cancel(int(pending[0]["id"]))
    finally:
        c.emergency_stop()
    prep.changed.emit({"state": "done", "what": "rag"})                        # depois da saída: desligado
    qtbot.wait(50)
    assert len(asked) == 2
