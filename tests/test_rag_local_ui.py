"""F5.1 L6, as telas da busca local, com a sessão e o tokenizer falsos e a pasta de modelos no tmp (A-13): o combo "Local"
sempre e um `UPDATE` por papel (A-01), a troca para o local pronto com a estimativa sem custo (A-05), o grupo "Busca" com o
preparo e o progresso de cada grupo, um download por vez (A-08), a pré-carga e a soltura da sessão no Histórico (A-11), a
pergunta do fim do preparo (A-12) e a varredura ao parar e ao cancelar a gravação (A-07)."""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from listener.llm import local_embed
from listener.model import AppState, JobStatus
from listener.rag import index, search
from listener.store import Store
from tests.fake_local import KEY, Local, ready_folder, use_local
from tests.fake_local import long_meeting as _long_meeting


@pytest.fixture(autouse=True)
def extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """O CI instala só o `[dev]`: o extra `local` conta como instalado (as fábricas da sessão são falsas)."""
    monkeypatch.setattr(local_embed, "extra_installed", lambda: True)
    monkeypatch.delenv("LISTENER_LLM", raising=False)


@pytest.fixture
def local(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Local]:
    search.clear_caches()
    yield use_local(store, tmp_path, monkeypatch)
    search.clear_caches()


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


def _controller(qapp: Any, platform: Any, store: Store, monkeypatch: pytest.MonkeyPatch, prep: Any) -> tuple[Any, list[bool]]:
    """O controlador sem bandeja, sem o pré-aquecimento do áudio, sem o diálogo do fim da gravação e com o lançamento dos
    workers trocado por uma lista; o preparador é o do teste."""
    from PySide6.QtWidgets import QMessageBox

    from listener.ui import controller as ctl

    monkeypatch.setattr(ctl, "preparer", lambda: prep)
    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)                  # nada de PortAudio aqui
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    launched: list[bool] = []

    def launch(light: bool = False) -> bool:
        launched.append(light)
        return True

    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(c, "launch_worker", launch)
    return c, launched


def test_controller_offers_reindex_when_the_local_prepare_ends(qtbot, qapp, platform, store, local, monkeypatch):
    """A-12: o fim do preparo da busca local, com o local ativo e pronto, pergunta "Reindexar agora/Depois" no próximo giro do
    laço; os outros estados e o ao vivo não perguntam; depois da saída, nada."""
    from listener.ui import controller as ctl
    from listener.ui import settings_min
    from listener.ui.live_prepare import ModelPreparer

    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    prep = ModelPreparer()
    asked: list[tuple[Any, ...]] = []
    answer = {"value": False}

    def ask(*args: Any) -> bool:
        asked.append(args)
        return answer["value"]

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    c, launched = _controller(qapp, platform, store, monkeypatch, prep)
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
    finally:
        c.emergency_stop()
    prep.changed.emit({"state": "done", "what": "rag"})                        # depois da saída: desligado
    qtbot.wait(50)
    assert len(asked) == 2


def test_stopping_or_cancelling_the_recording_sweeps_the_deferred_vectors(qtbot, qapp, platform, store, local, monkeypatch):
    """A-07: parar e cancelar a gravação rodam a varredura, e o `index` que o local adiou durante a gravação volta à fila (as
    outras razões da varredura ficam de fora: os vetores da reunião já existem e o modelo não está confirmado)."""
    from listener.capture.recorder import RecordConfig, SourceSpec
    from listener.ui.live_prepare import ModelPreparer

    mid = _long_meeting(store, 3)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid)
    c, launched = _controller(qapp, platform, store, monkeypatch, ModelPreparer())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        for finish in (c.stop_recording, c.cancel_recording):
            job = store.jobs.enqueue_index(mid, "edit") or 0                    # o último `index` da reunião adiou os vetores
            assert store.jobs.claim(pid=6, kinds=("index",))["id"] == job
            store.jobs.finish(job, JobStatus.DONE, timings={"index": {"deferred": True, "vectors": 0}})
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
