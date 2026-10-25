"""Modelos do pipeline (F2 item 17, R-4): pasta configurável (`pipeline.models_dir`, padrão `<dados do sistema>/models`),
`HF_HOME` dentro dela, symlink do Hugging Face desligado antes de qualquer download (WinError 1314 sem Modo de
Desenvolvedor), `HF_HUB_OFFLINE=1` quando os arquivos já estão completos, download como etapa do job com progresso e
mensagens próprias por falha (token ausente, termos de cada repositório gated não aceitos, extra `pipeline` ausente, sem
rede). Só o worker importa este módulo; torch e companhia entram preguiçosamente nos adaptadores (`whisper.py`,
`pyannote.py`). Escrito às cegas no WSL (sem token nem torch); verificado no Windows com o token (B4)."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..log import get_logger
from ..platform.common import paths
from .base import PermanentError, PipelineDeps, ProgressFn
from .types import JobConfig

log = get_logger("pipeline.models")

DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"
GATED_REPOS = (DIARIZATION_REPO, EMBEDDING_REPO)
READY_MARK = ".ready"
INSTALL_HINT = "instale o extra: pip install -e .[pipeline] (faster-whisper, pyannote.audio e torch)"


def models_dir(config: JobConfig) -> Path:
    return Path(config.models_dir).expanduser() if config.models_dir else paths.data_dir_suggestion() / "models"


def whisper_dir(config: JobConfig) -> Path:
    return models_dir(config) / f"faster-whisper-{config.whisper_model}"


def hf_home(config: JobConfig) -> Path:
    return models_dir(config) / "hf"


def ready_mark(config: JobConfig, name: str) -> Path:
    return models_dir(config) / f"{name}{READY_MARK}"


def apply_environment(config: JobConfig) -> dict[str, str]:
    """Variáveis que precisam existir antes de importar torch, huggingface_hub e ctranslate2."""
    home = hf_home(config)
    home.mkdir(parents=True, exist_ok=True)
    values = {"HF_HOME": str(home), "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
              "OMP_NUM_THREADS": str(max(1, config.torch_threads)), "MKL_NUM_THREADS": str(max(1, config.torch_threads)),
              "CT2_VERBOSE": "0", "TOKENIZERS_PARALLELISM": "false"}
    offline = ready_mark(config, "pyannote").exists() and whisper_dir(config).exists()
    if offline:
        values["HF_HUB_OFFLINE"] = "1"                       # tudo baixado: nada de bater na rede ao abrir
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.update(values)
    hub = sys.modules.get("huggingface_hub")                 # a constante é lida na importação: ajustar no módulo vivo (R2-19)
    constants = getattr(hub, "constants", None)
    if constants is not None:
        with suppress(Exception):
            constants.HF_HUB_OFFLINE = offline
    return values


def disable_hf_symlinks() -> None:
    """O cache do Hugging Face copia em vez de criar links (medido em 24/10: `WinError 1314`); chamar antes de baixar."""
    try:
        import huggingface_hub.file_download as fd

        fd.are_symlinks_supported = lambda *_a, **_k: False  # type: ignore[assignment]
    except Exception as e:  # sem huggingface_hub o erro aparece com mensagem própria mais adiante
        log.debug("não desliguei os symlinks do Hugging Face: %s", e)


def classify_error(e: BaseException, repo: str = "") -> PermanentError | None:
    """Traduz falhas de download e carga em mensagens próprias; None quando é uma falha comum (conta tentativa)."""
    name = type(e).__name__
    text = str(e)
    if isinstance(e, ImportError | ModuleNotFoundError):
        return PermanentError(f"{INSTALL_HINT} ({text})")
    if name in ("GatedRepoError",) or "gated" in text.lower() or "403" in text:
        return PermanentError(f"aceite os termos de uso de {repo or 'cada repositório do pyannote'} no Hugging Face com a conta do token")
    if name in ("RepositoryNotFoundError", "HfHubHTTPError") and ("401" in text or "Unauthorized" in text or "Invalid" in text):
        return PermanentError("token do Hugging Face inválido: confira nas Configurações (Processamento)")
    if name in ("LocalEntryNotFoundError", "OfflineModeIsEnabled") or "offline" in text.lower():
        return PermanentError(f"modelo {repo} ainda não baixado e o modo offline está ativo: ligue a rede e tente de novo")
    if name in ("ConnectionError", "ConnectTimeout", "ReadTimeout", "MaxRetryError", "NewConnectionError", "URLError") \
            or "Max retries" in text or "Name or service not known" in text or "getaddrinfo" in text:
        return PermanentError(f"sem rede para baixar {repo or 'os modelos'}; tente de novo com conexão")
    return None


def _progress(progress: ProgressFn, message: str, fraction: float) -> None:
    try:
        progress(message, fraction)
    except Exception as e:  # progresso nunca derruba o download
        log.debug("progresso: %s", e)


def download_whisper(config: JobConfig, progress: ProgressFn) -> Path:
    """`faster_whisper.download_model(size, output_dir=...)`: cópia local sem symlink; idempotente se a pasta existir."""
    target = whisper_dir(config)
    if (target / "model.bin").exists():
        return target
    _progress(progress, f"baixando modelo Whisper {config.whisper_model}…", 0.0)
    try:
        from faster_whisper import download_model

        target.parent.mkdir(parents=True, exist_ok=True)
        download_model(config.whisper_model, output_dir=str(target))
    except Exception as e:
        err = classify_error(e, f"faster-whisper {config.whisper_model}")
        if err is not None:
            raise err from e
        raise
    _progress(progress, f"modelo Whisper {config.whisper_model} pronto", 1.0)
    return target


def download_pyannote(config: JobConfig, token: str, progress: ProgressFn) -> None:
    """Pipeline community-1 e wespeaker no cache (`HF_HOME` dentro da pasta de modelos); marca `.ready` ao terminar."""
    mark = ready_mark(config, "pyannote")
    if mark.exists():
        return
    if not token:
        raise PermanentError("token do Hugging Face ausente: informe nas Configurações (Processamento) e aceite os termos dos "
                             f"repositórios {', '.join(GATED_REPOS)}")
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        raise PermanentError(f"{INSTALL_HINT} ({e})") from e
    for i, repo in enumerate(GATED_REPOS):
        _progress(progress, f"baixando {repo}…", i / len(GATED_REPOS))
        try:
            snapshot_download(repo, token=token)
        except Exception as e:
            err = classify_error(e, repo)
            if err is not None:
                raise err from e
            raise
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text("ok", encoding="utf-8")
    _progress(progress, "modelos do pyannote prontos", 1.0)


def prepare_models(config: JobConfig, token: str, progress: ProgressFn) -> None:
    """Etapa `download` do job (item 17): só baixa o que falta; erros permanentes têm mensagem própria."""
    apply_environment(config)
    disable_hf_symlinks()
    download_whisper(config, progress)
    download_pyannote(config, token, progress)
    apply_environment(config)                               # agora pode ligar o modo offline


def _adapter(module: str, name: str) -> Any:
    """Importa um adaptador real; sem o extra `pipeline` o erro é permanente e diz o que instalar."""
    try:
        import importlib

        return getattr(importlib.import_module(f"listener.pipeline.{module}"), name)
    except ImportError as e:
        raise PermanentError(f"{INSTALL_HINT} ({e})") from e


def real_deps(config: JobConfig, token: str) -> PipelineDeps:
    """Fábricas preguiçosas dos adaptadores reais; nada é importado até a etapa que precisa."""
    apply_environment(config)

    def transcriber() -> Any:
        cls = _adapter("whisper", "FasterWhisperTranscriber")
        return cls(whisper_dir(config), config.whisper_model, cpu_threads=config.torch_threads, hq=config.whisper_hq)

    def diarizer() -> Any:
        return _adapter("pyannote", "PyannoteDiarizer")(token, torch_threads=config.torch_threads)

    def embedder() -> Any:
        return _adapter("pyannote", "PyannoteEmbedder")(token, torch_threads=config.torch_threads)

    prepare: Callable[[ProgressFn], None] = lambda progress: prepare_models(config, token, progress)  # noqa: E731
    return PipelineDeps(transcriber, diarizer, embedder, prepare, name="real")


def deps_key(config: JobConfig, token: str = "") -> tuple[str, bool, int, str, str]:
    """Muda -> o worker reconstrói as dependências (modelo escolhido vale para o próximo job, CP8; token novo colado nas
    Configurações vale para a próxima tentativa, R2-13). O token entra por hash: nunca em log."""
    import hashlib

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12] if token else ""
    return (config.whisper_model, config.whisper_hq, config.torch_threads, config.models_dir, digest)
