"""Os codificadores de vídeo de CPU (CP14), do melhor para o substituto do próprio FFmpeg, e a escolha do primeiro que abre.
Servem ao vídeo exportado (F5.19, `ui/media_video.py`) e à gravação da tela (F5.27, `screen_rec/segment.py`). O "h264"
genérico do FFmpeg podia cair num codificador de placa de vídeo (A-03 da F5.19): por isso a lista é explícita. O PyAV chega
por parâmetro: este módulo não o importa."""
from __future__ import annotations

from contextlib import suppress
from fractions import Fraction
from typing import Any

CODECS = ("libx264", "libopenh264", "mpeg4")     # só de CPU (CP14), do melhor para o substituto do próprio FFmpeg


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


__all__ = ["CODECS", "pick_codec"]
