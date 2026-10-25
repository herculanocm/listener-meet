"""Tipos do pipeline pós-reunião (F2 item 1): dataclasses `frozen, slots` com tuplas e `np.ndarray` float32 (nunca listas
de dicts); `JobConfig` é o snapshot das `pipeline.*` lido do `config_json` do job; (de)serialização dos artefatos por
janela (`job_artifacts`): JSON comprimido com zlib mais bytes float32 para embeddings (item 10)."""
from __future__ import annotations

import json
import struct
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Self

import numpy as np

from ..config.defaults import SPECS
from ..model import ClusterStatus

SAMPLE_RATE = 16000
type Overlaps = tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class Word:
    start_ms: int
    end_ms: int
    text: str
    prob: float = 1.0

    @property
    def center_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2


@dataclass(frozen=True, slots=True)
class Turn:
    start_ms: int
    end_ms: int
    speaker: str                  # rótulo local da janela (SPEAKER_00) antes da costura; global (SPK_0) depois

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class AudioWindow:
    idx: int
    channel: str
    t0_ms: int                    # linha do tempo da sessão
    t1_ms: int
    samples: np.ndarray           # float32 mono a 16 kHz
    sample_rate: int = SAMPLE_RATE
    has_speech: bool = True
    overlap_ms: int = 0           # sobreposição com a janela anterior (0 quando o corte caiu num silêncio)

    @property
    def duration_ms(self) -> int:
        return self.t1_ms - self.t0_ms


@dataclass(frozen=True, slots=True)
class TranscriptWindow:
    idx: int
    t0_ms: int
    t1_ms: int
    words: tuple[Word, ...]
    language: str | None = None
    language_prob: float = 0.0
    speech_ms: int = 0
    overlap_ms: int = 0


@dataclass(frozen=True, slots=True)
class DiarizationWindow:
    idx: int
    t0_ms: int
    t1_ms: int
    turns: tuple[Turn, ...]                       # saída exclusiva (um falante por instante)
    overlap: Overlaps = ()                        # trechos com fala sobreposta (ms)
    embeddings: dict[str, np.ndarray] | None = None   # rótulo -> centroide (Q8 ou calculado pelo embedder)
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ClusterData:
    label: str
    speech_ms: int
    centroid: np.ndarray | None
    n_turns: int = 0


@dataclass(frozen=True, slots=True)
class MatchResult:
    label: str
    status: ClusterStatus
    person_id: int | None = None          # sugestão (auto, ambíguo) ou None
    profile_id: int | None = None
    similarity: float | None = None
    margin: float | None = None
    threshold: float | None = None        # limiar aplicado (do perfil ou global)


@dataclass(frozen=True, slots=True)
class JobConfig:
    """Snapshot das `pipeline.*` (mais o limiar de silêncio da captura) que vale para o job inteiro (CP8)."""
    transcriber: str = "whisper"
    whisper_model: str = "large-v3-turbo"
    whisper_hq: bool = False
    glossary: str = ""
    threshold: float = 0.60
    margin: float = 0.05
    models_dir: str = ""
    torch_threads: int = 4
    diar_window_s: int = 3600
    whisper_window_s: int = 600
    pause_on_record: bool = True
    silence_rms: float = 0.004
    diar_overlap_s: int = 30
    whisper_overlap_s: int = 2
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, raw: dict[str, str]) -> Self:
        """Lê o texto das chaves (do banco ou do `config_json`), validando pela tabela `SPECS`; inválido cai no padrão."""
        def get(key: str) -> Any:
            spec = SPECS[key]
            try:
                return spec.value(raw.get(key, spec.default_text))
            except (ValueError, TypeError):
                return spec.default
        return cls(transcriber=get("pipeline.transcriber"), whisper_model=get("pipeline.whisper_model"),
                   whisper_hq=get("pipeline.whisper_hq"), glossary=get("pipeline.glossary"), threshold=get("pipeline.threshold"),
                   margin=get("pipeline.margin"), models_dir=get("pipeline.models_dir"), torch_threads=get("pipeline.torch_threads"),
                   diar_window_s=get("pipeline.diar_window_s"), whisper_window_s=get("pipeline.whisper_window_s"),
                   pause_on_record=get("pipeline.pause_on_record"), silence_rms=get("capture.silence_rms"))

    @classmethod
    def from_json(cls, text: str | None) -> Self:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        return cls.from_settings({k: str(v) for k, v in data.items()})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- artefatos (item 10)
_MAGIC = b"LMA1"


def pack_artifact(header: dict[str, Any], blob: bytes = b"") -> bytes:
    """`LMA1` + tamanho do cabeçalho + JSON (zlib) + bytes crus (embeddings float32)."""
    head = zlib.compress(json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    return _MAGIC + struct.pack("<I", len(head)) + head + blob


def unpack_artifact(data: bytes) -> tuple[dict[str, Any], bytes]:
    if data[:4] != _MAGIC:
        raise ValueError("artefato em formato desconhecido")
    n = struct.unpack("<I", data[4:8])[0]
    header = json.loads(zlib.decompress(data[8:8 + n]).decode("utf-8"))
    return header, data[8 + n:]


def encode_transcript(t: TranscriptWindow) -> bytes:
    words = [(w.start_ms, w.end_ms, w.text, round(w.prob, 4)) for w in t.words]
    return pack_artifact({"idx": t.idx, "t0": t.t0_ms, "t1": t.t1_ms, "lang": t.language, "lang_p": t.language_prob,
                          "speech": t.speech_ms, "ov": t.overlap_ms, "words": words})


def decode_transcript(data: bytes) -> TranscriptWindow:
    h, _ = unpack_artifact(data)
    words = tuple(Word(int(a), int(b), str(c), float(d)) for a, b, c, d in h["words"])
    return TranscriptWindow(int(h["idx"]), int(h["t0"]), int(h["t1"]), words, h.get("lang"), float(h.get("lang_p") or 0.0),
                            int(h.get("speech") or 0), int(h.get("ov") or 0))


def encode_diarization(d: DiarizationWindow) -> bytes:
    labels = sorted(d.embeddings) if d.embeddings else []
    blob = b"".join(np.ascontiguousarray(d.embeddings[k], dtype="<f4").tobytes() for k in labels) if d.embeddings else b""
    dim = int(next(iter(d.embeddings.values())).size) if d.embeddings else 0
    return pack_artifact({"idx": d.idx, "t0": d.t0_ms, "t1": d.t1_ms, "turns": [(t.start_ms, t.end_ms, t.speaker) for t in d.turns],
                          "overlap": list(d.overlap), "labels": labels, "dim": dim, "model": d.model}, blob)


def decode_diarization(data: bytes) -> DiarizationWindow:
    h, blob = unpack_artifact(data)
    emb: dict[str, np.ndarray] | None = None
    if h.get("labels"):
        dim = int(h["dim"])
        arr = np.frombuffer(blob, dtype="<f4").astype(np.float32).reshape(len(h["labels"]), dim)
        emb = {str(k): arr[i].copy() for i, k in enumerate(h["labels"])}
    return DiarizationWindow(int(h["idx"]), int(h["t0"]), int(h["t1"]), tuple(Turn(int(a), int(b), str(c)) for a, b, c in h["turns"]),
                             tuple((int(a), int(b)) for a, b in h.get("overlap", [])), emb, h.get("model"))
