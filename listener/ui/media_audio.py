"""O áudio da reunião para exportar (F5.19, itens 1, 2 e 7), sem Qt: o que o player toca, num arquivo comum.

O plano (`AudioPlan`) sai como o do player (`Player._meeting`): a maior taxa gravada, os canais com pedaços, o fim do
último pedaço e as pausas da gravação, que ficam fora do arquivo (D-011). Os blocos de `BLOCK_MS` saem dos pedaços
decodificados uma vez por um `Chunks` próprio da tarefa (3 por canal na memória: a reunião nunca fica inteira; nunca o do
player, cujo cache não tem trava, A-11), em amostras absolutas, com L e R somados e um ganho único: `gain_for` leva o pico
da soma a `CEILING` (-1 dBFS) só quando ela passaria disso, sem amplificar. Duas passadas: a primeira mede o pico, a
segunda grava pelo soundfile, num temporário da pasta de destino trocado no fim (`export.atomic_target`).

O progresso, o cancelamento e a espera por uma gravação em curso vão por um `Tick` (`media_job.progress_tick`): recebe a
etapa e a fração feita e devolve True para parar. O libsndfile acusa a gravação que falhou no meio (o disco cheio) por um
`assert` e a que não abre por um `LibsndfileError` com o caminho no texto: as duas viram `MediaWriteError`, um `OSError`
sem o caminho (A-01, A-02)."""
from __future__ import annotations

import errno
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..rag.export import atomic_target
from .player_source import Chunks, pause_intervals

if TYPE_CHECKING:
    from ..store import Store

BLOCK_MS = 10_000                # um bloco da mistura (10 s a 16 kHz são 640 KB em float32)
CEILING = 0.89                   # -1 dBFS: o pico da soma passa a ser este quando passaria dele
PEAK, WRITE = "peak", "write"    # as etapas do `Tick`
WRITE_FAILED = "a gravação do arquivo falhou no meio (o disco pode estar cheio)"

type Tick = Callable[[str, float], bool]        # (etapa, fração feita de 0 a 1) -> True para parar (cancelado)


class ExportCancelledError(Exception):
    """O `Tick` pediu para parar: o temporário sai e nada fica no destino."""


class MediaWriteError(OSError):
    """A gravação falhou no meio ou o arquivo não abriu para gravar; o texto não leva o caminho (o título)."""

    def __init__(self, reason: str = WRITE_FAILED):
        super().__init__(errno.EIO, reason)


def step(tick: Tick, stage: str, fraction: float) -> None:
    """Manda o progresso ao `tick`; `ExportCancelledError` quando ele pede para parar."""
    if tick(stage, fraction):
        raise ExportCancelledError


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """Um formato do "Salvar áudio…": o rótulo do filtro do diálogo, a extensão, o formato e o subtipo do libsndfile e as
    opções fixas (A-04: o MP3 em VBR de nível 0,4, o mesmo que o padrão do libsndfile 1.2.2 dá: ~41 kbps na fala de 16 kHz)."""
    key: str
    label: str
    ext: str
    sf_format: str
    subtype: str
    options: tuple[tuple[str, str | float], ...] = ()

    @property
    def dialog_filter(self) -> str:
        return f"{self.label} (*{self.ext})"


FORMATS = (AudioFormat("mp3", "MP3", ".mp3", "MP3", "MPEG_LAYER_III", (("bitrate_mode", "VARIABLE"), ("compression_level", 0.4))),
           AudioFormat("flac", "FLAC, sem perda", ".flac", "FLAC", "PCM_16"),
           AudioFormat("wav", "WAV", ".wav", "WAV", "PCM_16"))


def available_formats() -> tuple[AudioFormat, ...]:
    """Os formatos que o libsndfile desta máquina grava, na ordem de `FORMATS` (o do Linux pode não ter o MP3, CP15)."""
    import soundfile as sf

    have = sf.available_formats()
    return tuple(f for f in FORMATS if f.sf_format in have and f.subtype in sf.available_subtypes(f.sf_format))


def format_for(path: str, chosen: AudioFormat, formats: Sequence[AudioFormat]) -> tuple[str, AudioFormat]:
    """O caminho e o formato a gravar: a extensão digitada vale quando é de um formato disponível; senão, a do filtro
    escolhido entra no fim do nome."""
    ext = Path(path).suffix.lower()
    for f in formats:
        if f.ext == ext:
            return path, f
    return path + chosen.ext, chosen


@dataclass(frozen=True, slots=True)
class AudioPlan:
    """O áudio a exportar: a taxa, os canais, os trechos da reunião com som (as pausas fora, em ms da reunião), as pausas e
    o fim do último pedaço (A-05: a duração vem dele, como no player, não da `duration_ms` da reunião)."""
    meeting_id: int
    rate: int
    channels: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]
    pauses: tuple[tuple[int, int | None], ...]
    end_ms: int

    @property
    def duration_ms(self) -> int:
        """A duração do arquivo: até o fim do último pedaço, sem as pausas."""
        return sum(b - a for a, b in self.spans)

    @property
    def frames(self) -> int:
        """As amostras do arquivo (as mesmas contas de `blocks`)."""
        return sum(_sample(b, self.rate) - _sample(a, self.rate) for a, b in self.spans)


