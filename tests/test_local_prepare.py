"""F5.1 L6, o preparo da busca local sem rede nem o hub real (A-13): arquivo presente só conferido, sha256 diferente apaga e
baixa de novo, baixado e diferente falha sem a marca, `.ready` atômico, o fake do `--prepare --what rag` não escreve e o
filho desliga o Xet antes do hub (R10-02); as falhas viram linha de erro com texto sem jargão, também as do hub com a classe
e a mensagem reais (o `LocalEntryNotFoundError` sem rede não é "modo offline", R10-01) e as de arquivo em uso, pasta sem
gravação e disco cheio (R10-03). As exceções do hub são classes falsas com o nome e a mensagem reais: nenhum teste importa
`huggingface_hub` no processo da suíte."""
from __future__ import annotations

import errno
import hashlib
import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from listener.live import protocol as pr
from listener.llm import local_embed, local_prepare
from listener.platform.common import process
from listener.store import Store

NO_CONNECTION = ("An error happened while trying to locate the file on the Hub and we cannot find the requested files in the local "
                 "cache. Please check your connection and try again or make sure your Internet connection is on.")
OFFLINE_DISK = ("Cannot find the requested files in the disk cache and outgoing traffic has been disabled. To enable hf.co "
                "look-ups and downloads online, set 'local_files_only' to False.")
OFFLINE_ADAPTER = ("Cannot reach https://huggingface.co/api/models/intfloat/multilingual-e5-small: offline mode is enabled. To "
                   "disable it, please unset the `HF_HUB_OFFLINE` environment variable.")
METADATA = ("Distant resource does not seem to be on huggingface.co. It is possible that a configuration issue prevents you from "
            "downloading resources from https://huggingface.co. Please check your firewall and proxy settings and make sure your "
            "SSL certificates are updated.")
TIMEOUT = ("HTTPSConnectionPool(host='huggingface.co', port=443): Max retries exceeded with url: /intfloat/multilingual-e5-small/"
           "resolve/614241f622f53c4eeff9890bdc4f31cfecc418b3/onnx/tokenizer.json (Caused by ConnectTimeoutError(<urllib3.connection."
           "HTTPSConnection object>, 'Connection to huggingface.co timed out. (connect timeout=10)'))")
SSL = ("HTTPSConnectionPool(host='huggingface.co', port=443): Max retries exceeded with url: /api/models (Caused by SSLError("
       "SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer "
       "certificate (_ssl.c:1010)')))")


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


def hub_error(name: str, message: str, base: type[BaseException] = Exception, *, cause: BaseException | None = None,
              status: int | None = None) -> BaseException:
    """Uma exceção com o nome e a mensagem da do hub (a classe real não é importada) e, como o hub faz, a causa embrulhada."""
    error = type(name, (base,), {})(message)
    if status is not None:
        error.response = _Response(status)  # type: ignore[attr-defined]
    error.__cause__ = cause
    return error


def no_connection(cause: BaseException) -> BaseException:
    return hub_error("LocalEntryNotFoundError", NO_CONNECTION, FileNotFoundError, cause=cause)


@pytest.fixture(autouse=True)
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(var, raising=False)


