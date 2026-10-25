"""Lado do app do ao vivo (F4): onde ficam os modelos e se estão prontos. Sem Qt e sem ML (regra estática de importações:
nada de torch, faster_whisper, pyannote nem onnxruntime aqui)."""
from __future__ import annotations

import os
from pathlib import Path

from ..config.defaults import LIVE_MODELS
from ..platform.common import paths

DEGRADE_MODEL = "base"            # F4 item 9: a degradação troca para este


def is_fake() -> bool:
    """`LISTENER_LIVE=fake`: VAD por energia, transcritor e embedder determinísticos (`tests/fake_live.py`), sem modelos."""
    return os.environ.get("LISTENER_LIVE", "").strip().lower() == "fake"


def live_models_dir(models_dir_setting: str = "") -> Path:
    """A mesma pasta de modelos da F2 (`pipeline.models_dir`; vazio = `<dados do sistema>/models`), sem importar
    `pipeline.models` (que o app não carrega)."""
    return Path(models_dir_setting).expanduser() if models_dir_setting else paths.data_dir_suggestion() / "models"


def whisper_model_dir(models_dir: Path, model: str) -> Path:
    return models_dir / f"faster-whisper-{model}"


def required_models(model: str) -> tuple[str, ...]:
    """O modelo escolhido mais o da degradação (`--prepare` baixa os dois)."""
    return tuple(dict.fromkeys((model, DEGRADE_MODEL)))


def models_ready(models_dir: Path, model: str = LIVE_MODELS[0]) -> bool:
    """Os modelos do ao vivo estão na pasta (`model.bin` de cada um); no modo fake, sempre."""
    if is_fake():
        return True
    return all((whisper_model_dir(models_dir, m) / "model.bin").exists() for m in required_models(model))
