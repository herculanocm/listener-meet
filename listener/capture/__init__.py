"""Gravação em dois canais sincronizados (L = microfone, R = app ou sistema), portável.

clock: relógio único da sessão · chunker: pedaços com preenchimento de buracos · audio: mono/reamostragem/FLAC ·
recorder: orquestra canais, pausa, mudo, marcadores, saúde e grava no SQLite por uma thread de trabalho.
"""
