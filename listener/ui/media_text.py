"""A faixa de texto do vídeo da reunião (F5.26, item 6; D-112): as legendas de `media_cues.text_track` numa faixa `mov_text`
(o texto temporizado do MP4) que o player liga e desliga. O PyAV chega pronto de `write_video` (só importado lá dentro).

[fato, sonda do lote 1, PyAV 19.0.2] O codificador `mov_text` só abre com um cabeçalho ASS (`subtitle_header`) e só aceita
o evento no formato ASS ("ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text"): texto solto sai vazio, por isso
um pacote vazio é erro. No texto, só a barra invertida é especial (`\\N`, `\\n` e `{\\` viram quebra e estilo): ela troca pelo
U+2216 (SET MINUS), parecido; as linhas de falas ao mesmo tempo vão com `\\N`. As amostras saem sem sobreposição (o MP4 empurra a
que começa antes do fim da anterior) e intercaladas com o áudio e os quadros a cada bloco (A-06); o muxer põe a amostra
vazia nos intervalos."""
from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .media_cues import TextSample

CODEC = "mov_text"
TIME_BASE = Fraction(1, 1000)
ASS_HEADER = ("[Script Info]\r\nScriptType: v4.00+\r\nPlayResX: 1280\r\nPlayResY: 720\r\n\r\n[V4+ Styles]\r\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
              "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
              "MarginV, Encoding\r\n"
              "Style: Default,Arial,36,&Hffffff,&Hffffff,&H0,&H0,0,0,0,0,100,100,0,0,1,1,0,2,20,20,20,0\r\n\r\n[Events]\r\n"
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n")
EVENT = "0,0,Default,,0,0,0,,"                  # o evento ASS sem os tempos (eles vão no pacote)
BACKSLASH, LOOKALIKE = "\\", chr(0x2216)     # a barra invertida e o SET MINUS, parecido


def ass_text(lines: Sequence[str]) -> str:
    """As linhas no texto do evento ASS: a barra invertida trocada, uma linha por `\\N`."""
    return "\\N".join(line.replace(BACKSLASH, LOOKALIKE) for line in lines)


class TextTrack:
    """A faixa `mov_text` no contêiner aberto (antes do primeiro pacote) e as amostras a gravar, em ordem."""

    def __init__(self, av: Any, out: Any, samples: Sequence[TextSample]):
        from av.subtitles.subtitle import SubtitleSet

        self.subtitle_set = SubtitleSet
        self.out, self.samples, self.next = out, samples, 0
        self.stream = out.add_stream(CODEC)
        self.stream.time_base = TIME_BASE
        cc = self.stream.codec_context
        cc.time_base = TIME_BASE
        cc.subtitle_header = ASS_HEADER.encode()

    def until(self, limit_ms: int | None) -> None:
        """Grava as amostras que começam antes de `limit_ms` (None: todas as que faltam)."""
        while self.next < len(self.samples) and (limit_ms is None or self.samples[self.next].start_ms < limit_ms):
            self._mux(self.samples[self.next])
            self.next += 1

    def _mux(self, sample: TextSample) -> None:
        span = sample.end_ms - sample.start_ms
        event = (EVENT + ass_text(sample.lines)).encode()
        packet = self.stream.codec_context.encode_subtitle(self.subtitle_set.create(event, 0, span, sample.start_ms * 1000))
        if packet.size <= 2:                     # só o tamanho de 2 bytes: o codificador recusou o texto
            raise RuntimeError("o codificador mov_text devolveu uma legenda vazia")
        packet.stream, packet.time_base = self.stream, TIME_BASE
        packet.pts = packet.dts = sample.start_ms
        packet.duration = span
        self.out.mux(packet)


def text_track_available(av: Any) -> bool:
    """O FFmpeg do PyAV tem o codificador `mov_text` (o nativo, que vem em toda compilação)."""
    return CODEC in av.codecs_available


__all__ = ["ASS_HEADER", "CODEC", "TextTrack", "ass_text", "text_track_available"]
