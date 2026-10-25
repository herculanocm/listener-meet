"""Processo ao vivo (F4): `python -m listener --live --db DIR --meeting ID` e `--live --prepare [--db DIR]`.

Não consome a fila (não é `--worker`): lançado pelo app ao começar a gravar (`Platform.spawn_child`), lê os quadros do
stdin (`protocol.py`) e responde em linhas JSON no stdout; morre com a gravação (EOF no stdin). Log próprio
(`listener-live.log` na pasta do log do app, como o worker), `Store` no papel WORKER, usado só pela thread principal.
O stdout do protocolo é uma cópia do descritor 1, que passa a apontar para o stderr (descartado pelo app): um `print`
perdido ou uma biblioteca nativa não corrompem as linhas.

Threads: `live-stdin` lê os quadros, reamostra para 16 kHz, roda o VAD incremental por canal e fecha janelas
(`vad.Segmenter`) numa fila única para os dois canais (sai primeiro a que fechou antes); a principal transcreve com um só
`WhisperModel` int8 (`beam_size=1`, sem timestamps, temperatura 0), filtra (`no_speech` e logprob como na F2, compressão
e alucinações comuns), grava a legenda em `captions` e só então emite a linha `caption`. Idioma: automático nas 2
primeiras janelas, depois travado no mais frequente (ou o de `meetings.language`). Degradação (máquina pura com relógio
injetado; o atraso é o da legenda, do fim da janela até o fim da transcrição): acima de 20 s por 30 s troca `small` por
`base`; persistindo mais 30 s, pula as janelas pendentes menos a mais nova de cada canal; nunca volta sozinho. EOF:
fecha as janelas em curso, transcreve a fila até o prazo do `hello`, grava `live_stopped`, emite `bye` e sai com 0.
`LISTENER_LIVE=fake` usa `tests/fake_live.py` (sem ML).

`--prepare` baixa `small` e `base` para `<pasta de modelos>/faster-whisper-<modelo>` (a mesma pasta da F2) com progresso
em linhas JSON e sai com 0 ou 1.
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import os
import re
import sys
import threading
import time
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO, Protocol

import numpy as np

from ..capture.audio import StreamResampler
from ..config import bootstrap
from ..config.defaults import LIVE_MODELS
from ..log import attach_file, get_logger, log_versions, setup
from ..model import EventType
from ..platform.common import paths
from ..store import Store
from ..store.captions import CaptionInsert
from ..store.core import MAIN_FILE
from .client import DEGRADE_MODEL, CpuMeter, is_fake, live_models_dir, whisper_model_dir
from .protocol import (BYE, CAPTION, CPU, ERROR, READY, STATE, STATS, AudioFrame, ControlFrame, FrameReader, Hello, ProtocolError,
                       encode_line)
from .vad import FRAME, SAMPLE_RATE, Segmenter, SegmenterConfig, SileroStream, Vad, WindowSpan, silero_session

log = get_logger("live")

LIVE_LOG = "listener-live.log"
LIVE_DISTS = ("faster-whisper", "ctranslate2", "onnxruntime", "torch", "pyannote.audio")
# filtros por segmento: os mesmos de `pipeline/whisper.py` (conferido em teste) mais a compressão do próprio Whisper
NO_SPEECH_MAX = 0.6
LOGPROB_MIN = -1.0
COMPRESSION_MAX = 2.4
# alucinações comuns do Whisper em silêncio e ruído (pt, en, es), comparadas sem acento, caixa nem pontuação
HALLUCINATIONS = frozenset({
    "obrigado por assistir", "obrigada por assistir", "obrigado por assistirem", "obrigado pela atencao e ate a proxima",
    "legendas pela comunidade amaraorg", "inscrevase", "inscrevase no canal", "se inscreva no canal", "deixe seu like",
    "ative o sininho", "thank you for watching", "thanks for watching", "please subscribe", "subtitles by the amaraorg community",
    "you", "gracias por ver", "gracias por ver el video", "suscribete al canal", "subtitulos realizados por la comunidad de amaraorg",
})
HALLUCINATION_MARKS = ("amaraorg",)
PROMPT_GLOSSARY_WORDS = 40
PROMPT_PREVIOUS_WORDS = 30
DEGRADE_DELAY_S = 20.0
DEGRADE_HOLD_S = 30.0
STALL_S = 2.0                    # canal com janela aberta e sem quadros enquanto o outro anda: o trecho vira silêncio
SILENCE_FILL_MS = 1500           # zeros guardados de um silêncio marcado (o bastante para fechar a janela aberta)
CPU_INTERVAL_S = 10.0
QUEUE_POLL_S = 0.2


# ---------------------------------------------------------------- motores (real aqui; fake em tests/fake_live.py)
@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    compression_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class Transcript:
    segments: tuple[Segment, ...]
    language: str | None = None
    language_prob: float = 0.0


class Transcriber(Protocol):
    name: str                    # "faster-whisper:small:int8": vai em `captions.model`
    model: str                   # "small" ou "base"

    def transcribe(self, samples: np.ndarray, *, language: str | None, prompt: str) -> Transcript: ...


class Engines(Protocol):
    name: str                    # "real" ou "fake"

    def vad(self) -> Vad: ...
    def transcriber(self, model: str, threads: int) -> Transcriber: ...


class LiveError(RuntimeError):
    """Falha que uma nova tentativa não resolve (modelo ausente, extra não instalado): o app não relança."""


class WhisperLive:
    """faster-whisper int8 por janela (F4 item 4): um só modelo carregado, `cpu_threads = live.threads`, `num_workers=1`."""

    def __init__(self, path: Path, model: str, threads: int):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise LiveError(f"instale o extra: pip install -e .[pipeline] ({e})") from e
        self.model = model
        self.name = f"faster-whisper:{model}:int8"
        self._model = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=max(1, int(threads)), num_workers=1)

    def transcribe(self, samples: np.ndarray, *, language: str | None, prompt: str) -> Transcript:
        segments, info = self._model.transcribe(
            np.ascontiguousarray(samples, dtype=np.float32), language=language, task="transcribe", beam_size=1, vad_filter=False,
            word_timestamps=False, condition_on_previous_text=False, without_timestamps=True, temperature=0.0,
            initial_prompt=prompt or None)
        segs = tuple(Segment(str(s.text), float(s.no_speech_prob), float(s.avg_logprob), float(s.compression_ratio))
                     for s in segments)                                          # o gerador só roda aqui
        return Transcript(segs, getattr(info, "language", None), float(getattr(info, "language_probability", 0.0) or 0.0))


class RealEngines:
    name = "real"

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir

    def vad(self) -> Vad:
        try:
            return SileroStream(silero_session())
        except ImportError as e:
            raise LiveError(f"instale o extra: pip install -e .[pipeline] ({e})") from e

    def transcriber(self, model: str, threads: int) -> Transcriber:
        path = whisper_model_dir(self.models_dir, model)
        if not (path / "model.bin").exists():
            raise LiveError(f"modelo {model} do ao vivo ausente em {path}: prepare nas Configurações (Ao vivo)")
        return WhisperLive(path, model, threads)


def build_engines(store: Store) -> Engines:
    if is_fake():
        try:
            from tests.fake_live import FakeEngines
        except ImportError as e:
            raise RuntimeError("LISTENER_LIVE=fake exige a pasta tests/ do repositório no caminho") from e
        return FakeEngines()
    return RealEngines(live_models_dir(store.get_setting("pipeline.models_dir")))


# ---------------------------------------------------------------- texto
def normalize_text(text: str) -> str:
    """Sem acento, sem caixa, sem pontuação, espaços simples: "Inscreva-se!" -> "inscrevase"."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", "", plain).split())


