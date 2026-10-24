"""Persistência (CP9): dois arquivos SQLite do app, anexados — listener.db (texto, configuração, vetores)
e listener-audio.db (pedaços FLAC). Um só ponto de acesso (Store), com lock para uso por várias threads."""
from .db import Store

__all__ = ["Store"]
