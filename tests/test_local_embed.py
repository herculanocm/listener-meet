"""F5.1 L6, o embedder local sem o ONNX nem o tokenizer reais (A-13): pooling médio com a máscara e L2, prefixos, corte em
512 tokens contado, lotes, sessão única por processo (threads, reabertura, soltura por ociosidade, o lock entre o cálculo, a
soltura e a pré-carga: R10-06), erros sem jargão (nunca "sem rede", A-10), o estado pelo extra e pela marca `.ready` desta
revisão, o registro sem chave nem SSL (A-02), a linha `local` só no caminho de escrita (A-01) e os `index` adiados pela
gravação ou com o local falho (A-07, R10-04). O preparo está em `tests/test_local_prepare.py`."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from listener.llm import local_embed, local_prepare
from listener.llm.base import ProviderUnavailableError
from listener.llm.types import Capability, EmbedTask, ProviderKind
from listener.model import JobKind, JobStatus
from tests.fake_local import Factories, expected_vectors, ready_folder


@pytest.fixture(autouse=True)
def extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """O CI instala só o `[dev]`: o extra `local` conta como instalado, salvo onde o teste diz que não (as fábricas são falsas)."""
    monkeypatch.setattr(local_embed, "extra_installed", lambda: True)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_session_pools_with_the_mask_prefixes_counts_cuts_and_batches(tmp_path):
    folder = ready_folder(tmp_path / "modelos")
    f = Factories()
    session = f.local_session(batch=2)
    texts = ["orçamento de 2027 aprovado", "a Ana manda a planilha", "auditoria"]
    vecs, tokens = session.embed(folder, texts, EmbedTask.DOCUMENT)
    assert f.session is not None and f.tokenizer is not None
    prefixed = [f"passage: {t}" for t in texts]
    assert f.tokenizer.batches == [prefixed[:2], prefixed[2:]] and len(f.session.feeds) == 2           # lotes de `batch`
    assert vecs.shape == (3, local_embed.DIM) and vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)
    assert np.allclose(vecs, expected_vectors(f.session, f.tokenizer, prefixed), atol=1e-5)               # o enchimento não vaza
    assert tokens == sum(len(f.tokenizer.ids(p)[0]) for p in prefixed)                                   # pela máscara
    feed = f.session.feeds[0]
    assert set(feed) == {"input_ids", "attention_mask", "token_type_ids"} and not feed["token_type_ids"].any()
    assert f.session.output_names[0] == [local_embed.OUTPUT]
    query, _ = session.embed(folder, ["quando começa a auditoria"], EmbedTask.QUERY)
    assert f.tokenizer.batches[-1] == ["query: quando começa a auditoria"]
    assert np.allclose(query, expected_vectors(f.session, f.tokenizer, ["query: quando começa a auditoria"]), atol=1e-5)
    long = " ".join(f"palavra{i}" for i in range(600))                                                   # passa de 512 tokens
    session.embed(folder, [long, "curto"], EmbedTask.DOCUMENT)
    assert session.cuts == 1 and session.loads == 1                                                        # uma sessão só
    empty, n = session.embed(folder, [], EmbedTask.DOCUMENT)
    assert empty.shape == (0, local_embed.DIM) and n == 0


def test_mean_pool_ignores_padding_and_survives_an_empty_mask():
    hidden = np.array([[[1.0, 0.0], [3.0, 0.0], [100.0, 100.0]], [[0.0, 2.0], [0.0, 0.0], [0.0, 0.0]]], np.float32)
    mask = np.array([[1, 1, 0], [0, 0, 0]])
    out = local_embed.mean_pool(hidden, mask)
    assert np.allclose(out[0], [1.0, 0.0]) and np.allclose(out[1], [0.0, 0.0])                         # sem divisão por zero


def test_session_loads_once_reopens_on_threads_or_folder_and_releases_when_idle(tmp_path):
    folder = ready_folder(tmp_path / "modelos")
    clock = Clock()
    f = Factories()
    session = f.local_session(clock=clock)
    session.configure(2)
    assert session.preload(folder) >= 0.0 and session.loaded and f.sessions == [(folder / local_embed.ONNX_FILE, 2)]
    assert session.preload(folder) == 0.0 and session.loads == 1                                         # já aberta
    assert f.tokenizers == [folder / local_embed.TOKENIZER_FILE]
    session.configure(2)
    assert session.loaded                                                                                  # mesmo número: fica
    session.configure(1)
    assert not session.loaded                                                                              # reabre com 1 no uso
    session.embed(folder, ["x"], EmbedTask.QUERY)
    assert f.sessions[-1] == (folder / local_embed.ONNX_FILE, 1) and session.loads == 2
    other = ready_folder(tmp_path / "outra")
    session.embed(other, ["x"], EmbedTask.QUERY)
    assert session.loads == 3 and f.sessions[-1][0] == other / local_embed.ONNX_FILE                     # outra pasta: reabre
    clock.t += 599
    assert session.release_if_idle(600) is False and session.loaded                                     # ainda não
    clock.t += 2
    held = threading.Event()
    release = threading.Event()

    def busy() -> None:
        with session._lock:                                                                                # um cálculo em curso
            held.set()
            release.wait(5)

    t = threading.Thread(target=busy)
    t.start()
    held.wait(5)
    assert session.release_if_idle(600) is False and session.loaded                                     # em uso: não solta
    release.set()
    t.join(5)
    assert session.release_if_idle(600) is True and not session.loaded
    assert session.release_if_idle(600) is False and session.release() is False


def test_the_lock_covers_the_calculation_against_release_and_a_concurrent_preload(tmp_path):
    """R10-06: com um cálculo em curso (o `run` preso), a soltura não acontece; e uma pré-carga lenta numa thread com um
    `embed` na outra abrem uma sessão só (sem o lock no `embed`, as duas carregariam)."""
    folder = ready_folder(tmp_path / "modelos")
    f = Factories()
    session = f.local_session()
    session.embed(folder, ["aquece"], EmbedTask.QUERY)
    assert f.session is not None
    real_run, running, release = f.session.run, threading.Event(), threading.Event()

    def stuck_run(names: Any, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        running.set()
        release.wait(5)
        return real_run(names, feed)

    f.session.run = stuck_run                                                          # type: ignore[method-assign]
    worker = threading.Thread(target=session.embed, args=(folder, ["preso"], EmbedTask.QUERY))
    worker.start()
    assert running.wait(5)
    try:
        assert session.release_if_idle(0) is False and session.loaded                                    # em uso: fica
    finally:
        release.set()
        worker.join(5)
    assert session.release_if_idle(0) is True
    g, loading, gate = Factories(), threading.Event(), threading.Event()

    def slow_session(path: Path, threads: int) -> Any:
        loading.set()
        gate.wait(5)
        return g.make_session(path, threads)

    lazy = local_embed.LocalSession(slow_session, g.make_tokenizer)
    pre = threading.Thread(target=lazy.preload, args=(folder,))
    pre.start()
    assert loading.wait(5)
    user = threading.Thread(target=lazy.embed, args=(folder, ["consulta"], EmbedTask.QUERY))
    user.start()
    time.sleep(0.2)                                                                    # o `embed` chega ao lock (ou à carga)
    gate.set()
    pre.join(5)
    user.join(5)
    assert lazy.loads == 1 and len(g.sessions) == 1 and g.session is not None and len(g.session.feeds) == 1


def test_errors_become_unavailable_without_network_jargon(tmp_path, monkeypatch):
    from listener.rag.index import _reason

    folder = local_embed.model_dir(str(tmp_path / "modelos"))
    f = Factories()
    with pytest.raises(ProviderUnavailableError, match="não baixada"):
        f.local_session().embed(folder, ["x"], EmbedTask.QUERY)
    assert f.sessions == []                                                                                # nem tentou abrir
    ready_folder(tmp_path / "modelos")
    broken = Factories(fail_session=OSError("[WinError 126] onnxruntime.dll não carregou"))
    with pytest.raises(ProviderUnavailableError) as err:
        broken.local_session().embed(folder, ["x"], EmbedTask.QUERY)
    assert str(err.value) == local_embed.LOAD_FAILED and "rede" not in _reason(err.value)                # um OSError não vira "sem rede"
    missing = Factories(fail_tokenizer=ImportError("No module named 'tokenizers'"))
    with pytest.raises(ProviderUnavailableError, match="extra local"):
        missing.local_session().embed(folder, ["x"], EmbedTask.QUERY)
    session = f.local_session()
    session.embed(folder, ["x"], EmbedTask.QUERY)
    assert f.session is not None

    def out_of_memory(*_a: Any) -> list[np.ndarray]:
        raise MemoryError("bad allocation")

    monkeypatch.setattr(f.session, "run", out_of_memory)
    with pytest.raises(ProviderUnavailableError) as err:
        session.embed(folder, ["y"], EmbedTask.QUERY)
    assert str(err.value) == local_embed.RUN_FAILED and "bad allocation" not in _reason(err.value)
    monkeypatch.setattr(local_embed, "extra_installed", lambda: False)
    with pytest.raises(ProviderUnavailableError, match="extra local"):
        Factories().local_session().embed(folder, ["x"], EmbedTask.QUERY)
    provider = local_embed.LocalEmbedProvider(models_dir=str(tmp_path / "modelos"), session=f.local_session())
    with pytest.raises(ProviderUnavailableError, match="só calcula"):
        run(provider.complete(None))  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError, match="384"):
        run(provider.embed(["x"], model=local_embed.MODEL, dim=768, task=EmbedTask.QUERY))


def test_provider_embeds_reports_tokens_and_keeps_the_session_open(tmp_path):
    ready_folder(tmp_path / "modelos")
    f = Factories()
    session = f.local_session()
    provider = local_embed.LocalEmbedProvider(provider_id="local", models_dir=str(tmp_path / "modelos"), session=session)
    assert provider.kind == ProviderKind.LOCAL and provider.capabilities == frozenset({Capability.EMBEDDINGS})
    emb = run(provider.embed(["orçamento", "prazo do relatório"], model="qualquer", dim=None, task=EmbedTask.DOCUMENT))
    assert (emb.model, emb.dim, emb.vectors.shape) == (local_embed.MODEL, 384, (2, 384)) and emb.usage.input_tokens > 0
    run(provider.aclose())
    assert session.loaded                                                                                  # A-03: fechar não solta
    test = run(provider.test_connection("x"))
    assert test.ok and test.model == local_embed.MODEL and run(provider.count_tokens(None)) is None  # type: ignore[arg-type]
    assert [m.id for m in run(provider.list_models())] == [local_embed.MODEL]


def test_status_needs_the_extra_and_the_mark_of_this_revision(tmp_path, monkeypatch):
    from listener.platform.common import paths

    models = tmp_path / "modelos"
    assert local_embed.model_dir("") == paths.data_dir_suggestion() / "models" / "multilingual-e5-small"
    assert local_embed.model_dir(str(models)) == models / "multilingual-e5-small"
    assert local_embed.status(str(models)) == (False, local_embed.NOT_DOWNLOADED)
    folder = ready_folder(models)
    assert local_embed.status(str(models)) == (True, "")
    mark = json.loads((folder / ".ready").read_text(encoding="utf-8"))
    assert mark["repo"] == local_embed.REPO and mark["revision"] == local_embed.REVISION and mark["files"] == local_embed.FILES
    assert mark["checked_at"] and not (folder / ".ready.tmp").exists()
    (folder / ".ready").write_text(json.dumps(mark | {"revision": "0" * 40}), encoding="utf-8")
    assert local_embed.status(str(models))[0] is False                                                   # outra revisão: de novo
    local_prepare.write_ready(folder)
    (folder / local_embed.TOKENIZER_FILE).unlink()
    assert local_embed.status(str(models)) == (False, local_embed.NOT_DOWNLOADED)                       # arquivo sumiu
    (folder / ".ready").write_text("{lixo", encoding="utf-8")
    assert local_embed.is_ready(folder) is False
    monkeypatch.setattr(local_embed, "extra_installed", lambda: False)
    assert local_embed.status(str(models)) == (False, local_embed.NO_EXTRA)
    assert "onnxruntime" not in sys.modules and "tokenizers" not in sys.modules                         # A-13


def test_registry_loads_local_without_key_nor_ssl(store, tmp_path, monkeypatch):
    from listener.llm import registry
    from listener.llm.fake import FakeProvider

    monkeypatch.delenv("LISTENER_LLM", raising=False)
    monkeypatch.setattr(registry, "prepare_ssl", lambda _net: (_ for _ in ()).throw(AssertionError("o local não usa SSL")))
    provider = registry.load_provider("local", None, models_dir=str(tmp_path / "m"))
    assert isinstance(provider, local_embed.LocalEmbedProvider) and provider.folder == tmp_path / "m" / "multilingual-e5-small"
    store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    built, row = registry.build_provider(store, role="embed")
    assert isinstance(built, registry.RetryingProvider) and row["id"] == "local"
    assert isinstance(built.inner, local_embed.LocalEmbedProvider) and built.inner.folder == tmp_path / "modelos" / "multilingual-e5-small"
    monkeypatch.setenv("LISTENER_LLM", "fake")
    fake = registry.load_provider("local", None)
    assert isinstance(fake, FakeProvider) and fake.capabilities == frozenset({Capability.EMBEDDINGS})


def test_local_row_is_born_only_on_write_and_each_role_is_one_update(store, tmp_path):
    from listener.rag.index import embedder_status

    store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))
    assert store.providers.get("local") is None
    assert embedder_status(store).provider_id == "gemini" and store.providers.get("local") is None      # leitura não cria
    store.providers.ensure_local()
    store.providers.ensure_local()                                                                       # idempotente
    store.providers.set_role("embed", "local")
    assert store.providers.active_provider("embed")["id"] == "local" and store.providers.get("local")["api_key"] is None
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-123456")
    store.providers.set_role("llm", "gemini")
    store.providers.set_role("embed", "gemini")
    assert store.providers.active_provider("embed")["id"] == "gemini" and store.providers.get("local")["is_active_embed"] == 0
    store._x("UPDATE providers SET is_active_llm = 1 WHERE id = 'local'")                                # o local nunca é de texto
    store.providers.set_role("llm", None)
    assert store.providers.active_provider("llm") is None
    store._x("UPDATE providers SET is_active_llm = 1 WHERE id = 'local'")
    assert store.providers.active_provider("llm") is None
    store.providers.set_role("embed", None)
    assert store.providers.active_provider("embed") is None


def test_deferred_index_meetings_are_the_ones_whose_last_index_deferred(store):
    meetings = [store.create_meeting(f"m{i}", None, None, None) for i in range(3)]

    def index_job(mid: int, deferred: bool | None, status: JobStatus = JobStatus.DONE) -> int:
        job = store.jobs.enqueue(mid, JobKind.INDEX, {"reason": "sweep"})
        info = {} if deferred is None else {"index": {"deferred": deferred, "note": "x"}}
        store.jobs.finish(job, status, timings=info)
        return job

    index_job(meetings[0], True)
    index_job(meetings[1], True)
    index_job(meetings[1], False)                                                                        # o último não adiou
    index_job(meetings[2], None)
    store._x("UPDATE processing_jobs SET timings_json = '{lixo' WHERE meeting_id = ?", (meetings[2],))   # JSON inválido não quebra
    assert store.jobs.deferred_index_meetings() == [meetings[0]]
    store.jobs.enqueue(meetings[0], JobKind.INDEX, {"reason": "sweep"})                                  # já há outro na fila
    assert store.jobs.deferred_index_meetings() == []
