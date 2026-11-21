"""F5.27 (item 11): o vídeo da reunião com a tela gravada, 1920 x 1080. Em cima, numa área de 1920 x `AREA_H`, a tela
encaixada sem distorcer, centrada, com tarja na cor do fundo; embaixo, a faixa da legenda da F5.19 (`media_frames.BAND_LOOK`:
o nome em negrito, o cargo, o ponto na cor do falante e a anterior apagada acima). Sem tela (antes do primeiro pedaço, nos
buracos, depois do último), a tela escura da F5.19 inteira, nas medidas dela vezes 1,5. Os tempos vêm de `media_screen`.

A composição é feita em yuv420p, num quadro só guardado (`Canvas`): a faixa é desenhada e convertida uma vez por troca de
legenda, a tela é reduzida e convertida pelo reformatador do PyAV (o sws, em C, numa passada, com a média da área, que não
serrilha o texto) uma vez por quadro gravado que mudou, e cada uma vai para a sua parte do quadro; o vídeo repete o quadro
pronto enquanto nada muda. O PyAV chega pronto de `write_pictures` (só importado lá dentro). Montado na thread da interface
(as fontes do desenhista), usado só pela tarefa."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import numpy as np

from ..log import get_logger
from .media_frames import BAND_LOOK, FrameRenderer, Look, scaled
from .media_screen import FileClock, ScreenState, moments, shots
from .media_video import write_pictures

if TYPE_CHECKING:
    from pathlib import Path

    from ..store import Store
    from .media_audio import AudioPlan, Tick
    from .media_cues import Cue, Screen, TextSample
    from .media_screen import Decode, Read
    from .media_video import _Muxer

log = get_logger("ui.media")

SIZE = (1920, 1080)
BAND_H = 270                     # a faixa da legenda; a tela fica em 1920 x 810 (um 16:9 de 1920 sai em 1440 x 810, 75 %)
AREA_H = SIZE[1] - BAND_H
FULL_SCALE = 1.5                 # a tela escura da F5.19 (1280 x 720) em 1920 x 1080
BT709, MPEG_RANGE = 1, 1
VIDEO_THREADS = 1                # o codificador numa thread, como a F5.19
SCREEN_FACTOR = 2                # a tela na estimativa do espaço: o MP4 saiu 0,85 a 1,2 vez os pedaços (reuniões sintéticas)


def fit_box(width: int, height: int, area_w: int = SIZE[0], area_h: int = AREA_H) -> tuple[int, int, int, int]:
    """(x, y, largura, altura) da tela encaixada na área, sem distorcer e centrada, tudo par (o yuv420p pede)."""
    scale = min(area_w / max(1, width), area_h / max(1, height))
    w = min(area_w, max(2, round(width * scale) // 2 * 2))
    h = min(area_h, max(2, round(height * scale) // 2 * 2))
    return (area_w - w) // 4 * 2, (area_h - h) // 4 * 2, w, h


def hex_rgb(value: str) -> tuple[int, int, int]:
    """"#rrggbb" em (r, g, b)."""
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _planes(frame: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Os três planos de um quadro yuv420p, sem a folga do fim de cada linha."""
    out = []
    for plane in frame.planes:
        raw = np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)
        out.append(raw[:, : plane.width])
    return out[0], out[1], out[2]


