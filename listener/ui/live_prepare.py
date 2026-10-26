"""Modelos do ao vivo (F4 item 10): o que falta na pasta e o botão "Preparar modelos do ao vivo" das Configurações.

`ModelPreparer` roda `python -m listener --live --prepare --db DIR` num filho com pipe (`Platform.spawn_child`, que no Windows
já nasce sem janela); a thread `live-prepare` lê as linhas JSON (`progress`, `error`, `done`) e as entrega à thread da
interface por sinal enfileirado. Um objeto só no app inteiro (`preparer()`, sem parent, vive no módulo): fechar as
Configurações no meio não mata o download nem deixa thread emitindo para um diálogo destruído (o diálogo se desconecta do
sinal `changed` ao fechar e, reaberto, mostra o progresso em curso); um segundo pedido com o download rodando é recusado.
Sair do app mata o filho (`shutdown_preparer`). Sem Qt de interface aqui: só `QObject` e sinais.
F5.1 (A-08): o mesmo preparador baixa também a busca local (`start(..., what="rag")`, `--prepare --what rag`); o estado leva
`what` ("live" ou "rag") e cada grupo das Configurações mostra o seu; continua um download por vez no app inteiro."""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal

from ..config.defaults import SPECS
from ..live.client import is_fake, live_models_dir, required_models, whisper_model_dir
from ..live.protocol import decode_line
from ..llm import local_embed
from ..log import get_logger
from ..platform.common import process

if TYPE_CHECKING:
    from ..platform.base import ChildProcess
    from ..store import Store

log = get_logger("ui.live_prepare")

type Spawn = Callable[[list[str], Mapping[str, str] | None], ChildProcess]
EXIT_WAIT_S = 10.0
LIVE, RAG = "live", "rag"            # o que o preparo baixa (A-08): os modelos do ao vivo ou a busca local
WHAT_TEXT = {LIVE: "os modelos do ao vivo", RAG: "a busca local"}


def missing_models(store: Store) -> list[str]:
    """Modelos do ao vivo que faltam para o `live.model` atual (ele mais o da degradação); vazio = prontos."""
    if is_fake():
        return []
    models_dir = live_models_dir(store.get_setting("pipeline.models_dir"))
    return [m for m in required_models(store.get(SPECS["live.model"])) if not (whisper_model_dir(models_dir, m) / "model.bin").exists()]


def models_status_text(store: Store) -> str:
    """"prontos (small e base)" ou "faltam: small, base"."""
    missing = missing_models(store)
    if missing:
        return "faltam: " + ", ".join(missing)
    return "prontos (" + " e ".join(required_models(store.get(SPECS["live.model"]))) + ")"


def local_status_text(store: Store) -> str:
    """O estado da busca local nas Configurações (F5.1 item 3): "pronta" ou o motivo, sem jargão."""
    ok, reason = local_embed.status(store.get_setting("pipeline.models_dir"))
    if ok:
        return "pronta (e5-small na pasta de modelos; sem custo e sem rede)"
    return "não baixada" if reason == local_embed.NOT_DOWNLOADED else reason


