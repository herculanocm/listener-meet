"""O teste do token do Hugging Face (F5.11: U-113, A-29) e as mensagens dele, num módulo leve: nada de `huggingface_hub` no
nível do módulo (a interface lê as constantes; o filho importa o hub só no teste). `pipeline/models.py` usa as mesmas
mensagens ao baixar os modelos (`classify`), e a tela do teste também, sem o prefixo do dono dos repositórios.

`python -m listener --hf-check [--db DIR]`, no molde do `--live --prepare`: o token chega pelo stdin, numa linha (nunca na
linha de comando nem no log); o ambiente é o do worker (o app lança com `process.worker_env`, e o `HF_HOME` fica na pasta de
modelos do banco, sem o modo offline e sem telemetria); o filho faz `whoami` e o `auth_check` dos dois repositórios, com prazo
próprio de `DEADLINE_S`, escreve uma linha JSON (`{"type": "result", "status", "message", "repo"}`) e sai. O app mata o filho
no prazo dele, no Cancelar e na saída (`ui/hf_test.py`). `LISTENER_HF=fake` responde pelo token, sem rede (os testes)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO

DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"
GATED_REPOS = (DIARIZATION_REPO, EMBEDDING_REPO)
READY_MARK = ".ready"
PYANNOTE_READY = f"pyannote{READY_MARK}"     # a marca que o download do worker grava na pasta de modelos
FLAG = "--hf-check"
DEADLINE_S = 15.0                              # o prazo do filho; o app mata aos 20 s (comportamento 5)
FAKE_ENV = "LISTENER_HF"
INSTALL_HINT = "instale o extra: pip install -e .[pipeline] (faster-whisper, pyannote.audio e torch)"
INVALID_TEXT = "token do Hugging Face inválido: confira em Configurações, Chaves de acesso"
MISSING_TEXT = "token do Hugging Face ausente: informe em Configurações, Chaves de acesso"
OK_TEXT = "token aceito e os termos dos dois modelos de voz aceitos"
TIMEOUT_TEXT = "o Hugging Face não respondeu no prazo; tente de novo"
NETWORK_ERRORS = ("ConnectionError", "ConnectTimeout", "ReadTimeout", "MaxRetryError", "NewConnectionError", "URLError")


class Status(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    GATED = "gated"
    NETWORK = "network"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"      # sem `huggingface_hub`: o teste não está disponível
    ERROR = "error"


def gated_text(repo: str) -> str:
    return f"aceite os termos de uso de {repo or 'cada repositório do pyannote'} no Hugging Face com a conta do token"


def offline_text(repo: str) -> str:
    return f"modelo {repo} ainda não baixado e o modo offline está ativo: ligue a rede e tente de novo"


def network_text(repo: str) -> str:
    return f"sem rede para baixar {repo or 'os modelos'}; tente de novo com conexão"


def short(repo: str) -> str:
    """O nome do repositório sem o dono ("wespeaker-voxceleb-resnet34-LM"): fora das Configurações, sem jargão (U-106)."""
    return repo.rsplit("/", 1)[-1]


def classify(e: BaseException, repo: str = "") -> tuple[Status, str] | None:
    """A falha de download, carga ou teste em (situação, mensagem própria); None quando é uma falha comum."""
    name, text = type(e).__name__, str(e)
    if isinstance(e, ImportError | ModuleNotFoundError):
        return Status.UNAVAILABLE, f"{INSTALL_HINT} ({text})"
    if name == "GatedRepoError" or "gated" in text.lower() or "403" in text:
        return Status.GATED, gated_text(repo)
    if name in ("RepositoryNotFoundError", "HfHubHTTPError", "HTTPError") and ("401" in text or "Unauthorized" in text
                                                                               or "Invalid" in text):
        return Status.INVALID, INVALID_TEXT
    if name in ("LocalEntryNotFoundError", "OfflineModeIsEnabled") or "offline" in text.lower():
        return Status.OFFLINE, offline_text(repo)
    if name in NETWORK_ERRORS or "Max retries" in text or "Name or service not known" in text or "getaddrinfo" in text:
        return Status.NETWORK, network_text(repo)
    return None


def pyannote_ready(models_dir: Path) -> bool:
    """Os modelos de voz do worker já baixados (a marca `.ready`), sem importar `pipeline.models`."""
    return (models_dir / PYANNOTE_READY).exists()


def result(status: Status, message: str, repo: str = "") -> dict[str, str]:
    return {"type": "result", "status": str(status), "message": message, "repo": repo}


def _failure(e: BaseException, repo: str) -> dict[str, str]:
    found = classify(e, repo)
    if found is None:
        return result(Status.ERROR, f"{type(e).__name__}: o teste falhou", repo)    # o texto da exceção pode citar a conta
    return result(found[0], found[1], repo)


def _fake(token: str) -> dict[str, str]:
    """`LISTENER_HF=fake` (testes): o fim do token decide ("hf_x_ok", "_gated", "_net", "_slow"); o resto é inválido."""
    end = token.rsplit("_", 1)[-1]
    if end == "slow":
        time.sleep(DEADLINE_S + 5.0)
    fakes = {"ok": result(Status.OK, OK_TEXT), "gated": result(Status.GATED, gated_text(EMBEDDING_REPO), EMBEDDING_REPO),
             "net": result(Status.NETWORK, network_text(""))}
    return fakes.get(end, result(Status.INVALID, INVALID_TEXT))


def check(token: str) -> dict[str, str]:
    """`whoami` com o token e o `auth_check` de cada repositório: o primeiro que falha decide."""
    if not token:
        return result(Status.INVALID, MISSING_TEXT)
    if os.environ.get(FAKE_ENV) == "fake":
        return _fake(token)
    try:
        import huggingface_hub as hub
    except ImportError as e:
        return result(Status.UNAVAILABLE, f"{INSTALL_HINT} ({e})")
    try:
        hub.whoami(token=token)
    except Exception as e:
        return _failure(e, "")
    for repo in GATED_REPOS:
        try:
            hub.auth_check(repo, token=token)
        except Exception as e:
            return _failure(e, repo)
    return result(Status.OK, OK_TEXT)


def within(token: str, deadline_s: float) -> dict[str, str]:
    """`check` numa thread com prazo: uma chamada presa em código nativo (o caso da D-058) vira "sem resposta"."""
    box: list[dict[str, str]] = []
    worker = threading.Thread(target=lambda: box.append(check(token)), name="hf-check", daemon=True)
    worker.start()
    worker.join(deadline_s)
    return box[0] if box else result(Status.TIMEOUT, TIMEOUT_TEXT)


def environment(models_dir: Path) -> None:
    """O ambiente do download do worker, antes de importar o hub: o `HF_HOME` na pasta de modelos, sem telemetria e sem o
    modo offline (o teste precisa da rede, mesmo com os modelos já baixados)."""
    os.environ.update({"HF_HOME": str(models_dir / "hf"), "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"})
    os.environ.pop("HF_HUB_OFFLINE", None)


def serve(stdin: BinaryIO, stdout: BinaryIO, deadline_s: float = DEADLINE_S) -> int:
    """Lê o token (uma linha), testa e escreve o resultado numa linha JSON; 0 quando o token passou."""
    token = stdin.readline().decode("utf-8", errors="replace").strip()
    found = within(token, deadline_s)
    stdout.write((json.dumps(found, ensure_ascii=False) + "\n").encode("utf-8"))
    stdout.flush()
    return 0 if found["status"] == Status.OK else 1


def _models_dir(db: str | None) -> Path:
    """A pasta de modelos do banco (só leitura), ou a padrão."""
    from ..live.client import live_models_dir

    setting = ""
    if db:
        try:
            from ..store import Store

            with Store.open_readonly(Path(db)) as store:
                setting = store.get_setting("pipeline.models_dir")
        except Exception:  # sem o banco, a pasta padrão (o teste não depende dela)
            setting = ""
    return live_models_dir(setting)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --hf-check", description="testa o token do Hugging Face lido do stdin")
    ap.add_argument("--db", help="pasta do banco (a pasta de modelos dele vira o HF_HOME)")
    args = ap.parse_args(argv)
    environment(_models_dir(args.db))
    code = serve(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout.flush()
    os._exit(code)                                   # uma thread presa no hub não segura a saída do filho


def describe(found: Mapping[str, Any]) -> str:
    """A mensagem da tela: a do resultado, com o repositório sem o dono (fora das Configurações, sem jargão)."""
    repo = str(found.get("repo") or "")
    if found.get("status") == Status.GATED and repo:
        return gated_text(short(repo))
    return str(found.get("message") or "")


__all__ = ["DEADLINE_S", "DIARIZATION_REPO", "EMBEDDING_REPO", "FAKE_ENV", "FLAG", "GATED_REPOS", "INSTALL_HINT", "INVALID_TEXT",
           "MISSING_TEXT", "OK_TEXT", "PYANNOTE_READY", "READY_MARK", "Status", "check", "classify", "describe", "environment",
           "gated_text", "main", "network_text", "offline_text", "pyannote_ready", "result", "serve", "short", "within"]
