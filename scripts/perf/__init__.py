"""Sondas do lote 0 da F5.18 (D-096): os módulos `m_*` medem (M-01 a M-10) e `replay` toca uma reunião no filho ao vivo.
`common` tem a saída JSON, o banco real só leitura e os filhos; `audio` as fontes; `models` a carga dos modelos como o
worker faz. A linha de comando é `scripts/probe_perf.py`. Nada aqui faz parte do produto."""
from __future__ import annotations

__all__ = ["audio", "common", "models"]
