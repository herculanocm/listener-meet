"""Redação de chaves de API (CP10, R-6): a chave inteira e os seus 8 últimos caracteres somem de toda mensagem gravada
(`llm_calls.error`, `jobs.error`) e dos logs do pacote `llm` e do runner (filtro de logging, mensagem e traceback). A senha
do proxy (`llm.proxy_url`) entra no mesmo redator (F3.1 R3-04). Nada aqui conhece SDK."""
from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Iterable

MASK = "«chave»"
TAIL = 8
MIN_SECRET = 8


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def add(self, *secrets: str | None) -> None:
        with self._lock:
            for s in secrets:
                if s and len(s) >= MIN_SECRET:
                    self._secrets.add(s)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def patterns(self) -> list[str]:
        with self._lock:
            out = set(self._secrets)
        # a chave inteira primeiro; o sufixo pega ecos parciais (URL cortada, "...xyz12345")
        tails = {s[-TAIL:] for s in out if len(s) > TAIL + 4}
        return sorted(out, key=len, reverse=True) + sorted(tails, key=len, reverse=True)

    def redact(self, text: str | None) -> str:
        if not text:
            return text or ""
        out = text
        for p in self.patterns():
            if p in out:
                out = out.replace(p, MASK)
        return out


GLOBAL = Redactor()


def redact(text: str | None, secrets: Iterable[str | None] = ()) -> str:
    """Redige com os segredos globais mais os passados (chamadas pontuais)."""
    extra = Redactor()
    extra.add(*secrets)
    return extra.redact(GLOBAL.redact(text))


class RedactingFilter(logging.Filter):
    """Aplica a redação à mensagem formatada e ao traceback antes de qualquer handler (logs do app e do worker). O traceback
    (`exc_info`, com a cadeia `raise ... from e` inteira) vira `exc_text` já redigido: o `Formatter` usa `exc_text` quando
    existe e não formata o `exc_info` de novo [fato, `logging.Formatter.format`] (F3.1 R3-04)."""

    def __init__(self, redactor: Redactor = GLOBAL):
        super().__init__("redact")
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        if self.redactor.patterns():
            try:
                record.msg = self.redactor.redact(str(record.getMessage()))
                record.args = ()
                if record.exc_info and not record.exc_text:
                    text = "".join(traceback.format_exception(*record.exc_info)).removesuffix("\n")
                    record.exc_text = self.redactor.redact(text)
                elif record.exc_text:
                    record.exc_text = self.redactor.redact(record.exc_text)
                if record.stack_info:
                    record.stack_info = self.redactor.redact(record.stack_info)
            except Exception:  # um log malformado não pode derrubar o app
                return True
        return True


_FILTER = RedactingFilter()


def install_log_filter(logger_name: str = "listener") -> None:
    """Filtro no logger e em todos os seus handlers. Filtros de um logger não valem para registros propagados pelos filhos
    [fato]: é no handler (o `QueueHandler` do app, criado em `log.setup()`) que a redação alcança `listener.llm.*`."""
    logger = logging.getLogger(logger_name)
    if _FILTER not in logger.filters:
        logger.addFilter(_FILTER)
    for h in logger.handlers:
        if _FILTER not in h.filters:
            h.addFilter(_FILTER)
