"""Worker em subprocesso real (F2 L4): `python -m listener --worker --db DIR --once` com `LISTENER_PIPELINE=fake`, lançado na
coleta por `conftest.py`; comando e ambiente do worker; `models.py` sem rede e sem torch; estimativa de tempo."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from listener.model import JobStatus
from listener.pipeline import estimate
from listener.pipeline.base import PermanentError
from listener.pipeline.types import JobConfig
from listener.platform.common import process
from listener.store import Store
from tests import conftest


def test_worker_process_runs_one_job_with_own_log_and_low_priority():
    run = conftest.WORKER_RUN
    assert run, "o worker devia ter sido lançado na coleta (conftest.pytest_collection_modifyitems)"
    _out, err = run["proc"].communicate(timeout=90)
    assert run["proc"].returncode == 0, err
    assert "Traceback" not in err
    with Store.open_readonly(run["db"]) as store:
        job = store.jobs.get(run["job_id"])
        assert job["status"] == JobStatus.DONE and job["progress"] == 1.0 and job["heartbeat_at"] and job["worker_pid"] is None
        assert json.loads(job["config_json"])["pipeline.whisper_model"] == "large-v3-turbo"           # snapshot no claim
        timings = json.loads(job["timings_json"])
        assert timings["diarize"]["model"] == "fake:tone" and timings["diarize"]["audio_s"] == pytest.approx(3.0, abs=0.05)
        clusters = store.segments.list_clusters(run["meeting_id"])
        assert sorted(c["channel"] for c in clusters) == ["L", "R", "R"]
        assert store.get_meeting(run["meeting_id"])["status"] == "processada"
        assert store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0
        events = [e["type"] for e in store.list_events(run["meeting_id"])]
        assert "job_start" in events and "job_done" in events
    log_path = Path(run["root"]) / "logs" / "listener-worker.log"
    text = log_path.read_text(encoding="utf-8")
    assert "papel worker" in text and "prioridade baixa: ok" in text and "job 1 concluído" in text
    assert "RSS" in text                                                                           # memória logada por etapa
    app_log = Path(run["root"]) / "logs" / "listener.log"
    assert not app_log.exists() or "papel worker" not in app_log.read_text(encoding="utf-8")       # log próprio, não o do app


def test_worker_without_the_pipeline_extra_fails_permanently_with_a_clear_message():
    """R2-27: `--worker` sem LISTENER_PIPELINE=fake nesta máquina (sem torch): erro permanente, attempts 0, sem traceback."""
    run = conftest.WORKER_RUN.get("real")
    assert run, "o segundo worker devia ter sido lançado na coleta"
    _out, err = run["proc"].communicate(timeout=90)
    assert run["proc"].returncode == 0 and "erro permanente" in err                           # o traceback vai para o log, de propósito
    with Store.open_readonly(run["db"]) as store:
        job = store.jobs.get(run["job_id"])
        assert job["status"] == JobStatus.ERROR and job["attempts"] == 0 and "pip install" in job["error"]
        assert store.segments.list_clusters(run["meeting_id"]) == [] and store.get_meeting(run["meeting_id"])["status"] == "gravada"
    text = (Path(run["root"]) / "logs" / "listener-worker.log").read_text(encoding="utf-8")
    assert "erro permanente" in text


def test_worker_command_env_and_memory():
    assert process.worker_command() == [sys.executable, "-m", "listener", "--worker"]
    assert process.worker_command(light=True) == [sys.executable, "-m", "listener", "--worker", "--light"]
    env = process.worker_env(6, {"PATH": "/x"})
    assert env["OMP_NUM_THREADS"] == env["MKL_NUM_THREADS"] == "6" and env["CT2_VERBOSE"] == "0" and env["PATH"] == "/x"
    assert process.worker_env(0)["OMP_NUM_THREADS"] == "1"
    rss = process.memory_rss()
    if sys.platform in ("linux", "win32"):
        assert rss is not None and rss > 10_000_000                                          # R2-27: aqui tem de medir
    else:
        assert rss is None or rss > 0
    from listener.__main__ import main

    assert main(["--worker", "--db", "/caminho/que/nao/existe"]) == 3                             # banco ausente: erro claro, sem traceback


def test_models_helpers_without_network(tmp_path, monkeypatch):
    from listener.pipeline import models

    cfg = JobConfig(models_dir=str(tmp_path / "m"), whisper_model="small", torch_threads=3)
    assert models.models_dir(JobConfig()).name == "models" and models.whisper_dir(cfg) == tmp_path / "m" / "faster-whisper-small"
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    values = models.apply_environment(cfg)
    assert os.environ["HF_HOME"] == str(tmp_path / "m" / "hf") and values["OMP_NUM_THREADS"] == "3" and "HF_HUB_OFFLINE" not in os.environ
    (tmp_path / "m" / "faster-whisper-small").mkdir(parents=True)
    (tmp_path / "m" / "faster-whisper-small" / "model.bin").write_bytes(b"x")
    models.ready_mark(cfg, "pyannote").write_text("ok")
    from types import SimpleNamespace

    fake_hub = SimpleNamespace(constants=SimpleNamespace(HF_HUB_OFFLINE=False))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    assert models.apply_environment(cfg)["HF_HUB_OFFLINE"] == "1"                                 # tudo baixado: offline
    assert fake_hub.constants.HF_HUB_OFFLINE is True                                              # R2-19: no módulo já importado
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    models.ready_mark(cfg, "pyannote").unlink()
    with pytest.raises(PermanentError, match="token do Hugging Face ausente"):
        models.prepare_models(cfg, "", lambda m, f: None)
    (tmp_path / "m" / "faster-whisper-small" / "model.bin").unlink()
    with pytest.raises(PermanentError, match="pip install"):                                        # sem faster-whisper aqui
        models.prepare_models(cfg, "hf_x", lambda m, f: None)
    assert isinstance(models.classify_error(ModuleNotFoundError("torch")), PermanentError)
    assert "termos" in str(models.classify_error(RuntimeError("403 Client Error: gated repo"), "pyannote/x"))
    assert "sem rede" in str(models.classify_error(OSError("Max retries exceeded"), "r"))
    assert "offline" in str(models.classify_error(RuntimeError("OfflineModeIsEnabled"), "r"))
    assert models.classify_error(ValueError("outra coisa")) is None
    assert models.deps_key(cfg) == ("small", False, 3, str(tmp_path / "m"), "")
    assert models.deps_key(cfg, "hf_a") != models.deps_key(cfg, "hf_b") and "hf_a" not in str(models.deps_key(cfg, "hf_a"))   # R2-13
    deps = models.real_deps(cfg, "hf_x")
    assert deps.name == "real" and not deps.prepared
    transcriber = deps.transcriber()                                      # o adaptador carrega sem torch...
    assert transcriber.name == "faster-whisper:small:int8"
    from listener.pipeline.types import AudioWindow

    with pytest.raises(PermanentError, match="pip install"):            # ...e o primeiro uso sem o extra tem mensagem própria
        transcriber.transcribe(AudioWindow(0, "R", 0, 1000, np.zeros(16000, np.float32)), language=None, initial_prompt="")
    assert models._adapter("whisper", "FasterWhisperTranscriber") is type(transcriber)
    with pytest.raises(PermanentError, match="pip install"):
        models._adapter("inexistente", "X")
    for name in ("listener.pipeline.whisper", "listener.pipeline.pyannote", "listener.pipeline.models"):
        sys.modules.pop(name, None)
    for k in ("HF_HOME", "HF_HUB_OFFLINE", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "CT2_VERBOSE", "TOKENIZERS_PARALLELISM"):
        monkeypatch.delenv(k, raising=False)


def test_estimate_uses_seeds_then_measurements():
    seeds = 0.8 * 3600 + 0.05 * 3600 + 0.67 * 0.6 * 3600 + 0.67 * 0.3 * 3600
    assert estimate.remaining_seconds("diarize", 0.0, 3600, "{}") == pytest.approx(seeds)
    assert estimate.remaining_seconds(None, 0.0, 3600, "{}") == pytest.approx(seeds)                     # pendente: tudo pela frente
    half = estimate.remaining_seconds("diarize", 0.225, 3600, "{}")
    assert half == pytest.approx(seeds - 0.4 * 3600)                                                    # metade da diarização feita
    measured = json.dumps({"diarize:pyannote": 0.5, "transcribe_r:fw": 0.4})
    timings = json.dumps({"diarize": {"audio_s": 3600, "speech_s": 0, "wall_s": 1800, "model": "pyannote"},
                          "transcribe_r": {"audio_s": 1800, "speech_s": 900, "wall_s": 360, "model": "fw"}})
    rem = estimate.remaining_seconds("transcribe_r", 0.45 + 0.05 + 0.33 / 2, 3600, measured, timings)
    assert rem == pytest.approx(0.4 * 0.5 * 3600 * 0.5 + 0.67 * 0.3 * 3600)                             # fala medida: 50 %
    assert estimate.remaining_seconds("segments", 1.0, 3600, "{}") is None and estimate.remaining_seconds("x", 0.1, None, "{}") is None
    assert estimate.remaining_seconds("minutes", 0.1, 3600, "{}", kind="llm") is None                    # F3: sem RTF sobre áudio
    assert estimate.fmt_remaining(None) == "sem estimativa" and estimate.fmt_remaining(30) == "menos de 1 min"
    assert estimate.fmt_remaining(23 * 60) == "≈ 23 min" and estimate.fmt_remaining(3700) == "≈ 1 h 01 min"
    assert estimate.rtf_for("diarize", {"diarize:a": 0.9}, "b") == 0.9 and estimate.rtf_for("embed", {}, None) == 0.05


def test_light_worker_serves_only_llm_jobs_in_process(tmp_path, store):
    """F3 item 2: `_serve(light=True)` em processo pega só jobs `llm`, escreve `listener-llm.log` e deixa o pesado na fila."""
    from listener.model import JobKind
    from listener.pipeline import worker
    from listener.ui.queue import job_row_texts

    store.set_setting("log.path", str(tmp_path / "logs" / "listener.log"))
    mid = store.create_meeting("Reunião", None, None, None)
    store.finish_meeting(mid, 5000)
    heavy = store.jobs.enqueue(mid)
    llm = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": heavy})
    texts = job_row_texts(store.jobs.get(llm), store.get_meeting(mid), "{}")
    assert texts[1] == "LLM" and texts[5] == "sem estimativa"
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0
    row = store.jobs.get(llm)
    assert row["status"] in ("erro", "concluído") and row["worker_pid"] is None
    assert store.jobs.get(heavy)["status"] == "pendente"                                          # o leve não toca no pesado
    from listener import log as applog

    applog.flush()                                                                                  # o QueueListener grava em thread
    text = (tmp_path / "logs" / "listener-llm.log").read_text(encoding="utf-8")
    assert "papel worker-llm" in text and f"job {llm}" in text
    assert worker._serve(store, once=True, idle_exit_s=0.0, poll_s=0.0, light=True) == 0           # nada a fazer: sai