class Canvas:
    """O quadro 1920 x 1080 em yuv420p (Y, depois U e V, como o `from_ndarray` pede), com a área da tela e a faixa."""

    def __init__(self, mux: _Muxer, band: Callable[[Screen], np.ndarray], background: tuple[int, int, int]):
        from av.video.reformatter import Colorspace, Interpolation, VideoReformatter

        self.mux, self.band = mux, band
        w, h = SIZE
        self.buf = np.zeros((h * 3 // 2, w), np.uint8)
        self.y = self.buf[:h]
        self.u = self.buf[h: h + h // 4].reshape(h // 2, w // 2)
        self.v = self.buf[h + h // 4:].reshape(h // 2, w // 2)
        y, u, v = _planes(mux.yuv(np.full((16, 16, 3), background, np.uint8)))
        self.fill = (int(y[8, 8]), int(u[4, 4]), int(v[4, 4]))
        self._sws = VideoReformatter()
        self._sws_args = {"dst_colorspace": Colorspace.ITU709, "dst_color_range": MPEG_RANGE, "interpolation": Interpolation.AREA}
        self._box: tuple[int, int, int, int] | None = None

    def caption(self, screen: Screen) -> None:
        y, u, v = _planes(self.mux.yuv(self.band(screen)))
        self.y[AREA_H:] = y
        self.u[AREA_H // 2:] = u
        self.v[AREA_H // 2:] = v

    def screen(self, rgb: np.ndarray) -> None:
        box = fit_box(rgb.shape[1], rgb.shape[0])
        if box != self._box:                             # outro tamanho (a troca de monitor): a tarja de novo
            self.y[:AREA_H], self.u[: AREA_H // 2], self.v[: AREA_H // 2] = self.fill
            self._box = box
        x, y0, w, h = box
        src = self.mux.av.VideoFrame.from_numpy_buffer(np.ascontiguousarray(rgb), format="rgb24")
        py, pu, pv = _planes(self._sws.reformat(src, w, h, "yuv420p", **self._sws_args))
        self.y[y0: y0 + h, x: x + w] = py
        self.u[y0 // 2: (y0 + h) // 2, x // 2: (x + w) // 2] = pu
        self.v[y0 // 2: (y0 + h) // 2, x // 2: (x + w) // 2] = pv

    def frame(self) -> Any:
        frame = self.mux.av.VideoFrame.from_ndarray(self.buf, format="yuv420p")
        with suppress(AttributeError, ValueError):       # a etiqueta de cor do quadro, como a do reformatador
            frame.colorspace, frame.color_range = BT709, MPEG_RANGE
        return frame


class ScreenPictures:
    """Os quadros do vídeo com a tela (o `Pictures` de `write_pictures`): cada pedaço lido e decodificado uma vez, em ordem,
    só quando o vídeo chega nele; cada quadro composto uma vez por troca de legenda ou de tela."""

    def __init__(self, captions: Sequence[tuple[int, Screen]], chunks: Sequence[Mapping[str, Any]], spans: Sequence[tuple[int, int]],
                 read: Read, decode: Decode, band: FrameRenderer, full: FrameRenderer):
        clock = FileClock(spans)
        self.captions, self.band, self.full = captions, band, full
        self.moments = moments(captions, shots(chunks, read, decode, clock), clock.duration_ms)
        self.pending = next(self.moments, None)
        self.canvas: Canvas | None = None
        self.shown: tuple[int, tuple[int, int] | None] | None = None   # (legenda, quadro da tela) do quadro pronto
        self.caption: int | None = None                  # a legenda já na faixa
        self.shot: tuple[int, int] | None = None         # o quadro da tela já na área
        self._frame: Any = None
        self.screen_frames = 0                           # quadros gravados compostos (para o log)

    def until(self, mux: _Muxer, limit_ms: int | None, check: Callable[[], None]) -> None:
        while self.pending is not None and (limit_ms is None or self.pending[0] < limit_ms):
            t, ci, state = self.pending
            mux.encode(self._picture(mux, ci, state), t)
            check()
            self.pending = next(self.moments, None)

    def _picture(self, mux: _Muxer, ci: int, state: ScreenState) -> Any:
        key = (ci, state.key if state is not None else None)
        if key == self.shown:
            return self._frame
        self.shown = key
        screen = self.captions[ci][1]
        if state is None:
            self._frame = mux.yuv(self.full.render(screen))
            return self._frame
        if self.canvas is None:
            self.canvas = Canvas(mux, self.band.render, hex_rgb(self.band.t.window))
        if ci != self.caption:
            self.canvas.caption(screen)
            self.caption = ci
        if state.key != self.shot:
            self.canvas.screen(state.rgb)
            self.shot = state.key
            self.screen_frames += 1
        self._frame = self.canvas.frame()
        return self._frame


def renderers(title: str, date: str, cues: Sequence[Cue]) -> tuple[FrameRenderer, FrameRenderer]:
    """Na thread da interface: o desenhista da faixa (1920 x `BAND_H`) e o da tela escura inteira (1920 x 1080)."""
    band = FrameRenderer("", "", cues, size=(SIZE[0], BAND_H), look=BAND_LOOK)
    full = FrameRenderer(title, date, cues, size=SIZE, look=scaled(Look(), FULL_SCALE))
    return band, full


class ScreenJob:
    """O vídeo com a tela de uma reunião: montado na thread da interface (os desenhistas e o tamanho dos pedaços), `write`
    roda na tarefa."""

    def __init__(self, store: Store, plan: AudioPlan, title: str, date: str, cues: Sequence[Cue], screen_bytes: int):
        self.store, self.plan, self.screen_bytes = store, plan, screen_bytes
        self.band, self.full = renderers(title, date, cues)

    @property
    def extra_bytes(self) -> int:
        """O que a tela soma à estimativa do vídeo da F5.19, com folga (o espaço livre antes de gerar)."""
        return self.screen_bytes * SCREEN_FACTOR

    def write(self, target: Path, tick: Tick, captions: Sequence[tuple[int, Screen]], text: Sequence[TextSample] = ()) -> tuple[Path, int]:
        """Na tarefa: o MP4 de 1920 x 1080 (os pedaços listados de novo aqui, na conexão da tarefa e fora de transação, antes
        de o áudio ser lido; um pedaço apagado no meio vira buraco); devolve o caminho e os quadros gravados."""
        from ..screen_rec.segment import decode_segment

        store, mid = self.store, self.plan.meeting_id
        chunks = store.screen.list_chunks(mid)
        pictures = ScreenPictures(captions, chunks, self.plan.spans, lambda idx: store.screen.get_chunk(mid, idx), decode_segment,
                                  self.band, self.full)
        out, frames = write_pictures(store, self.plan, pictures, target, tick, SIZE, text, threads=VIDEO_THREADS)
        log.info("reunião %s: vídeo com a tela: %d pedaço(s), %d quadro(s) da tela compostos", mid, len(chunks), pictures.screen_frames)
        return out, frames


def screen_job(store: Store, plan: AudioPlan, title: str, date: str, cues: Sequence[Cue]) -> ScreenJob | None:
    """Na thread da interface: o vídeo com a tela, quando a reunião tem pedaços; None sem tela (o vídeo da F5.19)."""
    chunks = store.screen.list_chunks(plan.meeting_id)
    if not chunks:
        return None
    return ScreenJob(store, plan, title, date, cues, sum(int(c["bytes"]) for c in chunks))


__all__ = ["AREA_H", "BAND_H", "SIZE", "Canvas", "ScreenJob", "ScreenPictures", "fit_box", "hex_rgb", "renderers", "screen_job"]
