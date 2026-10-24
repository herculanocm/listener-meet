"""Log do app (F1.1 A8, D-033): stderr sempre; arquivo rotativo escolhido pelo usuário, com nível configurável (CP8)."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOGGER_NAME = "listener"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def setup(level: str = "INFO") -> logging.Logger:
    """Configura o logger do app com saída em stderr. Chamar uma vez, antes de abrir o banco."""
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(_level(level))
    log.propagate = False
    if not any(getattr(h, "_listener_stderr", False) for h in log.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(_FORMAT))
        h._listener_stderr = True
        log.addHandler(h)
    return log


def attach_file(path: str | Path, level: str = "INFO", max_bytes: int = 5_000_000, backups: int = 5) -> Path:
    """Liga (ou troca) o arquivo de log rotativo; cria a pasta se preciso. Devolve o caminho usado."""
    log = logging.getLogger(LOGGER_NAME)
    detach_file()
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(p, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.setFormatter(logging.Formatter(_FORMAT))
    h._listener_file = True
    log.addHandler(h)
    set_level(level)
    return p


def detach_file() -> None:
    log = logging.getLogger(LOGGER_NAME)
    for h in list(log.handlers):
        if getattr(h, "_listener_file", False):
            log.removeHandler(h)
            h.close()


def set_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(_level(level))


def current_file() -> Path | None:
    for h in logging.getLogger(LOGGER_NAME).handlers:
        if getattr(h, "_listener_file", False):
            return Path(h.baseFilename)
    return None


def _level(level: str) -> int:
    return getattr(logging, str(level or "INFO").upper(), logging.INFO)
