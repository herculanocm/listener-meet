"""F5.18, lote 1 (PF-01, PF-02, PF-03) com os fakes, sem modelos: a lease e o cancelamento conferidos dentro da janela (o gancho
do pyannote, o gerador do Whisper, os laços do embedder), a pausa fora do `wall_s`, o throttle de 2 s das leituras da lease, a
prioridade do processo seguindo a lease pela thread do heartbeat e o lote dos embeddings do pyannote em pares."""
from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from listener.model import JobStatus
from listener.pipeline.jobbase import JobCancelledError
from listener.pipeline.runner import JobRunner
from listener.pipeline.types import AudioWindow, DiarizationWindow, JobConfig, Turn
from tests.fake_pipeline import FakeDeps
from tests.fake_platform import FakePlatform
from tests.test_runner import CFG, SCRIPT, T0, _record, _run

LEASE = (T0 + timedelta(minutes=10)).isoformat(timespec="milliseconds")


@pytest.fixture
def platform(tmp_path, clock):
    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


@pytest.fixture
def forget_adapters():
    """Os adaptadores saem do `sys.modules` ao fim: a regra dura (test_runner) confere que o app não os puxa."""
    yield
    for name in ("listener.pipeline.whisper", "listener.pipeline.pyannote", "listener.pipeline.models"):
        sys.modules.pop(name, None)


def _claimed(platform: FakePlatform, store: Any) -> tuple[int, Any]:
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    return job_id, store.jobs.claim(pid=1)


# ---------------------------------------------------------------- PF-01: a lease e o cancelamento dentro da janela
def test_lease_written_inside_the_window_pauses_the_job_and_resumes_without_redoing_it(platform, store):
    """Aceite 2: a lease escrita no meio da 1ª janela da diarização leva o job a `pausado`/WAITING pelo gancho do fake (não entre
    janelas), apagada o devolve a `executando` e a janela segue de onde estava (uma chamada ao fake por janela); o tempo em pausa,
    medido com `sleep` de verdade, fica fora do `wall_s` da etapa."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    seen: list[tuple[str, str | None]] = []
    written: list[int] = []

    def during(window: AudioWindow, i: int) -> None:
        if window.channel == "R" and i == 1 and not written:             # a 1ª janela do R da diarização, no meio dela
            written.append(window.idx)
            store.set_setting("pipeline.paused_until", LEASE)

    def sleep(seconds: float) -> None:
        row = store.jobs.get(job_id)
        seen.append((row["status"], row["step"]))
        time.sleep(seconds)                                               # tempo real: a pausa tem de ficar fora do wall_s
        if len(seen) == 3:
            store.set_setting("pipeline.paused_until", "")

    deps = FakeDeps(SCRIPT, during=during)
    _, status = _run(store, deps, now=lambda: T0, sleep=sleep, wait_poll_s=0.1, lease_poll_s=0.0)
    assert status == JobStatus.DONE and written == [0]
    assert seen == [("pausado", "waiting")] * 3                           # 3 x 0,1 s em pausa, dentro da janela
    assert [c[0] for c in deps.tones.calls] == [0, 1]                     # a janela 0 não foi refeita
    assert deps.tones.hooks == 2 * 3 and deps.scripted.hooks == len(deps.scripted.calls) * 3
    job = store.jobs.get(job_id)
    wall = json.loads(job["timings_json"])["diarize"]["wall_s"]
    assert job["status"] == "concluído" and 0 <= wall < 0.15, wall        # a pausa (0,3 s) saiu do wall_s
    assert "diarize:fake:tone" in json.loads(store.get_setting("pipeline.rtf_json"))


def test_cancel_requested_inside_the_window_cancels_the_job_from_the_hook(platform, store):
    """O pedido de cancelamento lido pelo gancho sobe como `JobCancelledError` de dentro do fake e cai no `run()` como `cancelado`,
    sem esperar a janela acabar; nenhum `sleep` (não é pausa) e a janela corrente nem chega a ser gravada."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    acted: list[tuple[int, int]] = []

    def during(window: AudioWindow, i: int) -> None:
        if window.channel == "R" and i == 1 and not acted:
            acted.append((window.idx, i))
            store.jobs.request_cancel(job_id)

    deps = FakeDeps(SCRIPT, during=during)
    _, status = _run(store, deps, sleep=lambda s: pytest.fail("cancelar não é pausar"))
    assert status == JobStatus.CANCELLED and store.jobs.get(job_id)["status"] == "cancelado" and acted == [(0, 1)]
    assert [c[0] for c in deps.tones.calls] == [0] and deps.tones.hooks == 0 and deps.scripted.calls == []
    assert store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0 and store.segments.list_clusters(mid) == []
    ev = next(e for e in store.list_events(mid) if e["type"] == "job_cancel")
    assert ev["data"]["step"] == "diarize"


