"""Persistência (CP9): `listener.db` (texto, configuração, vetores) mais um arquivo de áudio por mês
(`listener-audio-AAAA-MM.db`, FLAC em pedaços; D-039). Um só ponto de acesso (`Store`), uma conexão por thread."""
from .db import Store

__all__ = ["Store"]
