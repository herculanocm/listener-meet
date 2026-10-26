"""Preparo da busca local da F5.1 (A-09, comportamento 2): `--live --prepare --what rag` baixa o e5-small na revisão fixada
(`local_embed.REPO`, `REVISION`, `FILES`) para `<pasta de modelos>/multilingual-e5-small/`, confere o sha256 de cada arquivo e
grava a marca `.ready` (JSON, atômica) no fim. Arquivo presente só é conferido; com o sha256 diferente, sai e é baixado de
novo; baixado e diferente, sai e o preparo falha sem a marca. A marca antiga sai antes: um preparo que falha deixa "não
baixada". Toda falha vira uma linha de erro para o app, com texto sem jargão (o técnico vai para o log), e o código 1:
download (sem rede, Hugging Face fora do ar, certificado ou proxy da empresa, modo offline, disco cheio) e arquivo (em uso
por outro programa, pasta sem gravação, disco cheio), inclusive ao apagar a marca e ao gravá-la (R10-01, R10-03).

O download é o `hf_hub_download` com `local_dir` (cópia real, sem links simbólicos): interrompido, continua do `.incomplete`
na próxima vez, com o Xet desligado pelo filho (`HF_HUB_DISABLE_XET`, R10-02). `huggingface_hub` só dentro de função (A-13);
os testes trocam o download (`prepare(..., download=...)`)."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..log import get_logger
from . import local_embed

log = get_logger("llm.local")

type Emit = Callable[[dict[str, Any]], None]
type Download = Callable[[str, Path], Path]

NO_NETWORK = "sem rede para baixar a busca local: tente de novo com conexão (continua de onde parou)"
OFFLINE = "o modo offline do Hugging Face está ligado (HF_HUB_OFFLINE): desligue e tente de novo"
HUB_DOWN = "o Hugging Face não respondeu (erro {status}): tente de novo mais tarde (continua de onde parou)"
CERTIFICATE = "o certificado do download não foi aceito (inspeção TLS da empresa?): tente em outra rede"
BLOCKED = "a rede bloqueou o download (proxy, firewall ou certificado da empresa?): tente em outra rede"
DISK_FULL = "sem espaço em disco na pasta de modelos"
IN_USE = ("o arquivo está em uso por outro programa ou a pasta de modelos não aceita gravação: feche o programa que usa o "
          "arquivo e tente de novo")
SHA_MISMATCH = "o arquivo baixado não confere com o esperado (sha256): foi apagado; tente de novo"
NET_ERRORS = frozenset({"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "ChunkedEncodingError", "ProtocolError",
                        "IncompleteRead", "RemoteDisconnected", "LocalEntryNotFoundError"})
NET_HINTS = ("max retries", "getaddrinfo", "name or service not known", "timed out", "check your connection", "11001",
             "connection aborted", "connection reset")
TRUE = frozenset({"1", "ON", "YES", "TRUE"})


def hub_download(filename: str, folder: Path) -> Path:
    """Um arquivo do repositório na revisão fixada, direto na pasta (`local_dir`); um download interrompido continua do
    `.incomplete` na próxima vez (com o Xet desligado: o Xet recomeçaria do zero)."""
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(local_embed.REPO, filename, revision=local_embed.REVISION, local_dir=str(folder)))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_ready(folder: Path) -> Path:
    """`.ready` em JSON (repositório, revisão e sha256), atômico: escrito ao lado e trocado."""
    mark = folder / local_embed.READY_MARK
    tmp = folder / f"{local_embed.READY_MARK}.tmp"
    payload = local_embed.ready_payload() | {"checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(mark)
    return mark


# ---------------------------------------------------------------- erros em texto para o usuário
def _chain(e: BaseException) -> list[BaseException]:
    """A exceção e as causas dela (o hub embrulha a falha de rede num `LocalEntryNotFoundError`)."""
    out: list[BaseException] = []
    cur: BaseException | None = e
    while cur is not None and len(out) < 8 and all(cur is not seen for seen in out):
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _status(e: BaseException) -> int | None:
    code = getattr(getattr(e, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def offline_env() -> bool:
    """O modo offline do hub ligado no ambiente (`HF_HUB_OFFLINE` ou `TRANSFORMERS_OFFLINE`)."""
    return any(os.environ.get(var, "").strip().upper() in TRUE for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"))


def download_error(e: BaseException) -> str:
    """A falha do download em texto para o usuário, olhando também as causas (R10-01): sem rede (o `LocalEntryNotFoundError`
    do hub também), modo offline só com `OfflineModeIsEnabled` ou o ambiente, certificado, proxy ou firewall, o Hugging Face
    fora do ar (5xx), disco cheio e arquivo em uso."""
    chain = _chain(e)
    names = {type(x).__name__ for x in chain}
    text = " | ".join(str(x) for x in chain).lower()
    status = next((s for x in chain if (s := _status(x)) is not None), None)
    if any(isinstance(x, ImportError) for x in chain):
        return local_embed.NO_EXTRA
    if "OfflineModeIsEnabled" in names or offline_env():
        return OFFLINE
    if any(isinstance(x, OSError) and x.errno == errno.ENOSPC for x in chain) or "no space left" in text:
        return DISK_FULL
    if names & {"FileMetadataError", "ProxyError"}:
        return BLOCKED
    if "SSLError" in names or "certificate" in text or "ssl" in text:
        return CERTIFICATE
    if status is not None and status >= 500:
        return HUB_DOWN.format(status=status)
    if names & NET_ERRORS or any(h in text for h in NET_HINTS):
        return NO_NETWORK
    if any(isinstance(x, PermissionError) for x in chain):
        return IN_USE
    return f"o download falhou ({type(e).__name__}): tente de novo (continua de onde parou)"


def file_error(e: OSError) -> str:
    """A falha ao ler, apagar ou gravar na pasta de modelos (R10-03): `PermissionError` é, no Windows, o arquivo aberto por
    outro programa e, nos outros sistemas, a pasta sem gravação (a frase cobre os dois, como o U-030)."""
    if e.errno == errno.ENOSPC:
        return DISK_FULL
    if isinstance(e, PermissionError):
        return IN_USE
    return f"não consegui ler ou gravar na pasta de modelos ({type(e).__name__}): tente de novo"


# ---------------------------------------------------------------- preparo
def _progress(label: str, state: str, fraction: float) -> dict[str, Any]:
    return {"type": "progress", "model": label, "status": state, "fraction": round(min(1.0, fraction), 3)}


def _failed(emit: Emit, label: str, message: str) -> int:
    emit({"type": "error", "model": label, "message": message})
    return 1


def _one_file(folder: Path, name: str, digest: str, label: str, get: Download, emit: Emit, fraction: float) -> bool:
    """Um arquivo conferido (ou baixado e conferido); False com a linha de erro do download ou do sha256 já emitida. Erro de
    arquivo (`OSError` ao ler ou apagar) sobe para `prepare`."""
    path = folder / name
    if path.is_file():
        emit(_progress(label, "conferindo", fraction))
        if sha256_of(path) == digest:
            return True
        log.warning("busca local: %s com sha256 diferente do fixado: apagado e baixado de novo", path)
        path.unlink(missing_ok=True)
    emit(_progress(label, "baixando", fraction))
    try:
        got = get(name, folder)
    except Exception as e:  # rede, proxy, disco, extra ausente: a linha de erro é para o usuário
        log.warning("busca local: download de %s falhou: %s: %s (causa: %r)", name, type(e).__name__, e, e.__cause__)
        _failed(emit, label, download_error(e))
        return False
    if sha256_of(got) != digest:
        got.unlink(missing_ok=True)
        log.warning("busca local: %s baixado com sha256 diferente do fixado (%s): apagado", name, digest[:12])
        _failed(emit, label, SHA_MISMATCH)
        return False
    return True


def prepare(folder: Path, emit: Emit, *, download: Download | None = None) -> int:
    """Os dois arquivos na revisão fixada, conferidos pelo sha256, e a marca `.ready` no fim (0); 1 com a linha de erro."""
    get = download or hub_download
    files = dict(local_embed.FILES)
    weights = {name: float(local_embed.SIZES.get(name, 1)) for name in files}
    total = sum(weights.values()) or 1.0
    done = 0.0
    step = "pasta"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / local_embed.READY_MARK).unlink(missing_ok=True)
        for name, digest in files.items():
            step = local_embed.LABELS.get(name, name)
            if not _one_file(folder, name, digest, step, get, emit, done / total):
                return 1
            done += weights[name]
            emit(_progress(step, "pronto", done / total))
        step = "marca"
        write_ready(folder)
    except OSError as e:  # arquivo em uso por outro programa, pasta sem gravação, disco cheio (R10-03)
        log.warning("busca local: preparo parou em %s: %s: %s", step, type(e).__name__, e)
        return _failed(emit, step, file_error(e))
    log.info("busca local pronta em %s (revisão %s)", folder, local_embed.REVISION[:8])
    return 0


__all__ = ["download_error", "file_error", "hub_download", "offline_env", "prepare", "sha256_of", "write_ready"]