def is_hallucination(text: str) -> bool:
    norm = normalize_text(text)
    return norm in HALLUCINATIONS or any(mark in norm.replace(" ", "") for mark in HALLUCINATION_MARKS)


def caption_text(result: Transcript) -> str:
    """Texto dos segmentos que passam nos filtros; vazio quando nada passa ou quando é uma alucinação conhecida."""
    kept = [s.text.strip() for s in result.segments
            if s.no_speech_prob <= NO_SPEECH_MAX and s.avg_logprob >= LOGPROB_MIN and s.compression_ratio <= COMPRESSION_MAX]
    text = " ".join(t for t in kept if t)
    return "" if not text or is_hallucination(text) else text


def glossary_terms(*sources: str | None) -> list[str]:
    """Termos separados por vírgula ou ponto e vírgula, sem repetir (o primeiro texto tem prioridade no corte)."""
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for term in (t.strip() for t in (source or "").replace(";", ",").split(",")):
            if term and term.casefold() not in seen:
                seen.add(term.casefold())
                terms.append(term)
    return terms


def build_prompt(terms: Sequence[str], previous: str) -> str:
    """`initial_prompt` = glossário da reunião (~40 palavras) + o fim da legenda anterior do canal (~30 palavras)."""
    fixed = " ".join(", ".join(terms).split()[:PROMPT_GLOSSARY_WORDS])
    context = " ".join(previous.split()[-PROMPT_PREVIOUS_WORDS:])
    return f"{fixed} {context}".strip()


