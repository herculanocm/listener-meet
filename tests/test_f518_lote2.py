"""F5.18, lote 2 (PF-04, PF-07, PF-08) com os fakes, sem torch nem modelos: o `get_embeddings` da `LeanDiarization` com um modelo
falso (o tronco uma vez por lote de trechos, a escolha entre a máscara limpa e a cheia, o gancho por lote), as guardas caindo
no original, o lote em trechos sem o x3; o gate de eco do L no runner (o transcritor e o reforço do perfil recebem zeros nos
blocos de eco e as amostras originais na fala própria, as contagens no `timings_json`, nada muda com o gate desligado ou na
sala presencial); e a limpeza do PF-08 (o id do modelo de embedding, os rótulos da sonda Q8 pela anotação cheia)."""
from __future__ import annotations

import contextlib
import functools
import json
import logging
import math
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.model import JobStatus
from listener.pipeline import lean_diarization as lean
from listener.pipeline.echo_gate import EchoGate, EchoStats, stats_from_json
from listener.pipeline.runner import JobRunner
from listener.pipeline.types import AudioWindow, DiarizationWindow, JobConfig, Turn
from listener.store.playback import decode_range
from tests.fake_pipeline import FakeDeps, ScriptedTranscriber
from tests.test_runner import _run

SR = 16000
CFG = JobConfig(diar_window_s=30, whisper_window_s=30, whisper_overlap_s=0, diar_overlap_s=0, silence_rms=0.01, threshold=0.6, margin=0.05)
SCRIPT = {"R": [(0.5, 29.0, "fala da reunião")], "L": [(10.5, 19.5, "minha fala")]}


@pytest.fixture
def no_torch(monkeypatch):
    """O torch fora do caminho novo: tensores são arrays numpy e o `inference_mode` é um `nullcontext`."""
    monkeypatch.setattr(lean, "_tensor", lambda a, _device: np.asarray(a))
    monkeypatch.setattr(lean, "_numpy", np.asarray)
    monkeypatch.setattr(lean, "_quiet", contextlib.nullcontext)


# ---------------------------------------------------------------- PF-04: os fakes do pipeline
class _Audio:
    def crop(self, file: dict[str, Any], chunk: Any, mode: str = "pad") -> tuple[np.ndarray, int]:
        assert mode == "pad"
        wav = file["waveform"]
        a, b = round(chunk.start * SR), round(chunk.end * SR)
        out = np.zeros((1, b - a), np.float32)
        part = wav[:, a:min(b, wav.shape[1])]
        out[:, :part.shape[1]] = part
        return out, SR


class _Resnet:
    def __init__(self, two_emb_layer: bool = False):
        self.two_emb_layer = two_emb_layer


