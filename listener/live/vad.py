"""VAD incremental e janelas do ao vivo (F4 item 3), no processo filho.

`SileroStream`: o Silero v6 do faster-whisper (ONNX) chamado direto na sessão, com o estado `h`, `c` e as 64 amostras
de contexto carregados entre chamadas (o `__call__` do faster-whisper zera tudo a cada chamada e ainda escreve zeros na
entrada): probabilidades idênticas às da chamada única [fato, 25/10], ~0,5 % de um núcleo. Só importa o faster-whisper
quando a sessão real é pedida (`silero_session`).

`Segmenter` (puro): probabilidades por quadro de 512 amostras a 16 kHz viram janelas. Como o Whisper custa por janela e
não por segundo [fato, 25/10], a janela fecha com 500 ms de pausa depois de 4 s de fala, com 1,2 s de silêncio, ou em
10 s; menos de 0,5 s de fala é descartado. Histerese 0,5 para entrar em fala e 0,35 para sair (a do VAD do
faster-whisper); a janela começa 200 ms antes da fala (nunca antes do fim da anterior) e termina até 300 ms depois dela.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME = 512                       # quadro do Silero a 16 kHz (32 ms)
CONTEXT = 64                      # amostras de contexto que o Silero v6 lê antes de cada quadro
STATE_SHAPE = (1, 1, 128)


class Vad(Protocol):
    """Um por canal: `process` devolve a probabilidade de fala de cada quadro completo e guarda o resto."""
    @property
    def pending(self) -> int: ...
    def process(self, x: np.ndarray) -> np.ndarray: ...
    def reset(self) -> None: ...


def silero_session() -> Any:
    """Sessão ONNX do Silero v6 que vem com o faster-whisper (cacheada por ele: uma para os dois canais)."""
    from faster_whisper.vad import get_vad_model

    return get_vad_model().session


class SileroStream:
    """Silero incremental: entradas `input` [B, 576] (64 de contexto + 512), `h` e `c` [1, 1, 128]."""

    def __init__(self, session: Any):
        self.session = session
        self._h = np.zeros(STATE_SHAPE, np.float32)
        self._c = np.zeros(STATE_SHAPE, np.float32)
        self._ctx = np.zeros(CONTEXT, np.float32)
        self._rest = np.zeros(0, np.float32)

    @property
    def pending(self) -> int:
        return len(self._rest)

    def reset(self) -> None:
        """Depois de um silêncio marcado (pausa, mudo, buraco): começa do zero, como uma chamada nova."""
        self._h = np.zeros(STATE_SHAPE, np.float32)
        self._c = np.zeros(STATE_SHAPE, np.float32)
        self._ctx = np.zeros(CONTEXT, np.float32)
        self._rest = np.zeros(0, np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if len(self._rest):
            x = np.concatenate([self._rest, x])
        n = (len(x) // FRAME) * FRAME
        self._rest = x[n:].copy()
        if n == 0:
            return np.zeros(0, np.float32)
        b = x[:n].reshape(-1, FRAME)
        contexts = np.concatenate([self._ctx[None, :], b[:-1, -CONTEXT:]], 0)
        batch = np.ascontiguousarray(np.concatenate([contexts, b], 1), dtype=np.float32)
        probs, self._h, self._c = self.session.run(None, {"input": batch, "h": self._h, "c": self._c})
        self._ctx = b[-1, -CONTEXT:].copy()
        return np.asarray(probs, dtype=np.float32).reshape(-1)


@dataclass(frozen=True, slots=True)
class WindowSpan:
    """Janela fechada, em amostras a 16 kHz na linha do tempo da sessão: `[start, end)`; `speech` = amostras com fala."""
    start: int
    end: int
    speech: int

    @property
    def start_ms(self) -> int:
        return self.start * 1000 // SAMPLE_RATE

    @property
    def end_ms(self) -> int:
        return self.end * 1000 // SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    threshold: float = 0.5
    neg_threshold: float = 0.35
    long_speech_ms: int = 4000      # depois de tanta fala...
    long_pause_ms: int = 500        # ...esta pausa já fecha
    silence_ms: int = 1200          # silêncio que fecha qualquer janela
    max_ms: int = 10_000            # corte duro
    min_speech_ms: int = 500        # abaixo disso a janela é descartada
    preroll_ms: int = 200
    pad_ms: int = 300
    rate: int = SAMPLE_RATE
    frame: int = FRAME

    def samples(self, ms: int) -> int:
        return ms * self.rate // 1000


@dataclass(slots=True)
class Segmenter:
    """Probabilidades (ou silêncio marcado) -> janelas. Não guarda áudio: quem chama recorta `[start, end)` do seu buffer."""
    cfg: SegmenterConfig = field(default_factory=SegmenterConfig)
    cursor: int = 0                 # próxima amostra esperada
    floor: int = 0                  # nenhuma janela começa antes daqui (início do canal ou fim da anterior)
    open_start: int | None = None   # início da janela aberta
    speech: int = 0
    last_speech_end: int = 0
    silence_run: int = 0
    in_speech: bool = False
    short: int = 0                  # janelas descartadas por pouca fala
    cuts: int = 0                   # janelas fechadas no corte duro

    @classmethod
    def starting_at(cls, start: int, cfg: SegmenterConfig | None = None) -> Segmenter:
        return cls(cfg or SegmenterConfig(), cursor=start, floor=start)

    def push(self, start: int, probs: Sequence[float] | np.ndarray) -> list[WindowSpan]:
        """Quadros consecutivos de `cfg.frame` amostras a partir de `start` (um salto à frente conta como silêncio)."""
        out: list[WindowSpan] = []
        if start > self.cursor:
            out += self.silence(self.cursor, start - self.cursor)
        f = self.cfg.frame
        for i, p in enumerate(np.asarray(probs, dtype=np.float32).reshape(-1)):
            s = start + i * f
            self.cursor = s + f
            speech = float(p) >= (self.cfg.neg_threshold if self.in_speech else self.cfg.threshold)
            self.in_speech = speech
            if self.open_start is None:
                if not speech:
                    continue
                self.open_start = max(s - self.cfg.samples(self.cfg.preroll_ms), self.floor)
                self.speech, self.silence_run = 0, 0
            if speech:
                self.speech += f
                self.silence_run = 0
                self.last_speech_end = s + f
            else:
                self.silence_run += f
            self._check(out)
        return out

    def silence(self, start: int, n: int) -> list[WindowSpan]:
        """`n` amostras sem fala a partir de `start` (pausa, mudo, buraco ou canal parado), em O(1)."""
        out: list[WindowSpan] = []
        if start > self.cursor:                       # salto à frente: o buraco também é silêncio
            n += start - self.cursor
        elif start < self.cursor:                     # sobreposição: só a parte nova conta
            n -= self.cursor - start
        if n <= 0:
            return out
        self.cursor += n
        self.in_speech = False
        if self.open_start is not None:
            self.silence_run += n
            self._check(out)
        return out

    def flush(self) -> list[WindowSpan]:
        """Fim do áudio (EOF): fecha a janela em curso; fala até o fim fica inteira."""
        out: list[WindowSpan] = []
        if self.open_start is not None:
            end = self.cursor if self.in_speech else min(self.cursor, self.last_speech_end + self.cfg.samples(self.cfg.pad_ms))
            self._close(end, out)
        self.in_speech = False
        return out

    def _check(self, out: list[WindowSpan]) -> None:
        if self.open_start is None:
            return
        c = self.cfg
        limit = self.open_start + c.samples(c.max_ms)
        pause_end = min(limit, self.last_speech_end + min(self.silence_run, c.samples(c.pad_ms)))
        if self.silence_run >= c.samples(c.silence_ms) or (self.speech >= c.samples(c.long_speech_ms)
                                                             and self.silence_run >= c.samples(c.long_pause_ms)):
            self._close(pause_end, out)
        elif self.cursor >= limit:
            self.cuts += 1
            self._close(limit, out)

    def _close(self, end: int, out: list[WindowSpan]) -> None:
        start = self.open_start
        if start is None:
            return
        end = max(end, start)
        self.open_start = None
        self.floor = end
        speech = min(self.speech, end - start)
        self.speech, self.silence_run = 0, 0
        if self.cursor > end and self.in_speech:
            # corte duro no meio da fala: a próxima janela continua daqui (sem pré-rolagem, que repetiria palavras)
            self.open_start = end
            self.speech = self.cursor - end
            self.last_speech_end = self.cursor
        if speech < self.cfg.samples(self.cfg.min_speech_ms):
            self.short += 1
            return
        out.append(WindowSpan(start, end, speech))
