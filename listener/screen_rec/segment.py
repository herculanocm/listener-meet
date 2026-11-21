"""F5.27: o pedaço da tela. `SegmentEncoder` recebe os quadros BGRA do BitBlt com o instante da sessão, reduz e converte numa
passada do reformatador do PyAV (o sws, em C, direto do buffer do print: nenhuma cópia em Python) e codifica num MP4 em
memória que vira um `Segment`; `decode_segment` lê de volta, para a exportação.

O codificador é o primeiro de `CODECS` que abre (CP14). No x264: `ultrafast` (a CPU de um quadro por segundo numa máquina
que já roda a legenda ao vivo) com `stillimage` (a tela é quase sempre parada) e `zerolatency`, que tira o quadro de atraso:
cada `encode` devolve o pacote do próprio quadro, e o custo medido por quadro é o real (sem ele, o último quadro só sai no
`close`). Uma thread no codificador e uma no reformatador: o processo filho roda abaixo do normal e não deve espalhar
trabalho pelos núcleos que a legenda ao vivo usa.

`CRF` e `KEYINT` vêm da medida do lote 0 (`scripts/probe_f527.py --encode`, 2026-11-20, i7-8565U: slides sintéticos de
1920 x 1080 com texto e uma tabela de letra de 13 px, um quadro por segundo, um slide novo a cada 40 s, pedaços de 60 s, 5 min):
| CRF, quadros entre chaves | slides | slides e câmera de 320 x 180 | PSNR da tabela |
| --- | --- | --- | --- |
| 20, 300 | 36 MB/h | 153 MB/h | 46,5 dB |
| 23, 300 | 29 MB/h | 134 MB/h | 45,0 dB |
| 23, 30 | 51 MB/h | 151 MB/h | 45,0 dB |
| 26, 300 | 33 MB/h | 101 MB/h | 42,3 dB |
- `CRF` = 23: o texto pequeno a 45 dB, e os slides no menor tamanho medido (o 26 sai maior nos slides: o quadro-chave pior é
  refinado pelos quadros P seguintes); com a câmera de quem apresenta, ainda abaixo dos 150 MB por hora do Aceite. O 20 passa
  do alvo com a câmera. Rolar uma planilha o tempo todo (24 px por quadro) dá ~390 MB por hora em qualquer CRF: não é o caso
  do Aceite, e o `max_side` menor é a saída.
- `KEYINT` = 300 quadros, o máximo de quadros de um pedaço (5 por segundo em 60 s): o pedaço já começa num quadro-chave e é
  decodificado inteiro e em ordem (a exportação), então um quadro-chave por pedaço basta (-44 % nos slides contra 30). O
  `ultrafast` não tem `scenecut`: a troca de slide sai como quadro P cheio de blocos intra, do tamanho de um quadro-chave.
- A redução, a conversão e a codificação de um quadro de 1920 x 1080 levaram 14 a 18 ms na mediana e 21 a 28 ms no p95.

O MP4 é fragmentado (`frag_keyframe+empty_moov`): o muxer escreve em ordem, sem voltar ao começo do buffer para o índice, e
o pedaço lido de volta não depende de nada fora dele. A base de tempo é de 1 ms: os pts são o instante do quadro menos
`t_start_ms`, e o MP4 guarda em 1/16000 s, que volta exato ao ms. A cor vai em BT.709 de faixa limitada, com a etiqueta no
fluxo, como o vídeo exportado (F5.19), e `decode_segment` lê com a mesma matriz."""
from __future__ import annotations

import functools
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

import numpy as np

from ..platform.screen_types import Frame
from .codec import pick_codec

CRF = 23                         # medido no lote 0 (a tabela acima)
KEYINT = 300                     # o máximo de quadros num pedaço (5 por segundo em 60 s): um quadro-chave por pedaço
X264 = {"preset": "ultrafast", "tune": "stillimage,zerolatency"}
OTHER_BITRATE = 300_000          # o openh264 e o mpeg4 (sem CRF): ~135 MB por hora, dentro do alvo de 150 MB
FRAGMENTED = {"movflags": "frag_keyframe+empty_moov+default_base_moof"}
BT709, MPEG_RANGE = 1, 1         # AVCOL_PRI/TRC/SPC_BT709 e AVCOL_RANGE_MPEG
MS = Fraction(1, 1000)

type FrameLike = Frame | np.ndarray


def fit_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """O tamanho do quadro gravado: a proporção mantida, o lado maior até `max_side`, nunca maior que o original e com as
    duas dimensões pares (o yuv420p pede); `ValueError` com tamanho ou limite inválido."""
    if width <= 0 or height <= 0 or max_side < 2:
        raise ValueError(f"tamanho inválido: {width}x{height}, lado máximo {max_side}")
    scale = min(1.0, max_side / max(width, height))
    return max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)


@dataclass(frozen=True, slots=True)
class Segment:
    """Um pedaço da tela: `data` é um MP4 autocontido que começa num quadro-chave, com os pts relativos a `t_start_ms` (o
    instante do primeiro quadro, em ms da sessão). Cada quadro vale do próprio instante até o seguinte; o último, até
    `t_end_ms` (exclusivo), que nunca passa de um intervalo nominal (`1000 / fps`) depois dele."""
    t_start_ms: int
    t_end_ms: int
    frames: int
    width: int
    height: int
    codec: str
    data: bytes = field(repr=False)


@functools.cache
def _codec_for(size: tuple[int, int]) -> str:
    """O codificador que abre neste tamanho, procurado uma vez por tamanho no processo (abrir o x264 custa alguns ms)."""
    import av

    return pick_codec(av, size)