class ModelPreparer(QObject):
    """Estado em `state`: {"state": idle | running | done | error, "what": live | rag, "model", "status", "fraction",
    "message"}."""
    changed = Signal(object)
    _line = Signal(object, object)        # (filho, mensagem): da thread `live-prepare` para a da interface

    def __init__(self) -> None:
        super().__init__(None)
        self.proc: ChildProcess | None = None
        self.what = LIVE
        self.state: dict[str, Any] = {"state": "idle", "what": LIVE}
        self.starts = 0                    # instrumentação dos testes: downloads lançados
        self._closed = False               # o app está saindo: a thread leitora não emite mais nada
        self._error = ""
        self._done = False
        self._line.connect(self._on_line, Qt.ConnectionType.QueuedConnection)

    @property
    def running(self) -> bool:
        return self.proc is not None

    def start(self, store: Store, spawn: Spawn, what: str = LIVE) -> bool:
        """Lança o `--prepare` (`what`: os modelos do ao vivo ou a busca local, A-08); False quando já há um rodando (nunca
        dois downloads, nem um de cada) ou o filho não nasceu."""
        if self.running or self._closed:
            return False
        self.what = what if what in WHAT_TEXT else LIVE
        argv = process.live_prepare_command(store.base_dir, self.what)
        try:
            proc = spawn(argv, process.live_env(1))
        except Exception as e:
            log.exception("não consegui lançar o preparo de %s", WHAT_TEXT[self.what])
            self._set({"state": "error", "message": f"não consegui iniciar: {e}"})
            return False
        if proc.stdout is None:
            with suppress(OSError):
                proc.kill()
            self._set({"state": "error", "message": "o processo de preparo nasceu sem saída"})
            return False
        if proc.stdin is not None:
            with suppress(Exception):
                proc.stdin.close()                         # o `--prepare` não lê o stdin
        self.proc = proc
        self.starts += 1
        self._error, self._done = "", False
        log.info("preparo de %s lançado (pid %s): %s", WHAT_TEXT[self.what], proc.pid, " ".join(argv))
        self._set({"state": "running", "model": "", "status": "iniciando…", "fraction": 0.0})
        threading.Thread(target=self._read, args=(proc,), name="live-prepare", daemon=True).start()
        return True

    def _read(self, proc: ChildProcess) -> None:
        stdout = proc.stdout
        try:
            while stdout is not None:
                raw = stdout.readline()
                if not raw:
                    break
                try:
                    msg = decode_line(raw)
                except ValueError:
                    log.debug("linha ilegível do preparo: %r", raw[:200])
                    continue
                if self._closed:
                    return
                self._line.emit(proc, msg)
        except (OSError, ValueError) as e:
            log.debug("leitura do preparo terminou: %s", e)
        code: int | None
        try:
            code = proc.wait(timeout=EXIT_WAIT_S)
        except Exception:
            with suppress(OSError):
                proc.kill()
            code = None
        if not self._closed:
            self._line.emit(proc, {"type": "exit", "code": code})

    def _on_line(self, proc: object, msg: object) -> None:
        if proc is not self.proc or not isinstance(msg, dict):
            return
        match msg.get("type"):
            case "progress":
                self._set({"state": "running", "model": str(msg.get("model") or ""), "status": str(msg.get("status") or ""),
                           "fraction": float(msg.get("fraction") or 0.0)})
            case "error":
                self._error = f"{msg.get('model') or ''}: {msg.get('message') or 'falhou'}".strip(": ")
            case "done":
                self._done = True
            case "exit":
                self.proc = None
                code = msg.get("code")
                if code == 0 and self._done:
                    log.info("preparo de %s concluído", WHAT_TEXT[self.what])
                    self._set({"state": "done", "fraction": 1.0})
                else:
                    message = self._error or f"o preparo saiu com código {code}"
                    log.warning("preparo de %s falhou: %s", WHAT_TEXT[self.what], message)
                    self._set({"state": "error", "message": message})

    def cancel(self, reason: str) -> bool:
        """Interrompe o download em curso (ao começar a gravar: o preparo é fora da gravação, item 10, e o download disputaria
        a rede com a reunião); retomar depois continua de onde parou. False sem nada rodando."""
        proc, self.proc = self.proc, None
        if proc is None:
            return False
        with suppress(OSError):
            proc.kill()
        log.info("preparo de %s interrompido: %s", WHAT_TEXT[self.what], reason)
        self._set({"state": "error", "message": reason})
        return True

    def _set(self, state: dict[str, Any]) -> None:
        self.state = {**state, "what": self.what}
        self.changed.emit(dict(self.state))

    def shutdown(self) -> None:
        """O app está saindo: o download não continua órfão e a thread leitora para de emitir."""
        self._closed = True
        proc, self.proc = self.proc, None
        if proc is not None:
            with suppress(OSError):
                proc.kill()


_shared: dict[str, ModelPreparer] = {}


def preparer() -> ModelPreparer:
    """O preparador único do app (criado na thread da interface no primeiro uso)."""
    if "p" not in _shared:
        _shared["p"] = ModelPreparer()
    return _shared["p"]


def shutdown_preparer() -> None:
    p = _shared.get("p")
    if p is not None:
        p.shutdown()
