"""F5.18 (PF-07): o gate de eco do canal L no pipeline pesado, fora da sala presencial.

Hoje o L inteiro vai ao Whisper e ao reforço automático do perfil do usuário (P3), eco incluído: no job 136, 1.569 s (23 % do
job) transcrevendo o som da reunião que vazou no microfone e atribuindo o texto ao usuário. Por janela do L, o R do mesmo
trecho vem de `store.playback.decode_range` (o FLAC do R decodificado uma segunda vez, reamostrado à taxa da janela como
`iter_windows` faz), `capture/echo.py::echo_mask` marca os blocos de `ECHO_BLOCK_S` que são eco (a regra do ao vivo:
correlação cruzada >= `capture.echo_corr_min` e menos de 0,5 s de fala própria) e `apply_echo_mask` os zera numa cópia (o VAD
do Whisper pula o silêncio; os trechos com fala do `_embed_runs` também): o áudio gravado não muda (CP3). A máscara é
calculada uma vez por janela de cada etapa (as janelas da transcrição e do reforço do perfil não coincidem). `EchoStats` conta
os blocos conferidos e os zerados da etapa, que o runner leva ao `timings_json` (`blocks`, `echo_blocks`, `merge_stats`) e ao
log (`describe`); nunca o áudio nem texto (D-059). As contagens de uma janela ficam em `pending` até o runner gravar o artefato
dela e chamar `commit()` (R-01): uma janela que cai antes do artefato (erro ou cancelamento dentro dela) não conta, e a
retomada a refaz e conta uma vez só, como os tempos. Com `pipeline.echo_gate` desligado ou na sala presencial o runner não cria
o gate e nada muda.
Medido no M-05 (07/11): eco só na reunião sem headset (71 % dos blocos, correlação mediana 0,39); nas 13 com headset, a
correlação p95 fica em 0,16 ou menos, contra o limiar de 0,25."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from ..capture.audio import resample
from ..capture.echo import apply_echo_mask, echo_mask
from ..store.playback import decode_range
from .types import AudioWindow
from .windows import frame_rms

if TYPE_CHECKING:
    from ..store import Store

ECHO_BLOCK_S = 10.0              # o bloco do veredito (o M-05 mediu em blocos de 10 s)


@dataclass(slots=True)
class EchoStats:
    """Os blocos conferidos e os zerados numa etapa, e os segundos gastos no gate (fora do `wall_s` do modelo)."""
    blocks: int = 0
    echo_blocks: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, int]:
        return {"blocks": self.blocks, "echo_blocks": self.echo_blocks}

    def add(self, other: EchoStats) -> None:
        self.blocks += other.blocks
        self.echo_blocks += other.echo_blocks
        self.seconds += other.seconds


def merge_stats(stats: dict[str, EchoStats], timings: dict[str, Any]) -> None:
    """As contagens por etapa dentro do dicionário do `timings_json` (`blocks`, `echo_blocks`), só nas etapas presentes."""
    for step, s in stats.items():
        if step in timings:
            timings[step].update(s.as_dict())


def describe(stats: EchoStats | None) -> str | None:
    """A linha do log de uma etapa que passou pelo gate (None sem blocos); os segundos são só desta rodada: a retomada relê as
    contagens do `timings_json`, não o tempo."""
    if stats is None or not stats.blocks:
        return None
    return f"gate de eco no L: {stats.echo_blocks} de {stats.blocks} blocos zerados ({stats.seconds:.1f} s no gate nesta rodada)"


def stats_from_json(timings_json: str | None) -> dict[str, EchoStats]:
    """As contagens gravadas no `timings_json` de um job (por etapa), para a retomada somar em cima delas."""
    try:
        data = json.loads(timings_json) if timings_json else {}
    except ValueError:
        data = {}
    return {str(k): EchoStats(int(v.get("blocks") or 0), int(v.get("echo_blocks") or 0))
            for k, v in data.items() if isinstance(v, dict) and "echo_blocks" in v}


class EchoGate:
    """O gate de uma etapa: `clean(janela)` devolve a janela do L com os blocos de eco zerados e conta em `pending`; o runner chama
    `commit()` depois de gravar o artefato da janela, e só então a contagem entra em `stats` (R-01)."""

    def __init__(self, store: Store, meeting_id: int, *, corr_min: float, silence_rms: float, stats: EchoStats | None = None,
                 block_s: float = ECHO_BLOCK_S):
        self.store = store
        self.meeting_id = meeting_id
        self.corr_min = corr_min
        self.silence_rms = silence_rms
        self.block_s = block_s
        self.stats = stats if stats is not None else EchoStats()
        self.pending = EchoStats()                       # a janela corrente, até o artefato dela ser gravado

    def commit(self) -> None:
        """A janela foi gravada: as contagens dela entram em `stats`. Uma janela que cai antes do artefato não conta, e a
        retomada a refaz e conta uma vez só."""
        self.stats.add(self.pending)
        self.pending = EchoStats()

    def reference(self, w: AudioWindow) -> np.ndarray:
        """O R do mesmo trecho, à taxa da janela; vazio quando o R não tem pedaços ali (então nada é eco)."""
        r, sr = decode_range(self.store, self.meeting_id, "R", w.t0_ms, w.t1_ms)
        if r.size and sr != w.sample_rate:
            r = resample(r, sr, w.sample_rate)
        return r

    def clean(self, w: AudioWindow) -> AudioWindow:
        """A janela com os blocos de eco zerados (uma cópia) e `has_speech` reavaliado (uma janela toda zerada não tem fala);
        sem eco, a própria janela. Uma janela sem fala não decodifica o R: conta os blocos e sai."""
        t0 = time.perf_counter()
        block = max(1, int(self.block_s * w.sample_rate))
        if not w.has_speech or not w.samples.size:
            self.pending.blocks += max(1, len(w.samples) // block)
            return w
        mask = echo_mask(w.samples, self.reference(w), w.sample_rate, self.block_s, self.corr_min, self.silence_rms)
        self.pending.blocks += int(mask.size)
        self.pending.echo_blocks += int(np.count_nonzero(mask))
        out = w
        if np.any(mask):
            samples = apply_echo_mask(w.samples, mask, w.sample_rate, self.block_s)
            has_speech = bool(samples.size) and bool(np.any(frame_rms(samples, w.sample_rate) > self.silence_rms))
            out = AudioWindow(w.idx, w.channel, w.t0_ms, w.t1_ms, samples, w.sample_rate, has_speech, w.overlap_ms)
        self.pending.seconds += time.perf_counter() - t0
        return out


__all__ = ["ECHO_BLOCK_S", "EchoGate", "EchoStats", "describe", "merge_stats", "stats_from_json"]