class LanguageLock:
    """Idioma automático nas primeiras janelas (2 detecções com probabilidade >= 0,5; no máximo 4 tentativas), depois
    travado no mais frequente (empate: maior soma de probabilidades); um idioma fixo da reunião vale desde o começo."""

    def __init__(self, fixed: str | None = None, windows: int = 2, min_prob: float = 0.5, max_tries: int = 4):
        self.fixed = (fixed or "").split("-")[0].lower() or None
        self.windows = windows
        self.min_prob = min_prob
        self.max_tries = max_tries
        self.locked: str | None = None
        self.tries = 0
        self.seen: list[tuple[str, float]] = []

    @property
    def language(self) -> str | None:
        return self.fixed or self.locked

    def observe(self, language: str | None, prob: float) -> None:
        if self.language is not None:
            return
        self.tries += 1
        if language and prob >= self.min_prob:
            self.seen.append((language, prob))
        if len(self.seen) >= self.windows or (self.tries >= self.max_tries and self.seen):
            counts = Counter(lang for lang, _ in self.seen)
            weight: dict[str, float] = {}
            for lang, p in self.seen:
                weight[lang] = weight.get(lang, 0.0) + p
            self.locked = max(counts, key=lambda lang: (counts[lang], weight[lang]))
            log.info("idioma do ao vivo travado em %s (%s)", self.locked, dict(counts))


