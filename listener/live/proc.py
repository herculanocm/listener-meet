"""Processo ao vivo (F4): `python -m listener --live --db DIR --meeting ID` e `--live --prepare [--db DIR]`.

Não consome a fila (não é `--worker`): lançado pelo app ao começar a gravar (`Platform.spawn_child`), lê os quadros do
stdin (`protocol.py`) e responde em linhas JSON no stdout; morre com a gravação (EOF no stdin). Log próprio
(`listener-live.log` na pasta do log do app, como o worker), `Store` no papel WORKER. O stdout do protocolo é uma cópia do
descritor 1, que passa a apontar para o stderr (descartado pelo app): um `print` perdido ou uma biblioteca nativa não
corrompem as linhas. `--prepare` baixa `small` e `base` para `<pasta de modelos>/faster-whisper-<modelo>` (a mesma pasta
da F2) com progresso em linhas JSON e sai com 0 ou 1.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import suppress
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO

from ..config import bootstrap
from ..config.defaults import LIVE_MODELS
from ..log import attach_file, get_logger, log_versions, setup
from ..platform.common import paths
from ..store import Store
from ..store.core import MAIN_FILE
from .client import is_fake, live_models_dir, whisper_model_dir
from .protocol import BYE, READY, ControlFrame, FrameReader, Hello, ProtocolError, encode_line

log = get_logger("live")

LIVE_LOG = "listener-live.log"
LIVE_DISTS = ("faster-whisper", "ctranslate2", "onnxruntime", "torch", "pyannote.audio")


def live_log_path(store: Store) -> Path:
    base = store.get_setting("log.path") or str(store.default_log_path())
    return Path(base).expanduser().parent / LIVE_LOG


def _emit(out: BinaryIO, msg: dict[str, Any]) -> None:
    out.write(encode_line(msg))
    out.flush()


# ---------------------------------------------------------------- --prepare
def prepare(db_dir: Path | None, out: BinaryIO) -> int:
    """Baixa os modelos do ao vivo que faltam (`small` e `base`), com uma linha JSON por passo:
    `{"type": "progress", "model", "status": "baixando" | "pronto", "fraction"}`, `{"type": "error", "model", "message"}`
    (sai com 1) e `{"type": "done", "models_dir"}` (sai com 0)."""
    setting = ""
    if db_dir is not None and (db_dir / MAIN_FILE).exists():
        try:
            with Store.open_readonly(db_dir) as store:
                setting = store.get_setting("pipeline.models_dir")
                with suppress(Exception):
                    attach_file(live_log_path(store), store.get_setting("log.level"))
        except Exception as e:
            log.warning("não li a pasta de modelos do banco em %s: %s; usando a padrão", db_dir, e)
    target = live_models_dir(setting)
    total = len(LIVE_MODELS)
    fake = is_fake()
    if not fake:
        from ..pipeline.models import disable_hf_symlinks

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        disable_hf_symlinks()
    for i, model in enumerate(LIVE_MODELS):
        dest = whisper_model_dir(target, model)
        if fake or (dest / "model.bin").exists():
            _emit(out, {"type": "progress", "model": model, "status": "pronto", "fraction": round((i + 1) / total, 3)})
            continue
        _emit(out, {"type": "progress", "model": model, "status": "baixando", "fraction": round(i / total, 3)})
        try:
            from faster_whisper import download_model

            dest.parent.mkdir(parents=True, exist_ok=True)
            download_model(model, output_dir=str(dest))
        except Exception as e:
            from ..pipeline.models import classify_error

            err = classify_error(e, f"faster-whisper {model}")
            log.warning("download do modelo %s falhou: %s", model, err or e)
            _emit(out, {"type": "error", "model": model, "message": str(err or e)})
            return 1
        _emit(out, {"type": "progress", "model": model, "status": "pronto", "fraction": round((i + 1) / total, 3)})
    _emit(out, {"type": "done", "models_dir": str(target)})
    return 0


# ---------------------------------------------------------------- --live
def read_hello(reader: FrameReader) -> Hello:
    first = reader.read()
    if not isinstance(first, ControlFrame):
        raise ProtocolError("o primeiro quadro precisa ser o hello")
    return Hello.from_msg(first.msg)


def serve(stdin: BinaryIO, stdout: BinaryIO, store: Store, meeting_id: int) -> int:
    """Esqueleto do protocolo (L1): `hello` -> `ready`, consome os quadros até o EOF -> `bye`."""
    reader = FrameReader(stdin)
    if store.get_meeting(meeting_id) is None:
        _emit(stdout, {"type": "error", "message": f"reunião {meeting_id} não existe", "retry": False})
        return 2
    try:
        hello = read_hello(reader)
    except ProtocolError as e:
        log.warning("reunião %s: %s", meeting_id, e)
        _emit(stdout, {"type": "error", "message": str(e), "retry": False})
        return 2
    _emit(stdout, {"type": READY, "model": hello.model, "names": hello.names})
    frames = 0
    with suppress(ProtocolError):
        while reader.read() is not None:
            frames += 1
    _emit(stdout, {"type": BYE, "frames": frames})
    return 0


def _protocol_stdout() -> BinaryIO:
    """Cópia do descritor 1 para o protocolo; o 1 passa a apontar para o stderr."""
    with suppress(Exception):
        sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(fd, "wb")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --live", description="processo ao vivo (F4): legendas durante a gravação")
    ap.add_argument("--db", help="pasta do banco (padrão: o ponteiro da configuração do sistema)")
    ap.add_argument("--meeting", type=int, help="reunião em gravação")
    ap.add_argument("--prepare", action="store_true", help="baixa os modelos do ao vivo (small e base) e sai")
    args = ap.parse_args(argv)
    db_dir = Path(args.db) if args.db else bootstrap.read_db_dir(paths.config_dir())
    setup("INFO")
    if args.prepare:
        return prepare(db_dir, sys.stdout.buffer)
    if db_dir is None or args.meeting is None:
        print("live: informe --db DIR e --meeting ID", file=sys.stderr)  # noqa: T201
        return 2
    out = _protocol_stdout()
    try:
        store = Store.open_worker(db_dir)
    except Exception as e:
        print(f"live: não abri o banco em {db_dir}: {e}", file=sys.stderr)  # noqa: T201
        with suppress(Exception):
            _emit(out, {"type": "error", "message": f"banco indisponível: {e}", "retry": False})
        return 3
    with store:
        with suppress(Exception):
            attach_file(live_log_path(store), store.get_setting("log.level"))
        versions = {}
        for dist in LIVE_DISTS:
            with suppress(metadata.PackageNotFoundError):
                versions[dist] = metadata.version(dist)
        log_versions({"papel": "live", "pid": str(os.getpid()), "banco": str(store.base_dir), "reunião": str(args.meeting),
                      **({"modo": "fake"} if is_fake() else {}), **versions})
        return serve(sys.stdin.buffer, out, store, int(args.meeting))
