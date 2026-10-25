"""Fixtures comuns (F1.2 M-27): banco temporário, plataforma simulada com relógio manual e logging restaurado.
F2: o worker em subprocesso real (`--worker --once`, `LISTENER_PIPELINE=fake`) é lançado já na coleta, quando
`tests/test_worker.py` está na sessão, para o seu tempo de execução correr em paralelo ao resto da suíte."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from listener import log as applog
from listener.capture.clock import ManualClock
from listener.capture.recorder import RecordConfig, Recorder, SourceSpec
from listener.store import Store
from tests.fake_platform import FakePlatform

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Cada teste começa e termina com o logger do app como estava (nível, handlers, propagação, listener). Os handlers de
    captura do pytest ficam fora da foto: o pytest 9 os pendura em todo logger sem propagação durante cada fase e os tira
    ele mesmo; devolvê-los no teardown os deixaria presos no logger (F3)."""
    logger = logging.getLogger(applog.LOGGER_NAME)
    level, propagate = logger.level, logger.propagate
    handlers = [h for h in logger.handlers if type(h).__name__ != "LogCaptureHandler"]
    yield
    applog.shutdown()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


_TEMPLATE: dict[str, Path] = {}


def template_db() -> Path:
    """Banco já migrado, criado uma vez por sessão: copiar custa 1 ms contra 29 ms de migrar v1..v4 a cada teste."""
    if "dir" not in _TEMPLATE:
        root = Path(tempfile.mkdtemp(prefix="listener-template-"))
        with Store(root / "db"):
            pass
        _TEMPLATE["dir"] = root / "db"
    return _TEMPLATE["dir"]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    dst = tmp_path / "db"
    shutil.copytree(template_db(), dst)
    with Store(dst) as s:
        yield s


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def platform(tmp_path: Path, clock: ManualClock) -> FakePlatform:
    return FakePlatform(tmp_path, clock=clock)


def make_config(**kw: object) -> RecordConfig:
    base: dict[str, object] = {"source": SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), "chunk_seconds": 0.5,
                               "silence_rms": 0.01, "l_alert_seconds": 1.0, "r_alert_seconds": 1.0, "retry_base_s": 0.001,
                               "stop_timeout_s": 0.2}
    base.update(kw)
    return RecordConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def recorder_factory(platform: FakePlatform, store: Store):
    """Cria gravadores em linha (sem thread) ligados ao banco e à plataforma simulada."""
    def make(cfg: RecordConfig | None = None, sink: object = None, **kw: object) -> Recorder:
        mid = store.create_meeting("t", "meeting", None, None)
        return Recorder(platform, sink or store, mid, cfg or make_config(), clock=platform.clock, inline_worker=True, **kw)  # type: ignore[arg-type]
    return make


WORKER_RUN: dict[str, Any] = {}      # preenchido por `start_worker_process`; lido por tests/test_worker.py


def start_worker_process(root: Path, fake: bool = True) -> dict[str, Any]:
    """Grava uma reunião de 3 s (dois tons no R), enfileira o job e lança `python -m listener --worker --db DIR --once`.
    Com `fake=False` o worker tenta os adaptadores reais: sem o extra `pipeline` o job termina em erro permanente."""
    from listener.capture.recorder import SourceSpec

    db = root / "db"
    platform = FakePlatform(root, r_format=(16000, 1), system_format=(16000, 1))
    with Store(db) as store:
        store.upsert_me("Eu", role="Gerente")
        store.set_setting("log.path", str(root / "logs" / "listener.log"))
        mid = store.create_meeting("Reunião do worker", "meeting", None, "Ana")
        rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
        rec.start()
        platform.pump(1.5)
        rec.switch_source(SourceSpec("system", label="Sistema"))
        platform.pump(1.5)
        rec.stop()
        job_id = store.jobs.enqueue(mid)
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_PIPELINE"} | {"PYTHONUTF8": "1"}
    if fake:
        env["LISTENER_PIPELINE"] = "fake"
    argv = [sys.executable, "-m", "listener", "--worker", "--db", str(db), "--once"]
    proc = subprocess.Popen(argv, env=env, cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,  # noqa: S603
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    return {"proc": proc, "db": db, "root": root, "meeting_id": mid, "job_id": job_id, "argv": argv}


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    if any(item.path.name == "test_worker.py" for item in items) and not WORKER_RUN:
        WORKER_RUN.update(start_worker_process(Path(tempfile.mkdtemp(prefix="listener-worker-test-"))))
        WORKER_RUN["real"] = start_worker_process(Path(tempfile.mkdtemp(prefix="listener-worker-real-")), fake=False)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for run in (WORKER_RUN, WORKER_RUN.get("real") or {}):
        if run and "proc" in run:
            proc = run["proc"]
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            shutil.rmtree(run["root"], ignore_errors=True)
    if "dir" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["dir"].parent, ignore_errors=True)
