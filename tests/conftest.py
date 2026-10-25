"""Fixtures comuns (F1.2 M-27): banco temporário, plataforma simulada com relógio manual e logging restaurado."""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from listener import log as applog
from listener.capture.clock import ManualClock
from listener.capture.recorder import RecordConfig, Recorder, SourceSpec
from listener.store import Store
from tests.fake_platform import FakePlatform

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Cada teste começa e termina com o logger do app como estava (nível, handlers, listener)."""
    logger = logging.getLogger(applog.LOGGER_NAME)
    level, handlers = logger.level, list(logger.handlers)
    yield
    applog.shutdown()
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "db") as s:
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
