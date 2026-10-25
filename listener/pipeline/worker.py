"""Processo do worker (F2 item 2): `python -m listener --worker [--db DIR] [--once]`.

Prioridade baixa, log próprio (`listener-worker.log` na pasta do log do app: dois processos no mesmo `RotatingFileHandler`
quebram a rotação no Windows), `Store` no papel WORKER, claim em laço com o snapshot das `pipeline.*`, `JobRunner` por job,
modelos carregados uma vez por processo e soltos ao ficar ocioso, saída após 60 s sem trabalho (o app relança quando
houver fila). `LISTENER_PIPELINE=fake` usa os fakes de `tests/fake_pipeline.py` (testes e CI, sem torch).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import suppress
from importlib import metadata
from pathlib import Path
from typing import Any

from ..config import bootstrap
from ..config.defaults import HF_PROVIDER_ID
from ..log import attach_file, get_logger, log_versions, setup
from ..platform.common import paths, process
from ..store import Store
from .base import PipelineDeps
from .runner import JobRunner
from .types import JobConfig

log = get_logger("worker")

IDLE_EXIT_S = 60.0
POLL_S = 2.0
RELEASE_AFTER_S = 10.0
WORKER_LOG = "listener-worker.log"
ML_DISTS = ("torch", "ctranslate2", "faster-whisper", "pyannote.audio", "huggingface-hub")


def worker_log_path(store: Store) -> Path:
    base = store.get_setting("log.path") or str(store.default_log_path())
    return Path(base).expanduser().parent / WORKER_LOG


def _platform_hooks() -> tuple[Any, Any]:
    """(`lower_own_priority`, `memory_rss`) da plataforma; a versão portável quando a camada não existe (Linux antes da F7)."""
    try:
        from ..platform import get_platform

        p = get_platform()
    except NotImplementedError:
        return process.lower_own_priority, process.memory_rss
    return p.lower_own_priority, p.memory_rss


def build_deps(config: JobConfig, token: str, fake: bool) -> PipelineDeps:
    if fake:
        try:
            from tests.fake_pipeline import FakeDeps
        except ImportError as e:
            raise RuntimeError("LISTENER_PIPELINE=fake exige a pasta tests/ do repositório no caminho") from e
        return FakeDeps()
    from .models import real_deps

    return real_deps(config, token)


def config_snapshot(store: Store) -> str:
    snap = store.settings.settings_with_prefix("pipeline.")
    snap["capture.silence_rms"] = store.get_setting("capture.silence_rms")
    return json.dumps(snap, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --worker", description="worker da fila de processamento (F2)")
    ap.add_argument("--db", help="pasta do banco (padrão: o ponteiro da configuração do sistema)")
    ap.add_argument("--once", action="store_true", help="processa no máximo um job e sai")
    ap.add_argument("--idle-exit", type=float, default=IDLE_EXIT_S)
    ap.add_argument("--poll", type=float, default=POLL_S)
    args = ap.parse_args(argv)
    db_dir = Path(args.db) if args.db else bootstrap.read_db_dir(paths.config_dir())
    if db_dir is None:
        print("worker: pasta do banco desconhecida; use --db DIR", file=sys.stderr)  # noqa: T201
        return 2
    setup("INFO")
    try:
        store = Store.open_worker(db_dir)
    except Exception as e:
        print(f"worker: não abri o banco em {db_dir}: {e}", file=sys.stderr)  # noqa: T201
        return 3
    with store:
        return _serve(store, once=args.once, idle_exit_s=args.idle_exit, poll_s=args.poll)


def _serve(store: Store, once: bool, idle_exit_s: float, poll_s: float) -> int:
    with suppress(Exception):
        attach_file(worker_log_path(store), store.get_setting("log.level"))
    versions = {}
    for dist in ML_DISTS:
        with suppress(metadata.PackageNotFoundError):
            versions[dist] = metadata.version(dist)
    log_versions({"papel": "worker", "pid": str(os.getpid()), "banco": str(store.base_dir), **versions})
    lower, memory = _platform_hooks()
    log.info("prioridade baixa: %s", "ok" if lower() else "não aplicada")
    fake = os.environ.get("LISTENER_PIPELINE", "").lower() == "fake"
    deps: PipelineDeps | None = None
    deps_key: tuple[Any, ...] | None = None
    idle_since: float | None = None
    released = False
    processed = 0
    while True:
        job = store.jobs.claim(os.getpid(), config_snapshot(store))
        if job is not None:
            idle_since, released = None, False
            config = JobConfig.from_json(job.get("config_json"))
            from .models import deps_key as key_of

            key = ("fake",) if fake else key_of(config)
            if deps is None or key != deps_key:
                deps, deps_key = build_deps(config, store.settings.provider_key(HF_PROVIDER_ID), fake), key
            log.info("job %s (reunião %s, %s) iniciado", job["id"], job["meeting_id"], job.get("kind"))
            status = JobRunner(store, deps, job, memory_rss=memory).run()
            log.info("job %s terminou: %s", job["id"], status)
            processed += 1
            if once:
                break
            continue
        if once:
            break
        now = time.monotonic()
        idle_since = idle_since if idle_since is not None else now
        if now - idle_since >= idle_exit_s:
            log.info("worker ocioso há %.0f s: encerrando (%d job(s) processado(s))", now - idle_since, processed)
            break
        if deps is not None and not released and now - idle_since >= RELEASE_AFTER_S:
            deps.release()
            released = True
        time.sleep(poll_s)
    return 0
