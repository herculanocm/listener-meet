"""Protocolo entre o app e o processo ao vivo (F4), puro: só `struct` e `json`.

Ida (stdin do filho): quadros binários little-endian com cabeçalho fixo de 22 bytes
  magic b"LV" · tipo u8 (1 = áudio, 2 = controle) · canal u8 (0 = L, 1 = R) · flags u8 · reservado u8 ·
  quadro inicial i64 · taxa u32 · n u32
- Áudio: `n` amostras int16 LE mono depois do cabeçalho; o quadro inicial é a posição do bloco na linha do tempo da
  sessão (a mesma do `t_start_ms` gravado), na taxa do bloco. Com `SILENCED` (pausa, ou mudo no canal L) ou `GAP` (buraco
  preenchido pela captura) não há amostras: `n` é a duração em quadros e o filho a trata como silêncio.
- Controle: `n` bytes de JSON UTF-8 depois do cabeçalho: `hello` primeiro (`t0_perf` da sessão e as opções da
  gravação) e `stats` antes do EOF (descartes do lado do app). EOF no stdin = fim da gravação.

Volta (stdout do filho): uma linha JSON UTF-8 por mensagem, com `type` em `ready`, `caption`, `state`, `cpu`, `error`,
`bye` (o JSON escapa quebras de linha, então a linha é a fronteira).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any, BinaryIO, Self

PROTOCOL_VERSION = 1
MAGIC = b"LV"
HEADER = struct.Struct("<2sBBBxqII")
HEADER_SIZE = HEADER.size
KIND_AUDIO = 1
KIND_CONTROL = 2
SILENCED = 1                      # pausa ou mudo: marcado, não zeros como fala (mesmo valor de `capture.chunker.TapFlag`)
GAP = 2                           # buraco preenchido pela captura (sem pacote do dispositivo)
CHANNELS = ("L", "R")
MAX_AUDIO_SAMPLES = 1 << 24       # ~17 min a 16 kHz num quadro: acima disso é corrupção, não áudio
MAX_CONTROL_BYTES = 1 << 20

# tipos das mensagens de volta (linhas JSON do filho) e de controle (ida)
READY, CAPTION, STATE, CPU, ERROR, BYE = "ready", "caption", "state", "cpu", "error", "bye"
HELLO, STATS = "hello", "stats"


class ProtocolError(ValueError):
    """Fluxo corrompido ou truncado no meio de um quadro."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    channel: str
    start: int                     # quadro inicial na linha do tempo da sessão, na taxa `rate`
    rate: int
    n: int                         # quadros (amostras mono)
    flags: int = 0
    pcm: bytes = b""               # int16 LE; vazio quando silencioso

    @property
    def silent(self) -> bool:
        return bool(self.flags & (SILENCED | GAP))

    @property
    def end(self) -> int:
        return self.start + self.n


@dataclass(frozen=True, slots=True)
class ControlFrame:
    msg: dict[str, Any]

    @property
    def type(self) -> str:
        return str(self.msg.get("type") or "")


type Frame = AudioFrame | ControlFrame


@dataclass(frozen=True, slots=True)
class Hello:
    """Primeiro quadro: relógio da sessão (`perf_counter` do início, comum aos processos no Windows e no Linux) e as
    opções que valem para a gravação inteira."""
    t0_perf: float
    names: bool = True
    model: str = "small"
    threads: int = 2
    eof_grace_s: float = 8.0       # depois do EOF, o filho transcreve o que está na fila até este prazo
    partial: bool = False          # F5.6 (A-23): o resumo parcial automático desta gravação; o filho o grava no `live_started`

    def to_msg(self) -> dict[str, Any]:
        return {"type": HELLO, "v": PROTOCOL_VERSION, "t0_perf": float(self.t0_perf), "names": bool(self.names),
                "model": str(self.model), "threads": int(self.threads), "eof_grace_s": float(self.eof_grace_s),
                "partial": bool(self.partial)}

    @classmethod
    def from_msg(cls, msg: dict[str, Any]) -> Self:
        if msg.get("type") != HELLO:
            raise ProtocolError(f"esperava hello, veio {msg.get('type')!r}")
        if msg.get("v") != PROTOCOL_VERSION:                # app e filho de versões diferentes (código trocado com o app aberto)
            raise ProtocolError(f"protocolo {msg.get('v')!r} do app, o processo ao vivo fala o {PROTOCOL_VERSION}: reinicie o app")
        try:
            return cls(t0_perf=float(msg["t0_perf"]), names=bool(msg.get("names", True)), model=str(msg.get("model") or "small"),
                       threads=max(1, int(msg.get("threads", 2))), eof_grace_s=max(0.0, float(msg.get("eof_grace_s", 8.0))),
                       partial=bool(msg.get("partial", False)))
        except (KeyError, TypeError, ValueError) as e:
            raise ProtocolError(f"hello inválido: {e}") from e


