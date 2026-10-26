"""Fixtures comuns (F1.2 M-27): banco temporário, plataforma simulada com relógio manual e logging restaurado.
F2: o worker em subprocesso real (`--worker --once`, `LISTENER_PIPELINE=fake`) é lançado já na coleta, quando
`tests/test_worker.py` está na sessão, para o seu tempo de execução correr em paralelo ao resto da suíte. F4: o mesmo
para o processo ao vivo (`--live`, `LISTENER_LIVE=fake`) quando `tests/test_live_child.py` está na sessão.
D-050: suíte rápida e lenta. Todo teste com `qtbot` ou `qapp` ganha a marca `lento` (telas); subprocesso, tempo real e
contas pesadas são marcados nos arquivos. O padrão local pula os lentos (`addopts` no pyproject); `--tudo` roda todos (o
CI e antes de commitar). Os subprocessos da coleta só nascem quando os testes deles ficaram na seleção."""
from __future__ import annotations

import gc
import importlib.util
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


@pytest.fixture(autouse=True)
def collect_qt_garbage(request: pytest.FixtureRequest) -> Iterator[None]:
    """Testes de interface terminam com a coleta de lixo na thread principal: um diálogo preso num ciclo de referências,
    coletado depois por uma thread do gravador, derruba o processo (segfault do Qt fora da thread da GUI, visto em 25/10
    com `test_llm_review.py` seguido de `test_llm_settings_warmup.py`)."""
    yield
    if "qtbot" in request.fixturenames:
        gc.collect()


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


def pipeline_installed() -> bool:
    """O extra `pipeline` neste venv: o worker "real" (R2-27) processaria de verdade e baixaria modelos, então nem nasce."""
    return any(importlib.util.find_spec(name) is not None for name in ("faster_whisper", "pyannote", "torch"))


def start_worker_process(root: Path, fake: bool = True) -> dict[str, Any]:
    """Grava uma reunião de 3 s (dois tons no R), enfileira o job e lança `python -m listener --worker --db DIR --once`.
    Com `fake=False` o worker tenta os adaptadores reais: sem o extra `pipeline` o job termina em erro permanente. A pasta
    de modelos fica dentro de `root`: o `HF_HOME` que o worker real cria antes de falhar não vai para a pasta do usuário."""
    from listener.capture.recorder import SourceSpec

    db = root / "db"
    platform = FakePlatform(root, r_format=(16000, 1), system_format=(16000, 1))
    with Store(db) as store:
        store.upsert_me("Eu", role="Gerente")
        store.set_setting("log.path", str(root / "logs" / "listener.log"))
        store.set_setting("pipeline.models_dir", str(root / "models"))
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


LIVE_RUN: dict[str, Any] = {}        # preenchido por `start_live_process`; lido por tests/test_live_child.py


def start_live_process(root: Path) -> dict[str, Any]:
    """Uma reunião no banco e `python -m listener --live --db DIR --meeting ID` em modo fake, com o stdin vindo de um
    arquivo (R fala 1,2 s e pausa 2 s; L fala 0,9 s): nada bloqueia a coleta."""
    from listener.live.protocol import SILENCED, Hello
    from tests.fake_live import session_input, tone

    db = root / "db"
    with Store(db) as store:
        store.set_setting("log.path", str(root / "logs" / "listener.log"))
        mid = store.create_meeting("subprocesso ao vivo", None, None, None)
    data = session_input(Hello(1000.0), [("R", tone(1.2), 0), ("R", 32000, SILENCED), ("L", tone(0.9, 440), 0)])
    (root / "stdin.bin").write_bytes(data)
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_LIVE"} | {"LISTENER_LIVE": "fake", "PYTHONUTF8": "1"}
    argv = [sys.executable, "-m", "listener", "--live", "--db", str(db), "--meeting", str(mid)]
    with (root / "stdin.bin").open("rb") as stdin:
        proc = subprocess.Popen(argv, env=env, cwd=str(Path(__file__).resolve().parent.parent), stdin=stdin,  # noqa: S603
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"proc": proc, "db": db, "root": root, "meeting_id": mid, "argv": argv}


UI_FIXTURES = frozenset({"qtbot", "qapp"})


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--tudo", action="store_true", help="roda também os testes marcados `lento` (D-050: o CI e antes de commitar)")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--tudo"):
        config.option.markexpr = ""                          # sem filtro de marca: a suíte inteira


def pytest_itemcollected(item: pytest.Item) -> None:
    """Telas (pytest-qt) são lentas por natureza: a marca vem da fixture, também para os testes de tela que vierem."""
    if UI_FIXTURES & set(getattr(item, "fixturenames", ())):
        item.add_marker(pytest.mark.lento)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    skipped_slow = len(terminalreporter.stats.get("deselected", []))
    if skipped_slow and config.option.markexpr == "not lento":
        terminalreporter.write_line(f"{skipped_slow} teste(s) lento(s) fora desta rodada: antes de commitar, "
                                    "`python -m pytest -q --tudo` (D-050)")


@pytest.hookimpl(trylast=True)                               # depois da seleção por marca: só lança o que vai rodar
def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    if any(item.path.name == "test_worker.py" for item in items) and not WORKER_RUN:
        WORKER_RUN.update(start_worker_process(Path(tempfile.mkdtemp(prefix="listener-worker-test-"))))
        if not pipeline_installed():
            WORKER_RUN["real"] = start_worker_process(Path(tempfile.mkdtemp(prefix="listener-worker-real-")), fake=False)
    if any(item.path.name == "test_live_child.py" and item.name.startswith("test_live_process") for item in items) and not LIVE_RUN:
        LIVE_RUN.update(start_live_process(Path(tempfile.mkdtemp(prefix="listener-live-test-"))))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for run in (WORKER_RUN, WORKER_RUN.get("real") or {}, LIVE_RUN):
        if run and "proc" in run:
            proc = run["proc"]
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            shutil.rmtree(run["root"], ignore_errors=True)
    if "dir" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["dir"].parent, ignore_errors=True)