def _sample(t_ms: int, rate: int) -> int:
    return round(t_ms * rate / 1000)


def sound_spans(end_ms: int, pauses: Sequence[tuple[int, int | None]]) -> tuple[tuple[int, int], ...]:
    """[0, `end_ms`) sem as pausas da gravação; uma pausa sem retomada vai até o fim."""
    out: list[tuple[int, int]] = []
    t = 0
    for a, b in sorted(pauses, key=lambda p: p[0]):
        if a >= end_ms:
            break
        if a > t:
            out.append((t, a))
        t = end_ms if b is None else max(t, b)
    if t < end_ms:
        out.append((t, end_ms))
    return tuple(out)


def plan_audio(store: Store, meeting_id: int) -> AudioPlan | None:
    """O plano pelo que está gravado (a taxa e o fim como o player calcula); None sem pedaço nenhum."""
    stats = store.chunk_stats(meeting_id)
    channels = tuple(ch for ch in ("L", "R") if ch in stats)
    if not channels:
        return None
    rate = max(int(stats[ch]["sr"] or 16000) for ch in channels)
    end = max(int(stats[ch]["t1"] or 0) for ch in channels)
    pauses = tuple(pause_intervals(store.list_events(meeting_id)))
    return AudioPlan(int(meeting_id), rate, channels, sound_spans(end, pauses), pauses, end)


def blocks(plan: AudioPlan, chunks: Chunks, gain: float = 1.0, block_ms: int = BLOCK_MS) -> Iterator[np.ndarray]:
    """A mistura em blocos de até `block_ms`, em ordem: L e R somados, vezes `gain`; silêncio onde não há pedaço."""
    scale = np.float32(gain)
    for a, b in plan.spans:
        t = a
        while t < b:
            e = min(b, t + block_ms)
            s0, s1 = _sample(t, plan.rate), _sample(e, plan.rate)
            out = np.zeros(s1 - s0, np.float32)
            for ch in plan.channels:
                out += chunks.window(ch, s0, s1)
            if gain != 1.0:
                out *= scale
            yield out
            t = e


def gain_for(peak: float) -> float:
    """O ganho único: 1 até o teto; acima, o que leva o pico a `CEILING` (nunca amplifica)."""
    return 1.0 if peak <= CEILING else CEILING / peak


def measure_peak(plan: AudioPlan, chunks: Chunks, tick: Tick) -> float:
    """O pico da soma de L e R (a primeira passada). O primeiro `tick` vem antes de decodificar: uma gravação em curso faz
    a tarefa esperar sem gastar CPU (A-07); `ExportCancelledError` quando o `tick` pede."""
    step(tick, PEAK, 0.0)
    top = 0.0
    done = 0
    for x in blocks(plan, chunks):
        if x.size:
            top = max(top, float(np.max(np.abs(x))))
        done += len(x)
        step(tick, PEAK, done / max(1, plan.frames))
    return top


def write_audio(store: Store, plan: AudioPlan, target: Path, fmt: AudioFormat, tick: Tick) -> Path:
    """O arquivo de áudio em `target` (mono, na taxa gravada): mede o pico, grava no temporário e troca no fim.
    `ExportCancelledError` quando o `tick` pede e `OSError` na escrita (`MediaWriteError` do libsndfile); nos dois, o
    temporário sai e o arquivo anterior fica."""
    import soundfile as sf

    chunks = Chunks(store, plan.meeting_id, plan.rate)
    gain = gain_for(measure_peak(plan, chunks, tick))
    with atomic_target(target) as tmp:
        done = 0
        try:
            with sf.SoundFile(str(tmp), "w", samplerate=plan.rate, channels=1, format=fmt.sf_format, subtype=fmt.subtype,
                              **dict(fmt.options)) as f:
                for x in blocks(plan, chunks, gain):
                    f.write(x)
                    done += len(x)
                    step(tick, WRITE, done / max(1, plan.frames))
        except (sf.SoundFileError, AssertionError) as e:   # o `write` confere o gravado por `assert` (soundfile 0.14)
            raise MediaWriteError from e
        step(tick, WRITE, 1.0)                           # R-01: o cancelar no fecho ainda vale; o anterior fica
    return target


__all__ = ["BLOCK_MS", "CEILING", "FORMATS", "PEAK", "WRITE", "AudioFormat", "AudioPlan", "ExportCancelledError", "MediaWriteError",
           "Tick", "available_formats", "blocks", "format_for", "gain_for", "measure_peak", "plan_audio", "sound_spans", "step",
           "write_audio"]
