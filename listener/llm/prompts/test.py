"""Tarefa `test` ("testar conexão" e "testar pelo worker"): o canário de `adapters_common.canary` (JSON mínimo com esquema)."""
from __future__ import annotations

from ..adapters_common import TEST_PROMPT, TEST_SCHEMA
from ..types import Message, Role

PROMPT_VERSION = "2.0"                  # D-071: o canário em pt-BR
SCHEMA: dict = TEST_SCHEMA


def build(_ctx: object = None) -> tuple[Message, ...]:
    return (Message(Role.USER, TEST_PROMPT),)
