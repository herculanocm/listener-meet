"""O vídeo da reunião (F5.19, itens 3, 5 e 7): MP4 com H.264 e AAC pelo PyAV, que só é importado dentro de `write_video`
(o app não o carrega ao abrir; `video_available` procura sem importar, como o .docx).

A tela só muda nas trocas das legendas: um quadro em cada troca e um a cada `REFRESH_MS` entre elas (taxa variável, com
a nominal de 1 quadro por segundo), cada tela desenhada e convertida uma vez e repetida. O áudio é o do "Salvar áudio…"
(`media_audio`: a mistura sem as pausas, com o mesmo ganho), em AAC mono na taxa gravada (reamostrado quando a taxa não
é do AAC). Áudio e quadros saem intercalados a cada bloco de 10 s (o FFmpeg só segura ~10 s para intercalar, A-06). O
codificador é o primeiro de `CODECS` que abre: só os de CPU, numa thread (A-03: o "h264" genérico do FFmpeg podia cair num
de placa de vídeo, contra a CP14). O PyAV abre o temporário pelo caminho, com `format="mp4"`: o `+faststart` reabre o
arquivo pelo nome no fim (A-06)."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import suppress
from fractions import Fraction
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..rag.export import atomic_target
from .media_audio import WRITE, AudioPlan, Tick, blocks, gain_for, measure_peak, step
from .player_source import Chunks

if TYPE_CHECKING:
    from ..store import Store
    from .media_cues import Screen

REFRESH_MS = 1000                # um quadro por segundo entre as trocas
CODECS = ("libx264", "libopenh264", "mpeg4")     # só de CPU (CP14), do melhor para o substituto do próprio FFmpeg
X264 = {"preset": "veryfast", "tune": "stillimage", "crf": "26"}
KEYINT = 30                      # quadros entre quadros-chave: com um por segundo, no máximo ~30 s (o arquivo ~20 % menor que com 10)
OTHER_BITRATE = 800_000          # o openh264 e o mpeg4: o texto nítido a 1 quadro por segundo
AAC_RATES = frozenset({8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000})
AAC_FALLBACK = 48_000
AUDIO_BITRATE = 48_000
BT709, MPEG_RANGE = 1, 1         # AVCOL_PRI/TRC/SPC_BT709 e AVCOL_RANGE_MPEG: a tela em BT.709, faixa limitada


def video_available() -> bool:
    """O PyAV instalado (vem com o extra pipeline, pelo faster-whisper), sem importá-lo."""
    return find_spec("av") is not None


def pick_codec(av: Any, size: tuple[int, int]) -> str:
    """O primeiro de `CODECS` que existe e abre com a tela (`size`) e a base de tempo do vídeo; `RuntimeError` sem nenhum."""
    for name in CODECS:
        with suppress(Exception):                # UnknownCodecError ou a abertura recusada: tenta o próximo
            cc = av.CodecContext.create(name, "w")
            cc.width, cc.height = size
            cc.pix_fmt = "yuv420p"
            cc.time_base = Fraction(1, 1000)
            cc.open()
            return name
    raise RuntimeError("o PyAV desta máquina não tem codificador de vídeo de CPU (libx264, openh264 ou MPEG-4)")


def frame_times(screens: Sequence[tuple[int, Screen]], duration_ms: int, refresh_ms: int = REFRESH_MS) -> Iterator[tuple[int, int]]:
    """(instante em ms, índice da tela) de cada quadro, em ordem: um em cada troca e um a cada `refresh_ms` até a próxima."""
    for i, (t, _screen) in enumerate(screens):
        end = screens[i + 1][0] if i + 1 < len(screens) else duration_ms
        k = t
        while k < end:
            yield k, i
            k += refresh_ms


def _video_stream(av: Any, out: Any, size: tuple[int, int]) -> Any:
    codec = pick_codec(av, size)
    stream = out.add_stream(codec, rate=Fraction(1000, REFRESH_MS))
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    stream.time_base = Fraction(1, 1000)
    cc = stream.codec_context
    cc.time_base = Fraction(1, 1000)
    cc.thread_count = 1
    cc.gop_size = KEYINT
    with suppress(AttributeError, ValueError):   # a etiqueta de cor: sem ela o player supõe, e as cores mudam um pouco
        cc.color_primaries, cc.color_trc, cc.colorspace, cc.color_range = BT709, BT709, BT709, MPEG_RANGE
    if codec == "libx264":
        stream.options = dict(X264)
    else:
        cc.bit_rate = OTHER_BITRATE
    return stream


def _audio_stream(av: Any, out: Any, rate: int) -> tuple[Any, Any]:
    """A faixa AAC mono e o reamostrador (None quando a taxa gravada já é do AAC)."""
    target = rate if rate in AAC_RATES else AAC_FALLBACK
    stream = out.add_stream("aac", rate=target)
    stream.codec_context.layout = "mono"
    stream.bit_rate = AUDIO_BITRATE
    resampler = None if target == rate else av.AudioResampler(format="fltp", layout="mono", rate=target)
    return stream, resampler


class _Muxer:
    """Os quadros e o áudio para o contêiner: cada tela convertida uma vez (a última fica guardada)."""

    def __init__(self, av: Any, out: Any, size: tuple[int, int], rate: int, render: Callable[[Screen], np.ndarray]):
        from av.video.reformatter import Colorspace

        self.av, self.out, self.rate, self.render = av, out, rate, render
        self.colorspace = Colorspace.ITU709
        self.video = _video_stream(av, out, size)
        self.audio, self.resampler = _audio_stream(av, out, rate)
        self._screen: int | None = None
        self._frame: Any = None
        self.frames = 0

    def picture(self, t_ms: int, index: int, screen: Screen) -> None:
        if index != self._screen:
            rgb = self.av.VideoFrame.from_ndarray(self.render(screen), format="rgb24")
            self._frame = rgb.reformat(format="yuv420p", dst_colorspace=self.colorspace, dst_color_range=MPEG_RANGE)
            self._screen = index
        self._frame.pts = t_ms
        self._mux(self.video.encode(self._frame))
        self.frames += 1

    def sound(self, x: np.ndarray, pos: int) -> None:
        frame = self.av.AudioFrame.from_ndarray(x[None, :], format="fltp", layout="mono")
        frame.sample_rate = self.rate
        frame.pts = pos
        for f in self.resampler.resample(frame) if self.resampler is not None else (frame,):
            self._mux(self.audio.encode(f))

    def flush(self) -> None:
        self._mux(self.video.encode(None))
        if self.resampler is not None:
            for f in self.resampler.resample(None):
                self._mux(self.audio.encode(f))
        self._mux(self.audio.encode(None))

    def _mux(self, packets: Any) -> None:
        for packet in packets:
            self.out.mux(packet)


def write_video(store: Store, plan: AudioPlan, screens: Sequence[tuple[int, Screen]], render: Callable[[Screen], np.ndarray],
                target: Path, tick: Tick, size: tuple[int, int]) -> tuple[Path, int]:
    """O MP4 em `target`: mede o pico (a primeira passada), grava áudio e quadros no temporário e troca no fim; devolve o
    caminho e os quadros gravados. `ExportCancelledError` quando o `tick` pede, `OSError` na escrita; nos dois, o
    temporário sai (o contêiner fecha antes, no `with`: o Windows não apaga arquivo aberto, A-21)."""
    import av

    chunks = Chunks(store, plan.meeting_id, plan.rate)
    gain = gain_for(measure_peak(plan, chunks, tick))
    with atomic_target(target) as tmp:
        with av.open(str(tmp), "w", format="mp4", options={"movflags": "+faststart"}) as out:
            mux = _Muxer(av, out, size, plan.rate, render)
            frames = frame_times(screens, plan.duration_ms)
            pending = next(frames, None)
            pos = 0
            for x in blocks(plan, chunks, gain):
                mux.sound(x, pos)
                pos += len(x)
                done_ms = pos * 1000 // plan.rate
                while pending is not None and pending[0] < done_ms:
                    mux.picture(pending[0], pending[1], screens[pending[1]][1])
                    pending = next(frames, None)
                step(tick, WRITE, pos / max(1, plan.frames))
            while pending is not None:              # o arredondamento do último bloco
                mux.picture(pending[0], pending[1], screens[pending[1]][1])
                pending = next(frames, None)
            mux.flush()
        step(tick, WRITE, 1.0)                       # R-01: depois do trailer e do `+faststart`, o cancelar ainda vale
    return target, mux.frames


__all__ = ["CODECS", "REFRESH_MS", "frame_times", "pick_codec", "video_available", "write_video"]