def test_in_window_checkpoint_throttles_the_lease_but_not_the_cancel(platform, store, monkeypatch):
    """Duas chamadas seguidas leem a lease uma vez só (throttle de 2 s) e o cancelamento duas; com `lease_poll_s = 0` toda
    chamada lê. Os dois checkpoints devolvem os segundos em pausa: 0.0 sem ela."""
    job_id, job = _claimed(platform, store)
    reads: list[str] = []
    real_get = store.settings.get_setting

    def spy(key: str, default: str | None = None) -> str:
        reads.append(key)
        return real_get(key, default)

    monkeypatch.setattr(store.settings, "get_setting", spy)
    cancels: list[int] = []
    real_cancel = store.jobs.cancel_requested

    def cancel_spy(jid: int) -> bool:
        cancels.append(jid)
        return real_cancel(jid)

    monkeypatch.setattr(store.jobs, "cancel_requested", cancel_spy)
    runner = JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False)       # o throttle padrão, 2 s
    assert runner.in_window_checkpoint() == 0.0 and runner.in_window_checkpoint() == 0.0
    assert reads.count("pipeline.paused_until") == 1 and cancels == [job_id, job_id]
    eager = JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False, lease_poll_s=0.0)
    reads.clear()
    assert eager.in_window_checkpoint() == 0.0 and eager.in_window_checkpoint() == 0.0
    assert reads.count("pipeline.paused_until") == 2 and runner.checkpoint() == 0.0
    # com a pausa manual, o checkpoint espera e devolve o que esperou; o job volta a `executando`
    store.set_setting("pipeline.paused_manual", True)

    def release(seconds: float) -> None:
        time.sleep(seconds)
        store.set_setting("pipeline.paused_manual", False)

    runner.sleep = release
    runner.wait_poll_s = 0.05
    assert runner.checkpoint() >= 0.05 and store.jobs.get(job_id)["status"] == "rodando"
    assert eager.in_window_checkpoint() == 0.0                                                  # a pausa já caiu
    store.jobs.request_cancel(job_id)
    with pytest.raises(JobCancelledError):
        runner.in_window_checkpoint()


