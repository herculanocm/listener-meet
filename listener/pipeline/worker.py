"""Processo do worker (F2 item 2): `python -m listener --worker [--db DIR] [--once] [--light]`.

Prioridade baixa, log próprio (`listener-worker.log` na pasta do log do app: dois processos no mesmo `RotatingFileHandler`
quebram a rotação no Windows), `Store` no papel WORKER, claim em laço com o snapshot das `pipeline.*`, `JobRunner` por job,
modelos carregados uma vez por processo e soltos ao ficar ocioso, saída após 60 s sem trabalho (o app relança quando
houver fila). `LISTENER_PIPELINE=fake` usa os fakes de `tests/fake_pipeline.py` (testes e CI, sem torch).
F3: `--light` é o worker da classe leve (só jobs `llm`): nunca importa torch, tem log próprio (`listener-llm.log`) e roda
mesmo durante uma gravação; o pesado, ao concluir um job que enfileirou tarefas de LLM, lança o leve se ele não estiver vivo.
F5: o leve também roda o `index` (despacho por `kind`); com `--once` nenhum worker lança o leve (os testes com subprocesso
não deixam um worker solto, F5A-16). O leve não sai ocioso enquanto houver `index` esperando o "não antes de" das edições
(R5-07): a espera é do job, não do app.
F5.1: no leve, o embedder local calcula com 2 threads (A-03) e o log de abertura traz as versões de onnxruntime e tokenizers.
D-058: o vigia dos laços do asyncio (`aio.watched`) fica ligado no worker: uma chamada presa em código nativo por
`aio.STALL_S` faz o worker gravar o motivo e o traceback e sair; o heartbeat vence, o app relança e o job é retomado.
F5.6 (A-37, F5A-17): cada job roda protegido (`_guarded`): uma falha fora do runner (a reunião excluída no meio do job, o
banco) volta à fila com a tentativa contada (o claim esgota, R-04) e o worker segue para o próximo, em vez de cair. R-01: ao
fim de cada job o worker solta os arquivos de áudio (o mapa de memória de 256 MB impede, no Windows, que o arquivo encolha
depois de uma exclusão); o próximo job os anexa de novo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from ..config import bootstrap
from ..config.defaults import HF_PROVIDER_ID
from ..llm import aio
from ..log import attach_file, dump_all_threads, get_logger, log_versions, setup, shutdown
from ..model import HEAVY_JOB_KINDS, LIGHT_JOB_KINDS, JobKind, JobStatus
from ..platform.common import paths, process
from ..store import Store
from .base import PermanentError, PipelineDeps
from .runner import JobRunner
from .types import JobConfig

log = get_logger("worker")

IDLE_EXIT_S = 60.0
POLL_S = 2.0
RELEASE_AFTER_S = 10.0
WORKER_LOG = "listener-worker.log"
LLM_WORKER_LOG = "listener-llm.log"
ML_DISTS = ("torch", "ctranslate2", "faster-whisper", "pyannote.audio", "huggingface-hub")
LLM_DISTS = ("google-genai", "openai", "anthropic", "truststore", "httpx", "onnxruntime", "tokenizers")   # F5.1: o embedder local
LOCAL_EMBED_THREADS = 2              # F5.1 (A-03): o e5 local no worker leve (prioridade baixa); o app usa 1
EXIT_STALLED = 70                    # D-058: saída pelo vigia do laço (o app relança e o job volta à fila)


def exit_stalled(reason: str) -> None:
    """D-058, o `on_stall` do vigia: o motivo e o traceback de todas as threads vão para o log e o processo sai na hora (a
    chamada presa não volta; nenhum `finally` a destrava). O heartbeat vence em `STALE_S`, o app relança o worker e o job
    é retomado (até `MAX_ATTEMPTS`)."""
    log.error("worker: %s; saindo para o app relançar (o job volta à fila)", reason)
    dump_all_threads()
    shutdown()
    os._exit(EXIT_STALLED)


def worker_log_path(store: Store, light: bool = False) -> Path:
    base = store.get_setting("log.path") or str(store.default_log_path())
    return Path(base).expanduser().parent / (LLM_WORKER_LOG if light else WORKER_LOG)


def _platform_hooks() -> tuple[Any, Any, Any]:
    """(`lower_own_priority`, `memory_rss`, `spawn_detached`) da plataforma; a versão portável quando a camada não existe
    (Linux antes da F7)."""
    try:
        from ..platform import get_platform

        p = get_platform()
    except NotImplementedError:
        return process.lower_own_priority, process.memory_rss, process.spawn_detached
    return p.lower_own_priority, p.memory_rss, p.spawn_detached


def run_llm_job(store: Store, job: Any) -> JobStatus:
    """Um job `llm` pelo `LlmJobRunner` (`pipeline/llm_steps.py`, importado só aqui: o worker pesado não precisa dele)."""
    try:
        from .llm_steps import LlmJobRunner
    except ImportError as e:
        store.jobs.finish(int(job["id"]), JobStatus.ERROR, error=f"camada de LLM indisponível nesta versão ({e})", count_attempt=False)
        return JobStatus.ERROR
    return LlmJobRunner(store, job).run()


def run_index_job(store: Store, job: Any) -> JobStatus:
    """Um job `index` (F5) pelo `IndexJobRunner` (`pipeline/index_steps.py`, importado só aqui)."""
    from .index_steps import IndexJobRunner

    return IndexJobRunner(store, job).run()


def run_light_job(store: Store, job: Any) -> JobStatus:
    """Despacho da classe leve por `kind` (F5A-01): `index` no runner do índice; o resto (`llm`) no da LLM."""
    if str(job.get("kind")) == JobKind.INDEX:
        return run_index_job(store, job)
    return run_llm_job(store, job)


def spawn_light_worker(store: Store, spawn: Any) -> bool:
    """Lança o worker leve quando há job leve (`llm` ou `index`) ativo sem worker vivo (o app pode estar fechado, P2)."""
    if not store.jobs.active(kinds=LIGHT_JOB_KINDS) or store.jobs.held_fresh(kinds=LIGHT_JOB_KINDS):
        return False
    try:
        pid = spawn(process.worker_command(light=True), process.worker_env(1))
    except Exception as e:
        log.warning("não consegui lançar o worker leve: %s", e)
        return False
    log.info("worker leve lançado (pid %s)", pid)
    return True


def build_deps(config: JobConfig, token: str, fake: bool, store: Store | None = None) -> PipelineDeps:
    if fake:
        try:
            from tests.fake_pipeline import FakeDeps
        except ImportError as e:
            raise RuntimeError("LISTENER_PIPELINE=fake exige a pasta tests/ do repositório no caminho") from e
        return FakeDeps()
    from .models import real_deps

    return real_deps(config, token, store)


def llm_transcriber_key(store: Store, config: JobConfig) -> tuple[str, str]:
    """(provedor, modelo rápido) do provedor ativo quando o transcritor é a LLM: trocar nas Configurações reconstrói."""
    if config.transcriber != "llm":
        return ("", "")
    from ..llm.jobconfig import LlmJobConfig

    cfg = LlmJobConfig.from_store(store)
    return (cfg.provider_id, cfg.fast_model)


def config_snapshot(store: Store) -> str:
    snap = store.settings.settings_with_prefix("pipeline.")
    snap["capture.silence_rms"] = store.get_setting("capture.silence_rms")
    return json.dumps(snap, ensure_ascii=False)


def llm_snapshot(store: Store) -> str:
    """`LlmJobConfig` do provedor ativo (nunca a chave) para o `config_json` do job `llm` (F3 item 15)."""
    from ..llm.jobconfig import LlmJobConfig

    return LlmJobConfig.from_store(store).to_json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --worker", description="worker da fila de processamento (F2)")
    ap.add_argument("--db", help="pasta do banco (padrão: o ponteiro da configuração do sistema)")
    ap.add_argument("--once", action="store_true", help="processa no máximo um job e sai")
    ap.add_argument("--light", action="store_true", help="worker leve (F3): só jobs de LLM, sem torch, log listener-llm.log")
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
        return _serve(store, once=args.once, idle_exit_s=args.idle_exit, poll_s=args.poll, light=args.light)


@dataclass(slots=True)
class _Deps:
    """As dependências do pesado: os modelos carregados uma vez por processo, refeitos quando a configuração muda."""
    fake: bool
    deps: PipelineDeps | None = None
    key: tuple[Any, ...] | None = None

    def get(self, store: Store, config: JobConfig, token: str) -> PipelineDeps:
        from .models import deps_key as key_of

        key = ("fake",) if self.fake else key_of(config, token, llm_transcriber_key(store, config))
        if self.deps is None or key != self.key:
            self.deps, self.key = build_deps(config, token, self.fake, store), key
        return self.deps


def _run_job(store: Store, job: Any, *, light: bool, once: bool, held: _Deps, memory: Any, spawn: Any) -> JobStatus | None:
    """Um job da classe; None quando o pesado nem começou (sem o extra do pipeline: erro permanente, sem contar como feito)."""
    if light:
        return run_light_job(store, job)
    config = JobConfig.from_json(job.get("config_json"))
    try:
        deps = held.get(store, config, store.settings.provider_key(HF_PROVIDER_ID))
    except PermanentError as e:
        store.jobs.finish(int(job["id"]), JobStatus.ERROR, error=str(e), count_attempt=False)
        log.warning("job %s: %s", job["id"], e)
        return None
    status = JobRunner(store, deps, job, memory_rss=memory).run()
    if status == JobStatus.DONE and not once:                  # `--once` não deixa um leve solto (F5A-16)
        spawn_light_worker(store, spawn)
    return status


def _guarded(store: Store, job: Any, **kw: Any) -> JobStatus | None:
    """F5.6 (A-37, F5A-17): o job que falha fora do runner volta à fila com a tentativa contada (R-04: só se ainda roda; um
    concluído fica concluído) e o worker segue. Depois de cada job, os arquivos de áudio soltos (R-01)."""
    try:
        return _run_job(store, job, **kw)
    except Exception as e:  # o worker nunca cai por um job: o próximo da fila espera
        log.exception("job %s: falha fora do runner; o worker segue para o próximo", job["id"])
        status, job_id = JobStatus.ERROR, int(job["id"])
        with suppress(Exception):                        # o banco preso aqui também não derruba: fica para o claim
            back = store.jobs.give_back(job_id, f"falha fora do runner: {type(e).__name__}")
            status = JobStatus.PENDING if back else JobStatus(str(store.jobs.get(job_id)["status"]))  # type: ignore[index]
        return status
    finally:
        with suppress(Exception):
            store.core.release_audio()


def _announce(store: Store, light: bool) -> None:
    """O log próprio do worker e a linha de abertura com as versões das bibliotecas da classe."""
    with suppress(Exception):
        attach_file(worker_log_path(store, light), store.get_setting("log.level"))
    versions = {}
    for dist in LLM_DISTS if light else ML_DISTS:
        with suppress(metadata.PackageNotFoundError):
            versions[dist] = metadata.version(dist)
    log_versions({"papel": "worker-llm" if light else "worker", "pid": str(os.getpid()), "banco": str(store.base_dir), **versions})


def _serve(store: Store, once: bool, idle_exit_s: float, poll_s: float, light: bool = False) -> int:
    _announce(store, light)
    lower, memory, spawn = _platform_hooks()
    log.info("prioridade baixa: %s", "ok" if lower() else "não aplicada")
    aio.configure_stall(exit_stalled)                              # D-058: chamada nativa presa não trava a fila
    store.core.release_audio()                                     # R-01: os anexos da abertura; o job anexa o que ler
    if light:                                                      # a sessão só abre no primeiro lote do embedder local
        from ..llm import local_embed

        local_embed.configure(LOCAL_EMBED_THREADS)
    kinds = LIGHT_JOB_KINDS if light else HEAVY_JOB_KINDS
    held = _Deps(os.environ.get("LISTENER_PIPELINE", "").lower() == "fake")
    idle_since: float | None = None
    released = False
    processed = 0
    while True:
        # a espera é lida antes do claim: um `index` que vença entre as duas consultas é pego pelo próprio claim (com o claim
        # primeiro, ele vencia no meio, nem esperava nem era pego, e o leve saía ocioso com ele pendente)
        waiting = light and bool(store.jobs.delayed(kinds))
        job = store.jobs.claim(os.getpid(), llm_snapshot(store) if light else config_snapshot(store), kinds=kinds)
        if job is not None:
            idle_since, released = None, False
            log.info("job %s (reunião %s, %s) iniciado", job["id"], job["meeting_id"], job.get("kind"))
            status = _guarded(store, job, light=light, once=once, held=held, memory=memory, spawn=spawn)
            if status is None:
                continue
            log.info("job %s terminou: %s", job["id"], status)
            processed += 1
            if once:
                break
            continue
        if once:
            break
        if waiting:                                                # há `index` esperando a edição assentar (R5-07): fica
            idle_since = None
            time.sleep(poll_s)
            continue
        now = time.monotonic()
        idle_since = idle_since if idle_since is not None else now
        if now - idle_since >= idle_exit_s:
            log.info("worker ocioso há %.0f s: encerrando (%d job(s) processado(s))", now - idle_since, processed)
            break
        if held.deps is not None and not released and now - idle_since >= RELEASE_AFTER_S:
            held.deps.release()
            released = True
        time.sleep(poll_s)
    return 0