def _bgra(frame: FrameLike) -> np.ndarray:
    """O quadro como uma vista HxWx4 de uint8 sobre o buffer do print (sem cópia; as linhas podem ter folga no fim)."""
    if isinstance(frame, np.ndarray):
        arr = frame
    else:
        flat = np.frombuffer(frame.data, dtype=np.uint8, count=frame.stride * frame.height)
        arr = flat.reshape(frame.height, frame.stride)[:, : frame.width * 4].reshape(frame.height, frame.width, 4)
    if arr.ndim != 3 or arr.shape[2] != 4 or arr.dtype != np.uint8:
        raise ValueError(f"o quadro não é BGRA de 8 bits: {arr.shape} {arr.dtype}")
    if arr.strides[1:] != (4, 1):                        # colunas salteadas: o sws só lê linhas contíguas por dentro
        arr = np.ascontiguousarray(arr)
    return arr


class SegmentEncoder:
    """Um pedaço em codificação: quadros de `src_width` x `src_height`, gravados em `fit_size(..., max_side)`. `add` pede os
    instantes em ordem (um instante repetido ou para trás anda 1 ms); `close` fecha o MP4 e devolve o `Segment`, ou None sem
    nenhum quadro. Não é thread-safe: um pedaço vive numa thread. O PyAV é importado aqui, ao abrir o primeiro pedaço."""

    def __init__(self, src_width: int, src_height: int, max_side: int, fps: int, *, crf: int = CRF, keyint: int = KEYINT):
        import av
        from av.video.reformatter import Colorspace, Interpolation, VideoReformatter

        if fps <= 0:
            raise ValueError(f"taxa inválida: {fps}")
        self.src_size = (src_width, src_height)
        self.size = fit_size(src_width, src_height, max_side)
        self.fps = fps
        self.frame_ms = round(1000 / fps)
        self.codec = _codec_for(self.size)
        self.frames = 0
        self.t_start_ms: int | None = None
        self._last_rel = -1
        self._av = av
        self._sws = VideoReformatter()
        self._sws_args = {"dst_colorspace": Colorspace.ITU709, "dst_color_range": MPEG_RANGE, "threads": 1,
                          "interpolation": Interpolation.AREA}     # a média da área: o texto reduzido sem serrilhado
        self._buf = io.BytesIO()
        self._out: Any = av.open(self._buf, "w", format="mp4", options=dict(FRAGMENTED))
        self._stream = self._open_stream(crf, keyint)

    def _open_stream(self, crf: int, keyint: int) -> Any:
        stream = self._out.add_stream(self.codec, rate=self.fps)
        stream.width, stream.height = self.size
        stream.pix_fmt = "yuv420p"
        stream.time_base = MS
        cc = stream.codec_context
        cc.time_base = MS
        cc.thread_count = 1
        cc.gop_size = keyint
        cc.color_primaries, cc.color_trc, cc.colorspace, cc.color_range = BT709, BT709, BT709, MPEG_RANGE
        if self.codec == "libx264":
            stream.options = {**X264, "crf": str(crf)}
        else:
            cc.bit_rate = OTHER_BITRATE
        return stream

    def add(self, frame: FrameLike, t_ms: int) -> None:
        """Reduz, converte e codifica um quadro do instante `t_ms` (ms da sessão); `ValueError` se o tamanho não é o do
        pedaço (quem grava fecha o pedaço e abre outro)."""
        arr = _bgra(frame)
        if (arr.shape[1], arr.shape[0]) != self.src_size:
            raise ValueError(f"quadro de {arr.shape[1]}x{arr.shape[0]} num pedaço de {self.src_size[0]}x{self.src_size[1]}")
        if self.t_start_ms is None:
            self.t_start_ms = t_ms
        rel = max(t_ms - self.t_start_ms, self._last_rel + 1)
        src = self._av.VideoFrame.from_numpy_buffer(arr, format="bgra")
        out = self._sws.reformat(src, self.size[0], self.size[1], "yuv420p", **self._sws_args)
        out.pts = rel
        self._mux(self._stream.encode(out))
        self._last_rel = rel
        self.frames += 1

    def close(self, t_end_ms: int | None = None) -> Segment | None:
        """Fecha o MP4. `t_end_ms` (ms da sessão) é o fim exclusivo do pedaço, preso entre 1 ms depois do último quadro e um
        intervalo nominal depois dele (o padrão). None sem nenhum quadro."""
        if self._out is None:
            raise RuntimeError("pedaço já fechado")
        self._mux(self._stream.encode(None))
        self._out.close()
        self._out = None
        if self.t_start_ms is None:
            return None
        last = self.t_start_ms + self._last_rel
        end = last + self.frame_ms if t_end_ms is None else min(max(t_end_ms, last + 1), last + self.frame_ms)
        return Segment(self.t_start_ms, end, self.frames, self.size[0], self.size[1], self.codec, self._buf.getvalue())

    def _mux(self, packets: Any) -> None:
        for packet in packets:
            self._out.mux(packet)


def decode_segment(data: bytes) -> Iterator[tuple[int, np.ndarray]]:
    """Os quadros de um `Segment.data`, em ordem: (instante relativo a `t_start_ms`, em ms; RGB24 HxWx3). O PyAV é importado
    aqui; um MP4 inválido sai como o erro do PyAV."""
    import av
    from av.video.reformatter import Colorspace

    with av.open(io.BytesIO(data), "r") as inp:
        stream = inp.streams.video[0]
        for frame in inp.decode(stream):
            if frame.pts is None:
                raise ValueError("quadro sem instante no pedaço")
            rgb = frame.to_ndarray(format="rgb24", src_colorspace=Colorspace.ITU709, src_color_range=MPEG_RANGE)
            yield round(frame.pts * frame.time_base * 1000), rgb


__all__ = ["CRF", "KEYINT", "Segment", "SegmentEncoder", "decode_segment", "fit_size"]