class Stage(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"        # trocou para `base`
    SKIPPING = "skipping"        # pula as janelas antigas (fica a mais nova de cada canal)


@dataclass(slots=True)
class DegradeMachine:
    """Atraso acima do limiar por `hold_s` sobe um estágio (normal -> degradado -> pulando); nunca desce sozinho."""
    clock: Callable[[], float]
    threshold_s: float = DEGRADE_DELAY_S
    hold_s: float = DEGRADE_HOLD_S
    stage: Stage = Stage.NORMAL
    over_since: float | None = None

    def update(self, delay_s: float) -> Stage | None:
        """Devolve o estágio novo quando muda; None quando fica onde está."""
        now = self.clock()
        if delay_s <= self.threshold_s:
            self.over_since = None
            return None
        if self.over_since is None:
            self.over_since = now
            return None
        if self.stage == Stage.SKIPPING or now - self.over_since < self.hold_s:
            return None
        self.stage = Stage.DEGRADED if self.stage == Stage.NORMAL else Stage.SKIPPING
        self.over_since = now
        return self.stage

    def skipping(self, delay_s: float) -> bool:
        return self.stage == Stage.SKIPPING and delay_s > self.threshold_s


# ---------------------------------------------------------------- canais e fila
@dataclass(frozen=True, slots=True)
class LiveWindow:
    channel: str
    start: int                   # amostras a 16 kHz na linha do tempo da sessão
    end: int
    speech: int
    samples: np.ndarray

    @property
    def start_ms(self) -> int:
        return self.start * 1000 // SAMPLE_RATE

    @property
    def end_ms(self) -> int:
        return self.end * 1000 // SAMPLE_RATE


class ChannelStream:
    """Um canal no filho: continuidade dos quadros, reamostragem para 16 kHz, VAD incremental, janelas e o áudio delas.
    Guarda só o áudio da janela aberta (ou a pré-rolagem); silêncio marcado não carrega amostras."""

    def __init__(self, channel: str, vad: Vad, cfg: SegmenterConfig | None = None):
        self.channel = channel
        self.vad = vad
        self.cfg = cfg or SegmenterConfig()
        self.seg = Segmenter(self.cfg)
        self.pos: int | None = None              # próxima amostra a 16 kHz
        self.vad_pos = 0                         # primeira amostra ainda não classificada pelo VAD
        self.rate: int | None = None
        self.next_in: int | None = None          # próximo quadro esperado, na taxa de entrada
        self.resampler: StreamResampler | None = None
        self._chunks: deque[tuple[int, np.ndarray]] = deque()
        self.lost_frames = 0                     # quadros que não chegaram (descarte do app): viram silêncio
        self._fill = self.cfg.samples(SILENCE_FILL_MS)

    @property
    def started(self) -> bool:
        return self.pos is not None

    @property
    def window_open(self) -> bool:
        return self.seg.open_start is not None

    def feed(self, frame: AudioFrame) -> list[LiveWindow]:
        out: list[LiveWindow] = []
        if frame.rate != self.rate:
            out += self._flush_resampler()
            self.rate = frame.rate
            self.resampler = StreamResampler(frame.rate, SAMPLE_RATE) if frame.rate != SAMPLE_RATE else None
            if self.pos is not None:
                self.next_in = self._to_in(self.pos)
        start, n, skip = frame.start, frame.n, 0
        if self.pos is None:
            self.pos = self.vad_pos = self._to16(start)
            self.seg = Segmenter.starting_at(self.pos, self.cfg)
        elif self.next_in is not None:
            if start + n <= self.next_in:
                return out                                       # já coberto (silêncio de um canal parado)
            if start < self.next_in:
                skip = self.next_in - start                      # sobreposição: só a parte nova
            elif start > self.next_in:
                self.lost_frames += start - self.next_in
                out += self._silence16(self._to16(start - self.next_in))
        self.next_in = start + n
        if frame.silent:
            out += self._silence16(self._to16(n - skip))
            return out
        x = np.frombuffer(frame.pcm, dtype="<i2")[skip:].astype(np.float32) / np.float32(32768.0)
        if self.resampler is not None:
            x = self.resampler.process(x)
        out += self._audio(x)
        return out

    def stall(self, target: int) -> list[LiveWindow]:
        """O outro canal já está em `target` (16 kHz) e este parou de receber: o trecho vira silêncio (fecha a janela)."""
        if self.pos is None or target <= self.pos:
            return []
        out = self._flush_resampler()
        out += self._silence16(target - (self.pos or 0))
        if self.next_in is not None:
            self.next_in = max(self.next_in, self._to_in(target))
        return out

    def flush(self) -> list[LiveWindow]:
        """EOF: a cauda do reamostrador e a janela em curso."""
        out = self._flush_resampler()
        return out + self._extract(self.seg.flush())

    # ---- interno
    def _to16(self, frames: int) -> int:
        rate = self.rate or SAMPLE_RATE
        return frames if rate == SAMPLE_RATE else round(frames * SAMPLE_RATE / rate)

    def _to_in(self, pos16: int) -> int:
        rate = self.rate or SAMPLE_RATE
        return pos16 if rate == SAMPLE_RATE else round(pos16 * rate / SAMPLE_RATE)

    def _flush_resampler(self) -> list[LiveWindow]:
        if self.resampler is None:
            return []
        y = self.resampler.flush()
        return self._audio(y) if len(y) else []

    def _append(self, x: np.ndarray) -> None:
        if len(x):
            self._chunks.append((self.pos or 0, x))
            self.pos = (self.pos or 0) + len(x)

    def _audio(self, x: np.ndarray) -> list[LiveWindow]:
        self._append(x)
        probs = self.vad.process(x)
        spans = self.seg.push(self.vad_pos, probs)
        self.vad_pos += len(probs) * FRAME
        windows = self._extract(spans)
        self._trim()
        return windows

    def _silence16(self, n: int) -> list[LiveWindow]:
        if n <= 0 or self.pos is None:
            return []
        k = min(n, self._fill)
        self._append(np.zeros(k, np.float32))                    # só o que uma janela aberta ainda pode usar
        pos = self.pos or 0
        spans = self.seg.silence(self.vad_pos, (pos - self.vad_pos) + (n - k))
        self.pos = self.vad_pos = pos + n - k
        self.vad.reset()
        windows = self._extract(spans)
        if n > k:
            self._chunks.clear()                                 # o buffer não é mais contínuo; nada aberto precisa dele
        self._trim()
        return windows

    def _extract(self, spans: list[WindowSpan]) -> list[LiveWindow]:
        return [LiveWindow(self.channel, sp.start, sp.end, sp.speech, self._slice(sp.start, sp.end)) for sp in spans]

    def _slice(self, a: int, b: int) -> np.ndarray:
        out = np.zeros(max(0, b - a), np.float32)
        for cs, arr in self._chunks:
            lo, hi = max(a, cs), min(b, cs + len(arr))
            if lo < hi:
                out[lo - a:hi - a] = arr[lo - cs:hi - cs]
        return out

    def _trim(self) -> None:
        keep = self.seg.open_start if self.seg.open_start is not None else self.vad_pos - self.cfg.samples(self.cfg.preroll_ms) - FRAME
        while self._chunks and self._chunks[0][0] + len(self._chunks[0][1]) <= keep:
            self._chunks.popleft()


class WindowQueue:
    """Uma fila para os dois canais: sai primeiro a janela que fechou antes (menor `end`), entre threads."""

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, LiveWindow]] = []
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self.closed = False

    def __len__(self) -> int:
        with self._cond:
            return len(self._heap)

    def put(self, w: LiveWindow) -> None:
        with self._cond:
            heapq.heappush(self._heap, (w.end, next(self._seq), w))
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def take(self, timeout: float) -> LiveWindow | None:
        with self._cond:
            if not self._heap and not self.closed:
                self._cond.wait(timeout)
            return heapq.heappop(self._heap)[2] if self._heap else None

    def drain(self) -> list[LiveWindow]:
        with self._cond:
            items = [w for _, _, w in sorted(self._heap, key=lambda item: (item[0], item[1]))]
            self._heap.clear()
            return items

    def prune(self, current: LiveWindow) -> tuple[bool, int]:
        """Modo pular: fica só a janela mais nova de cada canal (contando a `current`, já tirada da fila); devolve se a
        `current` sobrevive e quantas foram descartadas."""
        with self._cond:
            newest: dict[str, LiveWindow] = {current.channel: current}
            for _, _, w in self._heap:
                if w.channel not in newest or w.end > newest[w.channel].end:
                    newest[w.channel] = w
            keep = {id(w) for w in newest.values()}
            before = len(self._heap)
            self._heap = [item for item in self._heap if id(item[2]) in keep]
            heapq.heapify(self._heap)
            survives = id(current) in keep
            return survives, before - len(self._heap) + (0 if survives else 1)