def encode_audio(channel: str, start: int, rate: int, n: int, flags: int = 0, pcm: bytes = b"") -> bytes:
    """Quadro de áudio; silencioso (`SILENCED`/`GAP`) vai sem amostras."""
    silent = bool(flags & (SILENCED | GAP))
    if silent and pcm:
        raise ValueError("quadro silencioso não leva amostras")
    if not silent and len(pcm) != 2 * n:
        raise ValueError(f"esperava {2 * n} bytes de int16, vieram {len(pcm)}")
    return HEADER.pack(MAGIC, KIND_AUDIO, CHANNELS.index(channel), int(flags), int(start), int(rate), int(n)) + pcm


def encode_control(msg: dict[str, Any]) -> bytes:
    payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return HEADER.pack(MAGIC, KIND_CONTROL, 0, 0, 0, 0, len(payload)) + payload


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    """Lê exatamente `n` bytes (o pipe entrega aos pedaços); menos que isso só no EOF."""
    if n == 0:
        return b""
    first = stream.read(n)
    if not first or len(first) == n:
        return first or b""
    parts = [first]
    got = len(first)
    while got < n:
        part = stream.read(n - got)
        if not part:
            break
        parts.append(part)
        got += len(part)
    return b"".join(parts)


class FrameReader:
    """Lê quadros de um fluxo binário; `read()` devolve None no EOF limpo (fronteira de quadro)."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def read(self) -> Frame | None:
        head = _read_exact(self.stream, HEADER_SIZE)
        if not head:
            return None
        if len(head) < HEADER_SIZE:
            raise ProtocolError("cabeçalho truncado")
        magic, kind, channel, flags, start, rate, n = HEADER.unpack(head)
        if magic != MAGIC:
            raise ProtocolError(f"magic inválido: {magic!r}")
        if kind == KIND_CONTROL:
            if n > MAX_CONTROL_BYTES:
                raise ProtocolError(f"controle grande demais: {n} bytes")
            payload = _read_exact(self.stream, n)
            if len(payload) < n:
                raise ProtocolError("controle truncado")
            try:
                msg = json.loads(payload.decode("utf-8"))
            except ValueError as e:
                raise ProtocolError(f"controle ilegível: {e}") from e
            if not isinstance(msg, dict):
                raise ProtocolError("controle não é um objeto JSON")
            return ControlFrame(msg)
        if kind != KIND_AUDIO:
            raise ProtocolError(f"tipo de quadro desconhecido: {kind}")
        if channel >= len(CHANNELS) or rate <= 0:
            raise ProtocolError(f"quadro de áudio inválido (canal {channel}, taxa {rate})")
        if flags & (SILENCED | GAP):
            return AudioFrame(CHANNELS[channel], start, rate, n, flags)
        if n > MAX_AUDIO_SAMPLES:
            raise ProtocolError(f"áudio grande demais: {n} amostras")
        pcm = _read_exact(self.stream, 2 * n)
        if len(pcm) < 2 * n:
            raise ProtocolError("áudio truncado")
        return AudioFrame(CHANNELS[channel], start, rate, n, flags, pcm)


def encode_line(msg: dict[str, Any]) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def decode_line(line: bytes) -> dict[str, Any]:
    """Uma linha de volta; `ValueError` (ou `ProtocolError`, que é um) quando não é um objeto JSON com `type`."""
    msg = json.loads(line.decode("utf-8"))
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("linha sem objeto JSON com 'type'")
    return msg
