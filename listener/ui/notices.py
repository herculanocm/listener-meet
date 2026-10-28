"""O aviso do app (F5.3 L3, U-048), sem a janela: o que ele é (`Notice`: tipo, texto, texto curto e ação do clique), o que
o acompanha até o `Controller.notify` (`NoticeMeta`), o limite por minuto (A-10) e os textos dos avisos novos do item 12. A
janela, a posição e o gerente ficam em `ui/toast.py`.

- Tipo: pronto (verde), informação (azul) ou erro (vermelho), com as cores de `ui/common.py`.
- Texto curto (A-13): o texto sem o título da reunião ("Ata pronta"), o que aparece gravando quando a janela do aviso não
  saiu da captura de tela; o log leva o id da reunião e esse texto, nunca o título (R-03).
- Ação (A-12): a reunião abre a reunião (Q6); o erro de um job, a fila; o download, as Configurações; o chat da reunião, a
  aba Chat dela; o chat sobre todas as reuniões, o Histórico."""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..model import JOB_STEP_LABELS, JobKind
from .live_prepare import LIVE, RAG

MAX_PER_MINUTE = 10              # A-10: acima disto, numa janela de 60 s, o aviso fica só no log

# os textos dos avisos novos (item 12); os do processamento e da LLM concluídos seguem os de antes (U-020)
IDENTITY_READY = "sugestões de nomes prontas; a ata espera as vozes."
CHAT_READY, CHAT_FAILED = "resposta do chat pronta.", "o chat não respondeu (o motivo está na aba Chat)."
HISTORY_READY = "Resposta pronta em \"Perguntar às reuniões\"."
HISTORY_FAILED = "A pergunta às reuniões ficou sem resposta (o motivo está no Histórico)."
CRASH_SHORT = "Erro inesperado: os detalhes estão no log."
JOB_ERROR_WHERE: dict[str, str] = {JobKind.MEETING: "no processamento", JobKind.REPROCESS: "no reprocessamento", JobKind.LLM: "na LLM"}
DOWNLOAD_DONE: dict[str, str] = {LIVE: "Download concluído: os modelos do ao vivo estão prontos.",
                                 RAG: "Download concluído: a busca local está pronta."}
DOWNLOAD_FAILED: dict[str, str] = {LIVE: "O download dos modelos do ao vivo falhou", RAG: "O download da busca local falhou"}


class NoticeKind(StrEnum):
    READY = "pronto"             # verde: algo ficou pronto
    INFO = "informação"          # azul
    ERROR = "erro"               # vermelho


class Target(StrEnum):
    """O que o clique abre (A-12)."""
    MEETING = "reunião"
    MEETING_CHAT = "chat da reunião"
    QUEUE = "fila"
    SETTINGS = "configurações"
    HISTORY = "histórico"


@dataclass(frozen=True, slots=True)
class NoticeAction:
    target: Target
    meeting_id: int | None = None


@dataclass(frozen=True, slots=True)
class Notice:
    """Um aviso: o tipo (a cor), o texto, o texto curto (sem o título da reunião, A-13; vazio = o próprio texto) e a ação do
    clique."""
    kind: NoticeKind
    text: str
    short_text: str = ""
    action: NoticeAction | None = None

    @property
    def key(self) -> tuple[str, NoticeAction | None]:
        """Os iguais se juntam num aviso só: mesmo texto e mesma ação."""
        return self.text, self.action

    def display_text(self, private: bool) -> str:
        return (self.short_text or self.text) if private else self.text


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


@dataclass(frozen=True, slots=True)
class NoticeMeta:
    """O que acompanha o texto até o `Controller.notify`, que recebe só o texto (os testes o trocam por uma lista): o tipo, a
    ação, a reunião de que se fala com o texto sem o título (vai ao log e ao texto curto, R-03 e A-13), o texto curto e o
    texto do log, quando o da tela tem o que o log não leva (F5.3, A-22: o nome de um aparelho)."""
    kind: NoticeKind = NoticeKind.INFO
    action: NoticeAction | None = None
    about: tuple[int, str] | None = None
    short_text: str = ""
    log_text: str = ""

    def notice(self, text: str) -> Notice:
        short = self.short_text or (_capital(self.about[1]) if self.about is not None else "")
        return Notice(self.kind, text, short, self.action)

    def log_suffix(self, text: str) -> str:
        """" sobre a reunião 7: ata pronta" (o id e o texto, nunca o título) ou ": <texto>"."""
        if self.about is not None:
            return f" sobre a reunião {self.about[0]}: {self.about[1]}"
        return f": {self.log_text or text}"


class RateLimit:
    """No máximo `per_minute` avisos em qualquer janela de 60 s (A-10): uma exceção que se repete num timer não cobre a tela;
    o que passa do limite fica no log."""

    def __init__(self, per_minute: int = MAX_PER_MINUTE, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._times: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self._times and now - self._times[0] >= 60.0:
            self._times.popleft()
        if len(self._times) >= self.per_minute:
            return False
        self._times.append(now)
        return True


def job_error_text(job: Mapping[str, Any]) -> str | None:
    """A-11: "erro no processamento (etapa: transcrevendo a reunião); veja o motivo na fila.". None para quem não avisa: o
    índice (como no concluído) e o perfil de trecho (não roda, F6)."""
    where = JOB_ERROR_WHERE.get(str(job.get("kind")))
    if where is None:
        return None
    step = JOB_STEP_LABELS.get(str(job.get("step") or ""), "").removeprefix("LLM: ")
    tail = f" (etapa: {step})" if step else ""
    return f"erro {where}{tail}; veja o motivo na fila."


def download_text(state: Mapping[str, Any]) -> tuple[NoticeKind, str] | None:
    """O fim e o erro do preparo de modelos, do ao vivo ou da busca local (A-11); None para o resto (o progresso)."""
    what = RAG if state.get("what") == RAG else LIVE
    match state.get("state"):
        case "done":
            return NoticeKind.READY, DOWNLOAD_DONE[what]
        case "error":
            return NoticeKind.ERROR, f"{DOWNLOAD_FAILED[what]}: {str(state.get('message') or 'erro').rstrip('.')}."
        case _:
            return None


__all__ = ["CHAT_FAILED", "CHAT_READY", "CRASH_SHORT", "HISTORY_FAILED", "HISTORY_READY", "IDENTITY_READY", "MAX_PER_MINUTE",
           "Notice", "NoticeAction", "NoticeKind", "NoticeMeta", "RateLimit", "Target", "download_text", "job_error_text"]