# ---------------------------------------------------------------- saída
class Emitter:
    """Linhas JSON no stdout, uma por vez entre threads; o app fechou o pipe -> para de emitir e segue gravando."""

    def __init__(self, out: BinaryIO):
        self._out = out
        self._lock = threading.Lock()
        self.closed = False

    def emit(self, msg: dict[str, Any]) -> None:
        data = encode_line(msg)
        with self._lock:
            if self.closed:
                return
            try:
                self._out.write(data)
                self._out.flush()
            except (OSError, ValueError) as e:
                self.closed = True
                log.info("o app fechou a saída do ao vivo (%s); as legendas seguem só no banco", e)


@dataclass(slots=True)
class LiveStats:
    windows: int = 0             # janelas fechadas pelo VAD
    short: int = 0               # descartadas por menos de 0,5 s de fala
    filtered: int = 0            # sem texto depois dos filtros (silêncio, ruído, alucinação)
    skipped: int = 0             # puladas (modo pular e prazo do EOF)
    captions: int = 0
    degradations: int = 0
    errors: int = 0
    db_errors: int = 0
    app: dict[str, Any] = field(default_factory=dict)        # `stats` do app (descartes do deque)

    def as_dict(self, lost_frames: int) -> dict[str, Any]:
        return {"captions": self.captions, "windows": self.windows, "short": self.short, "filtered": self.filtered,
                "skipped": self.skipped, "degradations": self.degradations, "errors": self.errors, "db_errors": self.db_errors,
                "lost_frames": lost_frames, "app_dropped": int(self.app.get("dropped", 0) or 0)}


