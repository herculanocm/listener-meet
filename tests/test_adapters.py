"""Adaptadores reais (F2 L5) nesta máquina sem torch: os módulos importam, os construtores não carregam nada e o primeiro
uso falha com a mensagem própria (extra `pipeline` ausente ou token ausente). O comportamento real só se verifica no
Windows com o token (B4)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from listener.pipeline.base import PermanentError
from listener.pipeline.types import AudioWindow


@pytest.fixture
def forget_adapters():
    """Os módulos dos adaptadores saem do `sys.modules` ao fim: a regra dura (test_runner) confere que o app não os puxa."""
    yield
    for name in ("listener.pipeline.whisper", "listener.pipeline.pyannote", "listener.pipeline.models"):
        sys.modules.pop(name, None)


def test_whisper_adapter_imports_without_torch_and_fails_clearly(tmp_path, forget_adapters):
    from listener.pipeline.whisper import FasterWhisperTranscriber

    t = FasterWhisperTranscriber(tmp_path / "faster-whisper-small", "small", cpu_threads=2, hq=True)
    assert t.name == "faster-whisper:small:int8:hq" and t._model is None
    win = AudioWindow(0, "R", 0, 1000, np.zeros(16000, np.float32))
    with pytest.raises(PermanentError, match="pip install"):
        t.transcribe(win, language=None, initial_prompt="")
    assert "torch" not in sys.modules and "faster_whisper" not in sys.modules


def test_pyannote_adapters_import_without_torch_and_fail_clearly(forget_adapters):
    from listener.pipeline import pyannote as pa

    d = pa.PyannoteDiarizer("", torch_threads=2)
    assert d.name == "pyannote:speaker-diarization-community-1" and d.embedding_model is None
    win = AudioWindow(0, "R", 0, 1000, np.zeros(16000, np.float32))
    with pytest.raises(PermanentError, match="token"):
        d.diarize(win, min_speakers=1, max_speakers=3)
    with pytest.raises(PermanentError, match="pip install"):
        pa.PyannoteDiarizer("hf_x").diarize(win, min_speakers=None, max_speakers=None)
    e = pa.PyannoteEmbedder("hf_x")
    assert e.model == "pyannote/wespeaker-voxceleb-resnet34-LM" and e.dim == 256
    with pytest.raises(PermanentError, match="pip install"):
        e.embed(np.zeros(8000, np.float32), 16000)
    assert "torch" not in sys.modules and "pyannote" not in sys.modules

    class Seg:
        def __init__(self, s, e):
            self.start, self.end = s, e

    class Ann:
        def itertracks(self, yield_label=True):
            yield Seg(0.5, 2.0), None, "SPEAKER_01"
            yield Seg(0.0, 0.5), None, "SPEAKER_00"
            yield Seg(3.0, 3.0), None, "SPEAKER_00"

        def labels(self):
            return ["SPEAKER_00", "SPEAKER_01"]

        def get_overlap(self):
            return [Seg(1.0, 1.2)]

    assert pa.turns_of(Ann(), 1000) == (pa.Turn(1000, 1500, "SPEAKER_00"), pa.Turn(1500, 3000, "SPEAKER_01"))
    assert pa.overlaps_of(Ann(), 1000) == ((2000, 2200),) and pa.overlaps_of(object(), 0) == ()

    class Result:
        speaker_embeddings = np.array([[1.0, 0.0], [0.0, 2.0]], np.float32)

    probe = d._probe(Result(), Ann())
    assert probe is not None and np.allclose(probe["SPEAKER_01"], [0.0, 1.0]) and d._probe(object(), Ann()) is None


def test_adapter_modules_are_not_imported_by_app_modules_statically():
    """Regra dura (F2 item 1) conferida no código-fonte: imports de módulo (fora de função) de torch, faster_whisper,
    pyannote, whisper.py, pyannote.py e models.py não aparecem no que o app importa."""
    import ast

    root = Path(__file__).resolve().parent.parent / "listener"
    app_files = [*(root / "ui").glob("*.py"), root / "__main__.py", root / "pipeline" / "runner.py", root / "pipeline" / "worker.py",
                 root / "pipeline" / "estimate.py", root / "pipeline" / "types.py", root / "pipeline" / "base.py",
                 root / "pipeline" / "windows.py", root / "pipeline" / "align.py", root / "pipeline" / "profiles.py",
                 root / "pipeline" / "stitch.py"]
    banned = {"torch", "faster_whisper", "pyannote", "whisperx", "ctranslate2", "huggingface_hub"}
    banned_local = {"whisper", "pyannote", "models"}
    offenders: list[str] = []
    for path in app_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                                   # só o nível do módulo: dentro de função é preguiçoso
            if isinstance(node, ast.Import):
                offenders += [f"{path.name}: {a.name}" for a in node.names if a.name.split(".")[0] in banned]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in banned or (node.level and mod.split(".")[-1] in banned_local):
                    offenders.append(f"{path.name}: from {'.' * node.level}{mod}")
                offenders += [f"{path.name}: {a.name}" for a in node.names if node.level and a.name in banned_local and not mod]
    assert offenders == []


def test_llm_sdks_are_not_imported_at_module_level_outside_the_adapters():
    """Regra dura da F3 (F3A-24): `llm/registry.py`, `types.py`, `schema.py`, `base.py`, `fake.py`, `prompts/*`, `ui/*`, o
    runner e o worker nunca importam `google`, `openai`, `anthropic`, `httpx`, `httpx2`, `truststore` nem `pydantic` no
    nível do módulo; só `llm/gemini.py`, `llm/openai.py` e `llm/anthropic.py` podem."""
    import ast

    root = Path(__file__).resolve().parent.parent / "listener"
    adapters = {"gemini.py", "openai.py", "anthropic.py"}
    files = [p for p in (root / "llm").rglob("*.py") if p.name not in adapters] + list((root / "ui").glob("*.py")) + [
        root / "pipeline" / "runner.py", root / "pipeline" / "worker.py", root / "pipeline" / "jobbase.py", root / "store" / "core.py",
        root / "__main__.py"]
    steps = root / "pipeline" / "llm_steps.py"
    if steps.exists():
        files.append(steps)
    banned = {"google", "openai", "anthropic", "httpx", "httpx2", "truststore", "pydantic", "msgspec", "torch"}
    never = {"pydantic", "msgspec"}                        # nem dentro de função, no projeto e na suíte (contrato "Saída estruturada")
    restricted = {p.resolve() for p in files}
    offenders: list[str] = []
    for path in list(root.rglob("*.py")) + list((root.parent / "tests").glob("*.py")):   # cada arquivo analisado uma vez só
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = path.resolve() in restricted
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            at_top = node in tree.body
            offenders += [f"{path.name}: {n}" for n in names if n in never or (top and at_top and n in banned)]
    assert offenders == []