class WeSpeakerResNet34:
    """O modelo falso: `forward_frames` devolve a média do waveform por trecho e `forward_embedding` põe no vetor os quadros
    somados de cada slot (a prova de qual máscara entrou) e a média do trecho."""

    def __init__(self, two_emb_layer: bool = False):
        self.resnet = _Resnet(two_emb_layer)
        self.frames_calls: list[tuple[int, ...]] = []
        self.embed_calls: list[tuple[int, ...]] = []

    def forward_frames(self, waveforms: np.ndarray) -> np.ndarray:
        self.frames_calls.append(tuple(waveforms.shape))
        return waveforms.mean(axis=2)                                 # (lote, 1)

    def forward_embedding(self, frames: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
        assert weights is not None and weights.ndim == 3 and weights.dtype == np.float32
        self.embed_calls.append(tuple(weights.shape))
        out = np.zeros((*weights.shape[:2], 4), np.float32)
        out[:, :, 0] = weights.sum(axis=2)
        out[:, :, 1] = frames[:, :1]
        return out


class _Inference:
    sample_rate = SR
    dimension = 4

    def __init__(self, model: Any):
        self.model_ = model
        self.device = None

    @functools.cached_property
    def min_num_samples(self) -> int:
        return 640


class SpeakerDiarization:
    """O pipeline falso com o nome da classe instalada (a guarda olha o nome): o `get_embeddings` original devolve -1."""

    def __init__(self, model: Any, batch: int = 2, exclude_overlap: bool = True):
        self._embedding = _Inference(model)
        self._audio = _Audio()
        self.embedding_batch_size = batch
        self.embedding_exclude_overlap = exclude_overlap
        self.training = False
        self.original_calls = 0

    def get_embeddings(self, file: Any, binary_segmentations: Any, exclude_overlap: bool = False, hook: Any = None) -> np.ndarray:
        self.original_calls += 1
        return np.full((len(binary_segmentations.data), binary_segmentations.data.shape[2], 4), -1.0, np.float32)


class _Segmentations:
    """Um `SlidingWindowFeature` de mentira: `.data` (trechos, quadros, falantes), `.sliding_window.duration` e a iteração
    (Segment, data[i])."""

    def __init__(self, data: np.ndarray, duration: float = 10.0, step: float = 1.0):
        self.data = data
        self.sliding_window = SimpleNamespace(duration=duration, step=step)

    def __iter__(self) -> Any:
        return ((SimpleNamespace(start=i * self.sliding_window.step, end=i * self.sliding_window.step + self.sliding_window.duration),
                 self.data[i]) for i in range(len(self.data)))


def _segmentations(chunks: int = 5, frames: int = 20) -> _Segmentations:
    """Trecho 0: o falante 0 fala sempre, o 1 só sobreposto (quadros 0-9), o 2 nunca; os outros trechos: só o 0, e o trecho 1
    com NaN (costura parcial) no falante 1."""
    data = np.zeros((chunks, frames, 3), np.float32)
    data[:, :, 0] = 1.0
    data[0, :10, 1] = 1.0
    data[1, :, 1] = np.nan
    return _Segmentations(data)


def test_lean_get_embeddings_runs_the_trunk_once_per_batch_and_picks_the_masks_like_the_original(no_torch):
    """Cinco trechos em lotes de 2: `forward_frames` e `forward_embedding` 3 vezes (não 15, uma por slot); os pesos vão
    `(lote, falantes, quadros)`; a máscara limpa entra quando tem mais de `min_num_frames` quadros (10 do falante 0 no trecho
    0), senão a cheia (o falante 1, só sobreposto, fica com os seus 10 quadros); o slot vazio recebe pesos zero (nada de NaN);
    o NaN da segmentação vira 0; o gancho recebe `total` e `completed` por lote, como no original."""
    model = WeSpeakerResNet34()
    pipe = SpeakerDiarization(model)
    assert lean.enable(pipe, version="4.0.7") and lean.is_lean(pipe) and type(pipe).__name__ == "LeanDiarization"
    assert getattr(type(pipe), "LEAN", False) is True and not hasattr(SpeakerDiarization, "LEAN")
    assert lean.enable(pipe, version="4.0.7")                                                  # idempotente
    file = {"waveform": np.linspace(0.0, 1.0, 14 * SR, dtype=np.float32)[None, :], "sample_rate": SR}
    hooks: list[tuple[Any, ...]] = []

    def hook(step: str, artifact: Any, *, total: int | None = None, completed: int | None = None) -> None:
        hooks.append((step, None if artifact is None else artifact.shape, total, completed))

    seg = _segmentations()
    emb = pipe.get_embeddings(file, seg, exclude_overlap=True, hook=hook)
    assert emb.shape == (5, 3, 4) and emb.dtype == np.float32 and np.all(np.isfinite(emb))
    assert model.frames_calls == [(2, 1, 10 * SR), (2, 1, 10 * SR), (1, 1, 10 * SR)]
    assert model.embed_calls == [(2, 3, 20), (2, 3, 20), (1, 3, 20)]
    # min_num_frames = ceil(20 * 640 / 160000) = 1: a limpa entra com 2 quadros ou mais
    assert math.ceil(20 * 640 / (10.0 * SR)) == 1
    assert emb[0, :, 0].tolist() == [10.0, 10.0, 0.0] and emb[2, :, 0].tolist() == [20.0, 0.0, 0.0] and emb[1, 1, 0] == 0.0
    assert emb[0, 0, 1] == pytest.approx(float(file["waveform"][0, :10 * SR].mean()), abs=1e-6)       # o trecho certo entrou
    assert emb[4, 0, 1] == pytest.approx(float(file["waveform"][0, 4 * SR:].mean()) * (10 * SR - 0) / (10 * SR), abs=1e-6)
    assert hooks == [("embeddings", None, 3, 0), ("embeddings", (2, 3, 4), 3, 1), ("embeddings", (2, 3, 4), 3, 2),
                     ("embeddings", (1, 3, 4), 3, 3)]
    assert pipe.original_calls == 0
    # sem excluir a sobreposição, a máscara cheia entra sempre
    assert pipe.get_embeddings(file, seg, exclude_overlap=False)[0, :, 0].tolist() == [20.0, 10.0, 0.0]
    # em treino (o cache da otimização) e depois de `disable`, o original responde
    pipe.training = True
    assert np.all(pipe.get_embeddings(file, seg, exclude_overlap=True) == -1.0) and pipe.original_calls == 1
    pipe.training = False
    lean.disable(pipe)
    assert type(pipe).__name__ == "SpeakerDiarization" and not lean.is_lean(pipe)
    assert np.all(pipe.get_embeddings(file, seg) == -1.0) and pipe.original_calls == 2
    lean.disable(pipe)                                                                        # sem efeito fora do caminho novo
    assert lean.lean_class(SpeakerDiarization) is lean.lean_class(SpeakerDiarization)       # uma classe por base
    assert "torch" not in sys.modules


def test_lean_guards_fall_back_to_the_original_with_the_reason_in_the_log(caplog):
    """Versão fora da 4.x, outra classe de pipeline, pipeline sem os atributos, outro modelo, modelo sem `forward_frames` e
    `two_emb_layer` ligado: cada um cai no original com o motivo; o pipeline certo passa (None)."""
    good = SpeakerDiarization(WeSpeakerResNet34())
    assert lean.unsupported_reason(good, version="4.0.7") is None
    assert "3.3.2" in (lean.unsupported_reason(good, version="3.3.2") or "")
    assert "desconhecido" in (lean.unsupported_reason(good, version="") or "")

    class Other(SpeakerDiarization):
        pass

    assert "Other" in (lean.unsupported_reason(Other(WeSpeakerResNet34()), version="4.0.7") or "")
    bare = SpeakerDiarization(WeSpeakerResNet34())
    del bare._audio
    assert "_audio" in (lean.unsupported_reason(bare, version="4.0.7") or "")

    class SpeechBrainThing(WeSpeakerResNet34):
        pass

    assert "SpeechBrainThing" in (lean.unsupported_reason(SpeakerDiarization(SpeechBrainThing()), version="4.0.7") or "")
    assert "two_emb_layer" in (lean.unsupported_reason(SpeakerDiarization(WeSpeakerResNet34(two_emb_layer=True)), version="4.0.7") or "")
    legacy = type("WeSpeakerResNet34", (), {"resnet": _Resnet()})()                     # o nome certo, sem forward_frames
    assert "forward_frames" in (lean.unsupported_reason(SpeakerDiarization(legacy), version="4.0.7") or "")
    no_resnet = WeSpeakerResNet34()
    del no_resnet.resnet                                                                   # os métodos certos, sem o resnet
    assert "sem o resnet esperado" in (lean.unsupported_reason(SpeakerDiarization(no_resnet), version="4.0.7") or "")
    no_model = SpeakerDiarization(WeSpeakerResNet34())
    no_model._embedding = SimpleNamespace(model_=None)  # type: ignore[assignment]
    assert "model_" in (lean.unsupported_reason(no_model, version="4.0.7") or "")
    with caplog.at_level(logging.INFO, logger="listener"):
        assert lean.enable(SpeakerDiarization(WeSpeakerResNet34()), version="3.3.2") is False
    assert any("caminho original" in r.getMessage() and "3.3.2" in r.getMessage() for r in caplog.records)
    assert isinstance(lean.pyannote_version(), str)


def test_the_embedding_batch_counts_chunks_on_the_lean_path_and_pairs_on_the_original(forget_models):
    """`_set_embedding_batch`: 8 trechos ficam 8 com a `LeanDiarization` e viram 24 pares (x3 falantes locais) no original."""
    from listener.pipeline import pyannote as pa

    pipe = SpeakerDiarization(WeSpeakerResNet34(), batch=32)
    pipe._segmentation = SimpleNamespace(model=SimpleNamespace(specifications=SimpleNamespace(classes=["a", "b", "c"])))  # type: ignore[attr-defined]
    d = pa.PyannoteDiarizer("", embedding_batch=8)
    assert d.lean is False
    assert lean.enable(pipe, version="4.0.7")
    d._set_embedding_batch(pipe)
    assert pipe.embedding_batch_size == 8
    lean.disable(pipe)
    d._set_embedding_batch(pipe)
    assert pipe.embedding_batch_size == 24
    assert "torch" not in sys.modules


# ---------------------------------------------------------------- PF-08: a limpeza do id e da sonda Q8
def test_the_pipeline_embedding_id_is_checkpoint_slash_subfolder_never_the_alias_and_q8_stays_off(forget_models, monkeypatch, store):
    """O `embedding` do community-1 é `{"checkpoint", "subfolder"}`: o id vira `checkpoint/subfolder`, não "dict" nem o
    `EMBEDDING_REPO` (M-08: cosseno 0,88 numa voz); com isso o ramo Q8 de `_embed_window` não dispara e o embedder por turno
    recalcula; `_probe` lê os rótulos da anotação cheia (3), não da exclusiva (2)."""
    from listener.pipeline import pyannote as pa

    community = {"checkpoint": "pyannote/speaker-diarization-community-1", "subfolder": "embedding", "token": "hf_segredo"}
    resolved = pa._embedding_id(community)
    assert resolved == "pyannote/speaker-diarization-community-1/embedding" and "segredo" not in resolved
    assert resolved not in ("dict", pa.EMBEDDING_REPO) and pa._embedding_id({"checkpoint": "x"}) == "x"
    assert pa._embedding_id("pyannote/x") == "pyannote/x" and pa._embedding_id(None) is None and pa._embedding_id({}) is None
    assert pa._embedding_id(SimpleNamespace(name_or_path="m")) == "m"
    d = pa.PyannoteDiarizer("hf_x", torch_threads=2)
    pipe = SpeakerDiarization(WeSpeakerResNet34())
    pipe.embedding = community  # type: ignore[attr-defined]
    monkeypatch.setattr(d, "_pretrained", lambda repo, kind: pipe)
    supported = lean.unsupported_reason(pipe) is None                # True com o pyannote.audio 4.x instalado; False sem ele
    assert d._load() is pipe and d.embedding_model == resolved and d.lean is supported and lean.is_lean(pipe) is supported
    # a sonda Q8 pela anotação cheia; o `diarize` a recebe (o waveform falso dispensa o torch)
    full = SimpleNamespace(labels=lambda: ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
                           itertracks=lambda yield_label=True: iter([(SimpleNamespace(start=0.0, end=1.0), None, "SPEAKER_00"),
                                                                     (SimpleNamespace(start=1.0, end=2.0), None, "SPEAKER_01"),
                                                                     (SimpleNamespace(start=1.5, end=2.0), None, "SPEAKER_02")]),
                           get_overlap=lambda: [SimpleNamespace(start=1.5, end=2.0)])
    exclusive = SimpleNamespace(labels=lambda: ["SPEAKER_00", "SPEAKER_01"],
                                itertracks=lambda yield_label=True: iter([(SimpleNamespace(start=0.0, end=1.0), None, "SPEAKER_00"),
                                                                          (SimpleNamespace(start=1.0, end=2.0), None, "SPEAKER_01")]))
    vectors = np.eye(3, 4, dtype=np.float32) * 2
    result = SimpleNamespace(speaker_diarization=full, exclusive_speaker_diarization=exclusive, speaker_embeddings=vectors)
    probe = d._probe(result, full)
    assert probe is not None and set(probe) == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"} and probe["SPEAKER_02"][2] == 1.0
    assert d._probe(result, exclusive) is None                                        # a forma não bate: era o defeito

    class CallablePipe(SpeakerDiarization):
        def __call__(self, file: Any, **kwargs: Any) -> Any:
            return result

    d._pipeline = CallablePipe(WeSpeakerResNet34())
    monkeypatch.setattr(pa, "_waveform", lambda samples: samples)
    dw = d.diarize(AudioWindow(0, "R", 0, 2000, np.zeros(2 * SR, np.float32)), min_speakers=None, max_speakers=None)
    assert dw.embeddings is not None and set(dw.embeddings) == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"} and dw.model == resolved
    assert [t.speaker for t in dw.turns] == ["SPEAKER_00", "SPEAKER_01"] and dw.overlap == ((1500, 2000),)
    # o ramo Q8 do runner não dispara: o embedder (o `EMBEDDING_REPO` real) recalcula por turno
    job = SimpleNamespace(get=lambda k, d=None: {"id": 1, "meeting_id": 1}.get(k, d))
    runner = JobRunner(store, FakeDeps(), {"id": 1, "meeting_id": 1, "kind": "meeting"}, config=CFG, heartbeat_thread=False)  # type: ignore[arg-type]
    calls: list[int] = []

    class Embedder:
        model, dim = pa.EMBEDDING_REPO, 4

        def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
            calls.append(len(samples))
            return np.array([0.0, 0.0, 0.0, 1.0], np.float32)

    runner.deps._embedder_factory = Embedder  # type: ignore[assignment]
    w = AudioWindow(0, "R", 0, 4000, np.ones(4 * SR, np.float32) * 0.1)
    probed = DiarizationWindow(0, 0, 4000, (Turn(0, 2000, "SPEAKER_00"), Turn(2000, 4000, "SPEAKER_01")), (),
                               {"SPEAKER_00": vectors[0], "SPEAKER_01": vectors[1]}, resolved)
    out = runner._embed_window(w, probed)
    assert calls == [2 * SR, 2 * SR] and out.model == pa.EMBEDDING_REPO and out.embeddings is not None
    assert out.embeddings["SPEAKER_00"].tolist() == [0.0, 0.0, 0.0, 1.0]
    assert job.get("id") == 1 and "torch" not in sys.modules


# ---------------------------------------------------------------- PF-07: o gate de eco do L
def _tone(seconds: float, freq: float, amp: float = 0.3) -> np.ndarray:
    n = np.arange(int(seconds * SR))
    return (amp * np.sin(2 * math.pi * freq * n / SR)).astype(np.float32)


def _delayed(x: np.ndarray, ms: int, gain: float = 0.5) -> np.ndarray:
    out = np.zeros_like(x)
    k = ms * SR // 1000
    out[k:] = x[:-k] * gain
    return out


def _echo_meeting(store: Any, in_room: bool = False) -> int:
    """30 s: o R é um tom; o L é o R atrasado 20 ms (eco) nos blocos [0, 10) e [20, 30) e um tom próprio em [10, 20)."""
    r = _tone(30.0, 220.0)
    l = _delayed(r, 20)
    l[10 * SR:20 * SR] = _tone(10.0, 440.0)
    mid = store.create_meeting("Reunião", "meeting", None, None, is_in_room=in_room)
    for ch, x in (("R", r), ("L", l)):
        store.insert_chunk(mid, ch, 0, 0, len(x), SR, 0, encode_flac(x, SR))
    store.finish_meeting(mid, 30_000)
    return mid


class SpyTranscriber(ScriptedTranscriber):
    """Guarda uma cópia das amostras que o Whisper falso recebeu, por (canal, janela)."""

    def __init__(self, script: Any):
        super().__init__(script)
        self.samples: dict[tuple[str, int], np.ndarray] = {}

    def transcribe(self, window: AudioWindow, *, language: str | None, initial_prompt: str, on_segment: Any = None) -> Any:
        self.samples[(window.channel, window.idx)] = np.array(window.samples, copy=True)
        return super().transcribe(window, language=language, initial_prompt=initial_prompt, on_segment=on_segment)


class SpyEmbedder:
    """Guarda os trechos que o reforço do perfil do usuário embutiu (o `_embed_runs` do L)."""

    model, dim = "fake-spy-4", 4

    def __init__(self) -> None:
        self.lengths: list[int] = []

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        self.lengths.append(len(samples))
        return np.array([0.0, 0.0, 0.0, 1.0], np.float32)


def _deps() -> tuple[FakeDeps, SpyTranscriber, SpyEmbedder]:
    deps = FakeDeps(SCRIPT)
    spy, emb = SpyTranscriber(SCRIPT), SpyEmbedder()
    deps.scripted = spy
    deps._embedder_factory = lambda: emb  # type: ignore[assignment,return-value]
    return deps, spy, emb


def test_the_echo_gate_zeroes_the_echo_blocks_for_whisper_and_for_the_user_profile_and_counts_them(store, caplog):
    """Fora da sala, com o gate: o Whisper falso recebe zeros nos blocos [0, 10) e [20, 30) do L e as amostras originais em
    [10, 20); o reforço do perfil embute só a fala própria (10 s); `timings_json` do `transcribe_l` e do `embed` levam
    `blocks` 3 e `echo_blocks` 2, o evento `job_done` também, e o log diz quantos; o R vai cru; o gravado não muda."""
    store.upsert_me("Eu")
    mid = _echo_meeting(store)
    job_id = store.jobs.enqueue(mid)
    deps, spy, emb = _deps()
    with caplog.at_level(logging.INFO, logger="listener"):
        _, status = _run(store, deps, config=CFG)
    assert status == JobStatus.DONE
    l_in, r_in = spy.samples[("L", 0)], spy.samples[("R", 0)]
    l_raw, _ = decode_range(store, mid, "L", 0, 30_000)
    r_raw, _ = decode_range(store, mid, "R", 0, 30_000)
    assert len(l_in) == 30 * SR and not np.any(l_in[:10 * SR]) and not np.any(l_in[20 * SR:])
    assert np.array_equal(l_in[10 * SR:20 * SR], l_raw[10 * SR:20 * SR]) and np.any(l_raw[:SR])     # a fala própria intacta
    assert np.array_equal(r_in, r_raw) and np.any(r_in[:SR])                                        # o R cru
    assert emb.lengths == [30 * SR, 10 * SR]                              # o turno do R (30 s) e, no perfil, só a fala própria
    timings = json.loads(store.jobs.get(job_id)["timings_json"])
    assert (timings["transcribe_l"]["blocks"], timings["transcribe_l"]["echo_blocks"]) == (3, 2)
    assert (timings["embed"]["blocks"], timings["embed"]["echo_blocks"]) == (3, 2)
    assert "echo_blocks" not in timings["transcribe_r"] and timings["transcribe_l"]["speech_s"] > 0
    done = next(e for e in store.list_events(mid) if e["type"] == "job_done")
    assert done["data"]["timings"]["transcribe_l"]["echo_blocks"] == 2
    assert any("gate de eco no L: 2 de 3 blocos zerados" in r.getMessage() for r in caplog.records)
    assert store._q("SELECT COUNT(*) FROM voice_embeddings WHERE source = 'automatico'")[0][0] == 1
    assert [s["text"] for s in store.segments.list_segments(mid) if s["channel"] == "L"] == ["minha fala"]
    assert "pipeline.echo_gate" not in json.dumps(timings)                                            # só contagens


def test_with_the_gate_off_or_in_the_room_the_l_goes_raw(store):
    store.upsert_me("Eu")
    mid = _echo_meeting(store)
    job_id = store.jobs.enqueue(mid)
    deps, spy, emb = _deps()
    _, status = _run(store, deps, config=JobConfig(**{**CFG.to_dict(), "echo_gate": False}))
    l_raw, _ = decode_range(store, mid, "L", 0, 30_000)
    assert status == JobStatus.DONE and np.array_equal(spy.samples[("L", 0)], l_raw)
    assert emb.lengths == [30 * SR, 30 * SR - 320]                       # o R e o L inteiro (menos os 20 ms de atraso, mudos)
    timings = json.loads(store.jobs.get(job_id)["timings_json"])
    assert "echo_blocks" not in timings["transcribe_l"] and "echo_blocks" not in timings["embed"]
    # sala presencial: o L segue o caminho do R, sem gate
    mid2 = _echo_meeting(store, in_room=True)
    job2 = store.jobs.enqueue(mid2)
    deps2, spy2, _ = _deps()
    _, status = _run(store, deps2, job_id=job2, config=CFG)
    assert status == JobStatus.DONE and np.array_equal(spy2.samples[("L", 0)], l_raw)
    timings2 = json.loads(store.jobs.get(job2)["timings_json"])
    assert all("echo_blocks" not in v for v in timings2.values() if isinstance(v, dict))


def test_echo_gate_unit_cases_and_the_counts_survive_a_resume(store):
    """Janela sem fala: conta os blocos e sai sem decodificar; sem R gravado nada é eco e a janela volta ela mesma; toda eco
    vira zeros com `has_speech` falso; a contagem fica em `pending` até o `commit()` (R-01); `stats_from_json` relê as
    contagens do `timings_json` (a retomada soma em cima)."""
    mid = store.create_meeting("w", None, None, None)
    r = _tone(30.0, 220.0)
    store.insert_chunk(mid, "R", 0, 0, len(r), SR, 0, encode_flac(r, SR))
    store.finish_meeting(mid, 30_000)
    stats = EchoStats()
    gate = EchoGate(store, mid, corr_min=0.25, silence_rms=0.01, stats=stats)
    silent = AudioWindow(0, "L", 0, 30_000, np.zeros(30 * SR, np.float32), SR, False)
    assert gate.clean(silent) is silent and gate.pending.as_dict() == {"blocks": 3, "echo_blocks": 0} and stats.blocks == 0
    gate.commit()
    assert stats.as_dict() == {"blocks": 3, "echo_blocks": 0} and gate.pending.blocks == 0
    echo = AudioWindow(1, "L", 0, 30_000, _delayed(r, 20), SR, True)
    cleaned = gate.clean(echo)
    assert not np.any(cleaned.samples) and not cleaned.has_speech and cleaned.t1_ms == 30_000 and np.any(echo.samples)
    assert gate.pending.as_dict() == {"blocks": 3, "echo_blocks": 3} and gate.pending.seconds > 0 and stats.echo_blocks == 0
    gate.commit()
    assert stats.as_dict() == {"blocks": 6, "echo_blocks": 3} and stats.seconds > 0
    short = AudioWindow(2, "L", 0, 3_000, _delayed(r, 20)[:3 * SR], SR, True)                   # menor que um bloco: 1 bloco, nunca eco
    assert gate.clean(short) is short and gate.pending.as_dict() == {"blocks": 1, "echo_blocks": 0}
    gate.commit()
    assert (stats.blocks, stats.echo_blocks) == (7, 3)
    no_r = EchoGate(store, store.create_meeting("x", None, None, None), corr_min=0.25, silence_rms=0.01)
    assert no_r.clean(echo) is echo and no_r.pending.as_dict() == {"blocks": 3, "echo_blocks": 0} and no_r.reference(echo).size == 0
    assert stats_from_json('{"transcribe_l": {"wall_s": 1, "blocks": 3, "echo_blocks": 2}, "embed": {"wall_s": 1}}') == {
        "transcribe_l": EchoStats(3, 2)}
    assert stats_from_json("lixo") == {} and stats_from_json(None) == {}
    job = {"id": 7, "meeting_id": mid, "kind": "meeting", "timings_json": '{"transcribe_l": {"blocks": 3, "echo_blocks": 2}}'}
    runner = JobRunner(store, FakeDeps(), job, config=CFG, heartbeat_thread=False)  # type: ignore[arg-type]
    assert runner._echo_stats == {"transcribe_l": EchoStats(3, 2)}
    assert runner._timings_dict()["transcribe_l"]["echo_blocks"] == 2                              # relida e regravada


def test_the_echo_counts_enter_with_the_artifact_so_a_retry_does_not_double_them(store):
    """R-01: a janela do L cai uma vez (o Whisper falso levanta antes de o artefato existir): a contagem dela não entra no
    `timings_json` da falha; "Tentar de novo" refaz a janela e conta uma vez: `blocks` 3 no fim, não 6. O `embed`, com o artefato
    já gravado, não é refeito nem recontado."""
    store.upsert_me("Eu")
    mid = _echo_meeting(store)
    job_id = store.jobs.enqueue(mid)
    deps = FakeDeps(SCRIPT, fail_once_at={("L", 0)})
    _, status = _run(store, deps, config=CFG)
    assert status == JobStatus.ERROR and "queda simulada" in store.jobs.get(job_id)["error"]
    failed = json.loads(store.jobs.get(job_id)["timings_json"])
    assert (failed["transcribe_l"]["blocks"], failed["transcribe_l"]["echo_blocks"]) == (0, 0)      # a janela caiu antes do artefato
    assert (failed["embed"]["blocks"], failed["embed"]["echo_blocks"]) == (3, 2)
    assert store.jobs.retry(job_id)
    _, status = _run(store, deps, config=CFG)
    assert status == JobStatus.DONE
    timings = json.loads(store.jobs.get(job_id)["timings_json"])
    assert (timings["transcribe_l"]["blocks"], timings["transcribe_l"]["echo_blocks"]) == (3, 2)
    assert (timings["embed"]["blocks"], timings["embed"]["echo_blocks"]) == (3, 2)
    assert [c[:2] for c in deps.scripted.calls if c[0] == "L"] == [("L", 0), ("L", 0)]           # só a janela do L refeita


def test_job_config_and_the_snapshot_carry_the_gate_and_the_threshold(store):
    from listener.config.defaults import PIPELINE_KEYS, SPECS
    from listener.pipeline.worker import config_snapshot

    assert JobConfig().echo_gate is True and JobConfig().echo_corr_min == 0.25
    cfg = JobConfig.from_json('{"pipeline.echo_gate": "0", "capture.echo_corr_min": "0.4"}')
    assert cfg.echo_gate is False and cfg.echo_corr_min == 0.4 and cfg.to_dict()["echo_gate"] is False
    invalid = JobConfig.from_json('{"pipeline.echo_gate": "talvez", "capture.echo_corr_min": "7"}')
    assert invalid == JobConfig.from_json(None)                                                 # inválido: o padrão da tabela
    spec = SPECS["pipeline.echo_gate"]
    assert spec.advanced and spec.phase == "F5.18" and spec.default is True and "pipeline.echo_gate" in PIPELINE_KEYS
    store.set_setting("pipeline.echo_gate", False)
    store.set_setting("capture.echo_corr_min", "0.3")
    snap = json.loads(config_snapshot(store))
    assert "capture.echo_corr_min" in snap and "capture.silence_rms" in snap
    cfg2 = JobConfig.from_json(config_snapshot(store))
    assert cfg2.echo_gate is False and cfg2.echo_corr_min == 0.3