# ---------------------------------------------------------------- sessão
class LiveSession:
    """Uma gravação do começo ao fim (`run`): hello, modelos, leitura em thread própria, transcrição na principal, bye."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, store: Store, meeting_id: int, engines: Engines, *,
                 clock: Callable[[], float] = time.perf_counter, monotonic: Callable[[], float] = time.monotonic,
                 cpu: CpuMeter | None = None, cpu_interval_s: float = CPU_INTERVAL_S, stall_s: float = STALL_S,
                 segmenter: SegmenterConfig | None = None):
        self.reader = FrameReader(stdin)
        self.out = Emitter(stdout)
        self.store = store
        self.meeting_id = meeting_id
        self.engines = engines
        self.clock = clock
        self.monotonic = monotonic
        self.cpu = cpu or CpuMeter()
        self.cpu_interval_s = cpu_interval_s
        self.stall = round(stall_s * SAMPLE_RATE)
        self.seg_cfg = segmenter or SegmenterConfig()
        self.queue = WindowQueue()
        self.stats = LiveStats()
        self.degrade = DegradeMachine(monotonic)
        self.streams: dict[str, ChannelStream] = {}
        self.previous: dict[str, str] = {}
        self.hello = Hello(0.0)
        self.transcriber: Transcriber | None = None
        self.language = LanguageLock()
        self.terms: list[str] = []
        self.eof_at: float | None = None
        self._cpu_at: float | None = None
        self._thread: threading.Thread | None = None

    # ---- ciclo
    def run(self) -> int:
        meeting = self.store.get_meeting(self.meeting_id)
        if meeting is None:
            return self._fail(f"reunião {self.meeting_id} não existe", 2)
        try:
            first = self.reader.read()
            if not isinstance(first, ControlFrame):
                return self._fail("o primeiro quadro precisa ser o hello", 2)
            self.hello = Hello.from_msg(first.msg)
        except ProtocolError as e:
            return self._fail(str(e), 2)
        model = self.hello.model if self.hello.model in LIVE_MODELS else LIVE_MODELS[0]
        self.language = LanguageLock(meeting.get("language"))
        self.terms = glossary_terms(meeting.get("glossary"), self.store.get_setting("pipeline.glossary"))
        try:
            self.streams = {ch: ChannelStream(ch, self.engines.vad(), self.seg_cfg) for ch in ("L", "R")}
        except Exception as e:
            log.exception("VAD do ao vivo não carregou")
            return self._fail(str(e), 1)
        self._thread = threading.Thread(target=self._read_loop, name="live-stdin", daemon=True)
        self._thread.start()                                       # o VAD já fecha janelas enquanto o Whisper carrega
        t0 = time.perf_counter()
        try:
            self.transcriber = self.engines.transcriber(model, self.hello.threads)
        except Exception as e:
            log.exception("modelo %s do ao vivo não carregou", model)
            return self._fail(str(e), 1)
        log.info("reunião %s: %s carregado em %.1f s (%d threads, motor %s)", self.meeting_id, self.transcriber.name,
                 time.perf_counter() - t0, self.hello.threads, self.engines.name)
        self._event(EventType.LIVE_STARTED, {"model": self.transcriber.name, "threads": self.hello.threads, "names": False,
                                             "engine": self.engines.name, "pid": os.getpid()})
        self.out.emit({"type": READY, "model": model, "names": False, "pid": os.getpid()})
        self._loop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        lost = sum(s.lost_frames for s in self.streams.values())
        summary = self.stats.as_dict(lost) | {"model": self.transcriber.name if self.transcriber else None,
                                              "language": self.language.language}
        self._event(EventType.LIVE_STOPPED, summary)
        log.info("reunião %s: ao vivo encerrado: %s", self.meeting_id, summary)
        self.out.emit({"type": BYE, **summary})
        return 0

    def _fail(self, message: str, code: int) -> int:
        log.error("reunião %s: ao vivo não começou: %s", self.meeting_id, message)
        self.out.emit({"type": ERROR, "message": message, "retry": False})
        return code

    # ---- leitura (thread `live-stdin`)
    def _read_loop(self) -> None:
        try:
            while True:
                frame = self.reader.read()
                if frame is None:
                    break
                if isinstance(frame, ControlFrame):
                    if frame.type == STATS:
                        self.stats.app = dict(frame.msg)
                    continue
                stream = self.streams[frame.channel]
                windows = stream.feed(frame)
                windows += self._stall_guard(stream)
                for w in windows:
                    self._enqueue(w)
                self._maybe_cpu()
        except ProtocolError as e:
            log.warning("reunião %s: fluxo do app corrompido (%s); encerrando como EOF", self.meeting_id, e)
        except Exception:
            log.exception("reunião %s: leitura do ao vivo falhou; encerrando como EOF", self.meeting_id)
        finally:
            try:
                for s in self.streams.values():
                    for w in s.flush():
                        self._enqueue(w)
            except Exception:
                log.exception("reunião %s: fechar as janelas no EOF falhou", self.meeting_id)
            self.stats.short = sum(s.seg.short for s in self.streams.values())
            self.eof_at = self.monotonic()
            self.queue.close()

    def _enqueue(self, w: LiveWindow) -> None:
        self.stats.windows += 1
        self.queue.put(w)

    def _stall_guard(self, fed: ChannelStream) -> list[LiveWindow]:
        out: list[LiveWindow] = []
        lead = fed.pos or 0
        for other in self.streams.values():
            if other is not fed and other.started and other.window_open and lead - (other.pos or 0) > self.stall:
                out += other.stall(lead - self.stall)
        return out

    def _maybe_cpu(self) -> None:
        now = self.monotonic()
        if self._cpu_at is None:
            self._cpu_at = now
            self.cpu.sample()
            return
        if now - self._cpu_at >= self.cpu_interval_s:
            self._cpu_at = now
            pct = self.cpu.sample()
            if pct is not None:
                self.out.emit({"type": CPU, "live_pct": pct})

    # ---- transcrição (thread principal)
    def _loop(self) -> None:
        while True:
            if self.eof_at is not None and self.monotonic() - self.eof_at > self.hello.eof_grace_s:
                left = self.queue.drain()
                if left:
                    self.stats.skipped += len(left)
                    log.warning("reunião %s: prazo do fim esgotado; %d janela(s) sem legenda", self.meeting_id, len(left))
                break
            job = self.queue.take(QUEUE_POLL_S)
            if job is None:
                if self.queue.closed and not len(self.queue):
                    break
                continue
            if self.degrade.skipping(self._age(job)):            # modo pular: só a mais nova de cada canal
                survives, dropped = self.queue.prune(job)
                self.stats.skipped += dropped
                if not survives:
                    continue
            self._transcribe(job)
            latency = self._age(job)                             # o atraso que a legenda teve (ou teria)
            stage = self.degrade.update(latency)
            if stage is not None:
                self._on_stage(stage, latency)

    def _on_stage(self, stage: Stage, delay: float) -> None:
        self.stats.degradations += 1
        current = self.transcriber
        if stage == Stage.DEGRADED and current is not None and current.model != DEGRADE_MODEL:
            try:
                self.transcriber = self.engines.transcriber(DEGRADE_MODEL, self.hello.threads)   # o `small` sai na troca
            except Exception:
                log.exception("reunião %s: não carreguei o %s da degradação; pulando janelas", self.meeting_id, DEGRADE_MODEL)
                self.degrade.stage = stage = Stage.SKIPPING
        name = self.transcriber.name if self.transcriber else ""
        log.warning("reunião %s: legenda atrasada %.0f s: %s (%s)", self.meeting_id, delay, stage, name)
        self._event(EventType.LIVE_DEGRADED, {"stage": str(stage), "delay_s": round(delay, 1), "model": name})
        self.out.emit({"type": STATE, "state": str(stage), "delay_s": round(delay, 1), "model": name})

    def _transcribe(self, job: LiveWindow) -> None:
        transcriber = self.transcriber
        if transcriber is None:
            self.stats.skipped += 1
            return
        prompt = build_prompt(self.terms, self.previous.get(job.channel, ""))
        try:
            result = transcriber.transcribe(job.samples, language=self.language.language, prompt=prompt)
        except Exception:
            self.stats.errors += 1
            log.exception("reunião %s: transcrição da janela %s %d-%d ms falhou", self.meeting_id, job.channel, job.start_ms, job.end_ms)
            return
        self.language.observe(result.language, result.language_prob)
        text = caption_text(result)
        if not text:
            self.stats.filtered += 1
            log.debug("janela %s %d-%d ms sem legenda: %s", job.channel, job.start_ms, job.end_ms,
                      [(sg.text.strip()[:60], round(sg.no_speech_prob, 2), round(sg.avg_logprob, 2), round(sg.compression_ratio, 2))
                       for sg in result.segments])
            return
        person_id, label, confidence = self._name(job)
        latency_ms = round((self.clock() - (self.hello.t0_perf + job.end / SAMPLE_RATE)) * 1000)
        row = CaptionInsert(self.meeting_id, job.channel, job.start_ms, job.end_ms, text, person_id, label, confidence,
                            transcriber.name, latency_ms)
        caption_id: int | None
        try:
            caption_id = self.store.captions.insert(row)
        except Exception as e:
            self.stats.db_errors += 1
            caption_id = None
            log.warning("reunião %s: legenda não gravada no banco: %s", self.meeting_id, e)
        self.stats.captions += 1
        self.previous[job.channel] = text
        self.out.emit({"type": CAPTION, "id": caption_id, "channel": job.channel, "t_start_ms": job.start_ms,
                       "t_end_ms": job.end_ms, "text": text, "person_id": person_id, "label": label, "confidence": confidence,
                       "latency_ms": latency_ms, "model": transcriber.name})

    def _name(self, _job: LiveWindow) -> tuple[int | None, str | None, float | None]:
        """Nome ao vivo (L3); por ora as legendas saem sem nome."""
        return None, None, None

    # ---- util
    def _age(self, job: LiveWindow) -> float:
        """Segundos entre o fim da janela (no relógio da captura) e agora."""
        return self.clock() - (self.hello.t0_perf + job.end / SAMPLE_RATE)

    def _t_ms(self) -> int:
        return max(0, round((self.clock() - self.hello.t0_perf) * 1000))

    def _event(self, type_: EventType, data: dict[str, Any]) -> None:
        try:
            self.store.add_event(self.meeting_id, self._t_ms(), type_, data)
        except Exception as e:
            self.stats.db_errors += 1
            log.warning("reunião %s: evento %s não gravado: %s", self.meeting_id, type_, e)


def serve(stdin: BinaryIO, stdout: BinaryIO, store: Store, meeting_id: int, engines: Engines | None = None, **kw: Any) -> int:
    """Uma sessão ao vivo; `engines` padrão = os reais (ou os fakes com `LISTENER_LIVE=fake`)."""
    return LiveSession(stdin, stdout, store, meeting_id, engines or build_engines(store), **kw).run()


def live_log_path(store: Store) -> Path:
    base = store.get_setting("log.path") or str(store.default_log_path())
    return Path(base).expanduser().parent / LIVE_LOG


def _emit(out: BinaryIO, msg: dict[str, Any]) -> None:
    out.write(encode_line(msg))
    out.flush()


# ---------------------------------------------------------------- --prepare
def prepare(db_dir: Path | None, out: BinaryIO) -> int:
    """Baixa os modelos do ao vivo que faltam (`small` e `base`), com uma linha JSON por passo:
    `{"type": "progress", "model", "status": "baixando" | "pronto", "fraction"}`, `{"type": "error", "model", "message"}`
    (sai com 1) e `{"type": "done", "models_dir"}` (sai com 0)."""
    setting = ""
    if db_dir is not None and (db_dir / MAIN_FILE).exists():
        try:
            with Store.open_readonly(db_dir) as store:
                setting = store.get_setting("pipeline.models_dir")
                with suppress(Exception):
                    attach_file(live_log_path(store), store.get_setting("log.level"))
        except Exception as e:
            log.warning("não li a pasta de modelos do banco em %s: %s; usando a padrão", db_dir, e)
    target = live_models_dir(setting)
    total = len(LIVE_MODELS)
    fake = is_fake()
    if not fake:
        from ..pipeline.models import disable_hf_symlinks

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        disable_hf_symlinks()
    for i, model in enumerate(LIVE_MODELS):
        dest = whisper_model_dir(target, model)
        if fake or (dest / "model.bin").exists():
            _emit(out, {"type": "progress", "model": model, "status": "pronto", "fraction": round((i + 1) / total, 3)})
            continue
        _emit(out, {"type": "progress", "model": model, "status": "baixando", "fraction": round(i / total, 3)})
        try:
            from faster_whisper import download_model

            dest.parent.mkdir(parents=True, exist_ok=True)
            download_model(model, output_dir=str(dest))
        except Exception as e:
            from ..pipeline.models import classify_error

            err = classify_error(e, f"faster-whisper {model}")
            log.warning("download do modelo %s falhou: %s", model, err or e)
            _emit(out, {"type": "error", "model": model, "message": str(err or e)})
            return 1
        _emit(out, {"type": "progress", "model": model, "status": "pronto", "fraction": round((i + 1) / total, 3)})
    _emit(out, {"type": "done", "models_dir": str(target)})
    return 0


# ---------------------------------------------------------------- entrada
def _protocol_stdout() -> BinaryIO:
    """Cópia do descritor 1 para o protocolo; o 1 passa a apontar para o stderr (descartado pelo app)."""
    with suppress(Exception):
        sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(fd, "wb")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --live", description="processo ao vivo (F4): legendas durante a gravação")
    ap.add_argument("--db", help="pasta do banco (padrão: o ponteiro da configuração do sistema)")
    ap.add_argument("--meeting", type=int, help="reunião em gravação")
    ap.add_argument("--prepare", action="store_true", help="baixa os modelos do ao vivo (small e base) e sai")
    args = ap.parse_args(argv)
    db_dir = Path(args.db) if args.db else bootstrap.read_db_dir(paths.config_dir())
    setup("INFO")
    if args.prepare:
        return prepare(db_dir, sys.stdout.buffer)
    if db_dir is None or args.meeting is None:
        print("live: informe --db DIR e --meeting ID", file=sys.stderr)  # noqa: T201
        return 2
    out = _protocol_stdout()
    try:
        store = Store.open_worker(db_dir)
    except Exception as e:
        print(f"live: não abri o banco em {db_dir}: {e}", file=sys.stderr)  # noqa: T201
        with suppress(Exception):
            _emit(out, {"type": ERROR, "message": f"banco indisponível: {e}", "retry": False})
        return 3
    with store:
        with suppress(Exception):
            attach_file(live_log_path(store), store.get_setting("log.level"))
        versions = {}
        for dist in LIVE_DISTS:
            with suppress(metadata.PackageNotFoundError):
                versions[dist] = metadata.version(dist)
        log_versions({"papel": "live", "pid": str(os.getpid()), "banco": str(store.base_dir), "reunião": str(args.meeting),
                      **({"modo": "fake"} if is_fake() else {}), **versions})
        try:
            engines = build_engines(store)
        except Exception as e:
            log.exception("motores do ao vivo indisponíveis")
            with suppress(Exception):
                _emit(out, {"type": ERROR, "message": str(e), "retry": False})
            return 1
        return serve(sys.stdin.buffer, out, store, int(args.meeting), engines)
