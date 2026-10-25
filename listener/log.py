"""Log do app (F1.1 A8, D-033; F1.2 M-10): stderr sempre; arquivo rotativo escolhido pelo usuário, com nível configurável
(CP8). Os handlers reais ficam atrás de um `QueueListener` (as threads de áudio nunca esperam disco); `faulthandler`
escreve no arquivo de log; exceções não tratadas (`sys.excepthook`, `threading.excepthook`) viram `log.exception` e,
quando há bandeja, um aviso ao usuário."""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import queue
import sqlite3
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from importlib import metadata
from pathlib import Path
from types import TracebackType
from typing import IO, Any

LOGGER_NAME = "listener"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
STDERR_HANDLER = "listener.stderr"
FILE_HANDLER = "listener.file"
QUEUE_HANDLER = "listener.queue"
_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


class _State:
    """Estado do módulo: o listener da fila, o arquivo do faulthandler, o notificador e os hooks."""
    listener: logging.handlers.QueueListener | None = None
    fault_file: IO[str] | None = None
    notify: Callable[[str], None] | None = None
    hooks_installed = False


_state = _State()


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def setup(level: str = "INFO") -> logging.Logger:
    """Configura o logger do app com saída em stderr atrás de uma fila. Chamar uma vez, antes de abrir o banco."""
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(_level(level))
    log.propagate = False
    if _find(QUEUE_HANDLER) is None:
        q: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        qh = logging.handlers.QueueHandler(q)
        qh.name = QUEUE_HANDLER
        log.addHandler(qh)
        stderr = logging.StreamHandler(sys.stderr)
        stderr.name = STDERR_HANDLER
        stderr.setFormatter(logging.Formatter(_FORMAT))
        _restart_listener(q, [stderr])
    _install_hooks()
    return log


def attach_file(path: str | Path, level: str = "INFO", max_bytes: int = 5_000_000, backups: int = 5) -> Path:
    """Liga (ou troca) o arquivo de log rotativo; cria a pasta se preciso. Devolve o caminho usado."""
    setup()
    detach_file()
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(p, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.name = FILE_HANDLER
    h.setFormatter(logging.Formatter(_FORMAT))
    listener = _state.listener
    if listener is None:
        raise RuntimeError("setup() não configurou o listener do log")
    _restart_listener(listener.queue, [*listener.handlers, h])
    _enable_faulthandler(p)
    set_level(level)
    return p


def detach_file() -> None:
    listener = _state.listener
    if listener is None:
        return
    keep = [h for h in listener.handlers if h.name != FILE_HANDLER]
    gone = [h for h in listener.handlers if h.name == FILE_HANDLER]
    if gone:
        _restart_listener(listener.queue, keep)
        for h in gone:
            h.close()
    if _state.fault_file is not None:
        with suppress(Exception):
            faulthandler.disable()
            _state.fault_file.close()
        _state.fault_file = None


def set_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(_level(level))


def current_file() -> Path | None:
    if _state.listener is not None:
        for h in _state.listener.handlers:
            if h.name == FILE_HANDLER and isinstance(h, logging.FileHandler):
                return Path(h.baseFilename)
    return None


def set_crash_notifier(fn: Callable[[str], None] | None) -> None:
    """Quem tem bandeja registra aqui para mostrar um aviso quando uma exceção escapa."""
    _state.notify = fn


def log_versions(extra: dict[str, str] | None = None) -> None:
    """Linha de abertura: Python, sistema, SQLite e as bibliotecas que importam para diagnóstico."""
    parts = [f"python {sys.version.split()[0]}", f"{sys.platform}", f"sqlite {sqlite3.sqlite_version}"]
    for dist in ("numpy", "scipy", "soundfile", "sounddevice", "proc-tap", "PySide6", "PyAudioWPatch", "pycaw"):
        with suppress(metadata.PackageNotFoundError):
            parts.append(f"{dist} {metadata.version(dist)}")
    for k, v in (extra or {}).items():
        parts.append(f"{k} {v}")
    get_logger().info("listener-meet %s; %s", _app_version(), ", ".join(parts))


def shutdown() -> None:
    """Para o listener e fecha os handlers (fim do processo e fixture dos testes)."""
    detach_file()
    log = logging.getLogger(LOGGER_NAME)
    listener = _state.listener
    if listener is not None:
        with suppress(Exception):
            listener.stop()
        for h in listener.handlers:
            with suppress(Exception):
                h.close()
        _state.listener = None
    for h in list(log.handlers):
        if h.name == QUEUE_HANDLER:
            log.removeHandler(h)


def flush() -> None:
    """Espera a fila esvaziar e os handlers gravarem (testes e encerramento)."""
    listener = _state.listener
    if listener is not None:
        with suppress(Exception):
            listener.stop()
            listener.start()
        for h in listener.handlers:
            with suppress(Exception):
                h.flush()


def handle_exception(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """`sys.excepthook` do app: traceback no log e aviso ao usuário; Ctrl+C segue o caminho padrão."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    get_logger().error("exceção não tratada", exc_info=(exc_type, exc, tb))
    _notify_user(f"{exc_type.__name__}: {exc}")


def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    """`threading.excepthook` do app: a thread morre, mas o traceback fica no log e o usuário é avisado."""
    if args.exc_type is SystemExit:
        return
    exc_info: Any = (args.exc_type, args.exc_value, args.exc_traceback)
    get_logger().error("exceção não tratada na thread %s", args.thread.name if args.thread else "?", exc_info=exc_info)
    _notify_user(f"{args.exc_type.__name__}: {args.exc_value}")


# ---------------------------------------------------------------- interno
def _app_version() -> str:
    from . import __version__
    return __version__


def _level(level: str) -> int:
    return getattr(logging, str(level or "INFO").upper(), logging.INFO)


def _find(name: str) -> logging.Handler | None:
    return next((h for h in logging.getLogger(LOGGER_NAME).handlers if h.name == name), None)


def _restart_listener(q: Any, handlers: list[logging.Handler]) -> None:
    if _state.listener is not None:
        with suppress(Exception):
            _state.listener.stop()
    _state.listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=True)
    _state.listener.start()


def _enable_faulthandler(path: Path) -> None:
    """Crash nativo (ctypes, COM, PortAudio) deixa o traceback no arquivo de log."""
    try:
        f = path.open("a", encoding="utf-8")   # fica aberto de propósito: o faulthandler escreve no descritor
        faulthandler.enable(file=f, all_threads=True)
    except Exception as e:
        get_logger().debug("faulthandler indisponível: %s", e)
        return
    if _state.fault_file is not None:
        with suppress(Exception):
            _state.fault_file.close()
    _state.fault_file = f


def _install_hooks() -> None:
    if _state.hooks_installed:
        return
    _state.hooks_installed = True
    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception


def _notify_user(message: str) -> None:
    if _state.notify is not None:
        with suppress(Exception):
            _state.notify(message)