def test_embedder_loops_call_the_checkpoint_every_ten_turns(platform, store, monkeypatch):
    """`_embed_window` (um forward do wespeaker por turno limpo) e `_embed_runs` (por trecho com fala do L) passam pelo checkpoint
    de dentro da janela a cada 10 turnos."""
    _, job = _claimed(platform, store)
    deps = FakeDeps(SCRIPT)
    runner = JobRunner(store, deps, job, config=CFG, heartbeat_thread=False)
    calls: list[int] = []

    def count() -> float:
        calls.append(1)
        return 0.0

    monkeypatch.setattr(runner, "in_window_checkpoint", count)
    sr = 16000
    t = np.arange(40 * sr, dtype=np.float32) / sr
    w = AudioWindow(0, "R", 0, 40_000, (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    turns = tuple(Turn(i * 1600, i * 1600 + 1600, f"SPEAKER_0{i % 2}") for i in range(25))     # 25 turnos limpos de 1,6 s
    out = runner._embed_window(w, DiarizationWindow(0, 0, 40_000, turns))
    assert len(calls) == 2 and out.embeddings is not None and set(out.embeddings) == {"SPEAKER_00", "SPEAKER_01"}
    calls.clear()
    loud = (0.5 * np.sin(2 * np.pi * 220 * t[: int(1.6 * sr)])).astype(np.float32)
    samples = np.concatenate([np.concatenate([loud, np.zeros(int(0.4 * sr), np.float32)]) for _ in range(15)])    # 15 trechos
    wl = AudioWindow(0, "L", 0, 30_000, samples)
    vec, ms = runner._embed_runs(wl, deps.tone_embedder, hook=runner.window_hook())
    assert len(calls) == 1 and ms == 15 * 1600 and vec.shape == (256,)
    assert runner._embed_runs(wl, deps.tone_embedder)[1] == ms and len(calls) == 1               # sem o gancho, igual


def test_whisper_calls_on_segment_between_the_generator_steps(tmp_path, forget_adapters):
    """`on_segment` roda uma vez por segmento, entre dois `next()` do gerador (depois de o segmento sair, antes de tratá-lo);
    sem ele o laço segue igual; a exceção dele sobe de dentro do laço."""
    from listener.pipeline.whisper import FasterWhisperTranscriber

    events: list[str] = []

    def segments() -> Any:
        for i in range(3):
            events.append(f"seg{i}")
            yield SimpleNamespace(start=i * 1.0, end=i * 1.0 + 0.8, no_speech_prob=0.1, avg_logprob=-0.2,
                                  words=[SimpleNamespace(word=f" w{i}", start=i * 1.0, end=i * 1.0 + 0.5, probability=0.9)])

    class Model:
        def transcribe(self, samples: np.ndarray, **kw: Any) -> tuple[Any, Any]:
            return segments(), SimpleNamespace(language="pt", language_probability=0.9)

    t = FasterWhisperTranscriber(tmp_path / "faster-whisper-small", "small")
    t._model = Model()
    win = AudioWindow(0, "R", 0, 3000, np.zeros(48000, np.float32))
    tw = t.transcribe(win, language="pt", initial_prompt="", on_segment=lambda: events.append("hook"))
    assert events == ["seg0", "hook", "seg1", "hook", "seg2", "hook"] and len(tw.words) == 3 and tw.speech_ms == 2400
    events.clear()
    assert len(t.transcribe(win, language="pt", initial_prompt="").words) == 3 and events == ["seg0", "seg1", "seg2"]

    def boom() -> None:
        raise JobCancelledError

    with pytest.raises(JobCancelledError):
        t.transcribe(win, language="pt", initial_prompt="", on_segment=boom)


# ---------------------------------------------------------------- PF-02: a prioridade pela thread do heartbeat
def test_heartbeat_thread_follows_the_lease_with_the_process_priority(platform, store):
    """A thread do heartbeat (aqui a cada 20 ms) vê a lease e pede a classe ociosa (True); a lease apagada pede a volta (False);
    o job que acaba com a lease ainda escrita volta no `_stop_heartbeat`, já `concluído`. O job em si não pausa (o teste age
    dentro de uma janela do fake, sem gancho)."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    duration = int(store.get_meeting(mid)["duration_ms"])
    calls: list[tuple[bool, str]] = []

    def set_priority(idle: bool) -> bool:
        calls.append((idle, str(store.jobs.get(job_id)["status"])))
        return True

    def wait_for(n: int) -> None:
        deadline = time.monotonic() + 5.0
        while len(calls) < n and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(calls) >= n, calls

    def acts(window: AudioWindow) -> None:
        if (window.channel, window.idx) == ("R", 0):
            store.set_setting("pipeline.paused_until", LEASE)
            wait_for(1)
            store.set_setting("pipeline.paused_until", "")
            wait_for(2)
        elif window.channel == "L" and window.t1_ms >= duration:          # a última janela: o job acaba com a lease escrita
            store.set_setting("pipeline.paused_until", LEASE)
            wait_for(3)

    deps = FakeDeps(SCRIPT, before=acts, hook_calls=0)
    _, status = _run(store, deps, now=lambda: T0, heartbeat_thread=True, heartbeat_s=0.02, set_priority=set_priority,
                     sleep=lambda s: pytest.fail("o job não devia pausar"))
    assert status == JobStatus.DONE
    assert calls == [(True, "rodando"), (False, "rodando"), (True, "rodando"), (False, "concluído")]
    assert len(store.core._conns) == 1                                     # a conexão da thread do heartbeat foi fechada


def test_light_runner_and_runner_without_the_hook_never_touch_the_priority(platform, store):
    """Sem `set_priority` (plataforma sem o controle) nada é chamado; o runner que ignora a lease (`pause_on_record` desligado,
    como o leve) também não, mesmo com a lease escrita."""
    store.upsert_me("Eu")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    calls: list[bool] = []

    def set_priority(idle: bool) -> bool:
        calls.append(idle)
        return True

    store.set_setting("pipeline.paused_until", LEASE)
    cfg = JobConfig(**{**CFG.to_dict(), "pause_on_record": False})
    _, status = _run(store, FakeDeps(SCRIPT), config=cfg, now=lambda: T0, heartbeat_thread=True, heartbeat_s=0.01,
                     set_priority=set_priority, sleep=lambda s: pytest.fail("ignora a lease"))
    assert status == JobStatus.DONE and calls == []


def test_platform_hooks_carry_the_priority_hook_and_the_fallbacks(monkeypatch, tmp_path):
    """`_platform_hooks()` devolve 4 itens, com a versão portável (sempre False) quando a camada não existe; o fake anota os
    pedidos; o padrão do Protocol é False."""
    from listener import platform as platform_pkg
    from listener.pipeline import worker
    from listener.platform.base import Platform
    from listener.platform.common import process

    def no_platform() -> Any:
        raise NotImplementedError("sem camada")

    monkeypatch.setattr(platform_pkg, "get_platform", no_platform)
    hooks = worker._platform_hooks()
    assert len(hooks) == 4 and hooks[3] is process.set_own_priority
    assert process.set_own_priority(True) is False and process.set_own_priority(False) is False
    fake = FakePlatform(tmp_path)
    assert fake.set_own_priority(True) and fake.set_own_priority(False) and fake.priorities == [True, False]
    assert Platform.set_own_priority(fake, True) is False


# ---------------------------------------------------------------- PF-03: o lote dos embeddings
def test_job_config_reads_and_writes_the_embedding_batch(store):
    from listener.config.defaults import PIPELINE_KEYS, SPECS

    assert JobConfig().embedding_batch == 8 and JobConfig.from_json(None).to_dict()["embedding_batch"] == 8
    assert JobConfig.from_json('{"pipeline.embedding_batch": "8"}').embedding_batch == 8
    assert JobConfig.from_json('{"pipeline.embedding_batch": "99"}').embedding_batch == 8          # fora da faixa: o padrão
    spec = SPECS["pipeline.embedding_batch"]
    assert spec.advanced and (spec.low, spec.high, spec.phase) == (4, 32, "F5.18") and "pipeline.embedding_batch" in PIPELINE_KEYS
    store.set_setting("pipeline.embedding_batch", "16")
    assert JobConfig.from_settings(store.settings.settings_with_prefix("pipeline.")).embedding_batch == 16


def test_diarizer_sets_the_embedding_batch_in_pairs_and_the_hook_lets_exceptions_through(forget_adapters):
    """O `embedding_batch_size` do pyannote conta pares (trecho, falante): 8 trechos x 3 falantes locais = 24 por padrão, os
    falantes lidos da instância quando ela os expõe; um objeto sem o atributo fica como está. O `hook` ignora os argumentos do
    pyannote e deixa a exceção subir."""
    from listener.pipeline import pyannote as pa

    class Pipe:
        embedding_batch_size = 32

    pipe = Pipe()
    pa.PyannoteDiarizer("", torch_threads=2)._set_embedding_batch(pipe)
    assert pipe.embedding_batch_size == 24
    pipe._segmentation = SimpleNamespace(model=SimpleNamespace(specifications=SimpleNamespace(classes=["a", "b"])))   # type: ignore[attr-defined]
    pa.PyannoteDiarizer("", embedding_batch=4)._set_embedding_batch(pipe)
    assert pipe.embedding_batch_size == 8
    pa.PyannoteDiarizer("")._set_embedding_batch(object())                                        # sem o atributo: nada muda
    seen: list[int] = []
    pa._hook(lambda: seen.append(1))("segmentation", None, file={}, total=3, completed=1)
    assert seen == [1]

    def boom() -> None:
        raise JobCancelledError

    with pytest.raises(JobCancelledError):
        pa._hook(boom)("embeddings", None, file={}, total=3, completed=2)
    assert "torch" not in sys.modules