def _fake_files(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Dois arquivos pequenos no lugar dos do e5, com o sha256 deles fixado (o preparo confere de verdade)."""
    content = {"onnx/model.onnx": b"pesos do modelo" * 100, "onnx/tokenizer.json": b'{"vocab": []}'}
    monkeypatch.setattr(local_embed, "FILES", {k: hashlib.sha256(v).hexdigest() for k, v in content.items()})
    monkeypatch.setattr(local_embed, "LABELS", {"onnx/model.onnx": "modelo", "onnx/tokenizer.json": "vocabulário"})
    return content


def _writer(content: dict[str, bytes], downloads: list[str] | None = None) -> Any:
    def download(name: str, where: Path) -> Path:
        if downloads is not None:
            downloads.append(name)
        path = where / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content[name])
        return path
    return download


def test_prepare_checks_present_files_downloads_the_rest_and_marks_ready(tmp_path, monkeypatch):
    content = _fake_files(monkeypatch)
    folder = tmp_path / "modelos" / "multilingual-e5-small"
    (folder / "onnx").mkdir(parents=True)
    (folder / "onnx" / "model.onnx").write_bytes(content["onnx/model.onnx"])                           # já presente e certo
    downloads: list[str] = []
    payload = dict(content)
    lines: list[dict[str, Any]] = []
    assert local_prepare.prepare(folder, lines.append, download=_writer(payload, downloads)) == 0
    assert downloads == ["onnx/tokenizer.json"]                                                           # o presente só é conferido
    assert [(m["model"], m["status"]) for m in lines] == [("modelo", "conferindo"), ("modelo", "pronto"), ("vocabulário", "baixando"),
                                                          ("vocabulário", "pronto")]
    assert lines[-1]["fraction"] == 1.0 and local_embed.is_ready(folder) and not (folder / ".ready.tmp").exists()
    (folder / "onnx" / "model.onnx").write_bytes(b"corrompido")                                          # presente e diferente:
    lines.clear()                                                                                         # sai e vem de novo
    assert local_prepare.prepare(folder, lines.append, download=_writer(payload, downloads)) == 0
    assert downloads[-1] == "onnx/model.onnx" and ("modelo", "baixando") in [(m["model"], m["status"]) for m in lines]
    payload["onnx/tokenizer.json"] = b"outra coisa"                                                      # baixado e diferente:
    (folder / "onnx" / "tokenizer.json").unlink()                                                        # apaga, sem marca
    lines.clear()
    assert local_prepare.prepare(folder, lines.append, download=_writer(payload, downloads)) == 1
    assert lines[-1] == {"type": "error", "model": "vocabulário", "message": local_prepare.SHA_MISMATCH}
    assert not (folder / "onnx" / "tokenizer.json").exists() and not (folder / ".ready").exists()


@pytest.mark.parametrize(("exc", "expected"), [
    (no_connection(hub_error("ConnectTimeout", TIMEOUT, ConnectionError)), local_prepare.NO_NETWORK),     # sem rede (R10-01)
    (no_connection(hub_error("OfflineModeIsEnabled", OFFLINE_ADAPTER, ConnectionError)), local_prepare.OFFLINE),
    (hub_error("LocalEntryNotFoundError", OFFLINE_DISK, FileNotFoundError), local_prepare.NO_NETWORK),   # sem a variável, rede
    (no_connection(hub_error("HfHubHTTPError", "503 Server Error: Service Unavailable for url: https://huggingface.co/api",
                             OSError, status=503)), local_prepare.HUB_DOWN.format(status=503)),
    (hub_error("SSLError", SSL, ConnectionError), local_prepare.CERTIFICATE),                             # sobe direto, sem embrulho
    (no_connection(hub_error("SSLError", SSL, ConnectionError)), local_prepare.CERTIFICATE),
    (no_connection(hub_error("FileMetadataError", METADATA, OSError)), local_prepare.BLOCKED),
    (hub_error("ProxyError", "Unable to connect to proxy", ConnectionError), local_prepare.BLOCKED),
    (hub_error("ChunkedEncodingError", "Connection broken: IncompleteRead(4096 bytes read)", ConnectionError),
     local_prepare.NO_NETWORK),                                                                           # caiu no meio do arquivo
    (ImportError("No module named 'huggingface_hub'"), local_embed.NO_EXTRA),
    (OSError(errno.ENOSPC, "No space left on device"), local_prepare.DISK_FULL),
    (PermissionError(errno.EACCES, "Permission denied"), local_prepare.IN_USE),
    (ValueError("estranho"), "o download falhou (ValueError): tente de novo (continua de onde parou)"),
])
def test_prepare_failure_keeps_no_mark_and_says_why(tmp_path, monkeypatch, exc, expected):
    _fake_files(monkeypatch)
    folder = tmp_path / "multilingual-e5-small"
    folder.mkdir()
    (folder / ".ready").write_text("{}", encoding="utf-8")                                              # a marca velha sai

    def download(_name: str, _where: Path) -> Path:
        raise exc

    lines: list[dict[str, Any]] = []
    assert local_prepare.prepare(folder, lines.append, download=download) == 1
    assert lines[-1] == {"type": "error", "model": "modelo", "message": expected} and not (folder / ".ready").exists()


def test_offline_message_only_with_offline_mode_on(monkeypatch):
    """R10-01: o `LocalEntryNotFoundError` do hub é "sem rede"; o modo offline só quando o ambiente o liga."""
    for message in (NO_CONNECTION, OFFLINE_DISK):
        error = hub_error("LocalEntryNotFoundError", message, FileNotFoundError)
        assert local_prepare.download_error(error) == local_prepare.NO_NETWORK
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        assert local_prepare.download_error(error) == local_prepare.OFFLINE
        monkeypatch.setenv("HF_HUB_OFFLINE", "0")
        assert local_prepare.download_error(error) == local_prepare.NO_NETWORK
        monkeypatch.delenv("HF_HUB_OFFLINE")


def test_file_errors_become_an_error_line_instead_of_killing_the_child(tmp_path, monkeypatch):
    """R10-03: arquivo em uso por outro programa (ao conferir ou apagar), disco cheio ao gravar a marca e a pasta que não
    abre viram a linha de erro e o código 1, com a marca fora."""
    content = _fake_files(monkeypatch)
    folder = tmp_path / "multilingual-e5-small"
    (folder / "onnx").mkdir(parents=True)
    (folder / "onnx" / "model.onnx").write_bytes(b"corrompido")
    (folder / ".ready").write_text("{}", encoding="utf-8")

    def in_use(_path: Path) -> str:
        raise PermissionError(errno.EACCES, "O arquivo já está sendo usado por outro processo")

    lines: list[dict[str, Any]] = []
    with monkeypatch.context() as m:
        m.setattr(local_prepare, "sha256_of", in_use)
        assert local_prepare.prepare(folder, lines.append, download=_writer(content)) == 1
    assert lines[-1] == {"type": "error", "model": "modelo", "message": local_prepare.IN_USE} and not (folder / ".ready").exists()
    assert "feche o programa que usa o arquivo e tente de novo" in local_prepare.IN_USE

    def disk_full(_folder: Path) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device")

    lines.clear()
    with monkeypatch.context() as m:
        m.setattr(local_prepare, "write_ready", disk_full)
        assert local_prepare.prepare(folder, lines.append, download=_writer(content)) == 1
    assert lines[-1] == {"type": "error", "model": "marca", "message": local_prepare.DISK_FULL}
    blocker = tmp_path / "arquivo"
    blocker.write_text("x", encoding="utf-8")
    lines.clear()
    assert local_prepare.prepare(blocker / "multilingual-e5-small", lines.append, download=_writer(content)) == 1   # não é pasta
    assert lines[-1]["type"] == "error" and lines[-1]["model"] == "pasta"


@pytest.mark.skipif(sys.platform != "win32", reason="só o Windows segura o arquivo aberto (sem FILE_SHARE_DELETE)")
def test_a_file_held_open_by_another_program_is_an_error_line_on_windows(tmp_path, monkeypatch):
    content = _fake_files(monkeypatch)
    folder = tmp_path / "multilingual-e5-small"
    (folder / "onnx").mkdir(parents=True)
    held = folder / "onnx" / "model.onnx"
    held.write_bytes(b"corrompido")                                                                      # sha256 errado: sai
    lines: list[dict[str, Any]] = []
    with held.open("rb"):                                                                                 # ...mas está aberto
        assert local_prepare.prepare(folder, lines.append, download=_writer(content)) == 1
    assert lines[-1] == {"type": "error", "model": "modelo", "message": local_prepare.IN_USE} and not (folder / ".ready").exists()


def _lines(out: io.BytesIO) -> list[dict[str, Any]]:
    return [pr.decode_line(line) for line in out.getvalue().splitlines()]


def test_live_prepare_what_rag_fake_writes_nothing_and_real_marks_ready(tmp_path, monkeypatch):
    from listener.live import proc

    db, models = tmp_path / "db", tmp_path / "modelos"
    with Store(db) as s:
        s.set_setting("pipeline.models_dir", str(models))
    monkeypatch.setenv("LISTENER_LIVE", "fake")
    out = io.BytesIO()
    assert proc.prepare(db, out, "rag") == 0
    lines = _lines(out)
    assert [(m["model"], m["status"]) for m in lines if m["type"] == "progress"] == [("modelo", "pronto"), ("vocabulário", "pronto")]
    assert lines[-1] == {"type": "done", "models_dir": str(models / "multilingual-e5-small")} and not models.exists()   # A-09
    monkeypatch.delenv("LISTENER_LIVE")
    content = _fake_files(monkeypatch)
    for var in ("HF_HOME", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_SYMLINKS_WARNING", "HF_HUB_DISABLE_XET"):
        monkeypatch.setenv(var, "antes")                                                                 # restauradas ao fim
    monkeypatch.setattr(local_prepare, "hub_download", _writer(content))
    out = io.BytesIO()
    assert proc.prepare(db, out, "rag") == 0
    assert _lines(out)[-1]["type"] == "done" and local_embed.is_ready(models / "multilingual-e5-small")
    assert os.environ["HF_HOME"] == str(models / "hf") and os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"                                                       # R10-02: o HTTP retoma
    argv = process.live_prepare_command(db, "rag")
    assert argv[-5:] == ["--prepare", "--what", "rag", "--db", str(db)] and "--what" not in process.live_prepare_command(db)
